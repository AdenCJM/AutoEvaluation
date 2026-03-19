"""
Shared Utilities
=================
Common functions used across the AutoEvaluation toolchain.
Centralises config loading, path resolution, and validation.
"""

import re
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


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
