"""
LLM Judge Evaluator
====================
Evaluation of output samples using a configured LLM as a judge.
Supports blind mode (default) and semi-blind mode where the judge sees
SKILL.md content for the task_accuracy dimension only.

Dimensions are read from config.yaml, so this works for any use case.

Usage:
    python3 tools/eval_llm_judge.py --sample-path .tmp/samples/baseline/sample_0.txt
    python3 tools/eval_llm_judge.py --sample-path sample.txt --output-path eval.json
    python3 tools/eval_llm_judge.py --sample-path sample.txt --skill-path SKILL.md
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from model_client import ModelClient
from utils import PROJECT_ROOT, load_config


def build_judge_prompt(dimensions: list[dict], skill_content: str = None) -> str:
    """Build a judge system prompt from config dimensions.

    If skill_content is provided (semi-blind mode), it is included with
    instructions to use it ONLY for the task_accuracy dimension.
    """
    lines = [
        "You are evaluating a piece of writing/output for quality. "
        "You are a strict, experienced evaluator.\n",
        "Score each dimension from 1-5 with a one-sentence justification.\n",
    ]

    if skill_content:
        lines.append(
            "For context, here are the instructions this output was supposed to follow:\n"
            "---SKILL---\n"
            f"{skill_content}\n"
            "---END SKILL---\n\n"
            "IMPORTANT: Use this context ONLY when evaluating the task_accuracy dimension. "
            "For all other dimensions, evaluate the output on its own merits.\n"
        )

    lines.append("Dimensions:\n")

    response_format = {}
    for i, dim in enumerate(dimensions, 1):
        lines.append(f"{i}. \"{dim['name']}\"")
        lines.append(dim["rubric"].strip())
        lines.append("")
        response_format[dim["name"]] = {"score": "N", "reason": "..."}

    lines.append(
        "Judge quality, not quantity: do NOT reward an output for being longer "
        "or more detailed unless the task explicitly asked for it.\n"
    )

    lines.append(
        "You MUST respond with ONLY valid JSON in exactly this format:"
    )
    lines.append(json.dumps(response_format))

    return "\n".join(lines)


def build_judge_schema(dimensions: list[dict]) -> dict:
    """JSON Schema for the judge response, built from config dimensions.

    Passed to ModelClient.generate_structured so providers with native
    structured output cannot return malformed scores.
    """
    props = {}
    for dim in dimensions:
        props[dim["name"]] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def judge_sample(
    sample_text: str,
    dimensions: list[dict],
    client: ModelClient,
    skill_content: str = None,
) -> dict:
    """Send sample to LLM judge and return normalised scores.

    Uses structured output (schema-enforced JSON where the provider supports
    it), retrying once on failure. A failed judge call returns a result with
    an "error" key — the aggregator excludes such samples rather than
    treating them as zero-score outputs.
    """
    system_prompt = build_judge_prompt(dimensions, skill_content=skill_content)
    schema = build_judge_schema(dimensions)
    user_prompt = f"Evaluate this output:\n\n---\n{sample_text}\n---"

    scores = None
    last_error = None
    for attempt in range(2):
        try:
            scores = client.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                max_tokens=2048,
            )
            break
        except Exception as e:
            last_error = e
            if attempt == 0:
                print(f"  Judge call failed ({type(e).__name__}), retrying once...", file=sys.stderr)

    if not isinstance(scores, dict):
        result = {"error": f"Judge call failed: {type(last_error).__name__}: {last_error}"}
        for dim in dimensions:
            result[dim["name"]] = {"score": 0, "normalised": 0.0, "reason": "judge error"}
        return result

    # Normalise 1-5 scores to 0-1
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
            raw_score = scores[name].get("score", 0)
            result[name] = {
                "score": raw_score,
                "normalised": normalise(raw_score),
                "reason": scores[name].get("reason", ""),
            }
        else:
            result[name] = {
                "score": 0,
                "normalised": 0.0,
                "reason": f"dimension '{name}' missing from judge response",
            }

    return result


def main():
    cfg = load_config()
    dimensions = cfg.get("llm_judge_dimensions", [])
    if not dimensions:
        print("Error: No llm_judge_dimensions defined in config.yaml", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="LLM judge evaluator")
    parser.add_argument("--sample-path", required=True, help="Path to the text sample file")
    parser.add_argument("--output-path", help="Path to write JSON output (default: stdout)")
    parser.add_argument("--skill-path", help="Path to SKILL.md for semi-blind evaluation (task_accuracy only)")
    args = parser.parse_args()

    sample_path = Path(args.sample_path)
    if not sample_path.exists():
        print(f"Error: Sample file not found: {sample_path}", file=sys.stderr)
        sys.exit(1)

    # Load skill content for semi-blind mode
    skill_content = None
    judge_sees_skill = cfg.get("judge_sees_skill", False)
    if judge_sees_skill and args.skill_path:
        skill_path = Path(args.skill_path)
        if skill_path.exists() and skill_path.stat().st_size > 0:
            skill_content = skill_path.read_text(encoding="utf-8")
        else:
            print(f"Warning: --skill-path '{args.skill_path}' not found or empty, falling back to blind evaluation", file=sys.stderr)

    client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"), judge=True)
    text = sample_path.read_text(encoding="utf-8")

    print(f"Judging sample: {sample_path.name}...", end=" ", flush=True)
    results = judge_sample(text, dimensions, client, skill_content=skill_content)
    print("done")

    output = json.dumps(results, indent=2)

    if args.output_path:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(f"Wrote evaluation to {args.output_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
