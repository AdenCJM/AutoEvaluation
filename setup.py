#!/usr/bin/env python3
"""
AutoEvaluation Setup Wizard
============================
Interactive CLI that walks you through first-run configuration.
Generates: config.yaml, SKILL.md, prompts/prompts.json, .env, .claude/settings.json

Usage:
    python3 setup.py                                    # Full interactive wizard
    python3 setup.py --defaults                         # Skip all prompts (Gemini, default rubric, 10 iterations)
    python3 setup.py --skill-file /path/to/SKILL.md     # Point at an existing skill
    python3 setup.py --skill-file SKILL.md --prompts-file prompts.json
"""

from __future__ import annotations

import argparse
import difflib
import getpass
import json
import os
import re
import shutil
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from utils import DEFAULT_MODELS, load_env, default_dimensions, split_prompt_sets, validate_prompts
from generate_config import write_all as _write_all
from run_state import atomic_write_text

# Cheap judge model per provider — offered as the default separate judge
_JUDGE_SUGGESTIONS = {
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-haiku-4-5",
}


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
# Sanitisation helpers
# ---------------------------------------------------------------------------

def _sanitise_prompt_id(raw_id: str, fallback: str = "prompt") -> str:
    """Sanitise a prompt ID to safe filename characters (alphanumeric, underscore, hyphen)."""
    sanitised = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_id).strip('_')
    return sanitised if sanitised else fallback


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

    # Try to extract YAML frontmatter
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

    # No valid frontmatter — use filename as name
    name = path.stem.lower().replace("_", "-").replace(" ", "-")
    return name, "", content


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------

def validate_api_key(provider: str, model: str, api_key_env: str, api_key: str) -> bool:
    """Validate an API key by making a tiny LLM call.

    Sets the env var temporarily, tries a minimal generation,
    and returns True if it succeeds.
    """
    print("\n  Validating API key...", end="", flush=True)

    # Set the env var so ModelClient can find it
    os.environ[api_key_env] = api_key

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
        err_name = type(e).__name__
        print(f" ✗ {err_name}: {e}")
        return False


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
        "1": ("gemini",) + DEFAULT_MODELS["gemini"],
        "2": ("openai",) + DEFAULT_MODELS["openai"],
        "3": ("anthropic",) + DEFAULT_MODELS["anthropic"],
    }

    provider, default_model, default_env = provider_map.get(choice, provider_map["1"])
    model = ask("Model name", default_model)
    api_key_env = default_env

    # Loop until we get a valid API key
    while True:
        api_key = getpass.getpass(
            f"Your API key (saved to .env as {api_key_env}; input hidden): "
        ).strip()
        if not api_key:
            print("  API key is required.")
            continue
        if validate_api_key(provider, model, api_key_env, api_key):
            break
        print("  API key validation failed. Please check and try again.")
        retry = ask("Try again? (y/n)", "y")
        if retry.lower() != "y":
            print("  Continuing without validation (key may fail during the run).")
            break

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


def step_prompts() -> list[dict]:
    """Step 3: Test prompts (interactive)."""
    print("\n" + "=" * 50)
    print("STEP 3: Test Prompts")
    print("=" * 50)
    print("Define test scenarios that the skill will be evaluated against.")
    print("Each prompt should be a realistic task that exercises the skill.")
    print("Aim for about 30 prompts covering realistic cases, constraints, and edge conditions.")
    print()

    prompts = []
    i = 1
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

    max_iterations = ask_int("Max iterations (0 = unlimited)", 10)
    max_hours = ask_float("Max hours (0 = unlimited)", 0)

    return max_iterations, max_hours


def step_judge(provider: str) -> dict:
    """Step 6: Judge model. A separate (cheaper) judge model avoids
    self-judging bias — the generator grading its own output style."""
    print("\n" + "=" * 50)
    print("STEP 6: Judge Model")
    print("=" * 50)
    print("By default the same model generates outputs AND judges them, which")
    print("lets the model reward its own style. A separate judge model gives")
    print("cleaner signal, and a cheaper one keeps costs down.")
    print()

    suggestion = _JUDGE_SUGGESTIONS.get(provider, "")
    use_separate = ask_choice(f"Use a separate judge model? (suggested: {suggestion})", ["y", "n"], "y")
    if use_separate.lower() != "y":
        return {}

    judge_model = ask("Judge model name", suggestion)
    # Same provider by default so the existing API key covers the judge;
    # a different provider (cross-family judging) is configurable in config.yaml
    return {
        "judge_provider": provider,
        "judge_model": judge_model,
        "judge_api_key_env": DEFAULT_MODELS[provider][1],
    }


