# Configuration Reference

This document explains every option in `config.yaml`. See [Getting Started](GETTING_STARTED.md) for a quick walkthrough, or [Troubleshooting](TROUBLESHOOTING.md) if something breaks.

## Provider & Model

### `provider`
**Type:** `string` (one of: `gemini`, `openai`, `anthropic`)  
**Default:** `gemini`  
**What it does:** Which LLM platform to use for generating samples and (optionally) judging them.

**Options:**

- **`gemini`** — Google Gemini (fast, cheap, good for beginners)
  - Model: `gemini-3.5-flash` (default) or `gemini-3.1-flash-lite` (cheap)
  - API key: `GEMINI_API_KEY`

- **`openai`** — OpenAI (powerful but pricier)
  - Model: `gpt-5.4` (default) or `gpt-5.4-mini` (cheap)
  - API key: `OPENAI_API_KEY`

- **`anthropic`** — Anthropic Claude (good reasoning, good price)
  - Model: `claude-sonnet-5` (default), `claude-opus-4-8` (max quality), `claude-haiku-4-5` (cheap, good judge)
  - API key: `ANTHROPIC_API_KEY`

Model IDs move fast. The source of truth is `DEFAULT_MODELS` in `tools/utils.py` and `_PRICING` in `tools/model_client.py`. Unknown models work when cost capping is disabled. With `max_cost_usd` set, startup fails until every configured model has a pricing entry.

---

### `model`
**Type:** `string`  
**Default:** `gemini-3.5-flash`  
**What it does:** Which model to use. Must match your provider.

**Recommendations:**

| Use case | Provider | Model | Why |
|----------|----------|-------|-----|
| Getting started (default) | Gemini | `gemini-3.5-flash` | Balanced cost/quality, fast |
| Cheapest | Gemini | `gemini-3.1-flash-lite` | Lowest cost per call |
| Default OpenAI | OpenAI | `gpt-5.4` | Balanced cost/quality |
| Cheap OpenAI | OpenAI | `gpt-5.4-mini` | Lower cost, weaker reasoning |
| Default Anthropic (recommended for modifying) | Anthropic | `claude-sonnet-5` | Good balance of quality/cost for hundreds of calls |
| Strongest possible | Anthropic | `claude-opus-4-8` | Best, most expensive |
| Cheap judge | Anthropic | `claude-haiku-4-5` | Cheap and consistent, recommended as a separate judge model |

---

### `api_key_env`
**Type:** `string`  
**Default:** `GEMINI_API_KEY`  
**What it does:** The environment variable name where your API key lives.

**Example:**
```yaml
provider: openai
model: gpt-5.4
api_key_env: OPENAI_API_KEY  # System reads from env var OPENAI_API_KEY
```

Then in `.env`:
```
OPENAI_API_KEY=sk-...
```

---

## Judge (Evaluation Model)

By default, the same model generates samples AND judges them. This is simple but has **self-judging bias** — the model might optimize outputs to please itself.

Using a separate judge model is now a first-class recommendation, not just an advanced option:

### `judge_provider` (optional)
**Type:** `string` (one of: `gemini`, `openai`, `anthropic`)  
**Default:** Omitted (uses primary provider)  
**What it does:** Which provider to use for evaluation. If omitted, uses primary provider.

### `judge_model` (optional)
**Type:** `string`  
**Default:** Omitted (uses primary model)  
**Recommended:** `claude-haiku-4-5` — cheap and consistent, good default judge regardless of which provider generates samples.  
**What it does:** Which model to use for judging. If omitted, uses primary model.

### `judge_api_key_env` (optional)
**Type:** `string`  
**Default:** Omitted (uses primary key)  
**What it does:** The environment variable for the judge's API key.

**Example: Use Gemini for generation, Claude Haiku for judging**

```yaml
# Primary (generation)
provider: gemini
model: gemini-3.5-flash
api_key_env: GEMINI_API_KEY

# Judge (evaluation)
judge_provider: anthropic
judge_model: claude-haiku-4-5
judge_api_key_env: ANTHROPIC_API_KEY
```

Then in `.env`:
```
GEMINI_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...
```

**Trade-off:**
- **Single model:** Cheaper, simpler setup, self-judging bias risk
- **Separate model (recommended):** Better signal, requires 2 API keys, more reliable — a cheap judge like `claude-haiku-4-5` keeps the cost difference small

---

### `judge_sees_skill` (optional)
**Type:** `boolean`  
**Default:** `true`  
**What it does:** Whether the judge can see `SKILL.md` when evaluating.

