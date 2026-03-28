#!/usr/bin/env python3
"""
AutoEvaluation Config Generator
=================================
Generates all config files from CLI arguments. Designed to be called by
Claude Code's /autoeval skill so setup happens conversationally, not
through terminal prompts.

Writes: config.yaml, SKILL.md, prompts/prompts.json, .env, .claude/settings.json

Usage:
    python3 tools/generate_config.py \
        --skill-name "writing-style" \
        --skill-description "Rules for natural, human-sounding writing" \
        --skill-content "Write like a human. Avoid AI cliches..." \
        --provider gemini \
        --model gemini-2.5-flash \
        --api-key "AIza..." \
        --metrics '[{"name":"human_score","weight":0.3,"rubric":"..."},...]' \
        --prompts '[{"id":"task_1","genre":"email","prompt":"Write an email..."},...]' \
        --iterations 10

    # Minimal (uses defaults for metrics and prompts):
    python3 tools/generate_config.py \
        --skill-name "my-skill" \
        --skill-content "Do the thing well." \
        --provider gemini \
        --api-key "AIza..."

    # Generate prompts with AI instead of providing them:
    python3 tools/generate_config.py \
        --skill-name "my-skill" \
        --skill-content "Do the thing well." \
        --provider gemini \
        --api-key "AIza..." \
        --generate-prompts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_METRICS = [
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

_DEFAULT_PROMPTS = [
    {
        "id": "task_1",
        "genre": "general",
        "prompt": "Write a short paragraph explaining what you do and why it matters.",
    },
    {
        "id": "task_2",
        "genre": "general",
        "prompt": "Write a brief email to a colleague summarising the key takeaways from a meeting.",
    },
    {
        "id": "task_3",
        "genre": "general",
        "prompt": "Draft a one-paragraph product description for a new feature aimed at technical users.",
    },
    {
        "id": "task_4",
        "genre": "general",
        "prompt": "Write a short Slack message to your team announcing a timeline change.",
    },
    {
        "id": "task_5",
        "genre": "general",
        "prompt": "Write a two-paragraph blog post intro about how your team solved a recent challenge.",
    },
]

_PROVIDER_MAP = {
    "gemini": ("gemini-2.5-flash", "GEMINI_API_KEY"),
    "openai": ("gpt-4o", "OPENAI_API_KEY"),
    "anthropic": ("claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
}


# ---------------------------------------------------------------------------
# AI Prompt Generation
# ---------------------------------------------------------------------------

def generate_prompts_with_ai(
    provider: str, model: str, api_key_env: str,
    skill_name: str, skill_description: str, skill_content: str,
) -> list[dict] | None:
    """Use the configured LLM to generate test prompts from a skill description."""
    print("Generating test prompts with AI...", end="", flush=True)

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from model_client import ModelClient
    client = ModelClient(provider=provider, model=model, api_key_env=api_key_env)

    system_prompt = """You are a test scenario designer for LLM skill evaluation.

Given a skill file (instructions that tell an LLM how to behave), generate 5-10 diverse test prompts
that would thoroughly exercise the skill across different situations.

Each prompt should:
- Be a realistic task a user might actually ask
- Test a different aspect or edge case of the skill
- Vary in length, complexity, and style
- Include at least one that tests boundary conditions

Respond with ONLY a valid JSON array. No markdown, no explanation. Each entry must have exactly these keys:
{"id": "short_snake_case_id", "genre": "category", "prompt": "the actual test prompt"}"""

    user_prompt = f"""Here is the skill to generate test prompts for:

Skill name: {skill_name}
Description: {skill_description or 'No description provided'}

Skill instructions:
{skill_content}

