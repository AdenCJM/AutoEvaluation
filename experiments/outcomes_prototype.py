"""
Outcomes Prototype — Managed Agents grading vs. AutoEval's own judge
======================================================================
Compares Anthropic Managed Agents "Outcomes" (rubric-graded, isolated-context
grader, iterate-until-satisfied) against AutoEval's existing hand-rolled
LLM-judge (tools/eval_llm_judge.py) on ONE test prompt.

This is a research prototype, not a production path. It answers a single
question: does letting Anthropic's managed grader (a fresh subagent, separate
context window, seeing only the rubric + artifact) evaluate output beat
AutoEval's current semi-blind judge, which is hand-orchestrated in-process?

NOT LIVE-TESTED. This machine has no ANTHROPIC_API_KEY, so the Managed
Agents / Outcomes code path below has never actually been run against the
API. It is written directly from:
  - https://platform.claude.com/docs/en/managed-agents/define-outcomes.md
  - https://platform.claude.com/docs/en/managed-agents/quickstart.md
(fetched July 2026) — the exact request/response shapes should be correct,
but treat first run as a live integration test, not a known-good path.

Estimated cost warning: creating an agent + environment is free-ish (control
plane calls), but each session that works toward an outcome consumes real
tokens across (a) the writer agent's turns, (b) the grader's evaluation per
iteration (up to max_iterations, default 3), and (c) the local judge's own
call afterwards on the same artifact. Budget for a handful of Sonnet-5-class
calls per run, more if the grader requests revisions.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 experiments/outcomes_prototype.py
    python3 experiments/outcomes_prototype.py --agent-id agent_... --environment-id env_...

Flags let you skip agent/environment creation on repeat runs — per Anthropic's
guidance, agents and environments are meant to be created once and reused,
not recreated per session. The session itself IS created fresh each run and
archived at the end; the agent and environment are left alone so you can
pass their IDs back in next time.
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from utils import load_config  # noqa: E402
from model_client import ModelClient  # noqa: E402
from eval_llm_judge import judge_sample, build_judge_schema  # noqa: E402 (schema unused here but kept for parity/reuse)


def build_rubric_markdown(dimensions: list[dict]) -> str:
    """Turn config.yaml's llm_judge_dimensions into a markdown rubric with one
    gradeable criterion per dimension, in the shape Outcomes expects.

    Outcomes' own guidance: make every criterion explicit and checkable
    ("GAAP net loss cited to sec.gov", not "good economics"). AutoEval's
    dimensions are already written as observable-feature rubrics (see
    config.template.yaml), so this is largely a reformatting job — each
    dimension becomes a heading with its rubric text as the checkable
    criterion, plus the 1-5 scale spelled out.
    """
    lines = ["# AutoEval Composite Rubric", ""]
    lines.append(
        "Grade the artifact against each dimension below. Each dimension is "
        "scored 1-5 against its stated criteria; report which score each "
        "dimension earns and why.\n"
    )
    for dim in dimensions:
        name = dim["name"]
        weight = dim.get("weight", 0)
        rubric_text = dim["rubric"].strip()
        lines.append(f"## {name} (weight: {weight})")
        lines.append(rubric_text)
        lines.append("")
    return "\n".join(lines)


def create_agent_and_environment(client, skill_content: str):
    """Create a fresh Managed Agents agent + environment.

    Per Anthropic's guidance these are meant to be created once and reused
    across many sessions (they're closer to "configuration" than to a
    single run) — hence the --agent-id/--environment-id flags in main() to
    skip this step on subsequent invocations.
    """
    print("Creating environment (cloud sandbox, unrestricted networking)...")
    environment = client.beta.environments.create(
        name="autoeval-outcomes-prototype",
        config={
            "type": "cloud",
            "networking": {"type": "unrestricted"},
        },
    )
    print(f"  environment.id = {environment.id}")

    print("Creating agent (system prompt = SKILL.md content, model claude-sonnet-5)...")
    agent = client.beta.agents.create(
        name="autoeval-outcomes-prototype-agent",
        model="claude-sonnet-5",
        system=skill_content,
        tools=[{"type": "agent_toolset_20260401"}],
    )
    print(f"  agent.id = {agent.id}")

    return agent, environment


def run_outcome_session(client, agent_id: str, environment_id: str, prompt_text: str, rubric_md: str):
    """Create a session, define the outcome, stream events, and return the
    accumulated artifact text plus the final outcome result/explanation.

    Per the docs: open the event stream FIRST (stream-first), then send the
    user.define_outcome event — the harness buffers events server-side until
    the stream attaches, so ordering here (stream, then send) is the
    documented pattern, not a race we need to guard against ourselves.
    """
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="AutoEval outcomes prototype — single prompt",
    )
    print(f"Session created: session.id = {session.id}")

    artifact_text_parts = []
    final_result = None
    final_explanation = None
    iteration_log = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.define_outcome",
                    "description": prompt_text,
                    "rubric": {"type": "text", "content": rubric_md},
                    "max_iterations": 3,
                }
            ],
        )

        for event in stream:
            etype = getattr(event, "type", None)

            if etype == "agent.message":
                # Accumulate agent-authored text as the artifact. Keeping this
                # simple deliberately: a production version would instead
                # fetch the artifact from /mnt/session/outputs/ via the Files
                # API scoped to the session (see docs), which is the correct
                # way to get a well-formed file rather than reconstructing it
                # from chat text. This prototype takes the simpler path.
                for block in getattr(event, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        artifact_text_parts.append(text)

            elif etype == "span.outcome_evaluation_start":
                iteration = getattr(event, "iteration", None)
                print(f"  [outcome_evaluation_start] iteration={iteration}")

            elif etype == "span.outcome_evaluation_end":
                iteration = getattr(event, "iteration", None)
                result = getattr(event, "result", None)
                explanation = getattr(event, "explanation", None)
                print(f"  [outcome_evaluation_end] iteration={iteration} result={result}")
                print(f"    explanation: {explanation}")
                iteration_log.append({"iteration": iteration, "result": result, "explanation": explanation})
                final_result = result
                final_explanation = explanation

            elif etype == "session.status_idle":
                stop_reason = getattr(event, "stop_reason", None)
                stop_reason_type = getattr(stop_reason, "type", None) if stop_reason else None
                print(f"  [session.status_idle] stop_reason.type={stop_reason_type}")
                if stop_reason_type != "requires_action":
                    break

            elif etype == "session.status_terminated":
                print("  [session.status_terminated] session ended")
                break

    artifact_text = "".join(artifact_text_parts)
    return session, artifact_text, final_result, final_explanation, iteration_log


def run_local_judge(artifact_text: str, dimensions: list[dict], skill_content: str) -> dict:
    """Score the same artifact with AutoEval's existing judge, for comparison."""
    cfg = load_config()
    judge_client = ModelClient.from_config(str(PROJECT_ROOT / "config.yaml"), judge=True)
    judge_sees_skill = cfg.get("judge_sees_skill", True)
    skill_for_judge = skill_content if judge_sees_skill else None
    return judge_sample(artifact_text, dimensions, judge_client, skill_content=skill_for_judge)


