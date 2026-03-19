"""
Experiment Runner — Orchestrator
=================================
Runs one full evaluation cycle: generate samples → eval deterministic →
eval LLM judge → aggregate → append to results.tsv.

All settings are read from config.yaml.

Usage:
    python3 tools/experiment_runner.py --run-id baseline
    python3 tools/experiment_runner.py --run-id exp_001 --description "Added examples"
    python3 tools/experiment_runner.py --run-id exp_001 --decision KEEP --description "Added examples"
"""

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import PROJECT_ROOT, load_config, sanitise_description, validate_config

TOOLS_DIR = PROJECT_ROOT / "tools"


def _validate_run_id(run_id: str) -> str:
    """Validate run_id contains only safe characters."""
    if not re.match(r'^[a-zA-Z0-9_-]+$', run_id):
        print(f"Error: run_id must be alphanumeric/underscore/hyphen, got: {run_id!r}", file=sys.stderr)
        sys.exit(1)
    return run_id


def _safe_path(user_path: str, must_exist: bool = False) -> Path:
    """Resolve a path and ensure it lives within PROJECT_ROOT."""
    resolved = Path(user_path).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        print(f"Error: Path escapes project root: {user_path}", file=sys.stderr)
        sys.exit(1)
    if must_exist and not resolved.exists():
        print(f"Error: Path not found: {user_path}", file=sys.stderr)
        sys.exit(1)
    return resolved


def get_all_metric_names(cfg: dict) -> list[str]:
    """Get ordered list of all metric names from config."""
    names = []
    for m in cfg.get("deterministic_metrics", []):
        names.append(m["name"])
    for m in cfg.get("llm_judge_dimensions", []):
        names.append(m["name"])
    return names


def run_tool(script, args):
    """Run a tool script and return the result."""
    cmd = [sys.executable, str(TOOLS_DIR / script)] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=300)
    except subprocess.TimeoutExpired:
        print(f"ERROR: {script} timed out after 300s", file=sys.stderr)
        # Return a failed result object
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=f"{script} timed out after 300s")


def run_experiment(
    run_id: str,
    cfg: dict,
    description: str = "",
    decision: str = "",
) -> dict:
    """Execute one full evaluation cycle."""
    skill_path = cfg.get("skill_path", "SKILL.md")
    prompts_path = cfg.get("prompts_path", "prompts/prompts.json")
    results_tsv = cfg.get("results_tsv", "results.tsv")
    has_deterministic = bool(cfg.get("deterministic_metrics"))

    metric_names = get_all_metric_names(cfg)

    samples_dir = PROJECT_ROOT / ".tmp" / "samples" / run_id
    evals_dir = PROJECT_ROOT / ".tmp" / "evals" / run_id
    evals_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # Step 1: Generate samples
    print(f"\n{'='*60}")
    print(f"EXPERIMENT: {run_id}")
    print(f"{'='*60}")
    print(f"\n[1/{'4' if has_deterministic else '3'}] Generating samples...")

    max_concurrent = cfg.get("max_concurrent", 1)
    result = run_tool("generate_samples.py", [
        "--skill-path", skill_path,
        "--prompts-path", prompts_path,
        "--output-dir", str(samples_dir),
        "--max-concurrent", str(max_concurrent),
    ])
    if result.returncode != 0:
        print(f"ERROR generating samples:\n{result.stderr}")
        sys.exit(1)
    print(result.stdout)

    # Get list of generated sample files
    sample_files = sorted(samples_dir.glob("sample_*.txt"))
    if not sample_files:
        print("ERROR: No sample files generated")
        sys.exit(1)

    step = 2

    # Step 2 (optional): Run deterministic eval
    if has_deterministic:
        print(f"[{step}/{4}] Running deterministic evaluation on {len(sample_files)} samples...")
        for sf in sample_files:
            sample_name = sf.stem
            out_path = evals_dir / f"{sample_name}_deterministic.json"
            result = run_tool("eval_deterministic.py", [
                "--sample-path", str(sf),
                "--output-path", str(out_path),
            ])
            if result.returncode != 0:
                print(f"  WARNING: Deterministic eval failed for {sf.name}: {result.stderr}")
        step += 1

    # Step N: Run LLM judge on each sample
    total_steps = 4 if has_deterministic else 3
    print(f"[{step}/{total_steps}] Running LLM judge evaluation on {len(sample_files)} samples...")

    def _run_judge(sf):
        sample_name = sf.stem
        out_path = evals_dir / f"{sample_name}_llm_judge.json"
        judge_args = [
            "--sample-path", str(sf),
            "--output-path", str(out_path),
        ]
        if cfg.get("judge_sees_skill", False):
            judge_args += ["--skill-path", str(PROJECT_ROOT / skill_path)]
        return sf.name, run_tool("eval_llm_judge.py", judge_args)

    if max_concurrent > 1 and len(sample_files) > 1:
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = [executor.submit(_run_judge, sf) for sf in sample_files]
            for future in as_completed(futures):
                name, result = future.result()
                if result.returncode != 0:
                    print(f"  WARNING: LLM judge failed for {name}: {result.stderr}")
                if result.stdout:
                    print(f"  {result.stdout.strip()}")
    else:
        for sf in sample_files:
            name, result = _run_judge(sf)
            if result.returncode != 0:
                print(f"  WARNING: LLM judge failed for {name}: {result.stderr}")
            if result.stdout:
                print(f"  {result.stdout.strip()}")
    step += 1

    # Step N: Aggregate scores
    print(f"[{step}/{total_steps}] Aggregating scores...")
    agg_path = evals_dir / "aggregate.json"
    result = run_tool("score_aggregator.py", [
        "--eval-dir", str(evals_dir),
        "--output-path", str(agg_path),
    ])
    print(result.stdout)
    if result.returncode != 0:
        print(f"ERROR aggregating scores:\n{result.stderr}")
        sys.exit(1)

    # Read aggregate results
    agg = json.loads(agg_path.read_text(encoding="utf-8"))

    elapsed = time.time() - start_time

    # Append to results.tsv
    tsv_path = PROJECT_ROOT / results_tsv
    header_cols = ["run_id", "timestamp", "composite_score"] + metric_names + ["change_description", "decision"]
    header = "\t".join(header_cols)

    if not tsv_path.exists():
        tsv_path.write_text(header + "\n", encoding="utf-8")

    metrics = agg["metric_averages"]
    row_parts = [
        run_id,
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        f"{agg['composite_score']:.4f}",
    ]
    for name in metric_names:
        row_parts.append(f"{metrics.get(name, 0.0):.4f}")
    row_parts.append(sanitise_description(description))
    row_parts.append(decision)

    row = "\t".join(row_parts)

    with open(tsv_path, "a", encoding="utf-8") as f:
        f.write(row + "\n")

    print(f"\nResults appended to {tsv_path}")
    print(f"Total time: {elapsed:.1f}s")
    print(f"\nCOMPOSITE SCORE: {agg['composite_score']:.4f}")

    return agg


def main():
    cfg = load_config()
    validate_config(cfg)
    parser = argparse.ArgumentParser(description="Run one full evaluation cycle")
    parser.add_argument("--run-id", required=True, help="Unique identifier for this run")
    parser.add_argument("--description", default="", help="One-line description of what changed")
    parser.add_argument("--decision", default="", help="KEEP, DISCARD, or BASELINE")
    args = parser.parse_args()

    run_experiment(
        run_id=_validate_run_id(args.run_id),
        cfg=cfg,
        description=sanitise_description(args.description),
        decision=args.decision,
    )


if __name__ == "__main__":
    main()