Generate 5-10 diverse test prompts that would thoroughly evaluate this skill. Return ONLY the JSON array."""

    try:
        response = client.generate(system_prompt, user_prompt, max_tokens=2048)
        print(" ✓")

        # Extract JSON from response
        json_text = response.strip()

        # Handle markdown code blocks
        if "```" in json_text:
            lines = json_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            if json_lines:
                json_text = "\n".join(json_lines)

        # Find JSON array
        start_idx = json_text.find("[")
        end_idx = json_text.rfind("]")
        if start_idx >= 0 and end_idx > start_idx:
            json_text = json_text[start_idx:end_idx + 1]

        prompts = json.loads(json_text)

        if not isinstance(prompts, list) or not prompts:
            raise ValueError("Expected a non-empty JSON array")

        # Validate and backfill
        valid_prompts = []
        for i, p in enumerate(prompts):
            if not isinstance(p, dict) or "prompt" not in p:
                continue
            p.setdefault("id", f"prompt_{i + 1}")
            p.setdefault("genre", "general")
            valid_prompts.append(p)

        if not valid_prompts:
            raise ValueError("No valid prompts in response")

        return valid_prompts

    except Exception as e:
        print(f" ✗ {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# API Key Validation
# ---------------------------------------------------------------------------

def validate_api_key(provider: str, model: str, api_key_env: str) -> bool:
    """Validate an API key by making a tiny LLM call."""
    print("Validating API key...", end="", flush=True)

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        from model_client import ModelClient
        client = ModelClient(provider=provider, model=model, api_key_env=api_key_env)
        response = client.generate("Respond with OK.", "Say OK.", max_tokens=8)
        if response and len(response.strip()) > 0:
            print(" ✓")
            return True
        else:
            print(" ✗ Empty response")
            return False
    except Exception as e:
        print(f" ✗ {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# File Writers
# ---------------------------------------------------------------------------

def write_all(
    skill_name: str,
    skill_description: str,
    skill_content: str,
    provider: str,
    model: str,
    api_key_env: str,
    api_key: str,
    metrics: list[dict],
    prompts: list[dict],
    iterations: int,
    max_hours: float = 0,
):
    """Write all config files atomically."""

    # 1. .env
    env_path = PROJECT_ROOT / ".env"
    env_lines = []
    if env_path.exists():
        # Preserve existing keys, update the one we need
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, _ = stripped.partition("=")
                if k.strip() == api_key_env:
                    continue  # We'll re-add it
            env_lines.append(line)
    env_lines.append(f"{api_key_env}={api_key}")
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print("  ✓ .env")

    # 2. config.yaml
    config = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "skill_path": "SKILL.md",
        "prompts_path": "prompts/prompts.json",
        "results_tsv": "results.tsv",
        "max_iterations": iterations,
        "max_hours": max_hours,
        "llm_judge_dimensions": [
            {
                "name": m["name"],
                "weight": m["weight"],
                "direction": m.get("direction", "higher_is_better"),
                "rubric": m["rubric"],
            }
            for m in metrics
        ],
        "deterministic_metrics": [],
    }
    cfg_path = PROJECT_ROOT / "config.yaml"
    cfg_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print("  ✓ config.yaml")

    # 3. SKILL.md
    skill_md = (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: {skill_description}\n"
        f"---\n\n"
        f"# {skill_name.replace('-', ' ').replace('_', ' ').title()} Rules\n\n"
        f"{skill_content}\n"
    )
    skill_path = PROJECT_ROOT / "SKILL.md"
    skill_path.write_text(skill_md, encoding="utf-8")
    print("  ✓ SKILL.md")

    # 4. prompts/prompts.json
    prompts_dir = PROJECT_ROOT / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    prompts_path = prompts_dir / "prompts.json"
    prompts_path.write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    print(f"  ✓ prompts/prompts.json ({len(prompts)} prompts)")

    # 5. .claude/settings.json
    claude_dir = PROJECT_ROOT / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "permissions": {
            "allow": [
                "Bash(python3 tools/experiment_runner.py *)",
                "Bash(python3 tools/generate_samples.py *)",
                "Bash(python3 tools/eval_deterministic.py *)",
                "Bash(python3 tools/eval_llm_judge.py *)",
                "Bash(python3 tools/score_aggregator.py *)",
                "Bash(python3 tools/dashboard_server.py *)",
                "Bash(python3 tools/generate_config.py *)",
                "Bash(python3 tools/run_loop.py *)",
                "Bash(open http://localhost:*)",
                "Bash(cp SKILL.md SKILL.md.best)",
                "Bash(cp SKILL.md.best SKILL.md)",
                "Bash(cat *)",
                "Bash(head *)",
                "Bash(tail *)",
                "Bash(wc *)",
            ]
        }
    }
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print("  ✓ .claude/settings.json")

    # 6. .gitignore
    gitignore_path = PROJECT_ROOT / ".gitignore"
    gitignore_content = ".env\n.tmp/\n__pycache__/\n*.pyc\n.claude/\nresults.tsv\nSKILL.md.best\nconfig.yaml\n"
    gitignore_path.write_text(gitignore_content, encoding="utf-8")
    print("  ✓ .gitignore")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate AutoEvaluation config files from CLI arguments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--skill-name", required=True, help="Short name for the skill (e.g. 'writing-style')")
    parser.add_argument("--skill-description", default="", help="One-line description")
    parser.add_argument("--skill-content", required=True, help="The skill instructions content")
    parser.add_argument("--provider", required=True, choices=["gemini", "openai", "anthropic"])
    parser.add_argument("--model", default="", help="Model name (defaults to provider's default)")
    parser.add_argument("--api-key", required=True, help="API key value")
    parser.add_argument("--metrics", default="", help="JSON array of metric dicts (default: 3 standard)")
    parser.add_argument("--prompts", default="", help="JSON array of prompt dicts (default: 5 generic)")
    parser.add_argument("--generate-prompts", action="store_true", help="Use AI to generate test prompts")
    parser.add_argument("--iterations", type=int, default=10, help="Max iterations (default: 10)")
    parser.add_argument("--max-hours", type=float, default=0, help="Max hours (default: 0 = unlimited)")
    parser.add_argument("--validate-key", action="store_true", default=True, help="Validate the API key (default: true)")
    parser.add_argument("--no-validate-key", dest="validate_key", action="store_false", help="Skip API key validation")

    args = parser.parse_args()

    # Provider defaults
    default_model, api_key_env = _PROVIDER_MAP[args.provider]
    model = args.model or default_model

    # Set API key in environment for validation and prompt generation
    os.environ[api_key_env] = args.api_key

    # Validate API key
    if args.validate_key:
        if not validate_api_key(args.provider, model, api_key_env):
            print("Error: API key validation failed.", file=sys.stderr)
            sys.exit(1)

    # Parse metrics
    if args.metrics:
        try:
            metrics = json.loads(args.metrics)
            if not isinstance(metrics, list):
                raise ValueError("Must be a JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error: Invalid --metrics JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        metrics = _DEFAULT_METRICS

    # Parse or generate prompts
    if args.prompts:
        try:
            prompts = json.loads(args.prompts)
            if not isinstance(prompts, list):
                raise ValueError("Must be a JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error: Invalid --prompts JSON: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.generate_prompts:
        prompts = generate_prompts_with_ai(
            args.provider, model, api_key_env,
            args.skill_name, args.skill_description, args.skill_content,
        )
        if prompts is None:
            print("AI prompt generation failed, using defaults.")
            prompts = _DEFAULT_PROMPTS
    else:
        prompts = _DEFAULT_PROMPTS

    # Write everything
    print("\nWriting config files:")
    write_all(
        skill_name=args.skill_name,
        skill_description=args.skill_description,
        skill_content=args.skill_content,
        provider=args.provider,
        model=model,
        api_key_env=api_key_env,
        api_key=args.api_key,
        metrics=metrics,
        prompts=prompts,
        iterations=args.iterations,
        max_hours=args.max_hours,
    )

    # Print summary
    print(f"\n{'='*50}")
    print("  CONFIG GENERATED SUCCESSFULLY")
    print(f"{'='*50}")
    print(f"  Skill:       {args.skill_name}")
    print(f"  Provider:    {args.provider} ({model})")
    print(f"  Metrics:     {len(metrics)} dimensions")
    print(f"  Prompts:     {len(prompts)} test scenarios")
    print(f"  Iterations:  {args.iterations}")
    print(f"{'='*50}")
    print()
    print("Ready to run: python3 tools/run_loop.py")


if __name__ == "__main__":
    main()
