#!/usr/bin/env python3
"""
Standalone Optimisation Loop Driver
=====================================
Runs the analyse → modify → evaluate → decide loop WITHOUT Claude Code.
Uses the configured LLM to do the "thinking" (analyse weaknesses, modify SKILL.md)
and the existing Python tools for evaluation.

Usage:
    python3 tools/run_loop.py --iterations 10
    python3 tools/run_loop.py --hours 2.5

Quick start (no config.yaml needed):
    python3 tools/run_loop.py --skill SKILL.md --provider gemini --iterations 5
"""

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import results_io
from decision import decide
from model_client import ModelClient
from utils import DEFAULT_MODELS, PROJECT_ROOT, default_dimensions, load_config, sanitise_description, validate_config

BEST_AGG_PATH = PROJECT_ROOT / "best_aggregate.json"
BEST_HOLDOUT_AGG_PATH = PROJECT_ROOT / "best_holdout_aggregate.json"


def get_next_run_id(results_tsv: Path) -> str:
    """Determine the next experiment ID from results.tsv."""
    rows = results_io.read_rows(results_tsv)
    if not rows:
        return "baseline"

    last_id = rows[-1].get("run_id") or ""
    if last_id == "baseline":
        return "exp_001"

    match = re.search(r"exp_(\d+)", last_id)
    if match:
        return f"exp_{int(match.group(1)) + 1:03d}"
    return f"exp_{len(rows):03d}"


def get_best_score(results_tsv: Path) -> float:
    """Read the best composite score from results.tsv."""
    return results_io.best_composite(results_tsv)


def get_recent_results(results_tsv: Path, n: int = 3) -> str:
    """Get the last N rows of results.tsv as a string for context."""
    if not results_tsv.exists():
        return "No results yet."

    lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
    header = lines[0] if lines else ""
    recent = lines[-n:] if len(lines) > n else lines[1:]
    return header + "\n" + "\n".join(recent)


def get_latest_run_id(results_tsv: Path) -> str | None:
    """Return the run_id of the most recent completed experiment."""
    return results_io.latest_run_id(results_tsv)


def _get_worst_samples_context(latest_run_id: str, n: int = 2) -> str:
    """Read the N worst-scoring samples and their judge reasoning.

    Returns a formatted string for the modifier LLM, or "" if data is unavailable.
    """
    evals_dir = PROJECT_ROOT / ".tmp" / "evals" / latest_run_id
    samples_dir = PROJECT_ROOT / ".tmp" / "samples" / latest_run_id

    if not evals_dir.exists():
        return ""

    # Read all judge JSONs and compute per-sample composite scores
    sample_scores = []
    for judge_file in sorted(evals_dir.glob("*_llm_judge.json")):
        try:
            data = json.loads(judge_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "error" in data:
            continue

        # Compute mean normalised score across all dimensions
        scores = []
        for key, val in data.items():
            if isinstance(val, dict) and "normalised" in val:
                scores.append(val["normalised"])
        if not scores:
            continue

        # Derive sample name: "sample_0_intro_email_llm_judge.json" → "sample_0_intro_email"
        sample_name = judge_file.name.replace("_llm_judge.json", "")
        mean_score = sum(scores) / len(scores)
        sample_scores.append({
            "sample_name": sample_name,
            "mean_score": mean_score,
            "judge_data": data,
        })

    if not sample_scores:
        return ""

    # Sort by score ascending (worst first), take N
    sample_scores.sort(key=lambda s: s["mean_score"])
    worst = sample_scores[:n]

    parts = []
    for entry in worst:
        sample_name = entry["sample_name"]
        # Read the sample text
        sample_file = samples_dir / f"{sample_name}.txt"
        sample_text = ""
        if sample_file.exists():
            raw = sample_file.read_text(encoding="utf-8")
            words = raw.split()
            if len(words) > 500:
                sample_text = " ".join(words[:500]) + "\n... [truncated]"
            else:
                sample_text = raw

        # Format judge reasoning
        reasons = []
        for dim_name, dim_val in entry["judge_data"].items():
            if isinstance(dim_val, dict) and "reason" in dim_val:
                reasons.append(f"  {dim_name}: {dim_val.get('score', '?')}/5 — {dim_val['reason']}")

        part = f"SAMPLE: {sample_name} (composite: {entry['mean_score']:.3f})"
        if reasons:
            part += "\nJudge reasoning:\n" + "\n".join(reasons)
        if sample_text:
            part += f"\nSample output:\n{sample_text}"
        parts.append(part)

    return "\n\n".join(parts)


def run_experiment(run_id: str, description: str = "", extra_args: list = None) -> dict:
    """Run the experiment runner and return the aggregate."""
    import subprocess
    cmd = [sys.executable, str(PROJECT_ROOT / "tools" / "experiment_runner.py"),
           "--run-id", run_id, "--description", description]
    if extra_args:
        cmd += extra_args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
            timeout=1800,  # 30 min timeout for full experiment cycle
        )
    except subprocess.TimeoutExpired:
        print(f"ERROR: experiment_runner.py timed out after 1800s", file=sys.stderr)
        return None
    print(result.stdout)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        return None

    agg_path = PROJECT_ROOT / ".tmp" / "evals" / run_id / "aggregate.json"
    if agg_path.exists():
        return json.loads(agg_path.read_text(encoding="utf-8"))
    return None


