"""
Deterministic Evaluator — Default (No-op)
==========================================
Placeholder deterministic evaluator. Returns an empty result set.

To add custom rule-based metrics, replace this file with your own
implementation. See examples/writing-style/eval_deterministic.py for
a complete example with 9 metrics.

Usage:
    python3 tools/eval_deterministic.py --sample-path .tmp/samples/baseline/sample_0.txt
    python3 tools/eval_deterministic.py --sample-path sample.txt --output-path eval.json
"""

import argparse
import json
import sys
from pathlib import Path


def evaluate_sample(text: str) -> dict:
    """Run deterministic metrics on a text sample.

    Override this function with your own metrics. Each metric should return
    a dict with at least a "score" key (0.0-1.0).

    Returns:
        dict: Metric name -> {score, ...} pairs.
    """
    return {}


def main():
    parser = argparse.ArgumentParser(description="Deterministic evaluator (default no-op)")
    parser.add_argument("--sample-path", required=True, help="Path to the text sample file")
    parser.add_argument("--output-path", help="Path to write JSON output (default: stdout)")
    args = parser.parse_args()

    sample_path = Path(args.sample_path)
    if not sample_path.exists():
        print(f"Error: Sample file not found: {sample_path}", file=sys.stderr)
        sys.exit(1)

    text = sample_path.read_text(encoding="utf-8")
    results = evaluate_sample(text)

    output = json.dumps(results, indent=2)

    if args.output_path:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(f"Wrote evaluation to {args.output_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
