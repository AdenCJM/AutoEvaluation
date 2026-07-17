---
name: autoeval
description: Run or resume AutoEvaluation to test and improve a SKILL.md through controlled experiments. Use when the user asks to optimise a skill, run the eval loop, measure prompt quality, or inspect an AutoEvaluation run.
---

# AutoEvaluation for Codex

Use the repository's deterministic driver instead of recreating the loop in chat.

## Before a run

1. Read `AGENTS.md`, `program.md`, and `config.template.yaml`.
2. Inspect `config.yaml`, `SKILL.md`, `prompts/prompts.json`, and `results.tsv` when present.
3. If setup is incomplete, use Codex's `request_user_input` tool for short choices. Never ask the user to paste an API key into chat. Tell them to put it in `.env` under the environment variable named by `api_key_env`.
4. Generate at least 30 diverse prompts. Reserve validation and final-test splits through `holdout_fraction` and `final_test_fraction`.
5. Do not alter `program.md`, evaluation tools, prompts, or config during an active run.

Use `python3 tools/generate_config.py` for non-interactive setup when its arguments are known, or `python3 setup.py` for the guided terminal wizard.

## Run

Start the dashboard if useful:

```bash
python3 tools/dashboard_server.py --port 8050
```

Run the crash-safe headless loop:

```bash
python3 autoeval.py run
```

Let the requested segment finish unless the user asks to stop. The driver owns locking, recovery, experiment isolation, statistical decisions, and cost accounting. Ordinary segments preserve the untouched final test; run `python3 autoeval.py finalize` only when the user confirms the campaign is complete.

After each experiment, report the run ID, hypothesis/change, score with uncertainty, decision, validation result, and cumulative cost. Accept user steering for the next iteration without manually editing files mid-experiment.

## Safety

- Keep secrets in `.env`; never print or commit them.
- Do not bypass the run lock or hand-edit `results.tsv`.
- Do not claim improvement from the training score alone. Report the untouched final-test result when available.
- If fewer than eight training prompt pairs are available, describe the decision as degraded threshold mode, not statistical significance.
- If an external benchmark would incur material API spend, state the expected scope before starting it.
