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
        --metrics '[{"name":"human_score","weight":0.3,"rubric":"..."},...]' \
        --prompts '[{"id":"task_1","genre":"email","prompt":"Write an email..."},...]' \
        --iterations 10

    # Minimal (uses defaults for metrics and prompts):
    python3 tools/generate_config.py \
        --skill-name "my-skill" \
        --skill-content "Do the thing well." \
        --provider gemini

    # Generate prompts with AI instead of providing them:
    python3 tools/generate_config.py \
        --skill-name "my-skill" \
        --skill-content "Do the thing well." \
        --provider gemini \
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
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from utils import DEFAULT_MODELS, default_dimensions, load_env
from run_state import atomic_write_text


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Shared with setup.py and run_loop.py via utils.default_dimensions()
_DEFAULT_METRICS = default_dimensions()

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
    {"id": "task_6", "genre": "formal email", "prompt": "Write a formal email explaining a missed deadline and the recovery plan."},
    {"id": "task_7", "genre": "customer support", "prompt": "Reply to a frustrated customer whose issue has recurred twice."},
    {"id": "task_8", "genre": "technical explanation", "prompt": "Explain rate limiting to a junior developer with one concrete example."},
    {"id": "task_9", "genre": "executive summary", "prompt": "Summarise a quarter where revenue rose but customer churn also increased."},
    {"id": "task_10", "genre": "documentation", "prompt": "Write setup instructions for a command-line tool, including verification and rollback."},
    {"id": "task_11", "genre": "proposal", "prompt": "Propose a small internal automation project, including scope, risks, and success measures."},
    {"id": "task_12", "genre": "incident report", "prompt": "Write a concise post-incident summary for a 45-minute production outage."},
    {"id": "task_13", "genre": "marketing", "prompt": "Describe a developer feature without hype or unsupported claims."},
    {"id": "task_14", "genre": "opinion", "prompt": "Argue for or against four-day work weeks and acknowledge the strongest counterargument."},
    {"id": "task_15", "genre": "editing", "prompt": "Rewrite this sentence plainly: 'We leveraged cross-functional synergies to operationalise strategic outcomes.'"},
    {"id": "task_16", "genre": "announcement", "prompt": "Announce a pricing change while explaining who is affected and when."},
    {"id": "task_17", "genre": "feedback", "prompt": "Give kind but direct feedback to a colleague whose reports are consistently late."},
    {"id": "task_18", "genre": "meeting notes", "prompt": "Turn messy meeting notes into decisions, owners, and open questions."},
    {"id": "task_19", "genre": "tutorial", "prompt": "Teach a beginner how to diagnose a failed deployment without assuming specialist knowledge."},
    {"id": "task_20", "genre": "comparison", "prompt": "Compare two project management approaches in a compact table and recommendation."},
    {"id": "task_21", "genre": "sensitive communication", "prompt": "Tell a team that a planned project has been cancelled without blaming individuals."},
    {"id": "task_22", "genre": "long-form", "prompt": "Write a structured 600-word article about why observability matters in distributed systems."},
    {"id": "task_23", "genre": "short-form", "prompt": "Explain a two-week delay in no more than 40 words."},
    {"id": "task_24", "genre": "creative", "prompt": "Write a short opening scene in which two engineers discover an unexpected system behaviour."},
    {"id": "task_25", "genre": "data commentary", "prompt": "Explain a chart showing sales up 12%, margin down 4%, and returns up 9%."},
    {"id": "task_26", "genre": "FAQ", "prompt": "Write five concise FAQ answers for a beta feature with known limitations."},
    {"id": "task_27", "genre": "boundary", "prompt": "Respond helpfully to an ambiguous request for 'a better update' by stating reasonable assumptions."},
    {"id": "task_28", "genre": "constraint", "prompt": "Write a useful project update using exactly three bullet points and no introduction."},
    {"id": "task_29", "genre": "contrarian", "prompt": "Challenge a proposal to add more process while remaining constructive and specific."},
    {"id": "task_30", "genre": "mixed audience", "prompt": "Explain a security change to both engineers and non-technical managers in one message."},
]

_PROVIDER_MAP = DEFAULT_MODELS


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

Given a skill file (instructions that tell an LLM how to behave), generate exactly 30 diverse test prompts
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

