#!/usr/bin/env python3
"""Run repeatable, isolated AutoEvaluation benchmark campaigns.

This command is intentionally dry-run by default because a publishable
benchmark can make thousands of paid model calls. Pass ``--execute`` only
after reviewing the printed scope and cost cap in config.yaml.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import PROJECT_ROOT, load_config, load_env, split_prompt_sets, validate_prompts


def copy_campaign_workspace(destination: Path, cfg: dict) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("tools", "config.yaml"):
        source = PROJECT_ROOT / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
    for configured_path in (
        Path(cfg.get("skill_path", "SKILL.md")),
        Path(cfg.get("prompts_path", "prompts/prompts.json")),
    ):
        if configured_path.is_absolute() or ".." in configured_path.parts:
            raise ValueError(f"benchmark inputs must be project-relative: {configured_path}")
        (destination / configured_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / configured_path, destination / configured_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated independent benchmark campaigns")
    parser.add_argument("--campaigns", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--output", default="benchmark-runs")
    parser.add_argument("--execute", action="store_true", help="Confirm paid execution")
    args = parser.parse_args()
    if args.campaigns < 2 or args.iterations < 1:
        parser.error("use at least 2 campaigns and 1 iteration")

    cfg = load_config()
    load_env()
    prompts = json.loads((PROJECT_ROOT / cfg.get("prompts_path", "prompts/prompts.json")).read_text(encoding="utf-8"))
    validate_prompts(prompts)
    train, validation, final_prompts = split_prompt_sets(
        prompts, float(cfg.get("holdout_fraction", 0.3)), float(cfg.get("final_test_fraction", 0.2))
    )
    print(f"Benchmark plan: {args.campaigns} independent campaigns × {args.iterations} attempts")
    print(f"Prompt split: {len(train)} train / {len(validation)} validation / {len(final_prompts)} final")
    print(f"Per-campaign cost cap: ${float(cfg.get('max_cost_usd', 0)):.2f} (0 means unlimited)")
    print(f"Output: {args.output}/")
    if not args.execute:
        print("Dry run only. Re-run with --execute after reviewing model access and budget.")
        return

    root = PROJECT_ROOT / args.output
    root.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index in range(1, args.campaigns + 1):
        campaign = root / f"campaign-{index:02d}"
        if campaign.exists():
            raise SystemExit(f"Refusing to replace existing {campaign}")
        copy_campaign_workspace(campaign, cfg)
        print(f"\nCampaign {index}/{args.campaigns}: {campaign}")
        run = subprocess.run(
            [
                sys.executable, "tools/run_loop.py", "--iterations", str(args.iterations),
                "--skip-final-test",
            ],
            cwd=campaign,
        )
        if run.returncode == 0:
            run = subprocess.run([sys.executable, "tools/run_loop.py", "--finalize"], cwd=campaign)
        final_path = campaign / "final_test_aggregate.json"
        final_result = json.loads(final_path.read_text(encoding="utf-8")) if final_path.exists() else None
        summaries.append({
            "campaign": index,
            "returncode": run.returncode,
            "final_test_score": final_result.get("composite_score") if final_result else None,
        })
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "campaigns": summaries,
        "iterations_per_campaign": args.iterations,
        "models": {"generation": cfg.get("model"), "judge": cfg.get("judge_model", cfg.get("model"))},
        "prompt_counts": {"train": len(train), "validation": len(validation), "final": len(final_prompts)},
    }
    (root / "benchmark-summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nBenchmark summary: {root / 'benchmark-summary.json'}")


if __name__ == "__main__":
    main()