**Modes:**

- **`true` (default, semi-blind)** — Judge sees the skill for the `task_accuracy` metric only. Other metrics stay blind. Recommended: task adherence can't be judged meaningfully without seeing the task.

- **`false`** — Judge evaluates fully blind (doesn't see the skill at all). Eliminates any chance of the judge being swayed by the skill's own framing, but `task_accuracy` loses context.

**Example: Semi-blind judging**

```yaml
judge_sees_skill: true
llm_judge_dimensions:
  - name: task_accuracy
    weight: 0.40
    # Judge will see SKILL.md for this metric
  - name: quality
    weight: 0.30
    # Judge will NOT see SKILL.md for this metric
```

---

## File Paths

### `skill_path`
**Type:** `string` (file path)  
**Default:** `SKILL.md`  
**What it does:** Path to the skill file being optimised.

Usually leave this as-is. If you want to optimise a different file:
```yaml
skill_path: my-custom-skill.md
```

### `prompts_path`
**Type:** `string` (file path)  
**Default:** `prompts/prompts.json`  
**What it does:** Path to the JSON file containing test scenarios.

Format:
```json
[
  {
    "id": "task_1",
    "genre": "cold email",
    "prompt": "Write a 200-word cold email..."
  },
  ...
]
```

### `results_tsv`
**Type:** `string` (file path)  
**Default:** `results.tsv`  
**What it does:** Where to save experiment history (append-only).

Each iteration appends one row. Columns: `run_id`, `timestamp`, `composite_score`, one column per configured metric, `change_description`, `decision`, then four columns added in the July 2026 modernisation — `composite_stddev`, `n_samples`, `judge_errors`, `holdout_composite`. See [results.tsv columns](#resultstsv-columns) below. Older files are migrated in place: `tools/results_io.py` extends the header with any missing columns rather than requiring a rewrite.

---

## Run Duration & Stopping Conditions

The loop stops when **any** of these limits is hit (whichever comes first).

### `max_iterations`
**Type:** `integer` (≥ 0)  
**Default:** `0` (unlimited)  
**What it does:** Maximum number of experiments to run.

**Examples:**
```yaml
max_iterations: 20    # Stop after 20 experiments
max_iterations: 0     # No limit, run forever (until time or cost limit)
```

---

### `max_hours`
**Type:** `float` (≥ 0)  
**Default:** `0` (unlimited)  
**What it does:** Maximum time to run (in hours). Accepts decimals.

**Examples:**
```yaml
max_hours: 2          # Stop after 2 hours
max_hours: 0.5        # Stop after 30 minutes
max_hours: 0          # No limit
```

---

### `max_cost_usd`
**Type:** `float` (≥ 0)  
**Default:** `0` (unlimited)  
**What it does:** Stop when estimated API spend exceeds this amount (USD).

Cost is tracked from token counts and standard provider list pricing, not actual billing. Time-limited promotions are intentionally excluded so guards remain conservative. If `max_cost_usd` is non-zero, the loop refuses to start when either the generation/modifier model or judge model lacks a pricing entry.

**Examples:**
```yaml
max_cost_usd: 10      # Stop if estimated cost > $10
max_cost_usd: 0       # No limit
```

---

### `convergence_window`
**Type:** `integer` (≥ 0)  
**Default:** `0` (disabled)  
**What it does:** Stop after N consecutive iterations with no improvement.

The system stops if it hasn't found a KEEP (per `accept_rule`) in the last N iterations.

**Examples:**
```yaml
convergence_window: 10    # Stop if no improvement for 10 iterations
convergence_window: 0     # Disabled, never stop automatically on convergence
```

**Trade-off:**
- **0 (disabled):** Lets it run longer, more chance to find improvements, higher cost
- **10:** Stops when it plateaus, saves API cost, but might miss late breakthroughs

---

### `min_improvement`
**Type:** `float` (0.0 to 1.0)  
**Default:** `0.01`  
**What it does:** Minimum score improvement required to KEEP a change under the legacy `accept_rule: simple`. Ignored when `accept_rule: paired` (the default) is active — see [Evaluation Statistics](#evaluation-statistics) below.

If `score_delta < min_improvement`, the change is DISCARDED even if the new score is slightly higher.

**Examples:**
```yaml
min_improvement: 0.01     # Only KEEP changes with delta > 1% (default)
min_improvement: 0.05     # Only KEEP changes with delta > 5% (conservative)
min_improvement: 0        # KEEP any positive improvement (risky with noisy judges)
```

**Trade-off:**
- **0.01 (default):** Good balance; filters noise without losing signal
- **0.05:** Conservative; only keeps strong improvements; convergence faster
- **0.00:** Aggressive; keeps any improvement; skill might churn with noise

---

## Evaluation Statistics

LLM-judge scores are noisy run-to-run. A single-draw comparison ("0.7185 vs 0.7120, so KEEP") mostly measures that noise. These settings improve the evidence available to the decision rule, but they do not turn a small prompt suite into proof. Use the untouched final test and repeated campaigns for publishable claims.

### `replicates_per_prompt`
**Type:** `integer` (≥ 1)  
**Default:** `3`  
**What it does:** How many completions to generate per prompt per experiment. Higher values reduce noise in the composite score at the cost of more API calls. Replicate samples are named `sample_{i}_{prompt_id}_r{k}` (prompt index, prompt id, replicate number).

```yaml
replicates_per_prompt: 3   # default; raise to 5+ if --measure-noise shows high variance
```

### `accept_rule`
**Type:** `string` (one of: `paired`, `simple`)  
**Default:** `paired`  
**What it does:** How KEEP/DISCARD is decided.

- **`paired` (default)** — `tools/decision.py` hierarchically resamples prompts and replicate scores between the candidate and current best. With at least eight shared prompts, KEEP requires the corrected interval to exclude zero. Smaller sets report `paired-degraded` and use `min_improvement`.
- **`simple`** — legacy behaviour: KEEP if `composite_score` delta exceeds `min_improvement`. No noise awareness.

```yaml
accept_rule: paired   # recommended
accept_rule: simple   # legacy min_improvement threshold
```

### `accept_confidence`
**Type:** `float` (0.0 to 1.0)  
**Default:** `0.95`  
**What it does:** Base confidence for the hierarchical bootstrap under `accept_rule: paired`. With `sequential_correction: true`, candidate test `k` spends `alpha / (k × (k + 1))`, including across resumed runs.

```yaml
accept_confidence: 0.95   # 95% CI must exclude zero to KEEP
accept_confidence: 0.90   # looser bar, more KEEPs, more risk of noise slipping through
```

### `holdout_fraction`
**Type:** `float` (0.0 to 1.0)  
**Default:** `0.3`  
**What it does:** Fraction of prompts reserved as the validation gate. These prompts are not used for the training interval, but their non-regression verdict does affect KEEP/DISCARD and therefore they are not a final test. Explicit `"split": "holdout"` and `"split": "validation"` are both accepted.

```yaml
holdout_fraction: 0.3   # last ~30% of prompts, or explicit "split": "holdout" entries
holdout_fraction: 0     # disabled — no overfitting guard
```

### `final_test_fraction`

**What it does:** Reserves an untouched final-test slice. It is scored for the baseline and once after the loop, but never affects candidate selection.

```yaml
final_test_fraction: 0.2
```

You can explicitly assign prompts with `"split": "final_test"`. If you inspect the result and then continue optimising, create a new final-test set before making another publishable claim.

### `sequential_correction`

**What it does:** Applies an alpha-spending schedule whose cumulative budget remains bounded across arbitrarily long or resumed campaigns.

```yaml
sequential_correction: true
```

### `min_valid_sample_frac`
**Type:** `float` (0.0 to 1.0)  
**Default:** `0.8`  
**What it does:** Minimum fraction of judge calls that must succeed for scoring to proceed. If more than 20% of judge calls fail (malformed response, API error, etc.), scoring aborts rather than computing a composite score from partial data. Judge failures are excluded from the score entirely — they are not counted as zeros.

```yaml
min_valid_sample_frac: 0.8   # abort if more than 20% of judge calls fail
```

### Measuring your noise floor

Before tuning `accept_confidence` or `replicates_per_prompt`, measure how much your setup actually varies:

```bash
python3 tools/run_loop.py --measure-noise 3
```

This re-runs the same baseline configuration 3 times (no skill changes) and reports the score spread, so you can judge whether the defaults are appropriately conservative for your judge model and prompt set.

---

## Performance

### `max_concurrent`
**Type:** `integer` (≥ 1)  
**Default:** `1`  
**What it does:** How many LLM API calls to run in parallel.

**Examples:**
```yaml
max_concurrent: 1     # Serial (default): slower, cheaper
max_concurrent: 4     # 4 parallel calls: 4× faster wall-clock, higher cost
```

**How it works:**

When generating 5 samples:
- **max_concurrent: 1** — Generate sample 1, wait, generate sample 2, wait, ... (5× time)
- **max_concurrent: 4** — Generate samples 1-4 in parallel, then sample 5 (≈2× time)

Same logic for evaluation.

**Cost:** Parallelism doesn't change total API calls, just how fast they run. 4 concurrent = same cost, much faster wall-clock time.

**Trade-off:**
- **1 (serial, default):** Simpler, safer (if one call fails, easier to debug), fine for overnight runs
- **4 (parallel):** 4× faster iteration time, same cost, better for interactive use

---

## LLM Judge Dimensions

The judge evaluates each sample on multiple dimensions (metrics). Each dimension:
- Is scored 1–5 by the LLM
- Gets normalised to 0–1
- Gets weighted and averaged into a composite score

### Structure

```yaml
llm_judge_dimensions:
  - name: metric_name
    weight: 0.30
    direction: higher_is_better  # or lower_is_better
    rubric: |
      Clear instructions for the LLM judge.
      Explain what you want and how to score it.
      1 = worst
      5 = best
```

---

### `name`
**Type:** `string`  
**What it does:** Identifier for this metric (appears in results, logs, dashboard).

**Example:**
```yaml
- name: natural_voice
- name: task_accuracy
- name: quality
```

---

### `weight`
**Type:** `float` (0.0 to 1.0)  
**What it does:** How much this metric contributes to the composite score.

**Rule:** All weights must sum to 1.0 (across LLM judge + deterministic metrics).

**Example: Three metrics, equal weight**
```yaml
llm_judge_dimensions:
  - name: natural_voice
    weight: 0.33
  - name: task_accuracy
    weight: 0.33
  - name: quality
    weight: 0.34  # 0.33 + 0.33 + 0.34 = 1.00
```

**Example: Accuracy is most important**
```yaml
llm_judge_dimensions:
  - name: task_accuracy
    weight: 0.60   # 60% of score
  - name: quality
    weight: 0.40   # 40% of score
# Total: 1.00
```

---

### `direction`
**Type:** `string` (one of: `higher_is_better`, `lower_is_better`)  
**Default:** `higher_is_better`  
**What it does:** How to interpret the 1–5 score.

**Example: Quality (higher is better)**
```yaml
- name: quality
  direction: higher_is_better  # Score 5 = good, score 1 = bad
```

**Example: Error rate (lower is better)**
```yaml
- name: error_rate
  direction: lower_is_better  # Score 5 = high error = bad, score 1 = low error = good
```

When direction is `lower_is_better`, the system inverts the score before weighting.

---

### `rubric`
**Type:** `string` (multi-line)  
**What it does:** Instructions for the LLM judge. Should clearly define what you're measuring and how to score it.

**Good rubric (specific, measurable):**
```yaml
rubric: |
  Does the output use contractions correctly?
  Contractions like "don't", "it's", "you'll" should appear instead of
  "do not", "it is", "you will" in informal contexts.
  1 = no contractions used
  3 = some contractions, inconsistent
  5 = natural, frequent contractions
```

**Bad rubric (vague):**
```yaml
rubric: "Is this good?"  # Too vague! What does "good" mean?
```

**Tips for writing good rubrics:**
- Be specific about what you're measuring
- Give concrete examples if possible
- Explain the 1–5 scale clearly
- Avoid moral judgment ("bad" / "good") in favour of objective criteria
- Ask the judge only for things it can observe in the text. "Detect whether this is AI-generated" is unreliable; "does sentence length vary / are there filler phrases" is checkable. This is why the default dimension changed from `human_score` (an AI-detection rubric) to `natural_voice` (observable prose features).

---

## Deterministic Metrics (Advanced)

Optional: add rule-based metrics alongside LLM judge metrics.

### `deterministic_metrics`
**Type:** `list` (empty or omitted by default)  
**What it does:** List of deterministic (non-LLM) metrics.

**Example:**
```yaml
deterministic_metrics:
  - name: banned_words
    weight: 0.15
    direction: higher_is_better
  - name: response_time_ms
    weight: 0.10
    direction: lower_is_better
```

For deterministic metrics to work, you need a custom `tools/eval_deterministic.py` that returns JSON:

```python
# tools/eval_deterministic.py
# Returns JSON with metric scores (0.0 to 1.0)
{
  "banned_words": {"score": 0.85},
  "response_time_ms": {"score": 0.72}
}
```

See `examples/writing-style/eval_deterministic.py` for a full example.

---

## Complete Example Config

```yaml
# Provider
provider: openai
model: gpt-5.4
api_key_env: OPENAI_API_KEY

# Optional: separate judge (recommended)
judge_provider: anthropic
judge_model: claude-haiku-4-5
judge_api_key_env: ANTHROPIC_API_KEY
judge_sees_skill: true

# Paths
skill_path: SKILL.md
prompts_path: prompts/prompts.json
results_tsv: results.tsv

# Evaluation statistics
replicates_per_prompt: 3
accept_rule: paired
accept_confidence: 0.95
holdout_fraction: 0.3
final_test_fraction: 0.2
sequential_correction: true
min_valid_sample_frac: 0.8

# Duration
max_iterations: 50
max_hours: 4
max_cost_usd: 20
convergence_window: 15

# Performance
max_concurrent: 4

# LLM Judge
llm_judge_dimensions:
  - name: task_accuracy
    weight: 0.40
    direction: higher_is_better
    rubric: |
      Does the output follow the skill instructions?
      1 = ignores instructions
      5 = perfect adherence

  - name: quality
    weight: 0.30
    direction: higher_is_better
    rubric: |
      Is the output well-written and clear?
      1 = poor quality
      5 = excellent

  - name: natural_voice
    weight: 0.30
    direction: higher_is_better
    rubric: |
      Judge only observable prose features: varied sentence length, no
      stock filler phrases, concrete word choice.
      1 = formulaic and templated
      5 = varied, specific, natural
```

---

## Common Configurations

### Fastest, Cheapest (Gemini, Headless)
```yaml
provider: gemini
model: gemini-3.1-flash-lite
api_key_env: GEMINI_API_KEY
max_iterations: 10
max_concurrent: 1
judge_sees_skill: false
```

### High-Quality, Thorough (Anthropic, Longer)
```yaml
provider: anthropic
model: claude-sonnet-5
api_key_env: ANTHROPIC_API_KEY
judge_provider: anthropic
judge_model: claude-opus-4-8
max_iterations: 50
max_concurrent: 4
judge_sees_skill: true
accept_rule: paired
accept_confidence: 0.95
convergence_window: 20
```

### Budget-Conscious ($5 Max)
```yaml
provider: gemini
model: gemini-3.1-flash-lite
max_cost_usd: 5
max_iterations: 0  # Run until cost limit
max_concurrent: 1
```

### Interactive (Fast Feedback)
```yaml
provider: openai
model: gpt-5.4
max_iterations: 5
max_concurrent: 4
max_hours: 0.5
```

---

## results.tsv Columns

`results.tsv` is header-based; `tools/results_io.py` reads and writes it and migrates older files in place (extending the header rather than requiring a rewrite). Columns, in order:

- `run_id`, `timestamp`, `composite_score` — identity and headline score
- one column per configured metric (LLM judge + deterministic)
- `change_description`, `decision` — what changed and KEEP/DISCARD
- `composite_stddev` — standard deviation of the composite score across replicates, for that run
- `n_samples` — number of judged samples the composite score was computed from
- `judge_errors` — count of judge calls that failed and were excluded from scoring
- `holdout_composite` — composite score on the holdout prompt set, if `holdout_fraction` > 0

Use `python3 tools/results_io.py --help` to update the last row of an existing `results.tsv` (e.g. to backfill a holdout score) without hand-editing the file.

## best_*.json Files

Gitignored JSON files in the project root persist the per-prompt references used by the statistical comparison and final audit:

- **`best_aggregate.json`** — per-prompt scores for the best run on the training prompt set
- **`best_holdout_aggregate.json`** — per-prompt scores for the best run on the holdout prompt set (only populated if `holdout_fraction` > 0)
- **`baseline_final_test_aggregate.json`** — baseline score on the untouched final-test split
- **`final_test_aggregate.json`** — best skill's one-time final-test audit

Both are regenerated automatically whenever a KEEP happens. You shouldn't need to edit them by hand; delete them if you want to reset the paired comparison baseline (the next experiment will then compare only against `results.tsv`'s recorded best score).

---

## Validation

When you run `setup.py` or `run_loop.py`, the system validates your config:

- All required fields present
- Weights sum to ~1.0 (auto-normalised with warning if not)
- API key environment variables exist in `.env`
- Paths are readable/writable

If validation fails, you'll see a clear error message. Fix the issue and retry.
