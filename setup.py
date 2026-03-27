#!/usr/bin/env python3
"""
AutoEvaluation Setup Wizard
============================
Interactive CLI that walks you through first-run configuration.
Generates: config.yaml, SKILL.md, prompts/prompts.json, .env, .claude/settings.json

Usage:
    python3 setup.py                                    # Full interactive wizard
    python3 setup.py --defaults                         # Gemini + 10 iters, skip advanced config
    python3 setup.py --skill-file /path/to/SKILL.md     # Point at an existing skill
    python3 setup.py --skill-file SKILL.md --prompts-file prompts.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict:
    """Load key=value pairs from a .env file into a dict."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    """Prompt the user with an optional default."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    else:
        result = input(f"{prompt}: ").strip()
        while not result:
            result = input(f"  (required) {prompt}: ").strip()
        return result


def ask_choice(prompt: str, options: list[str], default: str = "") -> str:
    """Prompt the user to pick from options."""
    opts_str = " / ".join(options)
    return ask(f"{prompt} ({opts_str})", default)


def ask_int(prompt: str, default: int = 0) -> int:
    """Prompt for an integer."""
    while True:
        raw = ask(prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a number.")


def ask_float(prompt: str, default: float = 0.0) -> float:
    """Prompt for a float."""
    while True:
        raw = ask(prompt, str(default))
        try:
            return float(raw)
        except ValueError:
            print("  Please enter a number.")


def ask_multiline(prompt: str) -> str:
    """Prompt for multi-line input. End with a blank line."""
    print(f"{prompt} (enter a blank line to finish):")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------

def validate_api_key(provider: str, model: str, api_key_env: str, api_key: str) -> tuple[bool, str]:
    """Make a tiny test call to confirm the API key works.

    Sets os.environ[api_key_env] before instantiating ModelClient so the
    client finds the key. Returns (ok, error_message).
    """
    os.environ[api_key_env] = api_key
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from model_client import ModelClient
        client = ModelClient(provider=provider, model=model, api_key_env=api_key_env)
        client.generate("You are a test.", "Say ok in one word.")
        return True, ""
    except SystemExit:
        return False, f"ModelClient could not start — is {api_key_env} valid?"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# AI prompt generation
# ---------------------------------------------------------------------------

def ai_generate_prompts(
    provider: str,
    model: str,
    api_key_env: str,
    skill_name: str,
    skill_description: str,
    skill_content: str,
) -> list[dict]:
    """Use the configured LLM to generate 6 test prompts for the skill.

    Returns a list of dicts with keys: id, genre, prompt.
    Returns empty list on failure (caller falls back to manual entry).
    """
    import re as _re
    import json as _json

    os.environ.setdefault(api_key_env, os.environ.get(api_key_env, ""))
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from model_client import ModelClient
        client = ModelClient(provider=provider, model=model, api_key_env=api_key_env)

        system_prompt = (
            "You are helping set up an LLM evaluation suite. "
            "Generate test prompts that will be sent to an LLM following the given skill instructions. "
            "Each prompt should be a realistic task that exercises the skill."
        )
        user_prompt = f"""Skill name: {skill_name}
Skill description: {skill_description}

Skill content:
{skill_content[:2000]}

Generate 6 diverse test prompts. Return ONLY a JSON array with this exact structure:
[
  {{"id": "snake_case_id", "genre": "category", "prompt": "The full prompt text"}},
  ...
]

Make the prompts varied — different lengths, tones, edge cases. No markdown, no explanation, just the JSON array."""

        response = client.generate(system_prompt, user_prompt, max_tokens=2048)

        match = _re.search(r'\[.*\]', response, _re.DOTALL)
        if not match:
            return []
        prompts = _json.loads(match.group(0))

        valid = []
        for i, p in enumerate(prompts):
            if isinstance(p, dict) and "prompt" in p:
                valid.append({
                    "id": p.get("id", f"prompt_{i + 1}"),
                    "genre": p.get("genre", "general"),
                    "prompt": p["prompt"],
                })
        return valid

    except Exception as e:
        print(f"  Warning: AI prompt generation failed: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Skill file parsing
# ---------------------------------------------------------------------------

def parse_skill_file(path: Path) -> tuple[str, str, str]:
    """Read a skill file and extract name, description, and content.

    Supports files with or without YAML frontmatter. If frontmatter is
    present, name and description are extracted from it. Otherwise,
    the filename is used as the name.

    Returns:
        (name, description, full_content)
    """
    content = path.read_text(encoding="utf-8")

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1])
                if isinstance(meta, dict):
                    name = meta.get("name", path.stem)
                    description = meta.get("description", "")
                    return name, description, content
            except yaml.YAMLError:
                pass

    name = path.stem.lower().replace("_", "-").replace(" ", "-")
    return name, "", content