def step_advanced() -> dict:
    """Step 7: Advanced options (all optional)."""
    print("\n" + "=" * 50)
    print("STEP 7: Advanced Options (press Enter to skip each)")
    print("=" * 50)
    print("These are optional - defaults work well for most users.")
    print()

    judge_sees_skill = ask_choice("Semi-blind judge (judge sees SKILL.md for task_accuracy)?", ["y", "n"], "y")
    replicates = ask_int("Replicates per prompt - completions generated per test prompt (higher = less noise, more cost)", 3)
    convergence_window = ask_int("Convergence window - stop after N iterations with no improvement (0 = disabled)", 0)
    max_cost_usd = ask_float("Max cost in USD - stop when estimated spend exceeds this (0 = unlimited)", 10)
    max_concurrent = ask_int("Parallel workers for generation and evaluation (1 = serial)", 4)

    return {
        "judge_sees_skill": judge_sees_skill.lower() == "y",
        "replicates_per_prompt": max(1, replicates),
        "convergence_window": convergence_window,
        "max_cost_usd": max_cost_usd,
        "max_concurrent": max(1, max_concurrent),
    }


# ---------------------------------------------------------------------------
# AI Prompt Generator
# ---------------------------------------------------------------------------

def generate_test_prompts_with_ai(
    provider: str, model: str, api_key_env: str,
    skill_name: str, skill_description: str, skill_content: str,
) -> list[dict]:
    """Use the configured LLM to generate test prompts from a skill description.

    Asks the AI to generate 30 diverse test prompts that exercise
    different aspects of the skill.
    """
    print("\n" + "=" * 50)
    print("GENERATING TEST PROMPTS WITH AI")
    print("=" * 50)
    print(f"  Skill: {skill_name}")
    print(f"  Description: {skill_description}")
    print()
    print("  Generating diverse test scenarios...", end="", flush=True)

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
{"id": "short_snake_case_id", "genre": "category", "prompt": "the actual test prompt"}

Example format:
[
  {"id": "formal_email", "genre": "email", "prompt": "Write a formal email to a client about a project delay."},
  {"id": "casual_slack", "genre": "messaging", "prompt": "Write a casual Slack message updating your team on progress."}
]"""

    user_prompt = f"""Here is the skill to generate test prompts for:

Skill name: {skill_name}
Description: {skill_description}

Skill instructions:
{skill_content}

