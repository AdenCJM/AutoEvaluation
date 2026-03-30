"""
Shared Utilities
=================
Common functions used across the AutoEvaluation toolchain.
Centralises config loading, path resolution, and validation.
"""

import os
import re
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


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

    return cfg
