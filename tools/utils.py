"""
Shared Utilities
=================
Common functions used across the AutoEvaluation toolchain.
Centralises config loading, path resolution, and validation.
"""

import math
import os
import re
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Default model per provider — the single source of truth for every setup
# surface (setup.py, generate_config.py, run_loop.py quick-start).
# Verified against provider model pages 2026-07-16. When these move a
# generation, update here and in ModelClient._PRICING together.
DEFAULT_MODELS = {
    "gemini": ("gemini-3.5-flash", "GEMINI_API_KEY"),
    "openai": ("gpt-5.4", "OPENAI_API_KEY"),
    # claude-sonnet-5 balances cost and quality for a harness making hundreds
    # of calls. claude-opus-4-8 for maximum quality; claude-haiku-4-5 as a
    # cheap judge model.
    "anthropic": ("claude-sonnet-5", "ANTHROPIC_API_KEY"),
}


def load_env(env_path: Path = None) -> None:
    """Load a .env file into os.environ (if it exists).

    Handles KEY=value, KEY="value", and KEY='value' formats.
    Does not overwrite existing env vars.
    """
    if env_path is None:
        env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def default_dimensions() -> list[dict]:
    """Default LLM judge dimensions used by setup.py and run_loop.py."""
    return [
        {
            "name": "natural_voice",
            "weight": 0.30,
            "direction": "higher_is_better",
            "rubric": (
                "Judge only observable features of the prose: sentence and "
                "paragraph length vary naturally; no stock filler phrases "
                "('it's important to note', 'in today's world', 'delve'); "
                "concrete word choice over abstract hedging; transitions read "
                "as thought, not as template. "
                "1 = formulaic and templated, 5 = varied, specific, natural."
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


def load_config(config_path: str = None) -> dict:
    """Load config.yaml from project root or a specified path."""
    cfg_path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    if not cfg_path.exists():
        print(f"Error: config.yaml not found at {cfg_path}. Run 'python3 setup.py' first.", file=sys.stderr)
        sys.exit(1)
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def sanitise_description(desc: str) -> str:
    """Strip control characters from description to protect TSV integrity."""
    return re.sub(r'[\t\n\r\x00-\x1f]', ' ', desc).strip()


def split_prompt_sets(
    prompts: list[dict],
    holdout_fraction: float,
    final_test_fraction: float = 0.0,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split prompts into train, validation, and untouched final-test sets.

    ``holdout`` remains the on-loop validation set for backwards
    compatibility. ``final_test`` is evaluated at baseline and once after the
    loop; it never participates in KEEP/DISCARD decisions.
    """
    explicit_train = [p for p in prompts if p.get("split") == "train"]
    explicit_holdout = [p for p in prompts if p.get("split") in ("holdout", "validation")]
    explicit_final = [p for p in prompts if p.get("split") == "final_test"]
    recognised = ("train", "holdout", "validation", "final_test")
    unassigned = [p for p in prompts if p.get("split") not in recognised]

    n = len(unassigned)
    n_final = max(1, round(n * final_test_fraction)) if final_test_fraction > 0 and n > 2 else 0
    n_holdout = max(1, round(n * holdout_fraction)) if holdout_fraction > 0 and n > 1 else 0
    # Always preserve at least one automatically assigned training prompt.
    overflow = max(0, n_holdout + n_final - max(0, n - 1))
    while overflow and n_final:
        n_final -= 1
        overflow -= 1
    while overflow and n_holdout:
        n_holdout -= 1
        overflow -= 1

    final_start = n - n_final
    holdout_start = final_start - n_holdout
    auto_train = unassigned[:holdout_start]
    auto_holdout = unassigned[holdout_start:final_start]
    auto_final = unassigned[final_start:] if n_final else []
    return (
        explicit_train + auto_train,
        explicit_holdout + auto_holdout,
        explicit_final + auto_final,
    )


def validate_prompts(prompts: list[dict]) -> None:
    """Validate prompt records before any paid model call is made."""
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts file must contain a non-empty JSON array")
    seen = set()
    allowed_splits = {None, "train", "holdout", "validation", "final_test"}
    for index, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            raise ValueError(f"prompt {index} must be an object")
        for field in ("id", "genre", "prompt"):
            if not isinstance(prompt.get(field), str) or not prompt[field].strip():
                raise ValueError(f"prompt {index} requires a non-empty string {field!r}")
        if prompt["id"] in seen:
            raise ValueError(f"duplicate prompt id: {prompt['id']!r}")
        seen.add(prompt["id"])
        if prompt.get("split") not in allowed_splits:
            raise ValueError(
                f"prompt {prompt['id']!r} has invalid split {prompt.get('split')!r}"
            )


def split_prompts(prompts: list[dict], holdout_fraction: float) -> tuple[list[dict], list[dict]]:
    """Split prompts into (train, holdout) sets deterministically.

    Prompts carrying an explicit "split" key ("train" or "holdout") are
    honoured as-is. Of the remainder, the last rounded fraction of prompts
    prompts (in file order) become holdout, so the assignment is stable
    across runs and re-reads.
    """
    train, holdout, final_test = split_prompt_sets(prompts, holdout_fraction, 0.0)
    return train, holdout + final_test


def validate_config(cfg: dict) -> dict:
    """Validate config and return it (possibly with auto-normalised weights).

    Checks:
    - Required keys: provider, model, api_key_env, llm_judge_dimensions
    - Each dimension has name, weight, rubric
    - Weights sum ≈ 1.0 (auto-normalises with warning if not)
    """
    required = ["provider", "model", "api_key_env", "llm_judge_dimensions"]
    for key in required:
        if key not in cfg or cfg[key] is None:
            print(f"Error: Missing required config key: '{key}'", file=sys.stderr)
            sys.exit(1)

    dimensions = cfg["llm_judge_dimensions"]
    if not dimensions:
        print("Error: llm_judge_dimensions must contain at least one dimension", file=sys.stderr)
        sys.exit(1)

    for i, dim in enumerate(dimensions):
        for field in ("name", "weight", "rubric"):
            if field not in dim:
                print(f"Error: llm_judge_dimensions[{i}] missing required field: '{field}'", file=sys.stderr)
                sys.exit(1)

    # Collect all weights (LLM + deterministic)
    all_metrics = list(dimensions) + list(cfg.get("deterministic_metrics", []))
    total_weight = sum(m["weight"] for m in all_metrics)

    if abs(total_weight - 1.0) > 0.01:
        print(f"Warning: Metric weights sum to {total_weight:.4f}, not 1.0. Auto-normalising.", file=sys.stderr)
        for m in all_metrics:
            m["weight"] = m["weight"] / total_weight

    # Evaluation-statistics settings (defaults applied here so downstream
    # tools can read them without re-defaulting)
    cfg.setdefault("replicates_per_prompt", 3)
    cfg.setdefault("accept_rule", "paired")
    cfg.setdefault("accept_confidence", 0.95)
    cfg.setdefault("min_valid_sample_frac", 0.8)
    cfg.setdefault("holdout_fraction", 0.3)
    cfg.setdefault("final_test_fraction", 0.0)
    cfg.setdefault("sequential_correction", True)

    if not isinstance(cfg["replicates_per_prompt"], int) or cfg["replicates_per_prompt"] < 1:
        print("Error: replicates_per_prompt must be an integer >= 1", file=sys.stderr)
        sys.exit(1)
    if cfg["accept_rule"] not in ("paired", "simple"):
        print(f"Error: accept_rule must be 'paired' or 'simple', got {cfg['accept_rule']!r}", file=sys.stderr)
        sys.exit(1)
    if not (0.5 <= float(cfg["accept_confidence"]) < 1.0):
        print("Error: accept_confidence must be in [0.5, 1.0)", file=sys.stderr)
        sys.exit(1)
    if not (0.0 <= float(cfg["min_valid_sample_frac"]) <= 1.0):
        print("Error: min_valid_sample_frac must be in [0, 1]", file=sys.stderr)
        sys.exit(1)
    if not (0.0 <= float(cfg["holdout_fraction"]) <= 0.5):
        print("Error: holdout_fraction must be in [0, 0.5]", file=sys.stderr)
        sys.exit(1)
    if not (0.0 <= float(cfg["final_test_fraction"]) <= 0.5):
        print("Error: final_test_fraction must be in [0, 0.5]", file=sys.stderr)
        sys.exit(1)
    if float(cfg["holdout_fraction"]) + float(cfg["final_test_fraction"]) > 0.6:
        print("Error: holdout_fraction + final_test_fraction must be <= 0.6", file=sys.stderr)
        sys.exit(1)

    return cfg
