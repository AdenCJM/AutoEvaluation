# Configuration Reference

This document explains every option in `config.yaml`. See [Getting Started](GETTING_STARTED.md) for a quick walkthrough, or [Troubleshooting](TROUBLESHOOTING.md) if something breaks.

## Provider & Model

### `provider`
**Type:** `string` (one of: `gemini`, `openai`, `anthropic`)  
**Default:** `gemini`  
**What it does:** Which LLM platform to use for generating samples and (optionally) judging them.

**Options:**

- **`gemini`** — Google Gemini (fast, cheap, good for beginners)
  - Model: `gemini-2.5-flash` or `gemini-1.5-pro`
  - API key: `GEMINI_API_KEY`

- **`openai`** — OpenAI (powerful but pricier)
  - Model: `gpt-4o`, `gpt-4-turbo`, `gpt-3.5-turbo`
  - API key: `OPENAI_API_KEY`

- **`anthropic`** — Anthropic Claude (good reasoning, good price)
  - Model: `claude-sonnet-4-20250514`, `claude-opus-4-20250805`, `claude-haiku-4-5-20251001`
  - API key: `ANTHROPIC_API_KEY`

---

### `model`
**Type:** `string`  
**Default:** `gemini-2.5-flash`  
**What it does:** Which model to use. Must match your provider.

**Recommendations:**

| Use case | Provider | Model | Why |
|----------|----------|-------|-----|
| Getting started (cheapest) | Gemini | `gemini-2.5-flash` | $0.075/M input, $0.30/M output, fast |
| Better quality outputs | OpenAI | `gpt-4o` | Stronger reasoning, but 10× cost |
| Best reasoning for modifying | Anthropic | `claude-sonnet-4-20250514` | Good balance of quality/cost |
| Strongest possible | Anthropic | `claude-opus-4-20250805` | Best, most expensive |
| Fastest (but weaker) | OpenAI | `gpt-3.5-turbo` | Cheap, but lower quality |

---

### `api_key_env`
**Type:** `string`  
**Default:** `GEMINI_API_KEY`  
**What it does:** The environment variable name where your API key lives.

**Example:**
```yaml
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY  # System reads from env var OPENAI_API_KEY
```

Then in `.env`:
```
OPENAI_API_KEY=sk-...
```

---

## Judge (Evaluation Model)

By default, the same model generates samples AND judges them. This is simple but has **self-judging bias** — the model might optimize outputs to please itself.

For better signal, use a separate judge model:

### `judge_provider` (optional)
**Type:** `string` (one of: `gemini`, `openai`, `anthropic`)  
**Default:** Omitted (uses primary provider)  
**What it does:** Which provider to use for evaluation. If omitted, uses primary provider.

### `judge_model` (optional)
**Type:** `string`  
**Default:** Omitted (uses primary model)  
**What it does:** Which model to use for judging. If omitted, uses primary model.

### `judge_api_key_env` (optional)
**Type:** `string`  
**Default:** Omitted (uses primary key)  
**What it does:** The environment variable for the judge's API key.

**Example: Use Gemini for generation, GPT-4 for judging**

```yaml
# Primary (generation)
provider: gemini
model: gemini-2.5-flash
api_key_env: GEMINI_API_KEY

# Judge (evaluation)
judge_provider: openai
judge_model: gpt-4o
judge_api_key_env: OPENAI_API_KEY
```

Then in `.env`:
```
GEMINI_API_KEY=AIza...
OPENAI_API_KEY=sk-...
```

**Trade-off:**
- **Single model (default):** Cheaper (~$2 per iteration), simpler setup, some bias risk
- **Separate model:** Better signal (~$4 per iteration), requires 2 API keys, more reliable

---

### `judge_sees_skill` (optional)
**Type:** `boolean`  
**Default:** `false`  
**What it does:** Whether the judge can see `SKILL.md` when evaluating.

**Modes:**

- **`false` (default)** — Judge evaluates blind (doesn't see the skill). Unbiased, but the judge can't check "did the output follow the skill?"

- **`true`** — Judge sees the skill for the `task_accuracy` metric only. Other metrics stay blind. Useful if accuracy is critical and you trust the judge.

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

Each iteration appends one row with run_id, score, decision, timestamp.

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

Cost is tracked from token counts and provider pricing, not actual billing.

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

The system stops if it hasn't found a better score (above `min_improvement` threshold) in the last N iterations.

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
**What it does:** Minimum score improvement required to KEEP a change. Filters out noise.

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
- name: human_score
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
  - name: human_score
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
model: gpt-4o
api_key_env: OPENAI_API_KEY

# Optional: separate judge
judge_provider: anthropic
judge_model: claude-sonnet-4-20250514
judge_api_key_env: ANTHROPIC_API_KEY
judge_sees_skill: false

# Paths
skill_path: SKILL.md
prompts_path: prompts/prompts.json
results_tsv: results.tsv

# Duration
max_iterations: 50
max_hours: 4
max_cost_usd: 20
convergence_window: 15
min_improvement: 0.02

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

  - name: human_score
    weight: 0.30
    direction: higher_is_better
    rubric: |
      Would a human find this to be human-written or AI-generated?
      1 = obviously AI
      5 = indistinguishable from skilled human

# Deterministic
deterministic_metrics:
  - name: banned_words
    weight: 0.15
    direction: higher_is_better
```

---

## Common Configurations

### Fastest, Cheapest (Gemini, Headless)
```yaml
provider: gemini
model: gemini-2.5-flash
api_key_env: GEMINI_API_KEY
max_iterations: 10
max_concurrent: 1
judge_sees_skill: false
```

### High-Quality, Thorough (Anthropic, Longer)
```yaml
provider: anthropic
model: claude-sonnet-4-20250514
api_key_env: ANTHROPIC_API_KEY
judge_provider: anthropic
judge_model: claude-opus-4-20250805
max_iterations: 50
max_concurrent: 4
judge_sees_skill: true
min_improvement: 0.02
convergence_window: 20
```

### Budget-Conscious ($5 Max)
```yaml
provider: gemini
model: gemini-2.5-flash
max_cost_usd: 5
max_iterations: 0  # Run until cost limit
max_concurrent: 1
```

### Interactive (Fast Feedback)
```yaml
provider: openai
model: gpt-4o
max_iterations: 5
max_concurrent: 4
max_hours: 0.5
```

---

## Validation

When you run `setup.py` or `run_loop.py`, the system validates your config:

- ✅ All required fields present
- ✅ Weights sum to ~1.0 (auto-normalised with warning if not)
- ✅ API key environment variables exist in `.env`
- ✅ Paths are readable/writable

If validation fails, you'll see a clear error message. Fix the issue and retry.
