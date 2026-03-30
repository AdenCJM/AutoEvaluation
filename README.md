# AutoEvaluation

**Evals that fix themselves.**

Give it a prompt, a set of test scenarios, and a scoring rubric. It runs autonomously: generate outputs, score them, read the judge's reasoning, find the weakest metric, rewrite the prompt to fix it, re-score, keep or revert. Hill-climbing on prompt engineering, fully hands-off.

I pointed it at a writing style guide and let it run overnight. It made 20 attempts, kept 2, and improved the composite score from 0.9508 to 0.9692. The changes it made: strengthened contraction rules, added concrete before/after examples for em dash replacement. Every other LLM prompt optimiser (DSPy, TextGrad, MIPRO) requires you to write Python. This one works on plain markdown files.

Point it at any LLM instruction set. Go to bed. Wake up with a measurably better prompt.

## How it works

```
                    ┌─────────────────────────────────────────┐
                    │           OPTIMISATION LOOP              │
                    │                                          │
                    │  ┌──────────┐    ┌──────────┐            │
                    │  │ Analyse  │───▶│  Modify  │            │
                    │  │ weakness │    │ SKILL.md │            │
                    │  │ + judge  │    └─────┬────┘            │
                    │  │ reasoning│          │                 │
                    │  └────▲─────┘    ┌─────▼────┐            │
                    │       │         │ Evaluate │            │
                    │       │         │ samples  │            │
                    │  ┌────┴─────┐   └─────┬────┘            │
                    │  │  Decide  │◀────────┘                 │
                    │  │ keep/rev │                            │
                    │  └──────────┘                            │
                    └─────────────────────────────────────────┘
```

1. **Analyse** — reads the weakest metrics AND the actual sample outputs that scored poorly, including the judge's reasoning for each score. The modifier sees *why* scores are low, not just numbers.
2. **Modify** — makes ONE targeted change to the skill instructions, grounded in concrete failure examples.
3. **Evaluate** — generates outputs using the modified skill, scores them against your rubric.
4. **Decide** — if the score improved above the noise threshold, keep the change; otherwise revert. Small deltas that could be random noise are filtered out.
5. **Repeat** — until the iteration, time, or cost limit is hit (or indefinitely).

### What makes this different

Every other prompt optimiser treats prompts as parameters to optimise computationally. DSPy requires a Python DSL. AutoPrompt needs labelled datasets. OpenAI's optimizer is platform-locked. Meta's prompt-ops is Llama-only.

AutoEvaluation treats prompts as prose documents that an LLM reads, critiques, and rewrites. No DSL. No compilation step. No framework lock-in. Just a markdown file and test prompts. It's "editor doing revision" vs "compiler doing gradient descent."

### Real results

I ran AutoEvaluation on an anti-AI writing style guide (the included example) for 20 iterations using Gemini 2.5 Flash:

```
Iteration   Score    Decision   What the AI changed
─────────   ─────    ────────   ────────────────────────────────────────────
baseline    0.9508   —          Starting point
exp_002     0.9600   KEEP       Strengthened contraction rule with emphasis
exp_005     0.9692   KEEP       Added concrete em-dash before/after example
```