Generate exactly 30 diverse test prompts that would thoroughly evaluate this skill. Return ONLY the JSON array."""

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
    skill_content: str | None,
    provider: str,
    model: str,
    api_key_env: str,
    api_key: str,
    metrics: list[dict],
    prompts: list[dict] | None,
    iterations: int,
    max_hours: float = 0,
    judge_provider: str = "",
    judge_model: str = "",
    skill_path_config: str = "SKILL.md",
    advanced: dict | None = None,
):
    """Write all config files atomically.

    Shared by tools/generate_config.py's CLI (used by the /autoeval skill)
    and setup.py (interactive wizard + --defaults mode) — this is the single
    place config.yaml / .env / SKILL.md / prompts.json / settings.json /
    .gitignore get written, so schema changes only happen once.

    skill_content=None skips writing SKILL.md (e.g. an external file was
    already copied into place). prompts=None skips writing prompts.json
    likewise. advanced carries setup.py's optional judge_sees_skill /
    replicates_per_prompt / convergence_window / max_cost_usd /
    max_concurrent overrides.
    """

    # 1. .env — preserve existing keys, update/add the one we need
    env_path = PROJECT_ROOT / ".env"
    env_lines = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, _ = stripped.partition("=")
                if k.strip() == api_key_env:
                    continue  # We'll re-add it
            env_lines.append(line)
    env_lines.append(f"{api_key_env}={api_key}")
    atomic_write_text(env_path, "\n".join(env_lines) + "\n")
    print("  ✓ .env")

    # 2. config.yaml
    config = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "skill_path": skill_path_config,
        "prompts_path": "prompts/prompts.json",
        "results_tsv": "results.tsv",
        "max_iterations": iterations,
        "max_hours": max_hours,
        "judge_sees_skill": True,
        "replicates_per_prompt": 3,
        "accept_rule": "paired",
        "accept_confidence": 0.95,
        "min_valid_sample_frac": 0.8,
        "holdout_fraction": 0.3,
        "final_test_fraction": 0.2,
        "sequential_correction": True,
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
    if judge_model:
        config["judge_provider"] = judge_provider or provider
        config["judge_model"] = judge_model
        config["judge_api_key_env"] = DEFAULT_MODELS.get(judge_provider or provider, (None, api_key_env))[1]
    if advanced:
        config["judge_sees_skill"] = advanced.get("judge_sees_skill", True)
        config["replicates_per_prompt"] = advanced.get("replicates_per_prompt", 3)
        config["convergence_window"] = advanced.get("convergence_window", 0)
        config["max_cost_usd"] = advanced.get("max_cost_usd", 0)
        config["max_concurrent"] = advanced.get("max_concurrent", 1)
    cfg_path = PROJECT_ROOT / "config.yaml"
    atomic_write_text(cfg_path, yaml.dump(config, default_flow_style=False, sort_keys=False))
    print("  ✓ config.yaml")

    # 3. SKILL.md (skip if skill_content is None — e.g. an external file was
    # already copied into place, or the caller wants to keep the existing one)
    if skill_content is not None:
        skill_md = (
            f"---\n"
            f"name: {skill_name}\n"
            f"description: {skill_description}\n"
            f"---\n\n"
            f"# {skill_name.replace('-', ' ').replace('_', ' ').title()} Rules\n\n"
            f"{skill_content}\n"
        )
        skill_path = PROJECT_ROOT / skill_path_config
        atomic_write_text(skill_path, skill_md)
        print(f"  ✓ {skill_path_config}")

    # 4. prompts/prompts.json (skip if prompts is None)
    if prompts is not None:
        prompts_dir = PROJECT_ROOT / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompts_path = prompts_dir / "prompts.json"
        atomic_write_text(prompts_path, json.dumps(prompts, indent=2) + "\n")
        print(f"  ✓ prompts/prompts.json ({len(prompts)} prompts)")

    # 5. .claude/settings.json — narrowly scoped commands used by /autoeval.
    # Avoid generic readers such as `cat *`: unattended setup should not grant
    # blanket access to secrets or unrelated files.
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
                "Bash(python3 tools/decision.py *)",
                "Bash(python3 tools/results_io.py *)",
                "Bash(open http://localhost:*)",
                "Bash(cp SKILL.md SKILL.md.best)",
                "Bash(cp SKILL.md.best SKILL.md)",
                "Bash(cp .tmp/evals/* best_aggregate.json)",
                "Bash(cp .tmp/evals/* best_holdout_aggregate.json)",
            ]
        }
    }
    settings_path = claude_dir / "settings.json"
    atomic_write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    print("  ✓ .claude/settings.json")

    # 6. .gitignore — append missing entries only; never clobber an existing
    # (possibly hand-maintained) file
    gitignore_path = PROJECT_ROOT / ".gitignore"
    required = [".env", ".tmp/", "__pycache__/", "*.pyc", "results.tsv",
                "SKILL.md.best", "config.yaml", "best_aggregate.json",
                "best_holdout_aggregate.json", "baseline_final_test_aggregate.json",
                "final_test_aggregate.json"]
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [e for e in required if e not in existing_lines and f"/{e}" not in existing_lines]
    if missing:
        content = existing.rstrip("\n") + ("\n" if existing else "") + "\n".join(missing) + "\n"
        atomic_write_text(gitignore_path, content)
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
    parser.add_argument("--api-key", default="",
                        help="Deprecated: prefer the provider environment variable or .env")
    parser.add_argument("--judge-provider", default="", choices=["", "gemini", "openai", "anthropic"],
                        help="Separate judge provider (defaults to --provider when --judge-model is set)")
    parser.add_argument("--judge-model", default="",
                        help="Separate judge model, e.g. a cheaper model, to avoid self-judging bias")
    parser.add_argument("--metrics", default="", help="JSON array of metric dicts (default: 3 standard)")
    parser.add_argument("--prompts", default="", help="JSON array of prompt dicts (default: 30 diverse)")
    parser.add_argument("--generate-prompts", action="store_true", help="Use AI to generate test prompts")
    parser.add_argument("--iterations", type=int, default=10, help="Max iterations (default: 10)")
    parser.add_argument("--max-hours", type=float, default=0, help="Max hours (default: 0 = unlimited)")
    parser.add_argument("--validate-key", action="store_true", default=True, help="Validate the API key (default: true)")
    parser.add_argument("--no-validate-key", dest="validate_key", action="store_false", help="Skip API key validation")

    args = parser.parse_args()

    # Provider defaults
    default_model, api_key_env = _PROVIDER_MAP[args.provider]
    model = args.model or default_model

    # Prefer .env/environment so secrets do not appear in shell history or the
    # process list. --api-key remains for backwards compatibility.
    load_env()
    api_key = args.api_key or os.environ.get(api_key_env, "")
    if not api_key:
        print(f"Error: set {api_key_env} in .env or the environment.", file=sys.stderr)
        sys.exit(1)
    os.environ[api_key_env] = api_key

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
        api_key=api_key,
        metrics=metrics,
        prompts=prompts,
        iterations=args.iterations,
        max_hours=args.max_hours,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
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
