# Skill Optimisation Loop

You are running an autonomous optimisation loop. Your goal is to maximise the composite evaluation score by iteratively modifying a skill file (`SKILL.md`).

All settings — model, paths, metrics, and run duration — are in `config.yaml`.

## Setup (run once at start)

1. Read the current `SKILL.md` and `config.yaml`
2. Check if `results.tsv` exists. If it does, you're resuming — skip to the loop. Read the TSV to find the current best score and iteration count.
3. If no `results.tsv`, run the baseline:
   ```
   python3 tools/experiment_runner.py --run-id baseline --description "Initial baseline" --decision BASELINE
   ```
4. Read the baseline composite score from the output
5. Copy `SKILL.md` to `SKILL.md.best`: `cp SKILL.md SKILL.md.best`
6. Optionally start the dashboard in the background: `python3 tools/dashboard_server.py &`

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

By default, the same model generates outputs and judges them. For better evaluation signal, you can configure a **separate judge model** to avoid self-judging bias:

```yaml
# In config.yaml — optional, falls back to primary provider if not set
judge_provider: openai
judge_model: gpt-4o
judge_api_key_env: OPENAI_API_KEY
```

You can also enable **semi-blind judging** where the judge sees `SKILL.md` when scoring the `task_accuracy` dimension only (other dimensions stay blind):

```yaml
judge_sees_skill: true
```

These are configured in `config.yaml` — do not change them during a run.

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
- Read the composite score and per-metric breakdown from the output

### Step 4: Decide

- Read the best composite score from `results.tsv` (highest value in the composite_score column)
- If new composite score **>** best score:
  - **KEEP** the change
  - `cp SKILL.md SKILL.md.best`
  - Update the results.tsv row: set decision to KEEP
    (rewrite the last line of results.tsv to add KEEP in the decision column)
  - Log: "KEEP — score improved from X to Y"
- If new composite score **<=** best score:
  - **DISCARD** the change
  - `cp SKILL.md.best SKILL.md`
  - Update the results.tsv row: set decision to DISCARD
  - Log: "DISCARD — score did not improve (X vs best Y)"

### Step 5: Report

Output a brief summary of the iteration to the user:

```
Iteration N — exp_NNN
Hypothesis: [one sentence]
Change: [what you changed in SKILL.md]
Score: [previous best] → [new score] ([+/-delta])  [✓ KEEP / ✗ DISCARD]
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
- Keep the YAML frontmatter valid
- One change per iteration
- Trust the metrics, not your intuition about what "should" work
- Redirect all long outputs to files: `> output.log 2>&1`
- Keep your context lean: don't re-read all samples every iteration, just the ones relevant to the weakest metrics
