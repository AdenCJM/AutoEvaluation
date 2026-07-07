# Skill Optimisation Loop

You are running an autonomous optimisation loop. Your goal is to maximise the composite evaluation score by iteratively modifying a skill file (`SKILL.md`).

All settings — model, paths, metrics, and run duration — are in `config.yaml`.

## Setup (run once at start)

1. Read the current `SKILL.md` and `config.yaml`
2. Check if `results.tsv` exists. If it does, you're resuming — skip to the loop. Read the TSV to find the current best score and iteration count.
3. If no `results.tsv`, run the baseline (evaluates the train prompt split with `replicates_per_prompt` completions per prompt):
   ```
   python3 tools/experiment_runner.py --run-id baseline --description "Initial baseline" --decision BASELINE
   ```
4. If `holdout_fraction` in config.yaml is > 0, also establish the holdout baseline:
   ```
   python3 tools/experiment_runner.py --run-id baseline_holdout --description "Holdout baseline" --no-tsv --prompt-set holdout
   ```
5. Persist the baseline aggregates as the "best so far" reference (the decision tool compares candidates against these):
   ```
   cp .tmp/evals/baseline/aggregate.json best_aggregate.json
   cp .tmp/evals/baseline_holdout/aggregate.json best_holdout_aggregate.json
   ```
6. Copy `SKILL.md` to `SKILL.md.best`: `cp SKILL.md SKILL.md.best`
7. Optionally start the dashboard in the background: `python3 tools/dashboard_server.py &`

## Run Duration & Stopping Conditions

Check `config.yaml` for limits:
- `max_iterations`: stop after N experiments (0 = unlimited)
- `max_hours`: stop after N hours (0 = unlimited)
- `max_cost_usd`: stop when estimated API spend exceeds this amount (0 = unlimited)
- `convergence_window`: stop after N consecutive iterations with no improvement (0 = disabled)
- If multiple limits are set, stop when **any** limit is hit first
- If all are 0 or omitted, run indefinitely

Track these yourself as you loop. At the start of each iteration, check if you've exceeded any limit.

## Judge Configuration

By default, the same model generates outputs and judges them. For better evaluation signal, configure a **separate judge model** (recommended — a cheaper model works well) to avoid self-judging bias:

```yaml
# In config.yaml — optional, falls back to primary provider if not set
judge_provider: anthropic
judge_model: claude-haiku-4-5
judge_api_key_env: ANTHROPIC_API_KEY
```

You can also enable **semi-blind judging** where the judge sees `SKILL.md` when scoring the `task_accuracy` dimension only (other dimensions stay blind):

```yaml
judge_sees_skill: true
```

These are configured in `config.yaml` — do not change them during a run.

## Evaluation Statistics

Judge scores are noisy run-to-run, so decisions use statistics, not bare comparisons:

- `replicates_per_prompt` completions are generated per prompt so each experiment carries a variance estimate
- Prompts are split into a **train** set (optimised against every iteration) and a **holdout** set (`holdout_fraction`) used only to validate KEEPs — this catches changes that overfit the train prompts
- KEEP/DISCARD is decided by `tools/decision.py`, which pairs per-prompt scores between the candidate and the current best and requires the improvement to clear a bootstrap confidence interval (`accept_confidence`), not just to be positive

## The Loop

Repeat until a limit is hit (or indefinitely if no limits are set). After each experiment, output a brief summary (Step 5 below). If the user provides steering mid-run, incorporate it into your next hypothesis — otherwise continue autonomously. Do not pause to ask for permission; just report and proceed.

### Step 1: Analyse

- Read `results.tsv` to see score history
- Identify the **weakest 2–3 metrics** in the most recent run
- Read 2–3 sample files from `.tmp/samples/{latest_run_id}/` to see concrete failures
- Form a hypothesis: "Metric X is low because the skill instructions don't [specific observation]"
- Write your hypothesis in one sentence

### Step 2: Modify

- Make **ONE** targeted change to `SKILL.md` based on your hypothesis
- Write a one-line description of your change (this goes in results.tsv)

**Types of changes to try:**
- Add a concrete good/bad example for the weakest metric
- Reword a vague instruction to be more specific
- Add emphasis (bold, caps) for frequently violated rules
- Restructure: put the most-violated rules first (primacy effect)
- Add a "Common mistakes" section with before/after rewrites
- Experiment with instruction framing (imperative vs. descriptive)
- Add a self-check instruction ("Before outputting, verify that...")
- Test whether fewer rules with more examples beats more rules with fewer examples

