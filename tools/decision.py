"""
Noise-Aware Decision Rule
==========================
Decides KEEP vs DISCARD for a candidate SKILL.md by comparing its aggregate
against the current best, using per-prompt paired deltas and a bootstrap
confidence interval instead of a bare mean comparison.

Rationale: composite scores from a stochastic LLM judge carry run-to-run
noise around the size of the deltas the loop is trying to detect. Pairing by
prompt cancels prompt difficulty; the bootstrap CI stops the loop from
keeping (or discarding) changes on the strength of a lucky draw.

Usage (library):
    from decision import decide
    verdict = decide(candidate_agg, best_agg, cfg)

Usage (CLI, for the Claude-Code-driven loop in program.md):
    python3 tools/decision.py --candidate-agg .tmp/evals/exp_007/aggregate.json \
        --best-agg best_aggregate.json
    # prints a JSON verdict; exit code 0 = KEEP, 1 = DISCARD
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 42
MIN_PAIRS_FOR_BOOTSTRAP = 8


def _paired_deltas(candidate_per_prompt: dict, best_per_prompt: dict) -> list[float]:
    """Per-prompt composite deltas (candidate - best) over shared prompt ids."""
    shared = sorted(set(candidate_per_prompt) & set(best_per_prompt))
    return [
        float(candidate_per_prompt[p]["composite"]) - float(best_per_prompt[p]["composite"])
        for p in shared
    ]


def _bootstrap_ci(deltas: list[float], confidence: float) -> tuple[float, float]:
    """Two-sided bootstrap CI for the mean of deltas at the given confidence."""
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    means = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        resample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    alpha = 1.0 - confidence
    lo_idx = int((alpha / 2) * BOOTSTRAP_ITERATIONS)
    hi_idx = min(BOOTSTRAP_ITERATIONS - 1, int((1 - alpha / 2) * BOOTSTRAP_ITERATIONS))
    return means[lo_idx], means[hi_idx]


def _hierarchical_bootstrap_ci(
    candidate_per_prompt: dict,
    best_per_prompt: dict,
    confidence: float,
) -> tuple[float, float]:
    """Bootstrap prompts and stochastic replicates within each prompt.

    Candidate and baseline generations are independent, so replicate draws are
    resampled within each arm before their prompt-level means are differenced.
    Older aggregates without replicate arrays transparently degrade to their
    stored prompt means.
    """
    rng = random.Random(BOOTSTRAP_SEED)
    shared = sorted(set(candidate_per_prompt) & set(best_per_prompt))
    means = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        prompt_deltas = []
        for _ in shared:
            pid = shared[rng.randrange(len(shared))]
            c = candidate_per_prompt[pid]
            b = best_per_prompt[pid]
            c_values = c.get("replicates") or [float(c["composite"])]
            b_values = b.get("replicates") or [float(b["composite"])]
            c_draw = [float(c_values[rng.randrange(len(c_values))]) for _ in c_values]
            b_draw = [float(b_values[rng.randrange(len(b_values))]) for _ in b_values]
            prompt_deltas.append(statistics.mean(c_draw) - statistics.mean(b_draw))
        means.append(statistics.mean(prompt_deltas))
    means.sort()
    alpha = 1.0 - confidence
    lo_idx = int((alpha / 2) * BOOTSTRAP_ITERATIONS)
    hi_idx = min(BOOTSTRAP_ITERATIONS - 1, int((1 - alpha / 2) * BOOTSTRAP_ITERATIONS))
    return means[lo_idx], means[hi_idx]


def paired_verdict(
    candidate_per_prompt: dict,
    best_per_prompt: dict,
    confidence: float = 0.95,
    mode: str = "keep",
    min_improvement: float = 0.01,
) -> dict:
    """Paired comparison of candidate vs best per-prompt composites.

    mode="keep": accept only if the mean delta is positive with confidence
        (bootstrap CI lower bound > 0).
    mode="non-regression": accept unless the candidate is *significantly
        worse* (bootstrap CI upper bound < 0) — used for holdout checks.
    """
    deltas = _paired_deltas(candidate_per_prompt, best_per_prompt)
    if not deltas:
        return {
            "keep": False,
            "method": "paired",
            "mode": mode,
            "reason": "no shared prompts between candidate and best aggregates",
            "n_pairs": 0,
        }

    mean_delta = sum(deltas) / len(deltas)
    stddev = statistics.stdev(deltas) if len(deltas) > 1 else 0.0

    if len(deltas) < MIN_PAIRS_FOR_BOOTSTRAP:
        # Too few pairs for a meaningful CI — fall back to the conservative
        # threshold rule (a bare sign check on 1-2 pairs accepts pure noise)
        keep = mean_delta > min_improvement if mode == "keep" else mean_delta >= -min_improvement
        return {
            "keep": keep,
            "method": "paired-degraded",
            "mode": mode,
            "mean_delta": round(mean_delta, 4),
            "threshold": min_improvement,
            "n_pairs": len(deltas),
            "reason": f"only {len(deltas)} shared prompts — CI unavailable, "
                      f"required mean delta {'>' if mode == 'keep' else '>= -'}{min_improvement}",
        }

    has_replicates = all(
        candidate_per_prompt[p].get("replicates") and best_per_prompt[p].get("replicates")
        for p in set(candidate_per_prompt) & set(best_per_prompt)
    )
    if has_replicates:
        ci_low, ci_high = _hierarchical_bootstrap_ci(
            candidate_per_prompt, best_per_prompt, confidence
        )
        method = "hierarchical-bootstrap"
    else:
        ci_low, ci_high = _bootstrap_ci(deltas, confidence)
        method = "paired-bootstrap"
    if mode == "non-regression":
        keep = ci_high >= 0  # reject only if significantly worse
        reason = (
            f"holdout delta CI [{ci_low:.4f}, {ci_high:.4f}] — "
            + ("no significant regression" if keep else "significant regression")
        )
    else:
        keep = ci_low > 0
        reason = (
            f"paired delta CI [{ci_low:.4f}, {ci_high:.4f}] at {confidence:.0%} — "
            + ("improvement is significant" if keep else "improvement not distinguishable from noise")
        )

    return {
        "keep": keep,
        "method": method,
        "mode": mode,
        "mean_delta": round(mean_delta, 4),
        "delta_stddev": round(stddev, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "confidence": confidence,
        "n_pairs": len(deltas),
        "reason": reason,
    }


def decide(candidate_agg: dict, best_agg: dict | None, cfg: dict, mode: str = "keep") -> dict:
    """Dispatch on cfg['accept_rule']: 'paired' (default) or 'simple'."""
    rule = cfg.get("accept_rule", "paired")
    candidate_score = float(candidate_agg.get("composite_score", 0.0))
    best_score = float(best_agg.get("composite_score", 0.0)) if best_agg else 0.0

    if rule == "simple" or not best_agg or "per_prompt" not in candidate_agg or "per_prompt" not in (best_agg or {}):
        min_improvement = float(cfg.get("min_improvement", 0.01))
        delta = candidate_score - best_score
        keep = delta > min_improvement if mode == "keep" else delta > -min_improvement
        return {
            "keep": keep,
            "method": "simple",
            "mode": mode,
            "mean_delta": round(delta, 4),
            "threshold": min_improvement,
            "reason": f"composite delta {delta:.4f} vs threshold {min_improvement}"
                      + ("" if rule == "simple" else " (per-prompt data unavailable, fell back to simple rule)"),
        }

    confidence = float(cfg.get("accept_confidence", 0.95))
    if mode == "keep" and cfg.get("sequential_correction", True):
        # Alpha-spending schedule: alpha_k = alpha / (k * (k + 1)).
        # Since sum(1/(k(k+1))) == 1, the family-wise false-positive budget
        # remains bounded across an arbitrarily long or resumed campaign.
        index = max(1, int(cfg.get("_decision_index", 1)))
        alpha = (1.0 - confidence) / (index * (index + 1))
        confidence = 1.0 - alpha

    verdict = paired_verdict(
        candidate_agg["per_prompt"],
        best_agg["per_prompt"],
        confidence=confidence,
        mode=mode,
        min_improvement=float(cfg.get("min_improvement", 0.01)),
    )
    if mode == "keep" and cfg.get("sequential_correction", True):
        verdict["sequential_correction"] = "alpha-spending"
        verdict["decision_index"] = max(1, int(cfg.get("_decision_index", 1)))
        verdict["configured_confidence"] = float(cfg.get("accept_confidence", 0.95))
    verdict["candidate_composite"] = candidate_score
    verdict["best_composite"] = best_score
    return verdict


def main():
    parser = argparse.ArgumentParser(description="Noise-aware KEEP/DISCARD decision")
    parser.add_argument("--candidate-agg", required=True, help="Path to candidate aggregate.json")
    parser.add_argument("--best-agg", required=True, help="Path to best aggregate.json")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--mode", choices=["keep", "non-regression"], default="keep")
    parser.add_argument("--rule", choices=["paired", "simple"], default="paired")
    parser.add_argument("--min-improvement", type=float, default=0.01)
    args = parser.parse_args()

    candidate = json.loads(Path(args.candidate_agg).read_text(encoding="utf-8"))
    best_path = Path(args.best_agg)
    best = json.loads(best_path.read_text(encoding="utf-8")) if best_path.exists() else None

    cfg = {
        "accept_rule": args.rule,
        "accept_confidence": args.confidence,
        "min_improvement": args.min_improvement,
    }
    verdict = decide(candidate, best, cfg, mode=args.mode)
    print(json.dumps(verdict, indent=2))
    sys.exit(0 if verdict["keep"] else 1)


if __name__ == "__main__":
    main()