# ---------------------------------------------------------------------------
# Interactive steps
# ---------------------------------------------------------------------------

def step_provider() -> tuple[str, str, str, str]:
    """Step 1: Provider, model, and API key."""
    print("\n" + "=" * 50)
    print("STEP 1: LLM Provider")
    print("=" * 50)
    print("Which LLM provider do you want to use?")
    print("  1. Gemini (Google)")
    print("  2. OpenAI")
    print("  3. Anthropic (Claude)")

    choice = ask("Enter 1, 2, or 3", "1")

    provider_map = {
        "1": ("gemini", "gemini-2.5-flash", "GEMINI_API_KEY"),
        "2": ("openai", "gpt-4o", "OPENAI_API_KEY"),
        "3": ("anthropic", "claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
    }

    provider, default_model, default_env = provider_map.get(choice, provider_map["1"])
    model = ask("Model name", default_model)
    api_key_env = default_env
    api_key = ask(f"Your API key (will be saved to .env as {api_key_env})")

    return provider, model, api_key_env, api_key


def step_skill() -> tuple[str, str, str]:
    """Step 2: Skill description and initial content (interactive)."""
    print("\n" + "=" * 50)
    print("STEP 2: Your Skill")
    print("=" * 50)
    print("What skill are you optimising? This is the set of instructions")
    print("that tells the LLM how to behave for your use case.")
    print()
    print("Examples:")
    print("  - Writing style rules for blog posts")
    print("  - Sales email tone and structure")
    print("  - Code review feedback guidelines")
    print("  - Customer support response style")
    print()

    skill_name = ask("Give your skill a short name (e.g. 'sales-email-style')")
    skill_description = ask("One-line description of what this skill does")

    print()
    print("Now paste your skill instructions. These are the rules/guidelines")
    print("that the LLM should follow. If you don't have any yet, just describe")
    print("what you want and we'll create a starting point.")
    print()

    skill_content = ask_multiline("Skill instructions")

    skill_md = f"---\nname: {skill_name}\ndescription: {skill_description}\n---\n\n# {skill_name.replace('-', ' ').title()} Rules\n\n{skill_content}\n"
    return skill_name, skill_description, skill_md


def step_prompts(
    provider: str = "",
    model: str = "",
    api_key_env: str = "",
    skill_name: str = "",
    skill_description: str = "",
    skill_content: str = "",
) -> list[dict]:
    """Step 3: Test prompts (interactive, with optional AI generation)."""
    print("\n" + "=" * 50)
    print("STEP 3: Test Prompts")
    print("=" * 50)
    print("Define test scenarios that the skill will be evaluated against.")
    print("Each prompt should be a realistic task that exercises the skill.")
    print("Aim for 5-10 prompts covering different aspects of your use case.")
    print()

    prompts = []
    i = 1

    # Offer AI generation when we have provider info
    if provider and model and api_key_env and skill_name:
        use_ai = ask("Generate starter prompts with AI? (y/n)", "y")
        if use_ai.lower() == "y":
            print("  Generating prompts...")
            generated = ai_generate_prompts(
                provider, model, api_key_env,
                skill_name, skill_description, skill_content,
            )
            if generated:
                print(f"  ✓ Generated {len(generated)} prompts:\n")
                for p in generated:
                    print(f"    [{p['id']}] {p['prompt'][:80]}{'...' if len(p['prompt']) > 80 else ''}")
                print()
                accept = ask("Use these? (y / n / edit to add more)", "y")
                if accept.lower() == "y":
                    return generated
                elif accept.lower() == "edit":
                    prompts = generated
                    i = len(prompts) + 1
                    print("\nAdd more prompts. Leave ID blank to finish.")
                    while True:
                        print(f"\n--- Prompt {i} ---")
                        prompt_id = input("Short ID (blank to finish): ").strip()
                        if not prompt_id:
                            break
                        genre = ask("Category/genre", "general")
                        prompt_text = ask("The prompt itself")
                        prompts.append({"id": prompt_id, "genre": genre, "prompt": prompt_text})
                        i += 1
                    return prompts
                # n — fall through to manual entry
                print()
            else:
                print("  Falling back to manual entry.\n")

    # Manual entry
    while True:
        print(f"\n--- Prompt {i} ---")
        prompt_id = ask(f"Short ID (e.g. 'formal_email', 'quick_reply')")
        genre = ask(f"Category/genre (e.g. 'email', 'blog post', 'code review')")
        prompt_text = ask(f"The prompt itself")

        prompts.append({
            "id": prompt_id,
            "genre": genre,
            "prompt": prompt_text,
        })

        if i >= 3:
            more = ask("Add another prompt? (y/n)", "y")
            if more.lower() != "y":
                break
        i += 1

    return prompts


def step_eval_rubric() -> list[dict]:
    """Step 4: Evaluation dimensions."""
    print("\n" + "=" * 50)
    print("STEP 4: Evaluation Rubric")
    print("=" * 50)
    print("Define 2-5 quality dimensions the LLM judge will score (1-5 each).")
    print("Each dimension needs a name, weight (how important it is), and rubric.")
    print()
    print("Default dimensions (press Enter to accept, or define your own):")
    print("  1. human_score - Does it sound human-written?")
    print("  2. task_accuracy - Does it follow the skill instructions?")
    print("  3. quality - Is the output high quality?")
    print()

    use_defaults = ask("Use these default dimensions? (y/n)", "y")

    if use_defaults.lower() == "y":
        return _default_dimensions()

    dims = []
    total_weight = 0.0
    i = 1
    while True:
        print(f"\n--- Dimension {i} ---")
        name = ask("Dimension name (snake_case, e.g. 'clarity')")
        rubric = ask("Rubric (what does 1 mean? what does 5 mean?)")
        remaining = round(1.0 - total_weight, 2)
        weight = ask_float(f"Weight (remaining: {remaining})", round(remaining / max(1, 4 - i + 1), 2))
        total_weight += weight

        dims.append({"name": name, "weight": weight, "rubric": rubric})

        if i >= 2:
            if total_weight >= 0.99:
                print(f"  Weights sum to {total_weight:.2f} - done.")
                break
            more = ask("Add another dimension? (y/n)", "y")
            if more.lower() != "y":
                break
        i += 1

    return dims


def step_duration() -> tuple[int, float]:
    """Step 5: Run duration."""
    print("\n" + "=" * 50)
    print("STEP 5: Run Duration")
    print("=" * 50)
    print("How long should the optimisation loop run?")
    print("  - Set max iterations (e.g. 20 experiments)")
    print("  - Set max hours (e.g. 2.5 hours)")
    print("  - Set both (whichever limit is hit first)")
    print("  - Set both to 0 for unlimited (until manually stopped)")
    print()

    max_iterations = ask_int("Max iterations (0 = unlimited)", 0)
    max_hours = ask_float("Max hours (0 = unlimited)", 0)

    return max_iterations, max_hours


def step_advanced() -> dict:
    """Step 6: Advanced options (all optional)."""
    print("\n" + "=" * 50)
    print("STEP 6: Advanced Options (press Enter to skip each)")
    print("=" * 50)
    print("These are optional - defaults work well for most users.")
    print()

    judge_sees_skill = ask_choice("Semi-blind judge (judge sees SKILL.md for task_accuracy)?", ["y", "n"], "n")
    convergence_window = ask_int("Convergence window - stop after N iterations with no improvement (0 = disabled)", 0)
    max_cost_usd = ask_float("Max cost in USD - stop when estimated spend exceeds this (0 = unlimited)", 0)
    max_concurrent = ask_int("Parallel workers for generation and evaluation (1 = serial)", 1)

    return {
        "judge_sees_skill": judge_sees_skill.lower() == "y",
        "convergence_window": convergence_window,
        "max_cost_usd": max_cost_usd,
        "max_concurrent": max(1, max_concurrent),
    }


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_dimensions() -> list[dict]:
    return [
        {
            "name": "human_score",
            "weight": 0.30,
            "rubric": (
                "Does this read like a competent human wrote it? "
                "1 = obviously AI-generated, 5 = indistinguishable from human."
            ),
        },
        {
            "name": "task_accuracy",
            "weight": 0.40,
            "rubric": (
                "Does the output correctly follow the skill instructions? "
                "1 = ignores them, 5 = perfect adherence."
            ),
        },
        {
            "name": "quality",
            "weight": 0.30,
            "rubric": (
                "Is this high-quality output overall? "
                "1 = poor, 5 = excellent."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_files(
    provider, model, api_key_env, api_key,
    skill_content, skill_path_config,
    prompts, dimensions, max_iterations, max_hours,
    advanced=None,
):
    """Write all generated files."""
    # .env
    env_path = PROJECT_ROOT / ".env"
    env_path.write_text(f"# AutoEvaluation API key\n{api_key_env}={api_key}\n", encoding="utf-8")
    print(f"  ✓ .env")

    # config.yaml
    config = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "skill_path": skill_path_config,
        "prompts_path": "prompts/prompts.json",
        "results_tsv": "results.tsv",
        "max_iterations": max_iterations,
        "max_hours": max_hours,
        "llm_judge_dimensions": [
            {"name": d["name"], "weight": d["weight"], "direction": "higher_is_better", "rubric": d["rubric"]}
            for d in dimensions
        ],
        "deterministic_metrics": [],
    }
    if advanced:
        config["judge_sees_skill"] = advanced.get("judge_sees_skill", False)
        config["convergence_window"] = advanced.get("convergence_window", 0)
        config["max_cost_usd"] = advanced.get("max_cost_usd", 0)
        config["max_concurrent"] = advanced.get("max_concurrent", 1)
    cfg_path = PROJECT_ROOT / "config.yaml"
    cfg_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")
    print(f"  ✓ config.yaml")

    # SKILL.md (only write if skill_content is provided — skip if using external file)
    if skill_content is not None:
        skill_path = PROJECT_ROOT / skill_path_config
        skill_path.write_text(skill_content, encoding="utf-8")
        print(f"  ✓ {skill_path_config}")

    # prompts/prompts.json (only write if prompts are provided)
    if prompts is not None:
        prompts_dir = PROJECT_ROOT / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompts_path = prompts_dir / "prompts.json"
        prompts_path.write_text(json.dumps(prompts, indent=2), encoding="utf-8")
        print(f"  ✓ prompts/prompts.json")

    # .claude/settings.json (auto-approve for Claude Code autopilot)
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
                "Bash(cp SKILL.md SKILL.md.best)",
                "Bash(cp SKILL.md.best SKILL.md)",
                "Bash(sed -i '' 's/*$/\\tKEEP/' results.tsv)",
                "Bash(sed -i '' 's/*$/\\tDISCARD/' results.tsv)",
                "Bash(cat *)",
                "Bash(head *)",
                "Bash(tail *)",
                "Bash(wc *)",
            ]
        }
    }
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  ✓ .claude/settings.json (auto-approve for Claude Code)")

    # .gitignore
    gitignore_path = PROJECT_ROOT / ".gitignore"
    gitignore_content = ".env\n.tmp/\n__pycache__/\n*.pyc\n.claude/\nresults.tsv\nSKILL.md.best\nconfig.yaml\n"
    gitignore_path.write_text(gitignore_content, encoding="utf-8")
    print(f"  ✓ .gitignore")


