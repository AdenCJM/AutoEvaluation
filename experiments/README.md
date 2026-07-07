# Experiments

Prototypes exploring whether newer Anthropic platform primitives should replace parts of AutoEval's hand-rolled tooling. Nothing here is wired into the main loop (`program.md`, `tools/run_loop.py`) - these are standalone scripts you run manually to gather evidence before deciding whether to adopt a primitive.

## `outcomes_prototype.py` - Managed Agents Outcomes vs. AutoEval's own judge

### What it tests

AutoEval's inner loop generates one output per test prompt, then scores it with a hand-rolled LLM judge (`tools/eval_llm_judge.py`), optionally in semi-blind mode (the judge sees `SKILL.md` for the `task_accuracy` dimension only, per `judge_sees_skill` in `config.yaml`).

Anthropic's Managed Agents platform has a native primitive for exactly this shape of problem: **Outcomes**. You send a `user.define_outcome` event with a description and a markdown rubric, and the harness automatically spins up a **grader** - a fresh subagent in a separate context window that sees only the rubric and the artifact, not the writer's reasoning or tool calls - to score it, feeding revision requests back to the writer until the rubric is satisfied or `max_iterations` is reached (default 3, max 20).

That grader-isolation is architecturally the same idea as AutoEval's semi-blind judge, except Anthropic runs it as a managed platform feature rather than something hand-orchestrated in `eval_llm_judge.py`. Anthropic's own published number for this pattern is +10 percentage points task success vs. standard prompting loops, with the largest gains on the hardest tasks.

This prototype builds a rubric from `config.yaml`'s `llm_judge_dimensions`, runs one Managed Agents outcome session against the first prompt in `prompts/prompts.json`, and judges the same resulting artifact with the existing local judge - so both verdicts are printed side by side for direct comparison.

### What it does NOT test

- It does not aggregate across multiple prompts (AutoEval's actual keep/revert decision needs a composite score across the whole prompt set, not one artifact). Outcomes grades one artifact per outcome; it has no equivalent of `score_aggregator.py`'s weighted composite across LLM-judge + deterministic metrics.
- It does not replace the outer hill-climb over `SKILL.md` itself - Outcomes optimises one artifact against a rubric, not the instructions that produced many artifacts.
- It is a one-prompt spot check, not a benchmark. Treat any single run's result as anecdote, not evidence of a systematic advantage either way.

### How to run it

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 experiments/outcomes_prototype.py
```

On a repeat run, skip recreating the agent and environment (Anthropic's guidance is to create these once and reuse them across sessions):

```bash
python3 experiments/outcomes_prototype.py --agent-id agent_... --environment-id env_...
```

The script prints the created (or reused) `agent_id` / `environment_id` so you can pass them back in next time. The session itself is created fresh each run and archived at the end - only the session is disposable, not the agent/environment.

### Cost expectations

A single 3-iteration outcome session against one prompt costs on the order of a handful of `claude-sonnet-5` calls: the writer agent's turns, plus one grader evaluation per iteration (up to 3 by default), plus one additional local-judge call afterwards on the same artifact. Expect low-single-digit dollars per run at most - there is no hard cost cap enforced by the script itself, so don't loop this over the full prompt set without adding one.

### Status: experimental, unverified-live

This machine has no `ANTHROPIC_API_KEY` available, so `outcomes_prototype.py` has **never been run against the live Anthropic API**. The request/response shapes (agent/environment/session creation, the `user.define_outcome` event, stream event handling for `span.outcome_evaluation_start`/`_end` and `session.status_idle`) were written directly from Anthropic's Managed Agents documentation (`platform.claude.com/docs/en/managed-agents/define-outcomes` and `.../quickstart`, both fetched July 2026), not from a working run. The script compiles cleanly (`python3 -m py_compile experiments/outcomes_prototype.py`) but the first real invocation should be treated as validating the integration, not as a known-good path.

If you run it and something doesn't match the docs, that's a live-API discrepancy worth flagging - the beta surface (`managed-agents-2026-04-01`) is still evolving.
