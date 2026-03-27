# AutoEvaluation

**Evals that fix themselves.**

Give it a skill file, a set of test scenarios, and a scoring rubric. It runs autonomously: generate outputs, score them, find the weakest metric, rewrite the skill to fix it, re-score, keep or revert. Hill-climbing on prompt engineering, fully hands-off.

> *A skill file is any plain-text instruction set for an LLM — a writing style guide, a tone of voice document, a system prompt, a coding conventions file. If it tells an AI how to behave, AutoEvaluation can improve it.*

I pointed it at a writing style guide and let it run overnight. It made 20 attempts, kept 2, and improved the composite score from 0.9508 to 0.9692. The changes it made: strengthened contraction rules, added concrete before/after examples for em dash replacement. Every other LLM prompt optimiser (DSPy, TextGrad, MIPRO) requires you to write Python. This one works on plain markdown files.

Point it at any LLM instruction set. Go to bed. Wake up with a measurably better prompt.

---

## How it works

```
┌─────────────────────────────────────────────────────┐
│                  OPTIMISATION LOOP                  │
│                                                     │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐      │
│   │ Analyse  │───▶│  Modify  │───▶│ Evaluate │      │
│   │ weakness │    │ SKILL.md │    │ samples  │      │
│   └──────────┘    └──────────┘    └──────────┘      │
│        ▲                               │            │
│        │          ┌──────────┐         │            │
│        └──────────│  Decide  │◀────────┘            │
│                   │ keep/rev │                      │
│                   └──────────┘                      │
└─────────────────────────────────────────────────────┘
```

1. **Analyse** — reads the weakest metrics from the last run
2. **Modify** — makes ONE targeted change to the skill instructions
3. **Evaluate** — generates outputs using the modified skill, scores them against your rubric
4. **Decide** — if the score improved, keep the change; otherwise revert
5. **Repeat** — until the iteration/time limit is hit (or indefinitely)

### Real results

I ran AutoEvaluation on an anti-AI writing style guide (the included example) for 20 iterations using Gemini 2.5 Flash:

```
Iteration   Score    Decision   What the AI changed
─────────   ─────    ────────   ────────────────────────────────────────────
baseline    0.9508   —          Starting point
exp_002     0.9600   KEEP       Strengthened contraction rule with emphasis
exp_005     0.9692   KEEP       Added concrete em-dash before/after example
```