# ---------------------------------------------------------------------------
# Main: two modes — quick (with flags) or interactive (wizard)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Set up AutoEvaluation for your skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 setup.py                                    # Full interactive wizard
  python3 setup.py --defaults                         # Gemini + 10 iters, skip advanced config
  python3 setup.py --skill-file /path/to/SKILL.md     # Point at an existing skill
  python3 setup.py --skill-file SKILL.md --prompts-file prompts.json
        """,
    )
    parser.add_argument(
        "--skill-file",
        type=Path,
        help="Path to an existing skill file. Skips the skill entry step.",
    )
    parser.add_argument(
        "--prompts-file",
        type=Path,
        help="Path to an existing prompts JSON file. Skips the prompt entry step.",
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Skip steps 4-6 and use defaults: Gemini/gemini-2.5-flash, 3 rubric dimensions, 10 iterations.",
    )
    args = parser.parse_args()

    # --- Validate file flags ---
    if args.skill_file and not args.skill_file.exists():
        print(f"Error: Skill file not found: {args.skill_file}", file=sys.stderr)
        sys.exit(1)

    if args.prompts_file and not args.prompts_file.exists():
        print(f"Error: Prompts file not found: {args.prompts_file}", file=sys.stderr)
        sys.exit(1)

    if args.prompts_file:
        try:
            prompts_data = json.loads(args.prompts_file.read_text(encoding="utf-8"))
            if not isinstance(prompts_data, list) or not prompts_data:
                raise ValueError("Must be a non-empty JSON array")
            for i, p in enumerate(prompts_data):
                if not isinstance(p, dict) or "prompt" not in p:
                    raise ValueError(f"Entry {i} must be an object with at least a 'prompt' key")
                if "id" not in p:
                    p["id"] = f"prompt_{i}"
                if "genre" not in p:
                    p["genre"] = "general"
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error: Invalid prompts file: {e}", file=sys.stderr)
            sys.exit(1)

    # --- Header ---
    print("=" * 50)
    print("  AutoEvaluation Setup")
    print("=" * 50)

    if args.skill_file:
        skill_name, skill_desc, _ = parse_skill_file(args.skill_file)
        print(f"\n  Skill file: {args.skill_file}")
        print(f"  Skill name: {skill_name}")
        if skill_desc:
            print(f"  Description: {skill_desc[:80]}{'...' if len(skill_desc) > 80 else ''}")

    if args.prompts_file:
        print(f"  Prompts file: {args.prompts_file} ({len(prompts_data)} prompts)")

    if args.defaults:
        print("\n  Mode: --defaults  (Gemini · 10 iterations · default rubric)")

    print()

    # --- Step 1: Provider ---
    if args.defaults:
        provider = "gemini"
        model = "gemini-2.5-flash"
        api_key_env = "GEMINI_API_KEY"

        # Check env / .env before prompting
        env_vars = _load_env_file(PROJECT_ROOT / ".env")
        api_key = os.environ.get(api_key_env) or env_vars.get(api_key_env, "")
        if not api_key:
            import getpass
            api_key = getpass.getpass(f"  {api_key_env} not found. Enter your API key: ").strip()
            if not api_key:
                print("  Error: API key required.", file=sys.stderr)
                sys.exit(1)

        print(f"  Validating API key...")
        ok, err = validate_api_key(provider, model, api_key_env, api_key)
        if not ok:
            print(f"  Error: API key validation failed: {err}", file=sys.stderr)
            sys.exit(1)
        print(f"  ✓ API key valid\n")
    else:
        provider, model, api_key_env, api_key = step_provider()

        print(f"\n  Validating API key...")
        ok, err = validate_api_key(provider, model, api_key_env, api_key)
        if not ok:
            print(f"  Error: API key validation failed: {err}", file=sys.stderr)
            sys.exit(1)
        print(f"  ✓ API key valid")

    # --- Step 2: Skill ---
    skill_name_for_prompts = ""
    skill_desc_for_prompts = ""
    skill_content_for_prompts = ""

    if args.skill_file:
        dest = PROJECT_ROOT / "SKILL.md"
        if args.skill_file.resolve() != dest.resolve():
            shutil.copy2(args.skill_file, dest)
            print(f"\n  Copied {args.skill_file} -> SKILL.md")
        skill_content = None  # Don't overwrite in write_files
        skill_path_config = "SKILL.md"
        skill_name_for_prompts, skill_desc_for_prompts, skill_content_for_prompts = parse_skill_file(args.skill_file)
    else:
        skill_name_for_prompts, skill_desc_for_prompts, skill_content = step_skill()
        skill_content_for_prompts = skill_content
        skill_path_config = "SKILL.md"

    # --- Step 3: Prompts ---
    if args.prompts_file:
        prompts_dir = PROJECT_ROOT / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        dest = prompts_dir / "prompts.json"
        if args.prompts_file.resolve() != dest.resolve():
            shutil.copy2(args.prompts_file, dest)
            print(f"  Copied {args.prompts_file} -> prompts/prompts.json")
        prompts = None  # Don't overwrite in write_files
    else:
        prompts = step_prompts(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            skill_name=skill_name_for_prompts,
            skill_description=skill_desc_for_prompts,
            skill_content=skill_content_for_prompts,
        )

    # --- Steps 4-6: Rubric / Duration / Advanced ---
    if args.defaults:
        dimensions = _default_dimensions()
        max_iterations = 10
        max_hours = 0.0
        advanced = {
            "judge_sees_skill": False,
            "convergence_window": 0,
            "max_cost_usd": 0.0,
            "max_concurrent": 1,
        }
    else:
        dimensions = step_eval_rubric()
        max_iterations, max_hours = step_duration()
        advanced = step_advanced()

    # --- Write files ---
    print("\n" + "=" * 50)
    print("WRITING FILES")
    print("=" * 50)

    write_files(
        provider, model, api_key_env, api_key,
        skill_content, skill_path_config,
        prompts, dimensions, max_iterations, max_hours,
        advanced=advanced,
    )

    print("\n" + "=" * 50)
    print("  SETUP COMPLETE!")
    print("=" * 50)
    print()
    print("Next steps:")
    print()
    print("  Start the optimisation loop:")
    print()
    print("     claude -p program.md")
    print()
    print("  Or run headless (without Claude Code):")
    print()
    print("     python3 tools/run_loop.py")
    print()
    print("  To watch scores in real time:")
    print("     python3 tools/dashboard_server.py")
    print()


if __name__ == "__main__":
    main()