Generate exactly 30 diverse test prompts that would thoroughly evaluate this skill. Return ONLY the JSON array."""

    try:
        response = client.generate(system_prompt, user_prompt, max_tokens=2048)
        print(" ✓")

        # Extract JSON from response (handle markdown code blocks)
        json_text = response.strip()
        if json_text.startswith("```"):
            # Strip markdown code block
            lines = json_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block or not line.strip().startswith("```"):
                    json_lines.append(line)
            json_text = "\n".join(json_lines)

        # Try to find a JSON array in the response
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
            # Sanitise prompt ID to safe filename characters
            p["id"] = _sanitise_prompt_id(p["id"], fallback=f"prompt_{i + 1}")
            valid_prompts.append(p)

        if not valid_prompts:
            raise ValueError("No valid prompts in response")

        print(f"\n  Generated {len(valid_prompts)} test prompts:")
        for p in valid_prompts:
            print(f"    · [{p['id']}] ({p['genre']}) {p['prompt'][:70]}{'...' if len(p['prompt']) > 70 else ''}")

        return valid_prompts

    except Exception as e:
        print(f" ✗ {type(e).__name__}: {e}")
        print("  Falling back to manual prompt entry.")
        return None


def prompt_coverage(prompts: list[dict]) -> dict:
    genres = sorted({str(p.get("genre", "general")).strip().lower() for p in prompts})
    lengths = [len(str(p.get("prompt", "")).split()) for p in prompts]
    duplicates = []
    for i, left in enumerate(prompts):
        for j in range(i + 1, len(prompts)):
            ratio = difflib.SequenceMatcher(
                None,
                left.get("prompt", "").lower(),
                prompts[j].get("prompt", "").lower(),
            ).ratio()
            if ratio >= 0.82:
                duplicates.append((left.get("id"), prompts[j].get("id"), round(ratio, 2)))
    train, validation, final = split_prompt_sets(prompts, 0.3, 0.2)
    return {
        "genres": genres,
        "duplicates": duplicates,
        "short": sum(1 for length in lengths if length <= 12),
        "medium": sum(1 for length in lengths if 12 < length <= 30),
        "long": sum(1 for length in lengths if length > 30),
        "split": (len(train), len(validation), len(final)),
    }


def _print_prompt_coverage(prompts: list[dict]) -> None:
    coverage = prompt_coverage(prompts)
    train, validation, final = coverage["split"]
    print("\nEvaluation-plan coverage")
    print("-" * 50)
    print(f"  Prompts: {len(prompts)} across {len(coverage['genres'])} genres")
    print(f"  Length mix: {coverage['short']} short / {coverage['medium']} medium / {coverage['long']} long")
    print(f"  Planned split: {train} train / {validation} validation / {final} final test")
    if coverage["duplicates"]:
        pairs = ", ".join(f"{a}/{b}" for a, b, _ in coverage["duplicates"][:4])
        print(f"  WARNING: possible near-duplicates: {pairs}")
    if train < 8:
        print("  WARNING: fewer than 8 training prompts; statistical decisions will degrade.")
    if len(coverage["genres"]) < 5:
        print("  WARNING: narrow genre coverage; add realistic edge cases before launch.")


def regenerate_one_prompt(
    provider: str,
    model: str,
    api_key_env: str,
    skill_content: str,
    current: dict,
) -> dict | None:
    from model_client import ModelClient
    client = ModelClient(provider=provider, model=model, api_key_env=api_key_env)
    system = (
        "You design realistic test cases for LLM instructions. Return exactly one "
        "JSON object with id, genre, and prompt. Make it materially different from "
        "the supplied case while testing the same broad capability."
    )
    response = client.generate(
        system,
        f"Skill:\n{skill_content}\n\nReplace this test case:\n{json.dumps(current)}",
        max_tokens=500,
    )
    try:
        start, end = response.find("{"), response.rfind("}")
        item = json.loads(response[start:end + 1])
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "genre", "prompt")):
            raise ValueError("replacement is missing fields")
        item["id"] = _sanitise_prompt_id(item["id"], current.get("id", "prompt"))
        return item
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  Could not parse replacement: {exc}")
        return None


def review_prompt_plan(
    prompts: list[dict],
    provider: str,
    model: str,
    api_key_env: str,
    skill_content: str,
) -> list[dict]:
    """Small terminal workbench for inspecting and editing the eval suite."""
    while True:
        _print_prompt_coverage(prompts)
        print("\n  [a] accept  [l] list  [e] edit  [d] delete  [r] regenerate one  [n] new")
        action = ask("Choose an action", "a").lower()
        if action == "a":
            try:
                validate_prompts(prompts)
            except ValueError as exc:
                print(f"  Cannot accept this plan yet: {exc}")
                continue
            return prompts
        if action == "l":
            for index, prompt in enumerate(prompts, 1):
                print(f"  {index:>2}. [{prompt['id']}] {prompt['genre']}: {prompt['prompt']}")
            continue
        if action in {"e", "d", "r"}:
            index = ask_int("Prompt number", 1) - 1
            if not 0 <= index < len(prompts):
                print("  Invalid prompt number.")
                continue
            if action == "d":
                removed = prompts.pop(index)
                print(f"  Removed {removed['id']}.")
            elif action == "e":
                current = prompts[index]
                current["id"] = _sanitise_prompt_id(ask("ID", current["id"]), current["id"])
                current["genre"] = ask("Genre", current["genre"])
                current["prompt"] = ask("Prompt", current["prompt"])
            else:
                replacement = regenerate_one_prompt(
                    provider, model, api_key_env, skill_content, prompts[index]
                )
                if replacement:
                    prompts[index] = replacement
                    print(f"  Replaced prompt {index + 1}.")
            continue
        if action == "n":
            prompts.append({
                "id": _sanitise_prompt_id(ask("ID"), f"prompt_{len(prompts) + 1}"),
                "genre": ask("Genre"),
                "prompt": ask("Prompt"),
            })
            continue
        print("  Unknown action.")


def review_dimensions(dimensions: list[dict]) -> list[dict]:
    vague = ("high quality", "is it good", "excellent", "appropriate")
    print("\nRubric review")
    print("-" * 50)
    for index, dim in enumerate(dimensions, 1):
        rubric = str(dim.get("rubric", ""))
        warning = ""
        if len(rubric) < 60 or any(term in rubric.lower() for term in vague):
            warning = "  ⚠ make criteria more observable"
        print(f"  {index}. {dim['name']} ({float(dim['weight']):.0%}){warning}")
    while ask("Edit a rubric dimension? (y/n)", "n").lower() == "y":
        index = ask_int("Dimension number", 1) - 1
        if not 0 <= index < len(dimensions):
            print("  Invalid dimension number.")
            continue
        dimensions[index]["rubric"] = ask("Observable 1-to-5 criteria", dimensions[index]["rubric"])
        weight = ask_float("Weight", float(dimensions[index]["weight"]))
        if weight <= 0:
            print("  Weight must be greater than zero.")
            continue
        dimensions[index]["weight"] = weight
    total = sum(float(dim["weight"]) for dim in dimensions)
    if total <= 0:
        raise ValueError("rubric weights must sum to more than zero")
    if abs(total - 1.0) > 0.01:
        print(f"  Normalising rubric weights from {total:.2f} to 1.00.")
        for dim in dimensions:
            dim["weight"] = float(dim["weight"]) / total
    return dimensions


def estimate_campaign_plan(
    prompts: list[dict], dimensions: list[dict], provider: str, model: str,
    judge_model: str, iterations: int, replicates: int, skill_content: str,
) -> dict:
    """Return a transparent range, not a false-precision billing promise."""
    from model_client import ModelClient
    train, validation, final = split_prompt_sets(prompts, 0.3, 0.2)
    iterations = iterations or 10
    eval_calls = lambda count: count * replicates * 2
    baseline_calls = eval_calls(len(prompts))
    per_iteration = eval_calls(len(train)) + 1
    validation_calls = eval_calls(len(validation))
    final_calls = eval_calls(len(final))
    min_calls = baseline_calls + iterations * per_iteration + final_calls
    max_calls = min_calls + iterations * validation_calls

    gen_price = ModelClient.price_for_model(model)
    judge_price = ModelClient.price_for_model(judge_model or model)
    # Typical instruction-evaluation workload assumptions. The range is
    # intentionally broad and is labelled as an estimate in the UI.
    skill_tokens = max(200, len(skill_content) // 4)
    rubric_tokens = max(250, sum(len(str(d.get("rubric", ""))) for d in dimensions) // 4)
    gen_cost = (skill_tokens + 150) * gen_price[0] + 700 * gen_price[1] if gen_price else 0
    judge_cost = (rubric_tokens + 850) * judge_price[0] + 250 * judge_price[1] if judge_price else 0
    pair_cost = gen_cost + judge_cost
    modifier_cost = 6000 * gen_price[0] + 2500 * gen_price[1] if gen_price else 0
    base_pairs = len(prompts) * replicates
    min_pairs = base_pairs + iterations * len(train) * replicates + len(final) * replicates
    max_pairs = min_pairs + iterations * len(validation) * replicates
    min_cost = min_pairs * pair_cost + iterations * modifier_cost
    max_cost = max_pairs * pair_cost + iterations * modifier_cost
    return {
        "split": (len(train), len(validation), len(final)),
        "calls": (min_calls, max_calls),
        "cost": (min_cost, max_cost) if gen_price and judge_price else None,
        "iterations": iterations,
        "replicates": replicates,
    }


def print_campaign_plan(plan: dict, provider: str, model: str, judge_model: str, cap: float) -> None:
    train, validation, final = plan["split"]
    low_calls, high_calls = plan["calls"]
    print("\n" + "=" * 50)
    print("CAMPAIGN PLAN")
    print("=" * 50)
    print(f"  Prompts:       {train} train / {validation} validation / {final} final test")
    print(f"  Replicates:    {plan['replicates']} per prompt")
    print(f"  Attempts:      {plan['iterations']}")
    print(f"  Models:        {provider}/{model} · judge {judge_model or model}")
    print(f"  Estimated calls: {low_calls:,}–{high_calls:,}")
    if plan["cost"]:
        low, high = plan["cost"]
        print(f"  Estimated cost:  ${low:.2f}–${high:.2f} (standard list pricing)")
        if cap and low > cap:
            print(f"  WARNING: the ${cap:.2f} cap is below the low estimate; the campaign may stop early.")
    else:
        print("  Estimated cost:  unavailable for an unpriced model")
    print(f"  Hard cost cap:   {'unlimited' if not cap else f'${cap:.2f}'}")
    print("  Final test:      locked until `python3 autoeval.py finalize`")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_dimensions() -> list[dict]:
    return default_dimensions()


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


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_files(
    provider, model, api_key_env, api_key,
    skill_content, skill_path_config,
    prompts, dimensions, max_iterations, max_hours,
    advanced=None, judge=None,
):
    """Write all generated files.

    Thin wrapper around the shared tools/generate_config.write_all — keeps
    this function's signature stable for setup.py's callers while delegating
    the actual file-writing logic to a single implementation shared with the
    /autoeval skill's CLI.
    """
    judge = judge or {}

    # setup.py's callers pass fully-formed SKILL.md text (frontmatter already
    # applied by step_skill()), whereas write_all's skill_content branch
    # applies its own frontmatter wrapping for the /autoeval CLI's raw
    # instruction text. Write pre-formatted content verbatim here and let
    # write_all skip its own SKILL.md step, so both callers still share every
    # other file writer (.env, config.yaml, prompts.json, settings.json,
    # .gitignore).
    if skill_content is not None:
        skill_path = PROJECT_ROOT / skill_path_config
        atomic_write_text(skill_path, skill_content)
        print(f"  ✓ {skill_path_config}")

    _write_all(
        skill_name="",
        skill_description="",
        skill_content=None,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        api_key=api_key,
        metrics=dimensions,
        prompts=prompts,
        iterations=max_iterations,
        max_hours=max_hours,
        judge_provider=judge.get("judge_provider", ""),
        judge_model=judge.get("judge_model", ""),
        skill_path_config=skill_path_config,
        advanced=advanced,
    )


# ---------------------------------------------------------------------------
# Main: three modes — defaults (no prompts), quick (with flags), or interactive
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog=os.environ.get("AUTOEVAL_PROG_NAME", "setup.py"),
        description="Set up AutoEvaluation for your skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 setup.py                                    # Full interactive wizard
  python3 setup.py --defaults                         # Skip all prompts (Gemini, defaults, 10 iterations)
  python3 setup.py --defaults --provider openai       # Defaults but use OpenAI
  python3 setup.py --skill-file /path/to/SKILL.md     # Point at an existing skill
  python3 setup.py --skill-file SKILL.md --prompts-file prompts.json
        """,
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Skip all interactive prompts. Uses Gemini, 3 default rubric dimensions, "
             "30 default test prompts, 10 iterations. Requires API key in .env or environment.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["gemini", "openai", "anthropic"],
        help="Provider to use with --defaults (default: gemini).",
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
        "--generate-prompts",
        action="store_true",
        help="Use AI to generate test prompts from the skill file (requires --skill-file).",
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
                # Backfill missing id/genre so downstream tools don't break
                if "id" not in p:
                    p["id"] = f"prompt_{i}"
                if "genre" not in p:
                    p["genre"] = "general"
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error: Invalid prompts file: {e}", file=sys.stderr)
            sys.exit(1)

    # =====================================================================
    # --defaults mode: skip all interactive prompts
    # =====================================================================
    if args.defaults:
        provider_choice = args.provider or "gemini"
        model, api_key_env = DEFAULT_MODELS[provider_choice]
        provider = provider_choice

        # Load API key from .env or environment
        load_env(PROJECT_ROOT / ".env")

        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            print(f"Error: --defaults requires {api_key_env} in .env or environment.", file=sys.stderr)
            print(f"  echo \"{api_key_env}=your-key\" > .env", file=sys.stderr)
            sys.exit(1)

        # Validate the API key
        if not validate_api_key(provider, model, api_key_env, api_key):
            print(f"Error: API key validation failed. Check your {api_key_env}.", file=sys.stderr)
            sys.exit(1)

        # Skill
        if args.skill_file:
            skill_content = args.skill_file.read_text(encoding="utf-8")
        else:
            skill_content = None  # Keep existing SKILL.md or placeholder

        # Prompts
        if args.prompts_file:
            prompts = prompts_data
        elif args.generate_prompts and args.skill_file:
            skill_name, skill_desc, skill_text = parse_skill_file(args.skill_file)
            prompts = generate_test_prompts_with_ai(
                provider, model, api_key_env, skill_name, skill_desc, skill_text
            )
            if prompts is None:
                prompts = _DEFAULT_PROMPTS
        else:
            prompts = _DEFAULT_PROMPTS

        print("\n" + "=" * 50)
        print("SETUP (--defaults)")
        print("=" * 50)
        print(f"  Provider:    {provider} ({model})")
        print(f"  Rubric:      3 default dimensions")
        print(f"  Prompts:     {len(prompts) if prompts else 'existing'}")
        print(f"  Iterations:  10")

        effective_prompts = prompts or json.loads(
            (PROJECT_ROOT / "prompts/prompts.json").read_text(encoding="utf-8")
        )
        safe_advanced = {
            "judge_sees_skill": True,
            "replicates_per_prompt": 3,
            "convergence_window": 0,
            "max_cost_usd": 10,
            "max_concurrent": 4,
        }
        plan = estimate_campaign_plan(
            effective_prompts, _default_dimensions(), provider, model, "",
            10, 3, skill_content or (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8"),
        )
        print_campaign_plan(plan, provider, model, "", 10)

        print("\n" + "=" * 50)
        print("WRITING FILES")
        print("=" * 50)

        write_files(
            provider, model, api_key_env, api_key,
            skill_content, "SKILL.md",
            prompts, _default_dimensions(), 10, 0,
            advanced=safe_advanced,
        )

        print("\n" + "=" * 50)
        print("  SETUP COMPLETE!")
        print("=" * 50)
        print()
        print("  Start: ./start.sh  or  python3 autoeval.py run")
        print()
        return

    # =====================================================================
    # Interactive mode
    # =====================================================================

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

    print()

    # --- Step 1: Provider (always interactive) ---
    provider, model, api_key_env, api_key = step_provider()

    # --- Step 2: Skill ---
    if args.skill_file:
        # Keep the source untouched until the user accepts the preflight plan.
        skill_path_config = "SKILL.md"
        skill_name, skill_desc, skill_text = parse_skill_file(args.skill_file)
        skill_content = skill_text
    else:
        skill_name, skill_desc, skill_content = step_skill()
        skill_path_config = "SKILL.md"
        skill_text = skill_content

    # --- Step 3: Prompts ---
    if args.prompts_file:
        prompts = prompts_data
    else:
        # Offer AI prompt generation
        print("\n" + "=" * 50)
        print("STEP 3: Test Prompts")
        print("=" * 50)
        print("AutoEvaluation needs test prompts — realistic tasks that exercise your skill.")
        print()
        print("Options:")
        print("  1. Generate prompts with AI (recommended — uses your skill to create test scenarios)")
        print("  2. Enter prompts manually")
        print()
        prompt_choice = ask("Choose 1 or 2", "1")

        if prompt_choice == "1":
            prompts = generate_test_prompts_with_ai(
                provider, model, api_key_env,
                skill_name, skill_desc or "No description provided",
                skill_text or skill_content or "",
            )
            if prompts is None:
                # Fallback to manual if AI generation didn't work or user declined
                prompts = step_prompts()
        else:
            prompts = step_prompts()

    prompts = review_prompt_plan(
        prompts, provider, model, api_key_env, skill_text or skill_content or ""
    )

    # --- Step 4: Eval rubric ---
    dimensions = review_dimensions(step_eval_rubric())

    # --- Step 5: Duration ---
    max_iterations, max_hours = step_duration()

    # --- Step 6: Judge model ---
    judge = step_judge(provider)

    # --- Step 7: Advanced ---
    advanced = step_advanced()

    plan = estimate_campaign_plan(
        prompts,
        dimensions,
        provider,
        model,
        judge.get("judge_model", ""),
        max_iterations,
        advanced["replicates_per_prompt"],
        skill_text or skill_content or "",
    )
    print_campaign_plan(
        plan, provider, model, judge.get("judge_model", ""), advanced["max_cost_usd"]
    )
    if ask("Create this configuration? (y/n)", "y").lower() != "y":
        print("Setup cancelled; no configuration files were written.")
        return

    # --- Write files ---
    print("\n" + "=" * 50)
    print("WRITING FILES")
    print("=" * 50)

    write_files(
        provider, model, api_key_env, api_key,
        skill_content, skill_path_config,
        prompts, dimensions, max_iterations, max_hours,
        advanced=advanced, judge=judge,
    )

    print("\n" + "=" * 50)
    print("  SETUP COMPLETE!")
    print("=" * 50)
    print()
    print("Next steps:")
    print()
    print("  Start the optimisation loop:")
    print()
    print("     ./start.sh")
    print()
    print("  Or run directly:")
    print()
    print("     /autoeval in Claude Code   # conversational")
    print("     python3 tools/run_loop.py  # headless")
    print()
    print("  Watch scores in real time:")
    print("     python3 tools/dashboard_server.py")
    print()


if __name__ == "__main__":
    main()