def main():
    parser = argparse.ArgumentParser(description="Outcomes vs. local judge prototype")
    parser.add_argument("--agent-id", help="Reuse an existing Managed Agents agent instead of creating one")
    parser.add_argument("--environment-id", help="Reuse an existing Managed Agents environment instead of creating one")
    args = parser.parse_args()

    cfg = load_config()
    dimensions = cfg.get("llm_judge_dimensions", [])
    if not dimensions:
        print("Error: no llm_judge_dimensions in config.yaml", file=sys.stderr)
        sys.exit(1)

    prompts_path = PROJECT_ROOT / cfg.get("prompts_path", "prompts/prompts.json")
    if not prompts_path.exists():
        print(f"Error: prompts file not found at {prompts_path}", file=sys.stderr)
        sys.exit(1)
    prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
    if not prompts:
        print("Error: prompts file is empty", file=sys.stderr)
        sys.exit(1)
    first_prompt = prompts[0]
    prompt_text = first_prompt["prompt"]
    print(f"Using prompt: {first_prompt.get('id', '<unnamed>')} — {prompt_text[:80]}...")

    skill_path = PROJECT_ROOT / cfg.get("skill_path", "SKILL.md")
    skill_content = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    if not skill_content.strip():
        print(f"Error: {skill_path} is empty — nothing to use as the agent's system prompt", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: ANTHROPIC_API_KEY is not set.\n"
            "This prototype requires a live Anthropic API key because Managed "
            "Agents / Outcomes has no local emulation — it is a hosted platform "
            "feature. NOTE: this script has never been run against the live API "
            "on this machine (no key was available during development), so "
            "treat the first real run as validating the request/response shapes "
            "below, not as a known-good path.\n\n"
            "Estimated cost warning: a 3-iteration outcome session on one "
            "prompt is on the order of a handful of claude-sonnet-5 calls "
            "(writer turns + grader evaluations per iteration) plus one local "
            "judge call — expect low-single-digit-dollars at most for a single "
            "run, but there is no hard cap enforced by this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    from anthropic import Anthropic  # imported here so the API-key check above
    # can fire even in environments without the anthropic package installed

    client = Anthropic()  # picks up ANTHROPIC_API_KEY from the environment

    rubric_md = build_rubric_markdown(dimensions)
    print("\n--- Rubric sent to Outcomes ---")
    print(rubric_md)
    print("--- end rubric ---\n")

    if args.agent_id and args.environment_id:
        print(f"Reusing agent={args.agent_id} environment={args.environment_id}")
        agent_id, environment_id = args.agent_id, args.environment_id
    else:
        agent, environment = create_agent_and_environment(client, skill_content)
        agent_id, environment_id = agent.id, environment.id
        print(
            "\nNOTE: reuse these IDs on future runs instead of recreating "
            f"them:\n  --agent-id {agent_id} --environment-id {environment_id}\n"
        )

    session, artifact_text, outcome_result, outcome_explanation, iteration_log = run_outcome_session(
        client, agent_id, environment_id, prompt_text, rubric_md
    )

    print("\n--- Artifact produced (accumulated from agent.message events) ---")
    print(artifact_text[:2000] + ("..." if len(artifact_text) > 2000 else ""))
    print("--- end artifact ---\n")

    local_result = run_local_judge(artifact_text, dimensions, skill_content)

    print("\n============================================================")
    print("  VERDICT COMPARISON")
    print("============================================================")
    print("\nOutcome grader (Managed Agents, isolated-context):")
    print(f"  final result: {outcome_result}")
    print(f"  explanation: {outcome_explanation}")
    print(f"  iterations run: {len(iteration_log)}")
    for entry in iteration_log:
        print(f"    iteration {entry['iteration']}: {entry['result']}")

    print("\nLocal judge (tools/eval_llm_judge.py, semi-blind mode):")
    for dim_name, result in local_result.items():
        if isinstance(result, dict) and "normalised" in result:
            print(f"  {dim_name}: {result['score']}/5 (normalised {result['normalised']}) — {result['reason']}")

    print("\nSession IDs (agent and environment are NOT archived — reuse them):")
    print(f"  agent_id = {agent_id}")
    print(f"  environment_id = {environment_id}")
    print(f"  session_id = {session.id} (will be archived now)")

    print("\nArchiving session...")
    client.beta.sessions.archive(session.id)
    print("Done.")


if __name__ == "__main__":
    main()