**Constraints:**
- Do NOT make multiple unrelated changes in one iteration
- Do NOT delete the YAML frontmatter or main section headers
- Do NOT make changes so large that you can't attribute the score change to a specific edit
- Keep the skill under ~2000 words total (diminishing returns beyond that)

### Step 3: Evaluate

- Pick the next run_id: `exp_001`, `exp_002`, etc. (check results.tsv for the last number)
- Run:
  ```
  python3 tools/experiment_runner.py \
    --run-id exp_{NNN} \
    --description "{your one-line description}"
  ```
- Read the composite score (± stddev) and per-metric breakdown from the output
- If the runner reports judge failures and exits non-zero, the eval subsystem is broken — do NOT record a decision; fix the cause (API key, judge model) and re-run. After 3 consecutive failures, stop and report to the user.

### Step 4: Decide

- Run the noise-aware decision tool (exit code 0 = KEEP, 1 = DISCARD):
  ```
  python3 tools/decision.py \
    --candidate-agg .tmp/evals/exp_{NNN}/aggregate.json \
    --best-agg best_aggregate.json
  ```
- If the verdict is **DISCARD**:
  - `cp SKILL.md.best SKILL.md`
  - `python3 tools/results_io.py --decision DISCARD`
  - Log: "DISCARD — {reason from the verdict}"
- If the verdict is **KEEP** and `holdout_fraction` > 0, validate on the holdout set before committing:
  ```
  python3 tools/experiment_runner.py --run-id exp_{NNN}_holdout --description "Holdout validation" --no-tsv --prompt-set holdout
  python3 tools/decision.py \
    --candidate-agg .tmp/evals/exp_{NNN}_holdout/aggregate.json \
    --best-agg best_holdout_aggregate.json --mode non-regression
  ```
  - If the holdout check **fails** (significant regression on unseen prompts): treat as DISCARD — the change overfit the train prompts. Revert and log "DISCARD — holdout regression".
- If the verdict is **KEEP** (and the holdout check passed or is disabled):
  - `cp SKILL.md SKILL.md.best`
  - `cp .tmp/evals/exp_{NNN}/aggregate.json best_aggregate.json`
  - If a holdout run was made: `cp .tmp/evals/exp_{NNN}_holdout/aggregate.json best_holdout_aggregate.json`
  - `python3 tools/results_io.py --decision KEEP --holdout-composite {holdout score, if measured}`
  - Log: "KEEP — {reason from the verdict}"

### Step 5: Report

Output a brief summary of the iteration to the user:

```
Iteration N — exp_NNN
Hypothesis: [one sentence]
Change: [what you changed in SKILL.md]
Score: [previous best] → [new score] ± [stddev]  [✓ KEEP / ✗ DISCARD]
Verdict: [one-line reason from decision.py, incl. CI when available]
```

If the user responds with guidance ("focus on metric X", "try a different approach", "that's good enough — stop"), incorporate it before continuing. Otherwise proceed immediately to Step 6.

### Step 6: Continue

- Check all stopping conditions before continuing (iterations, hours, cost, convergence)
- If any limit is reached, output "Optimisation complete — ran N iterations in X hours. Best score: Y" and stop
- Otherwise, go back to Step 1
- If a tool errors, read the error, fix it, retry
- If you've made **5 consecutive DISCARD** decisions, try a fundamentally different strategy:
  - Reorder the entire document structure
  - Add a completely new section (e.g., "Examples of great output" or "Self-check before output")
  - Try removing rules instead of adding them (simplicity can improve adherence)
  - Combine two weak areas into a single focused rewrite

## Rules

- **Never modify files in `tools/` or `prompts/`** — the evaluation harness is fixed
- **Never modify `program.md`** — these are your instructions
- **Never modify `config.yaml`** — the configuration is fixed
- **Only modify `SKILL.md`** — this is the single file you optimise
- **Never hand-edit `results.tsv`, `best_aggregate.json`, or `best_holdout_aggregate.json`** — use `tools/results_io.py` and the `cp` commands above
- Keep the YAML frontmatter valid
- One change per iteration
- Trust the metrics, not your intuition about what "should" work
- Redirect all long outputs to files: `> output.log 2>&1`
- Keep your context lean: don't re-read all samples every iteration, just the ones relevant to the weakest metrics
