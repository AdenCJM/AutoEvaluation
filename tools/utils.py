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
# Verified against provider pricing pages 2026-07-07. When these move a
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


def split_prompts(prompts: list[dict], holdout_fraction: float) -> tuple[list[dict], list[dict]]:
    """Split prompts into (train, holdout) sets deterministically.

    Prompts carrying an explicit "split" key ("train" or "holdout") are
    honoured as-is. Of the remainder, the last ceil(n * holdout_fraction)
    prompts (in file order) become holdout, so the assignment is stable
    across runs and re-reads.
    """
    explicit_train = [p for p in prompts if p.get("split") == "train"]
    explicit_holdout = [p for p in prompts if p.get("split") == "holdout"]
    unassigned = [p for p in prompts if p.get("split") not in ("train", "holdout")]

    if holdout_fraction > 0 and len(unassigned) > 1:
        # round() tracks the fraction on small sets (ceil overshoots badly:
        # 3 prompts at 0.34 would hold out 2); always hold out at least 1
        n_holdout = max(1, round(len(unassigned) * holdout_fraction))
    else:
        n_holdout = 0
    # Never let the holdout swallow the training set
    n_holdout = min(n_holdout, max(0, len(unassigned) - 1))

    if n_holdout:
        auto_train, auto_holdout = unassigned[:-n_holdout], unassigned[-n_holdout:]
    else:
        auto_train, auto_holdout = unassigned, []

    return explicit_train + auto_train, explicit_holdout + auto_holdout


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

    return cfg
