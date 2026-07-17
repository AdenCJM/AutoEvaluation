"""Campaign lifecycle helpers for AutoEvaluation.

A campaign owns one prompt split, one experiment history, and one untouched
final-test result. Runtime files remain in their backwards-compatible root
locations while a campaign is active; ``archive_campaign`` moves a complete
snapshot into ``campaigns/<id>/`` before clearing the active runtime state.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from run_state import atomic_write_text
from utils import PROJECT_ROOT, load_config

CAMPAIGNS_DIR = PROJECT_ROOT / "campaigns"
ACTIVE_MANIFEST = PROJECT_ROOT / ".tmp" / "campaign.json"

ROOT_ARTIFACTS = (
    "config.yaml",
    "SKILL.md",
    "SKILL.md.best",
    "results.tsv",
    "best_aggregate.json",
    "best_holdout_aggregate.json",
    "baseline_final_test_aggregate.json",
    "final_test_aggregate.json",
)

TMP_ARTIFACTS = (
    "samples",
    "evals",
    "skills",
    "run-summary.md",
    "run_status.json",
    "run_state.json",
    "campaign.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return value or datetime.now().strftime("campaign-%Y%m%d-%H%M%S")


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_manifest() -> dict:
    try:
        return json.loads(ACTIVE_MANIFEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def ensure_manifest(name: str | None = None) -> dict:
    manifest = load_manifest()
    if manifest:
        return manifest
    manifest = {
        "id": _safe_id(name or datetime.now().strftime("campaign-%Y%m%d-%H%M%S")),
        "name": name or "AutoEvaluation campaign",
        "created_at": _now(),
        "status": "active",
        "git_commit": _git_commit(),
    }
    atomic_write_text(ACTIVE_MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return manifest


def update_manifest(**fields) -> dict:
    manifest = ensure_manifest()
    manifest.update(fields)
    manifest["updated_at"] = _now()
    atomic_write_text(ACTIVE_MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return manifest


def campaign_status() -> dict:
    manifest = ensure_manifest()
    cfg = load_config() if (PROJECT_ROOT / "config.yaml").exists() else {}
    results = PROJECT_ROOT / cfg.get("results_tsv", "results.tsv")
    rows = 0
    kept = 0
    if results.exists():
        import csv
        with results.open(encoding="utf-8", newline="") as handle:
            data = list(csv.DictReader(handle, delimiter="\t"))
        rows = len(data)
        kept = sum(1 for row in data if (row.get("decision") or "").strip() == "KEEP")
    final_path = PROJECT_ROOT / "final_test_aggregate.json"
    finalized = final_path.exists() or manifest.get("status") == "finalized"
    return {
        **manifest,
        "experiments": rows,
        "kept": kept,
        "finalized": finalized,
        "final_test_ready": rows > 0 and not finalized,
    }


def _copy_path(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def archive_campaign(name: str | None = None) -> Path | None:
    """Archive active campaign artifacts. Returns None when there is no run."""
    cfg = load_config() if (PROJECT_ROOT / "config.yaml").exists() else {}
    results = PROJECT_ROOT / cfg.get("results_tsv", "results.tsv")
    if not results.exists() and not (PROJECT_ROOT / "SKILL.md.best").exists():
        return None

    manifest = ensure_manifest(name)
    archive_id = _safe_id(name or manifest.get("id", "campaign"))
    destination = CAMPAIGNS_DIR / archive_id
    suffix = 2
    while destination.exists():
        destination = CAMPAIGNS_DIR / f"{archive_id}-{suffix}"
        suffix += 1

    for relative in ROOT_ARTIFACTS:
        _copy_path(PROJECT_ROOT / relative, destination / relative)
    prompts_path = cfg.get("prompts_path", "prompts/prompts.json")
    _copy_path(PROJECT_ROOT / prompts_path, destination / prompts_path)
    for relative in TMP_ARTIFACTS:
        _copy_path(PROJECT_ROOT / ".tmp" / relative, destination / ".tmp" / relative)
    for token_log in (PROJECT_ROOT / ".tmp").glob("token_usage_*.jsonl"):
        _copy_path(token_log, destination / ".tmp" / token_log.name)

    archived_manifest = {
        **manifest,
        "status": "archived",
        "archived_at": _now(),
        "archive_path": str(destination.relative_to(PROJECT_ROOT)),
        "finalized": (PROJECT_ROOT / "final_test_aggregate.json").exists(),
    }
    atomic_write_text(
        destination / "campaign.json",
        json.dumps(archived_manifest, indent=2) + "\n",
    )
    return destination


def clear_runtime_state() -> None:
    cfg = load_config() if (PROJECT_ROOT / "config.yaml").exists() else {}
    root_paths = list(ROOT_ARTIFACTS[2:])
    configured_results = cfg.get("results_tsv", "results.tsv")
    if configured_results not in root_paths:
        root_paths.append(configured_results)
    for relative in root_paths:
        path = PROJECT_ROOT / relative
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for relative in TMP_ARTIFACTS:
        path = PROJECT_ROOT / ".tmp" / relative
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for path in (PROJECT_ROOT / ".tmp").glob("token_usage_*.jsonl"):
        path.unlink(missing_ok=True)


def new_campaign(name: str | None = None, start_from_best: bool = True) -> tuple[Path | None, dict]:
    best_content = None
    best_path = PROJECT_ROOT / "SKILL.md.best"
    if start_from_best and best_path.exists():
        best_content = best_path.read_text(encoding="utf-8")
    archive = archive_campaign()
    clear_runtime_state()
    if best_content is not None:
        atomic_write_text(PROJECT_ROOT / "SKILL.md", best_content)
    manifest = ensure_manifest(name)
    return archive, manifest
