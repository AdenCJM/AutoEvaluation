# Getting Started with AutoEvaluation

This tutorial takes you from clone to a reviewed, finalized skill campaign. Budget about 15 minutes for setup; evaluation time depends on the model and campaign size.

## 1. Clone and install

```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
cd AutoEvaluation
python3 -m pip install -r requirements.txt
```

AutoEvaluation supports Gemini, OpenAI, and Anthropic. Create an API key with your chosen provider before setup.

## 2. Configure your campaign

Run the guided setup:

```bash
python3 autoeval.py init
```

The setup workbench:

1. Chooses the generation and judge models.
2. Accepts your existing `SKILL.md` or creates one from your description.
3. Generates about 30 test scenarios.
4. Shows prompt coverage, duplicates, and the train/validation/final-test split.
5. Lets you edit, delete, regenerate, and add scenarios before accepting them.
6. Reviews the rubric, warns about hard-to-observe criteria, and estimates campaign usage.
7. Runs preflight validation before writing the configuration.

Your API key is entered with hidden input and written only to the gitignored `.env` file. To verify the variable exists without printing the secret:

```bash
python3 -c "from pathlib import Path; print(any(line.startswith(('GEMINI_API_KEY=', 'OPENAI_API_KEY=', 'ANTHROPIC_API_KEY=')) for line in Path('.env').read_text().splitlines()))"
```

For a non-interactive local scaffold with safe defaults:

```bash
python3 autoeval.py init --defaults
```

## 3. Run the first segment

```bash
python3 autoeval.py run --iterations 3
```

The driver establishes a baseline, makes one attributable instruction change per experiment, evaluates repeated samples, and keeps only changes that pass the configured statistical and validation gates. It records:

- `results.tsv` — append-only experiment history
- `SKILL.md.best` — confirmed best instruction
- `.tmp/evals/<run>/decision.json` — rationale, confidence, duration, and estimated cost
- `.tmp/run_status.json` — live campaign progress

An ordinary run segment does **not** consume the untouched final-test split. You can safely inspect or resume the active campaign.

## 4. Inspect the evidence

```bash
python3 autoeval.py status
python3 autoeval.py dashboard --open
```

The dashboard shows the score trend, per-metric movement, campaign cost, KEEP/DISCARD history, and the weakest samples behind each decision. Select an experiment for its rationale and exact instruction diff, or choose **Compare baseline and best** for the full campaign change.

No key is required to explore the bundled read-only product demo:

```bash
python3 autoeval.py demo --open
```

## 5. Continue, then finalize once

Each `--iterations` value is the number of additional attempts in that segment:

```bash
python3 autoeval.py run --iterations 10
```

When you are genuinely finished tuning, explicitly consume the untouched final-test split once:

```bash
python3 autoeval.py finalize
```

Finalization writes the baseline/final audit and `.tmp/run-summary.md`. The dashboard then becomes a handoff screen where you can compare, copy, download, or install the confirmed best.

Do not continue tuning after inspecting the final test. Start a fresh campaign instead:

```bash
python3 autoeval.py new --name "Second independent campaign"
```

This archives the completed campaign under `campaigns/<campaign-id>/`, clears active runtime state, and seeds the new campaign from the prior confirmed best.

## 6. Run your own benchmark

For repeatable evidence across independent campaigns, start with a dry run:

```bash
python3 autoeval.py benchmark --campaigns 3 --iterations 10
```

Add `--execute` only after reviewing the estimated work. Each benchmark campaign runs in an isolated workspace and produces its own final-test result.

## Next steps

- [Configuration reference](CONFIG_REFERENCE.md) — models, limits, statistical gates, and split settings
- [Architecture](ARCHITECTURE.md) — experiment and campaign state machines
- [Scheduled runs](SCHEDULED_RUNS.md) — safe unattended segments
- [Troubleshooting](TROUBLESHOOTING.md) — recovery and diagnostics

The supported product path is `autoeval.py`; it delegates experiment execution to `tools/run_loop.py`, the state-machine source of truth.
