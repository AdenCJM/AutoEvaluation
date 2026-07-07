"""
Batch Sweep — Full-Suite Regression Scoring
============================================
Re-scores a skill file against the full prompt suite using the Anthropic
Batches API (50% of synchronous token prices) with 1-hour prompt caching on
the shared prefixes (the skill for generation, the rubric for judging).

Use for nightly regression baselines of SKILL.md.best — not for the tight
iterate-and-revert inner loop, since batches trade latency (minutes to an
hour) for cost.

Anthropic-only: the Batches API is an Anthropic platform feature. Both the
generation model and the judge model must be Anthropic models.

Usage:
    python3 tools/batch_sweep.py                          # SKILL.md.best, all prompts
    python3 tools/batch_sweep.py --skill SKILL.md --prompt-set train
    python3 tools/batch_sweep.py --run-id nightly_2026-07-08 --compare-best

Output: .tmp/samples/{run_id}/ and .tmp/evals/{run_id}/ in the standard
format, so score_aggregator and decision.py work on the result unchanged.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_llm_judge import build_judge_prompt, build_judge_schema
from model_client import ModelClient
from score_aggregator import aggregate
from utils import PROJECT_ROOT, load_config, load_env, split_prompts, validate_config

POLL_INTERVAL_SECONDS = 30
GEN_PREFIX = "gen__"
JUDGE_PREFIX = "judge__"


def _require_anthropic(cfg: dict) -> None:
    if cfg.get("provider") != "anthropic":
        print(
            "Error: batch_sweep uses the Anthropic Batches API — config provider "
            f"is '{cfg.get('provider')}'. Set provider: anthropic (the regular "
            "loop supports all providers; only the batch sweep is Anthropic-only).",
            file=sys.stderr,
        )
        sys.exit(1)
    judge_provider = cfg.get("judge_provider", cfg.get("provider"))
    if judge_provider != "anthropic":
        print(
            f"Error: judge_provider is '{judge_provider}' — the batch sweep needs "
            "an Anthropic judge model too.",
            file=sys.stderr,
        )
        sys.exit(1)


def _get_client(cfg: dict):
    """Anthropic SDK client (retries left at SDK defaults — batches are
    control-plane calls, not the hot path)."""
    try:
        import anthropic
    except ImportError:
        print("Error: pip install anthropic", file=sys.stderr)
        sys.exit(1)
    import os
    load_env()
    api_key = os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        print(f"Error: {cfg.get('api_key_env', 'ANTHROPIC_API_KEY')} not set", file=sys.stderr)
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


def build_generation_requests(skill_content: str, prompts: list[dict], model: str,
                              replicates: int, max_tokens: int = 4096) -> list[dict]:
    """One request per (prompt, replicate). The skill is the shared system
    prefix, cached for 1 hour so every request after the first reads it at
    ~10% of input price (stacking with the 50% batch discount)."""
    requests = []
    for i, p in enumerate(prompts):
        for k in range(max(1, replicates)):
            sample_id = f"sample_{i}_{p['id']}_r{k}" if replicates > 1 else f"sample_{i}_{p['id']}"
            requests.append({
                "custom_id": f"{GEN_PREFIX}{sample_id}",
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": [{
                        "type": "text",
                        "text": skill_content,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }],
                    "messages": [{"role": "user", "content": p["prompt"]}],
                },
            })
    return requests


def build_judge_requests(samples: dict, dimensions: list[dict], judge_model: str,
                         skill_content: str = None, max_tokens: int = 2048) -> list[dict]:
    """One judge request per sample. The rubric system prompt is the shared
    cached prefix; structured output guarantees parseable scores."""
    system_text = build_judge_prompt(dimensions, skill_content=skill_content)
    schema = build_judge_schema(dimensions)
    requests = []
    for sample_id, text in sorted(samples.items()):
        requests.append({
            "custom_id": f"{JUDGE_PREFIX}{sample_id}",
            "params": {
                "model": judge_model,
                "max_tokens": max_tokens,
                "system": [{
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }],
                "messages": [{"role": "user", "content": f"Evaluate this output:\n\n---\n{text}\n---"}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            },
        })
    return requests


def run_batch(client, requests: list[dict], label: str, timeout_hours: float = 2.0) -> dict:
    """Submit a batch, poll to completion, return {custom_id: text} for
    succeeded requests (failures are reported and omitted)."""
    print(f"Submitting {label} batch: {len(requests)} requests...")
    batch = client.messages.batches.create(requests=requests)
    print(f"  Batch {batch.id} — polling every {POLL_INTERVAL_SECONDS}s (most complete within an hour)")

    deadline = time.time() + timeout_hours * 3600
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        if time.time() > deadline:
            print(f"Error: batch {batch.id} did not finish within {timeout_hours}h "
                  f"(status: {batch.processing_status}). Results stay retrievable for 29 days — "
                  f"re-run later or inspect via the API.", file=sys.stderr)
            sys.exit(1)
        counts = getattr(batch, "request_counts", None)
        if counts is not None:
            print(f"  ...processing ({getattr(counts, 'processing', '?')} in flight, "
                  f"{getattr(counts, 'succeeded', '?')} done)")
        time.sleep(POLL_INTERVAL_SECONDS)

    outputs = {}
    failures = 0
    for result in client.messages.batches.results(batch.id):
        if result.result.type == "succeeded":
            msg = result.result.message
            text = next((b.text for b in msg.content if b.type == "text"), "")
            outputs[result.custom_id] = text
        else:
            failures += 1
            print(f"  WARNING: {result.custom_id} {result.result.type}", file=sys.stderr)
    print(f"  {label} batch done: {len(outputs)} succeeded, {failures} failed")
    return outputs


def normalise_judge_result(raw_text: str, dimensions: list[dict]) -> dict:
    """Convert a judge JSON string into the standard eval-file shape
    ({dim: {score, normalised, reason}}), matching eval_llm_judge output."""
    try:
        scores = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        scores = None
    if not isinstance(scores, dict):
        result = {"error": "Failed to parse batch judge response", "raw_response": raw_text}
        for dim in dimensions:
            result[dim["name"]] = {"score": 0, "normalised": 0.0, "reason": "judge error"}
        return result

    def normalise(score_val):
        try:
            s = float(score_val)
        except (TypeError, ValueError):
            s = 0.0
        return round(max(0.0, min(1.0, (s - 1) / 4)), 4)

    result = {}
    for dim in dimensions:
        name = dim["name"]
        if name in scores and isinstance(scores[name], dict):
            result[name] = {
                "score": scores[name].get("score", 0),
                "normalised": normalise(scores[name].get("score", 0)),
                "reason": scores[name].get("reason", ""),
            }
        else:
            result[name] = {"score": 0, "normalised": 0.0, "reason": f"dimension '{name}' missing"}
    return result


def estimate_cost_note(model: str, judge_model: str) -> str:
    gen_p = ModelClient.price_for_model(model)
    judge_p = ModelClient.price_for_model(judge_model)
    if not gen_p or not judge_p:
        return "Cost estimate unavailable (unknown model pricing)."
    return (f"Batch pricing is 50% of synchronous rates "
            f"({model}: ${gen_p[0]*5e5:.2f}/${gen_p[1]*5e5:.2f} per MTok effective; "
            f"cached prefix reads stack on top).")


def main():
    parser = argparse.ArgumentParser(description="Batch regression sweep via the Anthropic Batches API")
    parser.add_argument("--skill", default=None,
                        help="Skill file to score (default: SKILL.md.best if present, else config skill_path)")
    parser.add_argument("--prompt-set", choices=["all", "train", "holdout"], default="all")
    parser.add_argument("--replicates", type=int, default=None,
                        help="Completions per prompt (default: config replicates_per_prompt)")
    parser.add_argument("--run-id", default=None, help="Default: sweep_YYYYMMDD_HHMM")
    parser.add_argument("--timeout-hours", type=float, default=2.0)
    parser.add_argument("--compare-best", action="store_true",
                        help="Print a non-regression verdict against best_aggregate.json")
    args = parser.parse_args()

    cfg = validate_config(load_config())
    _require_anthropic(cfg)
    if args.replicates is None:
        args.replicates = cfg.get("replicates_per_prompt", 3)

    skill_path = Path(args.skill) if args.skill else (
        PROJECT_ROOT / "SKILL.md.best" if (PROJECT_ROOT / "SKILL.md.best").exists()
        else PROJECT_ROOT / cfg.get("skill_path", "SKILL.md")
    )
    if not skill_path.exists():
        print(f"Error: skill file not found: {skill_path}", file=sys.stderr)
        sys.exit(1)
    skill_content = skill_path.read_text(encoding="utf-8")

    prompts = json.loads((PROJECT_ROOT / cfg.get("prompts_path", "prompts/prompts.json")).read_text(encoding="utf-8"))
    if args.prompt_set in ("train", "holdout"):
        train, holdout = split_prompts(prompts, float(cfg.get("holdout_fraction", 0.3)))
        prompts = train if args.prompt_set == "train" else holdout
    if not prompts:
        print(f"Error: prompt set '{args.prompt_set}' is empty", file=sys.stderr)
        sys.exit(1)

    run_id = args.run_id or f"sweep_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
    model = cfg["model"]
    judge_model = cfg.get("judge_model", model)
    dimensions = cfg["llm_judge_dimensions"]

    print(f"Batch sweep '{run_id}': {skill_path.name} × {len(prompts)} prompts × {args.replicates} replicates")
    print(f"  Generation: {model} | Judge: {judge_model}")
    print(f"  {estimate_cost_note(model, judge_model)}")

    client = _get_client(cfg)

    # Phase 1: generation batch
    gen_requests = build_generation_requests(skill_content, prompts, model, args.replicates)
    gen_outputs = run_batch(client, gen_requests, "generation", args.timeout_hours)
    if not gen_outputs:
        print("Error: no samples generated", file=sys.stderr)
        sys.exit(1)

    samples_dir = PROJECT_ROOT / ".tmp" / "samples" / run_id
    samples_dir.mkdir(parents=True, exist_ok=True)
    samples = {}
    for custom_id, text in gen_outputs.items():
        sample_id = custom_id[len(GEN_PREFIX):]
        (samples_dir / f"{sample_id}.txt").write_text(text, encoding="utf-8")
        samples[sample_id] = text

    # Phase 2: judge batch (semi-blind if configured)
    judge_skill = skill_content if cfg.get("judge_sees_skill", False) else None
    judge_requests = build_judge_requests(samples, dimensions, judge_model, skill_content=judge_skill)
    judge_outputs = run_batch(client, judge_requests, "judge", args.timeout_hours)

    evals_dir = PROJECT_ROOT / ".tmp" / "evals" / run_id
    evals_dir.mkdir(parents=True, exist_ok=True)
    for sample_id in samples:
        raw = judge_outputs.get(f"{JUDGE_PREFIX}{sample_id}")
        result = normalise_judge_result(raw, dimensions) if raw is not None else {
            "error": "missing from batch results"
        }
        (evals_dir / f"{sample_id}_llm_judge.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

    # Optional deterministic metrics run locally (cheap, no API)
    if cfg.get("deterministic_metrics"):
        import subprocess
        for sample_id in samples:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "tools" / "eval_deterministic.py"),
                 "--sample-path", str(samples_dir / f"{sample_id}.txt"),
                 "--output-path", str(evals_dir / f"{sample_id}_deterministic.json")],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=300,
            )

    # Phase 3: aggregate (same engine as the loop)
    agg = aggregate(str(evals_dir), cfg)
    (evals_dir / "aggregate.json").write_text(json.dumps(agg, indent=2), encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"SWEEP COMPOSITE: {agg['composite_score']:.4f} ± {agg['composite_stddev']:.4f} "
          f"({agg['sample_count']} samples, {agg['judge_errors']} judge failures)")
    print(f"{'='*50}")
    print(f"Aggregate written to {evals_dir / 'aggregate.json'}")

    if args.compare_best:
        best_path = PROJECT_ROOT / "best_aggregate.json"
        if best_path.exists():
            from decision import decide
            best = json.loads(best_path.read_text(encoding="utf-8"))
            verdict = decide(agg, best, cfg, mode="non-regression")
            status = "OK" if verdict["keep"] else "REGRESSION"
            print(f"\nRegression check vs best_aggregate.json: {status} — {verdict['reason']}")
            sys.exit(0 if verdict["keep"] else 2)
        else:
            print("\n(no best_aggregate.json to compare against)")


if __name__ == "__main__":
    main()
