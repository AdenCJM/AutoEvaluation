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
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_config


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

    # Collect per-sample scores
    per_sample = []

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

        # Read LLM judge scores
        llm_file = eval_path / f"{sample_id}_llm_judge.json"
        if llm_file.exists():
            llm_data = json.loads(llm_file.read_text(encoding="utf-8"))
            for metric in llm_names:
                if metric in llm_data and "error" not in llm_data:
                    scores[metric] = llm_data[metric]["normalised"]
                else:
                    scores[metric] = 0.0
        else:
            for metric in llm_names:
                scores[metric] = 0.0

        per_sample.append({"sample_id": sample_id, "scores": scores})

    # Average across samples
    metric_averages = {}
    for metric in all_names:
        values = [s["scores"].get(metric, 0.0) for s in per_sample]
        metric_averages[metric] = round(statistics.mean(values), 4) if values else 0.0

    # Compute weighted composite
    # For lower_is_better metrics, invert the score (1 - raw) so composite always = higher is better
    composite = 0.0
    for m in all_names:
        raw = metric_averages.get(m, 0.0)
        effective = (1.0 - raw) if directions.get(m) == "lower_is_better" else raw
        composite += effective * weights.get(m, 0.0)
    composite = round(composite, 4)

    result = {
        "composite_score": composite,
        "metric_averages": metric_averages,
        "weights": weights,
        "directions": directions,
        "sample_count": len(per_sample),
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
    print(f"COMPOSITE SCORE: {result['composite_score']:.4f}")
    print(f"{'='*50}")
    for metric in all_names:
        avg = result["metric_averages"].get(metric, 0.0)
        weight = weights.get(metric, 0.0)
        bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
        print(f"  {metric:<22} {bar} {avg:.3f} (w={weight:.2f})")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
