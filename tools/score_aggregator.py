"""
Score Aggregator
================
Reads all per-sample eval JSONs for a run, averages across samples,
applies metric weights from config.yaml, and produces a single composite score.

Usage:
    python3 tools/score_aggregator.py --eval-dir .tmp/evals/baseline/
    python3 tools/score_aggregator.py --eval-dir .tmp/evals/baseline/ --output-path aggregate.json
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_config

_SAMPLE_ID_RE = re.compile(r'^sample_(\d+)_(.+?)(?:_r(\d+))?$')


def prompt_id_from_sample(sample_id: str) -> str:
    """Extract the prompt id from a sample id.

    Handles both legacy ("sample_0_intro_email") and replicate
    ("sample_0_intro_email_r2") naming.
    """
    m = _SAMPLE_ID_RE.match(sample_id)
    return m.group(2) if m else sample_id


def get_metrics_and_weights(cfg: dict) -> tuple[list[str], list[str], dict[str, float], dict[str, str]]:
    """Extract metric names, weights, and directions from config.
    Returns: (deterministic_metric_names, llm_metric_names, weights_dict, directions_dict)
    """
    weights = {}
    directions = {}
    det_names = []
    llm_names = []

    for m in cfg.get("deterministic_metrics", []):
        det_names.append(m["name"])
        weights[m["name"]] = m["weight"]
        directions[m["name"]] = m.get("direction", "higher_is_better")

    for m in cfg.get("llm_judge_dimensions", []):
        llm_names.append(m["name"])
        weights[m["name"]] = m["weight"]
        directions[m["name"]] = m.get("direction", "higher_is_better")

    return det_names, llm_names, weights, directions


def aggregate(eval_dir: str, cfg: dict) -> dict:
    """Aggregate all eval JSONs in a directory into a composite score."""
    det_names, llm_names, weights, directions = get_metrics_and_weights(cfg)
    all_names = det_names + llm_names
    eval_path = Path(eval_dir)

    # Find eval files
    det_files = sorted(eval_path.glob("*_deterministic.json")) if det_names else []
    llm_files = sorted(eval_path.glob("*_llm_judge.json"))

    if not llm_files and not det_files:
        print(f"Error: No eval files found in {eval_dir}", file=sys.stderr)
        sys.exit(1)

    # Determine sample IDs from whichever eval type exists
    if det_files:
        sample_ids = [f.name.replace("_deterministic.json", "") for f in det_files]
    else:
        sample_ids = [f.name.replace("_llm_judge.json", "") for f in llm_files]

    def _sample_composite(scores: dict) -> float:
        """Weighted composite for one sample (lower_is_better inverted)."""
        total = 0.0
        for m in all_names:
            raw = scores.get(m, 0.0)
            effective = (1.0 - raw) if directions.get(m) == "lower_is_better" else raw
            total += effective * weights.get(m, 0.0)
        return total

    # Collect per-sample scores. A sample whose LLM-judge eval failed is
    # EXCLUDED from aggregation (a judge failure is not a bad output) and
    # counted in judge_errors.
    per_sample = []
    judge_errors = 0

    for sample_id in sample_ids:
        scores = {}

        # Read deterministic scores
        if det_names:
            det_file = eval_path / f"{sample_id}_deterministic.json"
            if det_file.exists():
                det_data = json.loads(det_file.read_text(encoding="utf-8"))
                for metric in det_names:
                    if metric in det_data:
                        scores[metric] = det_data[metric]["score"]
                    else:
                        scores[metric] = 0.0
            else:
                for metric in det_names:
                    scores[metric] = 0.0

        # Read LLM judge scores — missing/errored file invalidates the sample
        if llm_names:
            llm_file = eval_path / f"{sample_id}_llm_judge.json"
            llm_data = None
            if llm_file.exists():
                try:
                    llm_data = json.loads(llm_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    llm_data = None
            if llm_data is None or "error" in llm_data:
                judge_errors += 1
                continue
            for metric in llm_names:
                if metric in llm_data and isinstance(llm_data[metric], dict):
                    scores[metric] = llm_data[metric].get("normalised", 0.0)
                else:
                    scores[metric] = 0.0

        per_sample.append({
            "sample_id": sample_id,
            "prompt_id": prompt_id_from_sample(sample_id),
            "scores": scores,
            "composite": round(_sample_composite(scores), 4),
        })

    samples_total = len(sample_ids)
    samples_used = len(per_sample)
    min_valid_frac = float(cfg.get("min_valid_sample_frac", 0.8))
    if samples_total and (samples_used / samples_total) < min_valid_frac:
        print(
            f"Error: only {samples_used}/{samples_total} samples have valid evaluations "
            f"({judge_errors} judge failures) — below min_valid_sample_frac "
            f"{min_valid_frac}. The judge subsystem is failing; fix it rather than "
            f"scoring on partial data.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not per_sample:
        print(f"Error: no valid samples to aggregate in {eval_dir}", file=sys.stderr)
        sys.exit(1)

    # Mean and stddev across valid samples
    metric_averages = {}
    metric_stddev = {}
    for metric in all_names:
        values = [s["scores"].get(metric, 0.0) for s in per_sample]
        metric_averages[metric] = round(statistics.mean(values), 4) if values else 0.0
        metric_stddev[metric] = round(statistics.stdev(values), 4) if len(values) > 1 else 0.0

    composites = [s["composite"] for s in per_sample]
    composite = round(statistics.mean(composites), 4)
    composite_stddev = round(statistics.stdev(composites), 4) if len(composites) > 1 else 0.0

    # Per-prompt composite means (replicates averaged) — the unit the paired
    # decision rule in decision.py compares
    per_prompt = {}
    for s in per_sample:
        per_prompt.setdefault(s["prompt_id"], []).append(s["composite"])
    per_prompt = {
        pid: {
            "composite": round(statistics.mean(vals), 4),
            "n": len(vals),
            "replicates": vals,
        }
        for pid, vals in per_prompt.items()
    }

    result = {
        "composite_score": composite,
        "composite_stddev": composite_stddev,
        "metric_averages": metric_averages,
        "metric_stddev": metric_stddev,
        "weights": weights,
        "directions": directions,
        "sample_count": samples_used,
        "samples_total": samples_total,
        "judge_errors": judge_errors,
        "per_prompt": per_prompt,
        "per_sample": per_sample,
    }

    return result


def main():
    cfg = load_config()
    _, _, weights, directions = get_metrics_and_weights(cfg)
    all_names = list(weights.keys())

    parser = argparse.ArgumentParser(description="Aggregate eval scores into composite score")
    parser.add_argument("--eval-dir", required=True, help="Directory containing eval JSONs")
    parser.add_argument("--output-path", help="Path to write aggregate JSON (default: stdout)")
    args = parser.parse_args()

    result = aggregate(args.eval_dir, cfg)

    output = json.dumps(result, indent=2)

    if args.output_path:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(f"Wrote aggregate to {args.output_path}")
    else:
        print(output)

    # Print summary
    print(f"\n{'='*50}")
    print(f"COMPOSITE SCORE: {result['composite_score']:.4f} ± {result['composite_stddev']:.4f}")
    if result.get("judge_errors"):
        print(f"  ({result['sample_count']}/{result['samples_total']} samples valid, "
              f"{result['judge_errors']} judge failures excluded)")
    print(f"{'='*50}")
    for metric in all_names:
        avg = result["metric_averages"].get(metric, 0.0)
        weight = weights.get(metric, 0.0)
        bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
        print(f"  {metric:<22} {bar} {avg:.3f} (w={weight:.2f})")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