def run_holdout_check(run_id: str, cfg: dict) -> dict | None:
    """Evaluate the current SKILL.md on the holdout prompt set (no TSV row).
    Returns the holdout aggregate, or None if the run failed or holdout is
    disabled."""
    if not float(cfg.get("holdout_fraction", 0)) > 0:
        return None
    return run_experiment(
        f"{run_id}_holdout", "Holdout validation",
        extra_args=["--no-tsv", "--prompt-set", "holdout"],
    )


def save_best_aggregates(train_agg: dict, holdout_agg: dict | None) -> None:
    """Persist the aggregates of the current best SKILL.md so future
    candidates can be compared with per-prompt pairing."""
    BEST_AGG_PATH.write_text(json.dumps(train_agg, indent=2), encoding="utf-8")
    if holdout_agg is not None:
        BEST_HOLDOUT_AGG_PATH.write_text(json.dumps(holdout_agg, indent=2), encoding="utf-8")


def load_best_aggregate(path: Path, results_tsv: Path) -> dict | None:
    """Load a persisted best aggregate. Falls back to a composite-only stub
    (from results.tsv) for runs started before per-prompt tracking existed."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    best = get_best_score(results_tsv)
    return {"composite_score": best} if best else None


def measure_noise(cfg: dict, n: int) -> None:
    """Evaluate the current SKILL.md n times with no edits and report the
    composite score spread — the noise floor every decision competes with."""
    import statistics as _stats
    print(f"Measuring evaluation noise: {n} repeated runs of the current SKILL.md...\n")
    scores = []
    for i in range(n):
        agg = run_experiment(f"noise_{i+1}", "Noise measurement",
                             extra_args=["--no-tsv", "--prompt-set", "train"])
        if agg:
            scores.append(agg["composite_score"])
            print(f"  Run {i+1}/{n}: composite {agg['composite_score']:.4f}")
        else:
            print(f"  Run {i+1}/{n}: FAILED")

    if len(scores) < 2:
        print("\nNot enough successful runs to estimate noise.", file=sys.stderr)
        sys.exit(1)

    mean = _stats.mean(scores)
    stddev = _stats.stdev(scores)
    print(f"\n{'='*50}")
    print(f"  NOISE FLOOR (n={len(scores)})")
    print(f"{'='*50}")
    print(f"  Composite mean:   {mean:.4f}")
    print(f"  Composite stddev: {stddev:.4f}")
    print(f"  Same-skill runs vary by ~±{2*stddev:.4f} (2σ).")
    print(f"  The paired accept rule handles this automatically; if you use")
    print(f"  accept_rule: simple, set min_improvement above {2*stddev:.4f}.")
    print(f"{'='*50}")


def _check_skill_completeness(original: str, candidate: str) -> bool:
    """Verify the candidate skill file wasn't truncated or corrupted.

    Checks:
    1. Starts with YAML frontmatter (---)
    2. Has at least 50% of the original's markdown headers
    3. Is longer than 50 characters
    """
    if len(candidate) < 50:
        return False

    # Check frontmatter
    if not candidate.strip().startswith("---"):
        return False

    # Check headers preserved
    original_headers = set(re.findall(r'^#{1,3}\s+.+', original, re.MULTILINE))
    if not original_headers:
        return True  # No headers to check
    candidate_headers = set(re.findall(r'^#{1,3}\s+.+', candidate, re.MULTILINE))
    overlap = len(original_headers & candidate_headers)
    return overlap >= len(original_headers) * 0.5


def analyse_and_modify(client: ModelClient, skill_path: Path, results_context: str, cfg: dict, force_radical: bool = False, latest_run_id: str = None) -> str:
    """Use the LLM to analyse weaknesses and modify the skill file.

    When latest_run_id is provided, reads the 2 worst-scoring samples and their
    judge reasoning to give the modifier concrete examples of failure.
    """
    current_skill = skill_path.read_text(encoding="utf-8")

    metric_names = []
    for m in cfg.get("deterministic_metrics", []):
        metric_names.append(m["name"])
    for m in cfg.get("llm_judge_dimensions", []):
        metric_names.append(f"{m['name']} — {m['rubric'][:80]}")

    # Gather enriched context from worst-scoring samples
    samples_context = ""
    if latest_run_id:
        samples_context = _get_worst_samples_context(latest_run_id)
        if samples_context:
            sample_names = re.findall(r'SAMPLE: (\S+)', samples_context)
            print(f"  Enriched context: {len(sample_names)} worst samples: {', '.join(sample_names)}")

    system_prompt = """You are an autonomous prompt engineer optimising a skill file (a set of instructions for an LLM).

