# Getting Started with AutoEvaluation

This tutorial walks you through AutoEvaluation from scratch: clone → setup → first run → reading results. You'll have a working optimisation loop in about 15 minutes.

## What You'll Build

By the end, you'll have:
- A local clone of AutoEvaluation
- A configured API key (Gemini, OpenAI, or Anthropic)
- Your first optimisation loop running
- A baseline score and at least one improved iteration

---

## Step 1: Clone the Repository

Get the code on your machine:

```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
cd AutoEvaluation
```

This creates a directory with everything you need: the loop orchestrator, config templates, and example skills.

---

## Step 2: Get an API Key

AutoEvaluation needs an LLM API key. Pick one:

**Option A: Gemini (Recommended for beginners)**

- Go to [Google AI Studio](https://aistudio.google.com/apikey)
- Click "Create API Key"
- Copy the key

**Option B: OpenAI**

- Go to [OpenAI API Keys](https://platform.openai.com/api/keys)
- Click "Create new secret key"
- Copy the key

**Option C: Anthropic**

- Go to [Anthropic Console](https://console.anthropic.com/account/keys)
- Click "Create Key"
- Copy the key

For this tutorial, we'll use Gemini. If you chose a different provider, substitute the model name and key below.

---

## Step 3: Create `.env` with Your API Key

In your `AutoEvaluation` directory, create a `.env` file:

```bash
# Create the file:
echo "GEMINI_API_KEY=your-actual-key-here" > .env
```

Replace `your-actual-key-here` with the key you just copied. Don't put quotes around the key.

Verify it was created:

```bash
cat .env
# Should print: GEMINI_API_KEY=AIza...
```

**Important:** `.env` is in `.gitignore`, so it won't accidentally be committed to GitHub.

---

## Step 4: Copy the Example Skill

AutoEvaluation ships with a complete working example: a writing style guide. Let's use it:

```bash
# Copy the example files:
cp examples/writing-style/SKILL.md SKILL.md
cp examples/writing-style/config.yaml config.yaml
cp examples/writing-style/prompts.json prompts/prompts.json
cp examples/writing-style/eval_deterministic.py tools/eval_deterministic.py
```

This gives you:
- **SKILL.md** — the writing style guide you'll optimise
- **config.yaml** — settings (model, evaluation rubric, iteration limits)
- **prompts.json** — 5 test scenarios (writing tasks the system will evaluate)
- **eval_deterministic.py** — rule-based evaluation metrics (e.g., checking for banned words)

Let's verify they're in place:

```bash
ls -la SKILL.md config.yaml prompts/prompts.json
# Should all exist
```

---

## Step 5: Run Your First Optimisation

Now you're ready to optimise! Run the headless loop:

```bash
python3 tools/run_loop.py --iterations 3
```

This command:
- Generates test outputs using your current SKILL.md
- Judges each output (LLM evaluates them blind)
- Calculates a baseline score
- Runs 3 iterations of analysis → modify → evaluate → decide
- Saves results to `results.tsv`

**Expected output:**

```
Loading config from config.yaml...
SKILL_PATH: SKILL.md
RESULTS_TSV: results.tsv

[1/3] Generating samples...
  [1/5] Generating: writing_tone (task: write a paragraph explaining...)...done (184 words, 3.2s)
  [2/5] Generating: use_contractions (task: write a short guide on using contractions)...done (156 words, 2.8s)
  ...
[2/3] Running LLM judge evaluation...
[3/3] Aggregating scores...

═══════════════════════════════════════════════════════════
COMPOSITE SCORE: 0.8358
═══════════════════════════════════════════════════════════
  human_score: 0.88
  task_accuracy: 0.82
  quality: 0.81

Baseline saved.

═══════════════════════════════════════════════════════════
Running exp_001...

Enriched context: 2 worst samples: writing_tone, use_contractions
Analysing weaknesses and modifying skill...
Change: Added emphasis on natural language patterns in contractions
Running exp_001...
COMPOSITE SCORE: 0.7768

✗ DISCARD — score did not improve (0.7768 < best 0.8358)

═══════════════════════════════════════════════════════════
Running exp_002...

Enriched context: 2 worst samples: writing_tone, use_contractions
Analysing weaknesses and modifying skill...
Change: Clarified the guidance on parallel structures
Running exp_002...
COMPOSITE SCORE: 0.8597

✓ KEEP — score improved 0.8358 → 0.8597 (delta 0.0239)

═══════════════════════════════════════════════════════════
Running exp_003...

...

═══════════════════════════════════════════════════════════
Optimisation complete — 3 iterations in 4m 12s
Best score: 0.8597 (+0.0239 from baseline)
Kept changes: 1
Results saved to results.tsv
═══════════════════════════════════════════════════════════
```

That's it! Your first optimisation loop completed. The system ran 3 experiments, kept 1 change, and improved your skill's score by +2.39%.

---

## Step 6: Review the Results

Let's see what happened:

**Check the full result history:**

```bash
cat results.tsv
```

Output will show:

```
run_id      composite_score  decision
baseline    0.8358           BASELINE
exp_001     0.7768           DISCARD
exp_002     0.8597           KEEP
exp_003     0.8400           DISCARD
```

- **baseline**: Your starting score (0.8358)
- **exp_001**: Tried a change, score dropped → DISCARD
- **exp_002**: Tried a different change, score improved → KEEP
- **exp_003**: Tried another change, score stayed similar → DISCARD

**Check which change was kept:**

```bash
git diff SKILL.md.best SKILL.md
```

Wait, both files are the same now? That's expected! When you KEEP a change, the system stores it in `SKILL.md.best`. After each experiment, `SKILL.md` either stays (if KEEP) or reverts (if DISCARD).

**Read the best skill:**

```bash
cat SKILL.md.best
```

This is your optimised skill — the best version found so far. It has the kept changes from exp_002.

---

## Step 7: Run More Iterations (Optional)

Want to keep optimising? Run more iterations:

```bash
python3 tools/run_loop.py --iterations 10
```

The loop will:
- Read the best score from `results.tsv` (currently 0.8597)
- Load `SKILL.md.best` as the starting point
- Run 7 more iterations (10 total, 3 already done)
- Keep trying to improve the score

---

## What Just Happened?

Here's the flow your first run executed:

1. **Baseline** — evaluated your SKILL.md as-is
2. **Iteration 1 (exp_001)** — read the judge's reasoning, identified weak areas, made one targeted change
3. **Evaluate** — scored the modified skill
4. **Decide** — score didn't improve → reverted to baseline
5. **Repeat** for iterations 2–3

The system is **hill-climbing**: each iteration tries one change, keeps it if the score improves, and reverts if not.

---

## Next Steps

### Run with Your Own Skill

Want to optimise your own instructions? 

```bash
# Option 1: Replace SKILL.md
cp /path/to/your/skill.md SKILL.md

# Option 2: Use the setup wizard
python3 setup.py
```

The setup wizard will ask you to:
1. Pick your LLM provider
2. Paste or describe your skill
3. Enter test scenarios (or let AI generate them)
4. Choose evaluation metrics
5. Set iteration limits

### Configure Advanced Features

Open `config.yaml` and try:

- **`max_concurrent: 4`** — run 4 LLM calls in parallel (faster but costs more)
- **`judge_provider: openai`** — use a different model as the judge (better signal, but requires 2 API keys)
- **`min_improvement: 0.05`** — only keep changes with 5%+ improvement (filters noise)
- **`convergence_window: 10`** — stop after 10 iterations with no improvement (saves API cost)

See the [Configuration Guide](docs/CONFIG_REFERENCE.md) for details.

### Watch the Dashboard (Optional)

In a separate terminal:

```bash
python3 tools/dashboard_server.py
```

Then open http://localhost:8050 in your browser to watch scores in real-time.

### Use Claude Code for Autonomous Mode

If you have [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed:

```bash
claude -p program.md
```

Claude will read `program.md` (the loop instructions) and run the optimisation autonomously. The loop will modify `SKILL.md`, track results, and report progress.

If that doesn't work, see [Troubleshooting: Claude Code Path Issues](docs/TROUBLESHOOTING.md#claude-code-path-issues).

---

## You've Built This

You now have:

✅ A cloned repo with working tools  
✅ An API key configured and tested  
✅ A baseline score for your skill  
✅ At least one experiment that improved the score  
✅ A `results.tsv` history you can inspect  
✅ A best skill (`SKILL.md.best`) you can use  

**From here:**

- **Keep optimising** → Run more iterations until the score plateaus
- **Switch to your own skill** → Repeat with your own instructions
- **Read the architecture** → See [Architecture & Design](docs/ARCHITECTURE.md) to understand why things work this way
- **Troubleshoot issues** → Check [Troubleshooting Guide](docs/TROUBLESHOOTING.md) if something breaks
- **Explore advanced features** → See README.md for parallel execution, custom metrics, GitHub Actions, etc.

Good luck! 🚀