18 of 20 attempts were discarded (score didn't improve). The 2 that stuck made targeted, specific changes. Total run time: ~2 hours. Total API cost: <$2.

The full experiment history is in `examples/writing-style/sample-results.tsv`.

![AutoEvaluation dashboard showing score trend and per-metric cards](docs/dashboard.png)

---

## Quick start

**Prerequisites:** Python 3.10+ and an API key for Gemini, OpenAI, or Anthropic.

### Option 1 — Claude Code (recommended)

No Python setup needed. Claude Code handles everything conversationally.

```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
cd AutoEvaluation
```

Open the project in Claude Code — either open the folder in VS Code with the Claude Code extension, or run `claude` in the project directory. Then type `/autoeval` or just describe what you want:

> `/autoeval`
> *"I want to optimise my writing style guide"*
> *"Help me set up AutoEvaluation for my email templates"*
> *"Run the optimisation loop"*

Claude walks through setup if anything is missing, runs the experiments, and reports each iteration directly in the chat:

```
Iteration 3 — exp_003
Hypothesis: task_accuracy is low because the skill doesn't constrain email length
Change: Added "Keep emails under 200 words" rule
Score: 0.6420 → 0.7185 (+0.0765)  ✓ KEEP
```

You can steer mid-run at any point — "focus on the tone metric", "try removing rules instead of adding them", or "that's good enough, stop" — and Claude adjusts.

**Try the included writing style example:**

> *"Copy the writing style example files and run the optimisation loop"*

**Already have a skill file?**

> *"Here's my skill file — [paste contents or give the path] — optimise it"*

Claude generates test prompts, configures the rubric, and starts the loop.

---

### Option 2 — Headless / terminal

```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
cd AutoEvaluation
echo "GEMINI_API_KEY=your-key" > .env
python3 setup.py          # interactive setup wizard
python3 tools/run_loop.py
```

> **Using OpenAI or Anthropic instead?** Set the matching key in `.env` (e.g. `OPENAI_API_KEY=your-key`) and select your provider when `setup.py` asks. See [BYO model](#byo-model).

**Try the included writing style example:**

```bash
cp examples/writing-style/SKILL.md SKILL.md
cp examples/writing-style/config.yaml config.yaml
cp examples/writing-style/prompts.json prompts/prompts.json
cp examples/writing-style/eval_deterministic.py tools/eval_deterministic.py
# Edit .env with your API key, then update config.yaml if not using Gemini
python3 tools/run_loop.py
```

**Limit the run:**

```bash
python3 tools/run_loop.py --iterations 10    # stop after 10 experiments
python3 tools/run_loop.py --hours 2.5        # stop after 2.5 hours
```

**Watch scores in real time** — open a second terminal:

```bash
python3 tools/dashboard_server.py
# then open http://localhost:8050
```

---

## What happens during a run

### 1. Baseline

The first run establishes your starting score before any changes are made:

```
[1/3] Generating samples...
  [1/5] intro_email (cold outreach)... done (187 words, 3.2s)
  ...
[2/3] Running LLM judge evaluation...
[3/3] Aggregating scores...
COMPOSITE SCORE: 0.6420
```

### 2. Optimisation iterations

The loop analyses weaknesses, modifies `SKILL.md`, and re-evaluates. Changes that improve the score are kept; the rest are reverted:

```
exp_001  COMPOSITE SCORE: 0.7185  KEEP    (added email length constraint)
exp_002  COMPOSITE SCORE: 0.7340  KEEP
exp_003  COMPOSITE SCORE: 0.7120  DISCARD (reverted)
exp_004  COMPOSITE SCORE: 0.7510  KEEP
...
```

### 3. End-of-run summary

When the run finishes, a summary is printed to the terminal and saved to `.tmp/run-summary.md`:

```
============================================================
  RUN COMPLETE
============================================================
  Iterations run:   10
  Time elapsed:     18m 42s
  Cost estimate:    $0.04

  Baseline score:   0.6420
  Best score:       0.7510  (+0.1090)

  Kept changes (3):
  · [exp_001] Added email length constraint
  · [exp_002] Strengthened tone instruction with example
  · [exp_004] Moved most-violated rules to top

  Best skill saved: SKILL.md.best
  To deploy:        cp SKILL.md.best ~/.claude/skills/my-skill/SKILL.md
============================================================
```

`SKILL.md.best` always holds the highest-scoring version found during the run. `SKILL.md` is also updated to match it.

### 4. Deploying the result

Once the run is done, you need to put the optimised skill somewhere it will be used.

**With Claude Code:** Claude automatically offers to deploy at the end of the run. It reads the skill name from the frontmatter and asks:

> *"Deploy the optimised skill to `~/.claude/skills/writing-style/SKILL.md`? This replaces the version Claude Code uses globally."*

Confirm and it copies the file. Claude Code picks it up immediately — no restart needed.

**Headless:** The summary prints the exact `cp` command to run. Copy and paste it:

```bash
cp SKILL.md.best ~/.claude/skills/writing-style/SKILL.md
```

If the skill doesn't exist yet in your skills directory, create it first:

```bash
mkdir -p ~/.claude/skills/my-skill
cp SKILL.md.best ~/.claude/skills/my-skill/SKILL.md
```

---

## Setting up your skill

### The skill file

`SKILL.md` is the file being optimised. It uses YAML frontmatter to identify itself:

```markdown
---
name: my-skill
description: One sentence describing when to use this skill.
---

# My Skill Rules

Your instructions go here...
```

The `name` field is used at the end of the run to find the right place to deploy the result. It should match the folder name under `~/.claude/skills/`.

### Test prompts

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
- `id` — short identifier (used in filenames)
- `genre` — category (used for context in evaluation)
- `prompt` — the actual task the LLM performs using your skill

Aim for 5–10 prompts covering different aspects of your skill. More prompts give more reliable scores, but each one costs an LLM call per iteration.

### Scoring rubric

The rubric is defined in `config.yaml`. Each dimension has a name, weight, direction, and description for the LLM judge:

```yaml
llm_judge_dimensions:
  - name: task_accuracy
    weight: 0.40
    direction: higher_is_better
    rubric: "Does the output correctly follow the skill instructions? 1=ignores them, 5=perfect adherence."
  - name: quality
    weight: 0.30
    direction: higher_is_better
    rubric: "Is this high-quality output overall? 1=poor, 5=excellent."
  - name: human_score
    weight: 0.30
    direction: higher_is_better
    rubric: "Does this read like a competent human wrote it? 1=obviously AI, 5=indistinguishable."
```

Weights must sum to 1.0 (auto-normalised with a warning if not).

---

## Configuration

All settings live in `config.yaml`. Run `python3 setup.py` to generate it interactively, or create it manually using `config.template.yaml` as a reference.

### BYO model

AutoEvaluation works with Gemini, OpenAI, and Anthropic. Set your provider in `config.yaml`:

```yaml
# Gemini (default)
provider: gemini
model: gemini-2.5-flash
api_key_env: GEMINI_API_KEY

# OpenAI
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY

# Anthropic
provider: anthropic
model: claude-sonnet-4-5
api_key_env: ANTHROPIC_API_KEY
```

Add your key to `.env`:

```
OPENAI_API_KEY=sk-abc123...
```

To add a custom provider, edit `tools/model_client.py` — it's a single file with an `elif` block per provider.

### Run limits

Control how long the loop runs:

```yaml
max_iterations: 20    # stop after 20 experiments (0 = unlimited)
max_hours: 2.5        # stop after 2.5 hours (0 = unlimited)
max_cost_usd: 5.00    # stop when estimated spend exceeds $5 (0 = unlimited)
convergence_window: 10  # stop after 10 iterations with no improvement (0 = disabled)
```

If multiple limits are set, whichever is hit first stops the loop.

---

## Advanced features

### Separate judge model

By default, the same model generates outputs and evaluates them — which creates self-judging bias. Use a different model for evaluation:

```yaml
judge_provider: openai
judge_model: gpt-4o
judge_api_key_env: OPENAI_API_KEY
```

If these keys are absent, the primary provider is used as fallback.

### Semi-blind judge

The judge normally evaluates outputs blind — it doesn't see your `SKILL.md`. Enable semi-blind mode to give the judge context for the `task_accuracy` dimension only:

```yaml
judge_sees_skill: true
```

Other dimensions (quality, human_score, etc.) stay blind.

### Custom deterministic metrics

By default, AutoEvaluation uses LLM-as-judge for all evaluation. For rule-based metrics (e.g. word count, banned phrase detection, regex checks):

1. Edit `tools/eval_deterministic.py` to return JSON:
   ```python
   {"word_count": {"score": 0.85}, "banned_phrases": {"score": 0.92}}
   ```
2. Add the metrics to `config.yaml`:
   ```yaml
   deterministic_metrics:
     - name: word_count
       weight: 0.15
     - name: banned_phrases
       weight: 0.10
   ```

See `examples/writing-style/` for a full example with 9 deterministic metrics.

### Parallel execution

Speed up generation and evaluation by running multiple LLM calls concurrently:

```yaml
max_concurrent: 4   # run 4 API calls in parallel (default: 1)
```

Partial failures are handled gracefully — if 1 of 10 calls fails, the other 9 still count.

---

## Always-on mode (GitHub Actions)

Run the optimisation on a schedule without keeping a terminal open:

```bash
mkdir -p .github/workflows
cp examples/github-actions/optimise.yml .github/workflows/optimise.yml
```

Then:
1. Push to GitHub
2. Go to **Settings → Secrets → Actions** and add a secret called `LLM_API_KEY`
3. The workflow runs daily at 2am UTC, or trigger it manually from the Actions tab

Each run checks out the repo, runs N iterations, and commits the updated `SKILL.md.best` and `results.tsv`.

See `examples/github-actions/README.md` for full setup and schedule customisation.

---

## Project structure

```
autoevaluation/
├── setup.py                  # Interactive setup wizard
├── config.yaml               # All settings (generated by setup.py)
├── config.template.yaml      # Reference config with all options documented
├── program.md                # Loop instructions (read by Claude Code)
├── SKILL.md                  # The skill being optimised
├── SKILL.md.best             # Highest-scoring version found (auto-managed)
├── results.tsv               # Full experiment history (append-only)
├── .env                      # API keys (git-ignored)
├── .claude/
│   └── skills/autoeval/      # /autoeval slash command (auto-installed)
├── prompts/
│   └── prompts.json          # Test scenarios
├── tools/
│   ├── utils.py              # Shared utilities (config loading, validation)
│   ├── model_client.py       # LLM provider abstraction (retry, token tracking)
│   ├── experiment_runner.py  # Orchestrator: one full eval cycle
│   ├── generate_samples.py   # Sample generator
│   ├── eval_deterministic.py # Rule-based metrics (optional, customisable)
│   ├── eval_llm_judge.py     # LLM-as-judge scoring
│   ├── score_aggregator.py   # Weighted composite scoring
│   ├── run_loop.py           # Headless loop driver
│   └── dashboard_server.py   # Live score dashboard (localhost:8050)
├── examples/
│   ├── writing-style/        # Complete example: anti-AI writing style
│   └── github-actions/       # GitHub Actions workflow (opt-in)
└── .gitignore
```

---

## Acknowledgment

This project is inspired by [Karpathy's AutoResearch](https://github.com/karpathy/autoresearch), which explores autonomous research workflows. AutoEvaluation borrows the core idea of an autonomous optimisation loop but applies it to prompt engineering: making LLM instructions measurably better through iterative hill-climbing. It doesn't implement or extend AutoResearch's original scope — it's a separate tool that took the concept and ran with it in a new direction.
