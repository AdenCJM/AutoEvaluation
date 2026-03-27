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
from model_client import ModelClient
from utils import PROJECT_ROOT, load_config, sanitise_description, validate_config


def get_next_run_id(results_tsv: Path) -> str:
    """Determine the next experiment ID from results.tsv."""
    if not results_tsv.exists():
        return "baseline"

    lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return "baseline"

    last_id = lines[-1].split("\t")[0]
    if last_id == "baseline":
        return "exp_001"

    match = re.search(r"exp_(\d+)", last_id)
    if match:
        return f"exp_{int(match.group(1)) + 1:03d}"
    return f"exp_{len(lines):03d}"


def get_best_score(results_tsv: Path) -> float:
    """Read the best composite score from results.tsv."""
    if not results_tsv.exists():
        return 0.0

    lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return 0.0

    best = 0.0
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                score = float(parts[2])
                best = max(best, score)
            except ValueError:
                pass
    return best


def get_recent_results(results_tsv: Path, n: int = 3) -> str:
    """Get the last N rows of results.tsv as a string for context."""
    if not results_tsv.exists():
        return "No results yet."

    lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
    header = lines[0] if lines else ""
    recent = lines[-n:] if len(lines) > n else lines[1:]
    return header + "\n" + "\n".join(recent)


def run_experiment(run_id: str, description: str = "") -> dict:
    """Run the experiment runner and return the aggregate."""
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "tools" / "experiment_runner.py"),
             "--run-id", run_id, "--description", description],
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


def analyse_and_modify(client: ModelClient, skill_path: Path, results_context: str, cfg: dict, force_radical: bool = False) -> str:
    """Use the LLM to analyse weaknesses and modify the skill file."""
    current_skill = skill_path.read_text(encoding="utf-8")

    metric_names = []
    for m in cfg.get("deterministic_metrics", []):
        metric_names.append(m["name"])
    for m in cfg.get("llm_judge_dimensions", []):
        metric_names.append(f"{m['name']} — {m['rubric'][:80]}")

    system_prompt = """You are an autonomous prompt engineer optimising a skill file (a set of instructions for an LLM).

Your job:
1. Analyse the recent evaluation results to find the weakest 2-3 metrics
2. Form a hypothesis about why those metrics are weak
3. Make ONE targeted change to the skill file to improve the weakest area
4. Return the FULL modified skill file

Rules:
- Make only ONE change per iteration
- Keep the YAML frontmatter intact
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
{chr(10).join(f'- {m}' for m in metric_names)}

Here is the current skill file:

{current_skill}

Analyse the weakest metrics, hypothesise why they're weak, and make ONE targeted change to improve them. Return the full modified skill file."""

    response = client.generate(system_prompt, user_prompt)

    # Parse response
    description = ""
    new_skill = ""

    desc_match = re.search(r"DESCRIPTION:\s*(.+)", response)
    if desc_match:
        description = desc_match.group(1).strip()

    skill_match = re.search(r"---SKILL---\s*\n(.*)", response, re.DOTALL)
    if skill_match:
        new_skill = skill_match.group(1).strip()

    if new_skill and len(new_skill) > 50:
        skill_path.write_text(new_skill, encoding="utf-8")
    elif new_skill:
        print(f"Warning: LLM returned suspiciously short skill ({len(new_skill)} chars), skipping write", file=sys.stderr)

    return sanitise_description(description) or "Automated modification"


def update_decision(results_tsv: Path, decision: str):
    """Update the decision column of the last row in results.tsv."""
    lines = results_tsv.read_text(encoding="utf-8").rstrip("\n").split("\n")
    if len(lines) > 1:
        last = lines[-1]
        if last.endswith("\t"):
            lines[-1] = last + decision
        else:
            parts = last.split("\t")
            parts[-1] = decision
            lines[-1] = "\t".join(parts)
        results_tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    if results_tsv.exists():
        lines = results_tsv.read_text(encoding="utf-8").strip().split("\n")
        header = lines[0].split("\t") if lines else []
        desc_idx = header.index("change_description") if "change_description" in header else -1
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            run_id_col = parts[0]
            try:
                score = float(parts[2])
            except ValueError:
                continue
            decision = parts[-1].strip() if parts else ""
            if run_id_col == "baseline":
                baseline_score = score
            if decision == "KEEP":
                desc = parts[desc_idx].strip() if desc_idx >= 0 and desc_idx < len(parts) else run_id_col
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