Your job:
1. Analyse the recent evaluation results AND the worst-scoring sample outputs to find concrete weaknesses
2. Form a hypothesis about why those metrics are weak, grounded in what the samples actually got wrong
3. Make ONE targeted change to the skill file to improve the weakest area
4. Return the FULL modified skill file

Rules:
- Make only ONE change per iteration
- Keep the YAML frontmatter intact
- Keep all section headers (# headings) from the original
- Keep the skill under 2000 words
- Don't make changes so large you can't attribute the score change

You must respond with EXACTLY this format:
DESCRIPTION: <one-line description of what you changed>
---SKILL---
<the complete modified SKILL.md content>"""

    if force_radical:
        system_prompt += """

IMPORTANT: The last 5 changes were all discarded. Try a FUNDAMENTALLY different approach: restructure the document, remove rules instead of adding them, or rewrite a section from scratch."""

    user_prompt = f"""Here are the recent evaluation results:

{results_context}

The metrics being evaluated are:
{chr(10).join(f'- {m}' for m in metric_names)}"""

    if samples_context:
        user_prompt += f"""

Here are the worst-scoring samples and the judge's reasoning for each:

{samples_context}"""

    user_prompt += f"""

Here is the current skill file:

{current_skill}

Analyse the weakest metrics and the concrete failures in the sample outputs. Hypothesise why they're weak and make ONE targeted change. Return the full modified skill file."""

    response = client.generate(system_prompt, user_prompt, max_tokens=8192)

    # Parse response
    description = ""
    new_skill = ""

    desc_match = re.search(r"DESCRIPTION:\s*(.+)", response)
    if desc_match:
        description = desc_match.group(1).strip()

    skill_match = re.search(r"---SKILL---\s*\n(.*)", response, re.DOTALL)
    if skill_match:
        new_skill = skill_match.group(1).strip()

    if new_skill and _check_skill_completeness(current_skill, new_skill):
        skill_path.write_text(new_skill, encoding="utf-8")
    elif new_skill:
        print(f"Warning: LLM returned incomplete/corrupted skill ({len(new_skill)} chars), skipping write", file=sys.stderr)

    return sanitise_description(description) or "Automated modification"


def update_decision(results_tsv: Path, decision: str, holdout_composite: float = None):
    """Update the decision (and optionally holdout score) of the last row."""
    updates = {"decision": decision}
    if holdout_composite is not None:
        updates["holdout_composite"] = f"{holdout_composite:.4f}"
    results_io.update_last_row(results_tsv, updates)


def _write_status(
    status: str,
    run_id: str,
    iteration: int,
    max_iterations: int,
    start_time: float,
    iter_times: list,
    client,
):
    """Write .tmp/run_status.json so the dashboard can show live progress."""
    tmp_dir = PROJECT_ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    avg_secs = sum(iter_times) / len(iter_times) if iter_times else 0
    remaining_iters = max(0, max_iterations - iteration) if max_iterations else 0
    eta_seconds = avg_secs * remaining_iters if avg_secs and remaining_iters else 0
    payload = {
        "status": status,
        "current_iteration": iteration,
        "max_iterations": max_iterations or 0,
        "start_time_iso": datetime.fromtimestamp(start_time).isoformat(),
        "avg_iteration_seconds": round(avg_secs, 1),
        "eta_seconds": round(eta_seconds),
        "cost_usd": round(client.estimated_cost_usd, 4),
        "last_updated_iso": datetime.now().isoformat(),
        "current_run_id": run_id,
    }
    status_path = PROJECT_ROOT / ".tmp" / "run_status.json"
    status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _get_skill_name(skill_best: Path) -> str | None:
    """Read the name field from SKILL.md.best YAML frontmatter."""
    if not skill_best.exists():
        return None
    text = skill_best.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def print_run_summary(results_tsv: Path, start_time: float, client, iterations_run: int):
    """Print an end-of-run summary and write .tmp/run-summary.md."""
    elapsed = time.time() - start_time
    elapsed_h = int(elapsed // 3600)
    elapsed_m = int((elapsed % 3600) // 60)
    elapsed_s = int(elapsed % 60)
    elapsed_str = f"{elapsed_h}h {elapsed_m}m {elapsed_s}s" if elapsed_h else f"{elapsed_m}m {elapsed_s}s"

    usage = client.usage_summary()
    best_score = get_best_score(results_tsv)

    # Parse results for baseline score and kept changes
    baseline_score = 0.0
    kept_changes = []
    for row in results_io.read_rows(results_tsv):
        run_id_col = row.get("run_id") or ""
        try:
            score = float(row.get("composite_score") or 0.0)
        except (TypeError, ValueError):
            continue
        if run_id_col == "baseline":
            baseline_score = score
        if (row.get("decision") or "").strip() == "KEEP":
            desc = (row.get("change_description") or run_id_col).strip()
            kept_changes.append(f"  · [{run_id_col}] {desc[:80]}")

    improvement = best_score - baseline_score
    improvement_str = f"+{improvement:.4f}" if improvement >= 0 else f"{improvement:.4f}"

    output_lines = [
        "",
        "=" * 60,
        "  RUN COMPLETE",
        "=" * 60,
        f"  Iterations run:   {iterations_run}",
        f"  Time elapsed:     {elapsed_str}",
        f"  Cost estimate:    ${usage['estimated_cost_usd']:.4f}",
        f"  Tokens used:      {usage['input_tokens']:,} in / {usage['output_tokens']:,} out",
        "",
        f"  Baseline score:   {baseline_score:.4f}",
        f"  Best score:       {best_score:.4f}  ({improvement_str})",
        "",
    ]
    if kept_changes:
        output_lines.append(f"  Kept changes ({len(kept_changes)}):")
        output_lines.extend(kept_changes)
        output_lines.append("")
    skill_best = PROJECT_ROOT / "SKILL.md.best"
    skill_name = _get_skill_name(skill_best)
    if skill_name:
        global_skill = Path.home() / ".claude" / "skills" / skill_name / "SKILL.md"
        if global_skill.exists():
            deploy_hint = f"  To deploy:        cp SKILL.md.best {global_skill}"
        else:
            deploy_hint = f"  To install:       mkdir -p ~/.claude/skills/{skill_name} && cp SKILL.md.best ~/.claude/skills/{skill_name}/SKILL.md"
    else:
        deploy_hint = None

    output_lines += [
        f"  Best skill saved: SKILL.md.best",
    ]
    if deploy_hint:
        output_lines.append(deploy_hint)
    output_lines += [
        "=" * 60,
        "",
    ]

    print("\n".join(output_lines))

    # Write .tmp/run-summary.md
    tmp_dir = PROJECT_ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    md_lines = [
        "# AutoEvaluation Run Summary",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Iterations:** {iterations_run}  ",
        f"**Time elapsed:** {elapsed_str}  ",
        f"**Cost estimate:** ${usage['estimated_cost_usd']:.4f}  ",
        "",
        "## Score",
        "",
        "| | Score |",
        "|---|---|",
        f"| Baseline | {baseline_score:.4f} |",
        f"| Best | {best_score:.4f} |",
        f"| Improvement | {improvement_str} |",
        "",
    ]
    if kept_changes:
        md_lines += ["## Kept Changes", ""]
        for c in kept_changes:
            md_lines.append(c.strip().replace("·", "-"))
        md_lines.append("")
    md_lines.append("Best skill file: `SKILL.md.best`")
    if skill_name:
        global_skill = Path.home() / ".claude" / "skills" / skill_name / "SKILL.md"
        if global_skill.exists():
            md_lines.append(f"\nTo deploy: `cp SKILL.md.best {global_skill}`")
        else:
            md_lines.append(f"\nTo install: `mkdir -p ~/.claude/skills/{skill_name} && cp SKILL.md.best ~/.claude/skills/{skill_name}/SKILL.md`")

    (PROJECT_ROOT / ".tmp" / "run-summary.md").write_text(
        "\n".join(md_lines), encoding="utf-8"
    )
    print(f"  Summary written to .tmp/run-summary.md\n")


def aggregate_token_usage() -> tuple[int, int]:
    """Read all per-process token usage logs and return (total_input, total_output)."""
    total_in, total_out = 0, 0
    log_dir = PROJECT_ROOT / ".tmp"
    if not log_dir.exists():
        return total_in, total_out
    for log_file in log_dir.glob("token_usage_*.jsonl"):
        try:
            for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    total_in += entry.get("input", 0)
                    total_out += entry.get("output", 0)
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return total_in, total_out


def get_total_cost(client: ModelClient) -> float:
    """Get total estimated cost across all processes (modifier + subprocesses)."""
    sub_in, sub_out = aggregate_token_usage()
    # Combine subprocess tokens with the modifier client's own tokens
    total_in = client.total_input_tokens + sub_in
    total_out = client.total_output_tokens + sub_out
    pricing = client.price_for_model(client.model)
    if pricing is None:
        return 0.0
    input_price, output_price = pricing
    return total_in * input_price + total_out * output_price


def _default_dimensions():
    """Default LLM judge dimensions for quick-start mode."""
    return default_dimensions()


def _quick_start_config(args) -> dict:
    """Build a config dict from CLI flags (no config.yaml needed)."""
    provider = args.provider
    if provider not in DEFAULT_MODELS:
        print(f"Error: Unknown provider '{provider}'. Supported: {', '.join(DEFAULT_MODELS)}", file=sys.stderr)
        sys.exit(1)

    default_model, default_key_env = DEFAULT_MODELS[provider]
    model = args.model or default_model
    api_key_env = default_key_env

    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(f"Error: Skill file not found: {skill_path}", file=sys.stderr)
        sys.exit(1)

    prompts_path = args.prompts or "prompts/prompts.json"
    if not (PROJECT_ROOT / prompts_path).exists():
        print(f"Error: Prompts file not found: {prompts_path}", file=sys.stderr)
        print("  Create a prompts file or use --prompts to specify one.", file=sys.stderr)
        sys.exit(1)

    iterations = args.iterations if args.iterations else 10
    hours = args.hours

    if not args.iterations and not args.hours:
        print(f"No --iterations or --hours specified, defaulting to {iterations} iterations.")

    cfg = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "skill_path": str(skill_path),
        "prompts_path": prompts_path,
        "results_tsv": "results.tsv",
        "max_iterations": iterations,
        "max_hours": hours,
        "max_cost_usd": 0,
        "convergence_window": 0,
        "max_concurrent": 1,
        "judge_sees_skill": True,
        "replicates_per_prompt": 3,
        "accept_rule": "paired",
        "accept_confidence": 0.95,
        "min_valid_sample_frac": 0.8,
        "holdout_fraction": 0.3,
        "llm_judge_dimensions": _default_dimensions(),
        "deterministic_metrics": [],
    }

    import yaml
    cfg_path = PROJECT_ROOT / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
    print(f"Generated config.yaml from CLI flags")

    return cfg


def main():
    parser = argparse.ArgumentParser(
        description="Run the optimisation loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick start (no config.yaml needed):
  python3 tools/run_loop.py --skill SKILL.md --provider gemini --iterations 5

With existing config:
  python3 tools/run_loop.py --iterations 10
  python3 tools/run_loop.py --hours 2.5
        """,
    )
    parser.add_argument("--iterations", type=int, default=0, help="Max iterations (0=use config)")
    parser.add_argument("--hours", type=float, default=0, help="Max hours (0=use config)")
    parser.add_argument("--skill", type=str, help="Path to skill file (enables quick-start mode, no config.yaml needed)")
    parser.add_argument("--provider", type=str, choices=["gemini", "openai", "anthropic"],
                        help="LLM provider (used with --skill)")
    parser.add_argument("--model", type=str, help="Model name override (default: provider's default)")
    parser.add_argument("--prompts", type=str, help="Path to prompts JSON file (default: prompts/prompts.json)")
    parser.add_argument("--measure-noise", type=int, default=0, metavar="N",
                        help="Evaluate the current SKILL.md N times with no edits and report the score noise floor, then exit")
    args = parser.parse_args()

    # Quick-start mode: --skill and --provider given, no config.yaml needed
    if args.skill:
        if not args.provider:
            print("Error: --provider is required when using --skill", file=sys.stderr)
            print("  Example: python3 tools/run_loop.py --skill SKILL.md --provider gemini", file=sys.stderr)
            sys.exit(1)
        cfg = _quick_start_config(args)
    else:
        cfg = load_config()

    validate_config(cfg)
    client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"))

    if args.measure_noise:
        measure_noise(cfg, args.measure_noise)
        return

    skill_path = PROJECT_ROOT / cfg.get("skill_path", "SKILL.md")
    skill_best = PROJECT_ROOT / "SKILL.md.best"
    results_tsv = PROJECT_ROOT / cfg.get("results_tsv", "results.tsv")

    max_iterations = args.iterations or cfg.get("max_iterations", 0)
    max_hours = args.hours or cfg.get("max_hours", 0)
    max_cost_usd = cfg.get("max_cost_usd", 0)
    convergence_window = cfg.get("convergence_window", 0)

    # Cost-cap sanity: an unknown model prices at $0, so the cap can't fire
    if max_cost_usd and not client.pricing_known:
        print(
            f"WARNING: max_cost_usd is set but there is no pricing entry for "
            f"'{client.model}' — the cost cap CANNOT trigger. Add the model to "
            f"ModelClient._PRICING or remove max_cost_usd.",
            file=sys.stderr,
        )

    # Self-judge warning
    if not cfg.get("judge_provider") and not cfg.get("judge_model"):
        print("Note: Using same model for generation and judging. For better signal, set judge_model (and ideally judge_provider) in config.yaml.")

    start_time = time.time()
    iteration = 0
    iter_times = []
    consecutive_discards = 0
    consecutive_failures = 0
    iterations_since_improvement = 0

    # Clear stale token usage logs from prior runs
    for old_log in (PROJECT_ROOT / ".tmp").glob("token_usage_*.jsonl"):
        old_log.unlink(missing_ok=True)

    # Baseline if needed (train set + holdout set, so future candidates have
    # per-prompt aggregates to pair against)
    if not results_tsv.exists() or len(results_tsv.read_text(encoding="utf-8").strip().split("\n")) <= 1:
        print("Running baseline experiment...")
        base_agg = run_experiment("baseline", "Initial baseline")
        if base_agg is None:
            print("Baseline experiment failed — cannot start the loop.", file=sys.stderr)
            sys.exit(1)
        shutil.copy2(skill_path, skill_best)
        base_holdout = run_holdout_check("baseline", cfg)
        save_best_aggregates(base_agg, base_holdout)
        update_decision(results_tsv, "BASELINE",
                        base_holdout["composite_score"] if base_holdout else None)

    while True:
        iteration += 1
        iter_start = time.time()

        # Check limits
        if max_iterations and iteration > max_iterations:
            print(f"\nReached max iterations ({max_iterations}). Stopping.")
            break
        if max_hours:
            elapsed_hours = (time.time() - start_time) / 3600
            if elapsed_hours >= max_hours:
                print(f"\nReached max hours ({max_hours}h). Stopping.")
                break
        total_cost = get_total_cost(client)
        if max_cost_usd and total_cost >= max_cost_usd:
            print(f"\nReached cost cap (${total_cost:.2f} >= ${max_cost_usd:.2f}). Stopping.")
            break
        if convergence_window and iterations_since_improvement >= convergence_window:
            print(f"\nConverged — no improvement in {convergence_window} iterations. Stopping.")
            break

        iter_label = f"{iteration}/{max_iterations}" if max_iterations else str(iteration)
        print(f"\n{'='*60}")
        print(f"ITERATION {iter_label}")
        print(f"{'='*60}")

        best_score = get_best_score(results_tsv)
        results_context = get_recent_results(results_tsv)

        # Get latest run_id for enriched context
        latest_run_id = get_latest_run_id(results_tsv)

        # Analyse and modify
        force_radical = consecutive_discards >= 5
        if force_radical:
            print("5 consecutive discards — forcing fundamentally different approach...")
        print("Analysing weaknesses and modifying skill...")
        description = analyse_and_modify(
            client, skill_path, results_context, cfg,
            force_radical=force_radical, latest_run_id=latest_run_id,
        )
        print(f"Change: {description}")

        # Snapshot SKILL.md before evaluation
        run_id = get_next_run_id(results_tsv)
        skills_dir = PROJECT_ROOT / ".tmp" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_path, skills_dir / f"{run_id}.md")

        print(f"Running experiment {run_id}...")
        agg = run_experiment(run_id, description)

        if agg is None:
            consecutive_failures += 1
            shutil.copy2(skill_best, skill_path)
            if consecutive_failures >= 3:
                print("3 consecutive experiment failures — aborting run. "
                      "Fix the eval pipeline (API key? judge model?) and re-run to resume.",
                      file=sys.stderr)
                break
            print(f"Experiment failed ({consecutive_failures}/3), retrying...")
            continue
        consecutive_failures = 0

        new_score = agg["composite_score"]

        # Decide: paired per-prompt comparison against the persisted best
        # aggregate (falls back to the simple threshold rule when per-prompt
        # data is unavailable, e.g. resuming a pre-July-2026 run)
        best_agg = load_best_aggregate(BEST_AGG_PATH, results_tsv)
        verdict = decide(agg, best_agg, cfg)
        keep, reason = verdict["keep"], verdict["reason"]
        print(f"Decision ({verdict['method']}): {reason}")

        # Confirm a train-set KEEP against the holdout set — an edit that
        # only helps the prompts it was optimised on is overfitting, not a win
        holdout_agg = None
        holdout_score = None
        if keep and float(cfg.get("holdout_fraction", 0)) > 0:
            print("Train-set improvement found — validating on holdout prompts...")
            holdout_agg = run_holdout_check(run_id, cfg)
            if holdout_agg is None:
                keep, reason = False, "holdout evaluation failed"
            else:
                holdout_score = holdout_agg["composite_score"]
                best_holdout = load_best_aggregate(BEST_HOLDOUT_AGG_PATH, results_tsv)
                hv = decide(holdout_agg, best_holdout, cfg, mode="non-regression")
                print(f"Holdout check: {hv['reason']}")
                if not hv["keep"]:
                    keep, reason = False, f"holdout regression — {hv['reason']}"

        if keep:
            print(f"KEEP — {best_score:.4f} → {new_score:.4f} ({reason})")
            shutil.copy2(skill_path, skill_best)
            save_best_aggregates(agg, holdout_agg)
            update_decision(results_tsv, "KEEP", holdout_score)
            consecutive_discards = 0
            iterations_since_improvement = 0
        else:
            print(f"DISCARD — {reason}")
            shutil.copy2(skill_best, skill_path)
            update_decision(results_tsv, "DISCARD", holdout_score)
            consecutive_discards += 1
            iterations_since_improvement += 1

        if consecutive_discards >= 5:
            print("5 consecutive discards detected — next iteration will use radical approach.")

        # Per-iteration timing and progress
        iter_elapsed = time.time() - iter_start
        iter_times.append(iter_elapsed)
        iter_m = int(iter_elapsed // 60)
        iter_s = int(iter_elapsed % 60)
        avg_secs = sum(iter_times) / len(iter_times)
        remaining = max_iterations - iteration if max_iterations else 0
        eta_m = int(avg_secs * remaining / 60) if remaining > 0 else 0
        cost = get_total_cost(client)

        eta_str = f"~{eta_m} min remaining · " if max_iterations and remaining > 0 else ""
        print(f"\n  {run_id} completed in {iter_m}m {iter_s}s")
        print(f"  Iteration {iter_label} · {eta_str}Cost: ${cost:.3f}")

        _write_status("running", run_id, iteration, max_iterations, start_time, iter_times, client)

    _write_status("complete", "", iteration - 1, max_iterations, start_time, iter_times, client)
    print_run_summary(results_tsv, start_time, client, iteration - 1)


if __name__ == "__main__":
    main()