18 of 20 attempts were discarded (score didn't improve enough to pass the noise threshold). The 2 that stuck made targeted, specific changes. Total run time: ~2 hours. Total API cost: <$2.

The full experiment history is in `examples/writing-style/sample-results.tsv`.

![AutoEvaluation dashboard showing score trend and per-metric cards](docs/dashboard.png)

## Quick start

### Prerequisites

- Python 3.10+
- An API key for your preferred LLM provider (Gemini, OpenAI, or Anthropic)

### One command start

```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
cd AutoEvaluation
echo "GEMINI_API_KEY=your-key" > .env
./start.sh
```

`start.sh` handles everything: checks your Python version, creates a virtual environment, installs only the provider SDK you need (not all three), validates your API key, runs setup if needed, and starts the optimisation loop. If anything is wrong, it tells you immediately.

### Try the included example

The repo ships with a complete working example (a writing style guide):

```bash
echo "GEMINI_API_KEY=your-key" > .env
cp examples/writing-style/SKILL.md SKILL.md
cp examples/writing-style/config.yaml config.yaml
cp examples/writing-style/prompts.json prompts/prompts.json
cp examples/writing-style/eval_deterministic.py tools/eval_deterministic.py
./start.sh
```

### Point at your own skill

Already have a skill file you want to optimise? Two options:

**Quick (no prompts, all defaults):**
```bash
echo "GEMINI_API_KEY=your-key" > .env
python3 setup.py --defaults --skill-file /path/to/your/SKILL.md --generate-prompts
./start.sh
```

This validates your API key, uses AI to generate test prompts from your skill file, applies sensible defaults (3 evaluation dimensions, 10 iterations), and you're running.

**Guided (interactive wizard):**
```bash
python3 tools/run_loop.py --skill path/to/your/SKILL.md --provider gemini --iterations 10
```

This auto-generates `config.yaml` with sensible defaults and starts optimising immediately.

### Setup wizard

```bash
python3 setup.py
```

The wizard walks you through:
1. **Provider + model** — pick Gemini, OpenAI, or Anthropic (API key validated instantly)
2. **Your skill** — paste or describe the instructions you want to optimise
3. **Test prompts** — AI generates prompts from your skill description, or enter manually
4. **Eval rubric** — set 2-5 quality dimensions (or use the defaults)
5. **Run duration** — max iterations, max hours, or unlimited

It generates: `config.yaml`, `SKILL.md`, `prompts/prompts.json`, `.env`, and `.claude/settings.json`.

**Skip all prompts:**

```bash
# All defaults: Gemini, default rubric, 5 generic prompts, 10 iterations
python3 setup.py --defaults

# Defaults with a custom skill and AI-generated prompts
python3 setup.py --defaults --skill-file SKILL.md --generate-prompts

# Defaults with OpenAI instead of Gemini
python3 setup.py --defaults --provider openai
```

**Already have a skill file?** Skip the paste step:

```bash
python3 setup.py --skill-file /path/to/your/SKILL.md
python3 setup.py --skill-file SKILL.md --prompts-file my-prompts.json
```

### With Claude Code (autonomous)

If you have [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed, it can drive the optimisation loop autonomously:

```bash
python3 setup.py    # or use --defaults
claude -p program.md
```

Claude reads `program.md`, which contains the loop instructions. It autonomously runs experiments, modifies your skill, and tracks results. All bash commands are auto-approved via `.claude/settings.json`.

### Watch scores in real time

Open another terminal:

```bash
python3 tools/dashboard_server.py
```

Then open http://localhost:8050 in your browser.

---

## How the optimiser thinks

The optimisation loop doesn't just look at score numbers. For each iteration, it:

1. **Reads the judge's reasoning** for the 2 worst-scoring samples. Not "task_accuracy = 0.72" but "the output ignored the instruction to avoid em dashes in paragraph 3."
2. **Reads the actual sample text** that scored poorly, so it can see the concrete failure.
3. **Makes one targeted change** based on that specific failure, not a guess from numbers.
4. **Validates the returned skill** hasn't been truncated or corrupted (checks frontmatter, section headers).
5. **Filters noise**: only keeps changes where the score improvement exceeds a configurable threshold (default 1%), so random variance doesn't pollute the skill.

This means the headless mode (`run_loop.py`) is just as effective as the Claude Code mode. Both see the same signal.

---

## Test prompts

Test prompts are realistic tasks that exercise your skill. Create `prompts/prompts.json`:

```json
[
  {
    "id": "intro_email",
    "genre": "cold outreach",
    "prompt": "Write a 200-word cold email to a VP of Engineering introducing our product."
  },
  {
    "id": "follow_up",
    "genre": "cold outreach",
    "prompt": "Write a 150-word follow-up email after no response to the initial outreach."
  }
]
```

Each prompt needs:
- `id` — short identifier (alphanumeric, underscores, hyphens; auto-sanitised)
- `genre` — category (used for context in evaluation)
- `prompt` — the actual task the LLM will perform using your skill

Aim for 5-10 prompts that cover different aspects of your skill. More prompts = more reliable scores, but each one costs an LLM call per iteration.

---

## BYO model

AutoEvaluation works with any LLM provider. Set your provider in `config.yaml`:

```yaml
# Gemini
provider: gemini
model: gemini-2.5-flash
api_key_env: GEMINI_API_KEY

# OpenAI
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY

# Anthropic
provider: anthropic
model: claude-sonnet-4-20250514
api_key_env: ANTHROPIC_API_KEY
```

Add your API key to `.env`:
```
OPENAI_API_KEY=sk-abc123...
```

To add a custom provider, edit `tools/model_client.py`. It's a single file with an `elif` block per provider.

---

## Run duration

Control how long the loop runs via CLI flags or `config.yaml`:

```bash
python3 tools/run_loop.py --iterations 20
python3 tools/run_loop.py --hours 2.5
```

Or in `config.yaml`:
```yaml
max_iterations: 20    # stop after 20 experiments
max_hours: 2.5        # stop after 2.5 hours
```

If both are set, whichever limit is hit first stops the loop. Set both to `0` for unlimited.

---

## Custom deterministic metrics (advanced)

By default, AutoEvaluation uses LLM-as-judge for all evaluation. If you want rule-based metrics too:

1. Create a custom `tools/eval_deterministic.py` that returns JSON:
   ```python
   {"metric_name": {"score": 0.85, ...}, "another_metric": {"score": 0.92, ...}}
   ```
2. Add them to `config.yaml`:
   ```yaml
   deterministic_metrics:
     - name: metric_name
       weight: 0.15
     - name: another_metric
       weight: 0.10
   ```

See `examples/writing-style/` for a full example with 9 deterministic metrics.

---

## Advanced features

### Separate judge model

By default, the same model generates outputs and evaluates them. This creates self-judging bias (the tool will warn you about this). Use a different model for evaluation:

```yaml
judge_provider: openai
judge_model: gpt-4o
judge_api_key_env: OPENAI_API_KEY
```

If these keys are absent, the primary provider is used as a fallback, with a warning.

### Semi-blind judge

The judge normally evaluates outputs blind. Enable semi-blind mode to give the judge context for the `task_accuracy` dimension only:

```yaml
judge_sees_skill: true
```

Other dimensions (quality, human_score, etc.) are still evaluated blind.

### Noise filtering

The optimiser only keeps changes where the score improvement exceeds a minimum threshold. This prevents random judge variance from polluting the skill.

```yaml
min_improvement: 0.01   # only keep changes with delta > 1% (default)
```

Set to `0` to keep any positive improvement (original behaviour). The convergence window also respects this threshold, so convergence detection actually works with noisy judges.

### Convergence detection

Stop automatically when the optimiser plateaus:

```yaml
convergence_window: 10   # stop after 10 iterations with no improvement above threshold
```

Set to `0` to disable (default).

### Cost capping

Set a budget limit on estimated API spend:

```yaml
max_cost_usd: 5.00   # stop when estimated cost exceeds $5
```

Cost is tracked accurately across all subprocesses (generation, evaluation, and the modifier), not just the modifier. Set to `0` for unlimited (default).

### Parallel execution

Speed up generation and evaluation by running multiple LLM calls concurrently:

```yaml
max_concurrent: 4   # run 4 API calls in parallel
```

Partial failures are handled gracefully. If 1 of 10 calls fails, the other 9 still count. Set to `1` for serial execution (default).

---

## Always-on mode (GitHub Actions)

Want the optimisation to run on a schedule? Copy the included workflow into your repo:

```bash
mkdir -p .github/workflows
cp examples/github-actions/optimise.yml .github/workflows/optimise.yml
```

Then:
1. Push to GitHub
2. Go to **Settings > Secrets > Actions** and add a secret called `LLM_API_KEY` with your API key
3. The workflow runs daily at 2am UTC (or trigger it manually from the Actions tab)

Each run checks out the repo, runs N iterations, and commits the updated `SKILL.md.best` and `results.tsv`.

See `examples/github-actions/README.md` for full setup instructions and schedule customisation.

---

## Example interaction flow

Here's what happens when you run the optimisation loop.

### 1. Baseline

The first run establishes your starting score:

```
[1/3] Generating samples...
  [1/5] Generating: intro_email (cold outreach)... done (187 words, 3.2s)
  ...
[2/3] Running LLM judge evaluation...
[3/3] Aggregating scores...
COMPOSITE SCORE: 0.6420
Note: Using same model for generation and judging. For better signal, set judge_provider in config.yaml.
```

### 2. Optimisation iterations

The loop reads the judge's reasoning, analyses weaknesses, modifies `SKILL.md`, and re-evaluates:

```
Enriched context: 2 worst samples: sample_3_quick_reply, sample_0_intro_email
Analysing weaknesses and modifying skill...
Change: Added "Keep emails under 200 words" rule
Running exp_001...
COMPOSITE SCORE: 0.7185
KEEP — score improved 0.6420 → 0.7185 (delta 0.0765 > threshold 0.01)
```

```
Running exp_002... COMPOSITE SCORE: 0.7340 — KEEP
Running exp_003... COMPOSITE SCORE: 0.7120 — DISCARD (delta 0.0000 below threshold 0.01)
Running exp_004... COMPOSITE SCORE: 0.7510 — KEEP
...
Optimisation complete — 20 iterations in 1.3 hours
Best score: 0.7510
```

### 3. Results

```
============================================================
  RUN COMPLETE
============================================================
  Iterations run:   20
  Time elapsed:     1h 23m 15s
  Cost estimate:    $1.4200
  Tokens used:      2,100,000 in / 890,000 out

  Baseline score:   0.6420
  Best score:       0.7510  (+0.1090)

  Kept changes (4):
  · [exp_001] Added email length constraint
  · [exp_002] Specified greeting format
  · [exp_004] Added concrete example of good vs bad sign-off
  · [exp_012] Restructured rules by priority

  Best skill saved: SKILL.md.best
============================================================
```

---

## Project structure

```
autoevaluation/
├── setup.py                  # Setup wizard (also accepts --skill-file flags)
├── start.sh                  # Zero-friction entry point
├── config.yaml               # All settings (generated by setup.py or --skill flag)
├── config.template.yaml      # Reference config with all options documented
├── program.md                # Loop instructions for Claude Code
├── SKILL.md                  # The skill being optimised (your instructions)
├── SKILL.md.best             # Current best version (auto-managed)
├── results.tsv               # Full experiment history
├── .env                      # API key (git-ignored)
├── .env.example              # Template showing required keys
├── .claude/settings.json     # Auto-approve rules for Claude Code (gitignored)
├── prompts/
│   └── prompts.json          # Test scenarios
├── tools/
│   ├── utils.py              # Shared utilities (config, env loading, validation)
│   ├── model_client.py       # LLM provider abstraction (retry, token tracking, cost)
│   ├── experiment_runner.py  # Orchestrator (one eval cycle)
│   ├── generate_samples.py   # Sample generator (supports parallel)
│   ├── eval_deterministic.py # Rule-based metrics (optional, customisable)
│   ├── eval_llm_judge.py     # LLM-as-judge metrics
│   ├── score_aggregator.py   # Weighted composite scoring
│   ├── run_loop.py           # Standalone loop driver (headless)
│   └── dashboard_server.py   # Live score dashboard
├── tests/
│   └── test_smoke.py         # 68 tests (import, config, judge parsing, aggregation, loop logic)
├── examples/
│   ├── writing-style/        # Full example: anti-AI writing style
│   └── github-actions/       # GitHub Actions workflow (opt-in)
└── .gitignore
```

## Acknowledgment

This project is inspired by [Karpathy's AutoResearch](https://github.com/karpathy/autoresearch), which explores autonomous research workflows. AutoEvaluation borrows the core idea of an autonomous optimisation loop but applies it to a different problem: making LLM instructions measurably better through iterative prompt engineering.