def _default_dimensions():
    """Default LLM judge dimensions for quick-start mode."""
    return [
        {
            "name": "human_score",
            "weight": 0.30,
            "direction": "higher_is_better",
            "rubric": (
                "Does this read like a competent human wrote it? "
                "1 = obviously AI-generated, 5 = indistinguishable from human."
            ),
        },
        {
            "name": "task_accuracy",
            "weight": 0.40,
            "direction": "higher_is_better",
            "rubric": (
                "Does the output correctly follow the skill instructions? "
                "1 = ignores them, 5 = perfect adherence."
            ),
        },
        {
            "name": "quality",
            "weight": 0.30,
            "direction": "higher_is_better",
            "rubric": (
                "Is this high-quality output overall? "
                "1 = poor, 5 = excellent."
            ),
        },
    ]


_PROVIDER_DEFAULTS = {
    "gemini": ("gemini-2.5-flash", "GEMINI_API_KEY"),
    "openai": ("gpt-4o", "OPENAI_API_KEY"),
    "anthropic": ("claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
}


def _quick_start_config(args) -> dict:
    """Build a config dict from CLI flags (no config.yaml needed)."""
    provider = args.provider
    if provider not in _PROVIDER_DEFAULTS:
        print(f"Error: Unknown provider '{provider}'. Supported: {', '.join(_PROVIDER_DEFAULTS)}", file=sys.stderr)
        sys.exit(1)

    default_model, default_key_env = _PROVIDER_DEFAULTS[provider]
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
        "judge_sees_skill": False,
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

    skill_path = PROJECT_ROOT / cfg.get("skill_path", "SKILL.md")
    skill_best = PROJECT_ROOT / "SKILL.md.best"
    results_tsv = PROJECT_ROOT / cfg.get("results_tsv", "results.tsv")

    max_iterations = args.iterations or cfg.get("max_iterations", 0)
    max_hours = args.hours or cfg.get("max_hours", 0)
    max_cost_usd = cfg.get("max_cost_usd", 0)
    convergence_window = cfg.get("convergence_window", 0)

    start_time = time.time()
    iteration = 0
    iter_times = []
    consecutive_discards = 0
    iterations_since_improvement = 0

    # Baseline if needed
    if not results_tsv.exists() or len(results_tsv.read_text(encoding="utf-8").strip().split("\n")) <= 1:
        print("Running baseline experiment...")
        run_experiment("baseline", "Initial baseline")
        update_decision(results_tsv, "BASELINE")
        shutil.copy2(skill_path, skill_best)

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
        if max_cost_usd and client.estimated_cost_usd >= max_cost_usd:
            print(f"\nReached cost cap (${client.estimated_cost_usd:.2f} >= ${max_cost_usd:.2f}). Stopping.")
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

        # Analyse and modify
        force_radical = consecutive_discards >= 5
        if force_radical:
            print("5 consecutive discards — forcing fundamentally different approach...")
        print("Analysing weaknesses and modifying skill...")
        description = analyse_and_modify(client, skill_path, results_context, cfg, force_radical=force_radical)
        print(f"Change: {description}")

        # Snapshot SKILL.md before evaluation
        run_id = get_next_run_id(results_tsv)
        skills_dir = PROJECT_ROOT / ".tmp" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_path, skills_dir / f"{run_id}.md")

        print(f"Running experiment {run_id}...")
        agg = run_experiment(run_id, description)

        if agg is None:
            print("Experiment failed, retrying...")
            shutil.copy2(skill_best, skill_path)
            continue

        new_score = agg["composite_score"]

        # Decide
        if new_score > best_score:
            print(f"KEEP — score improved {best_score:.4f} → {new_score:.4f}")
            shutil.copy2(skill_path, skill_best)
            update_decision(results_tsv, "KEEP")
            consecutive_discards = 0
            iterations_since_improvement = 0
        else:
            print(f"DISCARD — {new_score:.4f} vs best {best_score:.4f}")
            shutil.copy2(skill_best, skill_path)
            update_decision(results_tsv, "DISCARD")
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
        cost = client.estimated_cost_usd

        eta_str = f"~{eta_m} min remaining · " if max_iterations and remaining > 0 else ""
        print(f"\n  {run_id} completed in {iter_m}m {iter_s}s")
        print(f"  Iteration {iter_label} · {eta_str}Cost: ${cost:.3f}")

        _write_status("running", run_id, iteration, max_iterations, start_time, iter_times, client)

    _write_status("complete", "", iteration - 1, max_iterations, start_time, iter_times, client)
    print_run_summary(results_tsv, start_time, client, iteration - 1)


if __name__ == "__main__":
    main()
