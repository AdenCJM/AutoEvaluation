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
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import results_io
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
    prompt_set: str = "train",
    write_tsv: bool = True,
    replace_existing: bool = False,
) -> dict:
    """Execute one full evaluation cycle.

    prompt_set selects which prompt split to evaluate ("train" for loop
    iterations, "holdout" for validation, "all" for legacy behaviour).
    write_tsv=False runs an auxiliary evaluation (holdout check, noise
    measurement) without appending a row to results.tsv.
    """
    skill_path = cfg.get("skill_path", "SKILL.md")
    prompts_path = cfg.get("prompts_path", "prompts/prompts.json")
    results_tsv = cfg.get("results_tsv", "results.tsv")
    has_deterministic = bool(cfg.get("deterministic_metrics"))

    metric_names = get_all_metric_names(cfg)

    final_samples_dir = PROJECT_ROOT / ".tmp" / "samples" / run_id
    final_evals_dir = PROJECT_ROOT / ".tmp" / "evals" / run_id
    existing = [p for p in (final_samples_dir, final_evals_dir) if p.exists()]
    if existing and not replace_existing:
        names = ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in existing)
        raise RuntimeError(
            f"run_id {run_id!r} already has output ({names}); choose a new run id "
            "or pass --replace-run to explicitly replace an interrupted run"
        )
    if replace_existing:
        for path in existing:
            shutil.rmtree(path)

    # Work in a unique staging tree. Only a complete experiment is promoted
    # to the canonical run directories, so partial retries cannot contaminate
    # later scoring.
    work_root = PROJECT_ROOT / ".tmp" / "work" / f"{run_id}-{uuid.uuid4().hex[:10]}"
    samples_dir = work_root / "samples"
    evals_dir = work_root / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # Step 1: Generate samples
    print(f"\n{'='*60}")
    print(f"EXPERIMENT: {run_id} (prompt set: {prompt_set})")
    print(f"{'='*60}")
    print(f"\n[1/{'4' if has_deterministic else '3'}] Generating samples...")

    max_concurrent = cfg.get("max_concurrent", 1)
    result = run_tool("generate_samples.py", [
        "--skill-path", skill_path,
        "--prompts-path", prompts_path,
        "--output-dir", str(samples_dir),
        "--max-concurrent", str(max_concurrent),
        "--replicates", str(cfg.get("replicates_per_prompt", 1)),
        "--prompt-set", prompt_set,
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

    if agg.get("judge_errors"):
        print(f"Note: {agg['judge_errors']} judge failures excluded "
              f"({agg['sample_count']}/{agg.get('samples_total', agg['sample_count'])} samples used)")

    # Atomically promote complete outputs before recording the TSV row.
    final_samples_dir.parent.mkdir(parents=True, exist_ok=True)
    final_evals_dir.parent.mkdir(parents=True, exist_ok=True)
    samples_dir.replace(final_samples_dir)
    evals_dir.replace(final_evals_dir)
    try:
        work_root.rmdir()
    except OSError:
        pass

    # Append to results.tsv (skipped for auxiliary runs like validation checks)
    if write_tsv:
        tsv_path = PROJECT_ROOT / results_tsv
        metrics = agg["metric_averages"]
        row = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "composite_score": f"{agg['composite_score']:.4f}",
            "change_description": sanitise_description(description),
            "decision": decision,
            "composite_stddev": f"{agg.get('composite_stddev', 0.0):.4f}",
            "n_samples": agg.get("sample_count", 0),
            "judge_errors": agg.get("judge_errors", 0),
            "holdout_composite": "",
        }
        for name in metric_names:
            row[name] = f"{metrics.get(name, 0.0):.4f}"
        results_io.append_row(tsv_path, row, metric_names)
        print(f"\nResults appended to {tsv_path}")

    print(f"Total time: {elapsed:.1f}s")
    print(f"\nCOMPOSITE SCORE: {agg['composite_score']:.4f} ± {agg.get('composite_stddev', 0.0):.4f}")

    return agg


def main():
    cfg = load_config()
    validate_config(cfg)
    parser = argparse.ArgumentParser(description="Run one full evaluation cycle")
    parser.add_argument("--run-id", required=True, help="Unique identifier for this run")
    parser.add_argument("--description", default="", help="One-line description of what changed")
    parser.add_argument("--decision", default="", help="KEEP, DISCARD, or BASELINE")
    parser.add_argument("--prompt-set", choices=["all", "train", "holdout", "final_test"], default="train",
                        help="Prompt split to evaluate (default: train)")
    parser.add_argument("--no-tsv", action="store_true",
                        help="Auxiliary run: don't append a row to results.tsv")
    parser.add_argument("--replace-run", action="store_true",
                        help="Explicitly replace existing output for this run id")
    args = parser.parse_args()

    try:
        run_experiment(
            run_id=_validate_run_id(args.run_id),
            cfg=cfg,
            description=sanitise_description(args.description),
            decision=args.decision,
            prompt_set=args.prompt_set,
            write_tsv=not args.no_tsv,
            replace_existing=args.replace_run,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
