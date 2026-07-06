# Troubleshooting Guide

This guide addresses common issues when setting up and running AutoEvaluation.

## Entry Points: Which Path Should I Take?

The README shows three entry points (`start.sh`, `setup.py`, `run_loop.py`). This decision tree helps you pick the right one.

```
Do you have Claude Code installed?
├─ YES → Do you want autonomous hands-off optimisation?
│        ├─ YES → Use: claude -p program.md
│        │         (Skip to "Claude Code Path" below if it doesn't work)
│        └─ NO  → Use: python3 tools/run_loop.py
│
└─ NO  → Use: python3 tools/run_loop.py
```

### Claude Code Path Issues

**Issue:** Running `claude -p program.md` prints the file contents but doesn't start optimisation.

```bash
$ claude -p program.md
# Skill Optimisation Loop
# You are running an autonomous optimisation loop...
$ (returns to prompt, nothing happened)
```

**Cause:** In some Claude Code versions, `-p` prints the file instead of executing it as instructions.

**Fix:** Use the headless path instead:

```bash
python3 tools/run_loop.py --iterations 20
```

Or try the full command with the assistant system message:

```bash
claude --system "You are running an autonomous optimisation loop." -p program.md
```

**Check your version:**

```bash
claude --version
```

If you're on an older version of Claude Code, update it:

```bash
brew upgrade anthropic-claude
# or
npm install -g @anthropic-ai/claude
```

### Headless Path (Recommended)

The headless path **always works**:

```bash
# Make sure your venv is activated and config.yaml exists
python3 tools/run_loop.py --iterations 10
```

This is the most reliable entry point. It doesn't depend on Claude Code version or integration; it's a standard Python script.

---

## Configuration Issues

### Issue: "API key not found" or authentication errors

**Symptom:**

```
Error: GEMINI_API_KEY not found in environment
```

**Causes & fixes:**

1. **`.env` file doesn't exist or is empty**

   ```bash
   # Create .env in the project root:
   echo "GEMINI_API_KEY=your-actual-key" > .env
   ```

   The `.env` file is `.gitignore`d, so it's safe to store your key there.

2. **The key is in `.env` but not being loaded**

   If you just created `.env`, open a new terminal tab or run:

   ```bash
   source .env
   ```

3. **API key is invalid or expired**

   Verify the key is correct by testing the provider's CLI directly:

   ```bash
   # For Gemini:
   gcloud auth application-default print-access-token

   # For OpenAI:
   curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"

   # For Anthropic:
   curl https://api.anthropic.com/v1/messages -H "x-api-key: $ANTHROPIC_API_KEY" -X GET
   ```

---

### Issue: Example config hardcodes Gemini, but I want OpenAI

**Symptom:** You copied `examples/writing-style/config.yaml` but you have an OpenAI key, and the loop tries Gemini.

```yaml
provider: gemini  # ← The example hardcodes this
model: gemini-2.5-flash
api_key_env: GEMINI_API_KEY
```

**Fix:** Edit `config.yaml` to match your provider:

```yaml
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY
```

Then add your key to `.env`:

```bash
echo "OPENAI_API_KEY=sk-..." >> .env
```

**Or:** Run the setup wizard to generate a config that matches your provider:

```bash
python3 setup.py
```

---

### Issue: `config.yaml` missing

**Symptom:**

```
Error: config.yaml not found. Run `python3 setup.py` first.
```

**Cause:** You cloned the repo but haven't created a config.

**Fix:** Generate one interactively:

```bash
python3 setup.py
```

Or with defaults:

```bash
python3 setup.py --defaults
```

Both will generate `config.yaml`, `SKILL.md`, `prompts/prompts.json`, and `.env`.

---

## Skill File Issues

### Issue: `SKILL.md` is empty or has placeholder text

**Symptom:** You ran the optimisation but SKILL.md still says:

```
# [Your Skill Name]

[description here]
```

**Cause:** Setup wizard asks for your skill, but you didn't provide one.

**Fix:** Paste or describe your skill:

```bash
python3 setup.py --skill-file /path/to/your/skill.md
```

Or edit `SKILL.md` directly and add your actual instructions.

---

### Issue: "SKILL.md has been truncated" or validation fails

**Symptom:**

```
Warning: SKILL.md may have been corrupted during modification. Frontmatter missing.
```

**Cause:** The modifier LLM accidentally deleted the YAML frontmatter (the `---` header block).

**Fix:** Restore from backup:

```bash
cp SKILL.md.best SKILL.md
```

Then run again. The system will re-validate and should protect the frontmatter this time.

If this keeps happening, check `config.yaml` for memory limits (`max_tokens`). Setting it too low may force the LLM to truncate output.

---

## Evaluation Issues

### Issue: Scores are all 1.0 or all 5.0 (not varying)

**Symptom:**

```
Baseline score: 5.0000 (all metrics maxed out)
Every iteration: 5.0000
```

**Cause:** Your test prompts or rubric aren't challenging enough. The LLM is giving perfect scores to everything.

**Fix:** Make your test prompts and rubric more discriminating:

1. **Check your test prompts** (`prompts/prompts.json`):
   - Are they diverse enough to reveal weaknesses?
   - Are they specific to your skill's requirements?
   
   Example of weak prompts:
   ```json
   {"id": "task1", "prompt": "Write something."}
   ```
   
   Example of better prompts:
   ```json
   {"id": "task1", "prompt": "Write a 150-word cold email to a VP of Engineering. Avoid em dashes."}
   ```

2. **Check your rubric** (`config.yaml`, `llm_judge_dimensions`):
   - Are your criteria clear and specific?
   - Do they reveal failures?
   
   Example of weak rubric:
   ```yaml
   rubric: "Is this good?"  # Too vague!
   ```
   
   Example of better rubric:
   ```yaml
   rubric: |
     Does the output follow the contraction rules? 
     Check for "do not" instead of "don't", etc.
     1 = ignores contractions entirely
     5 = perfect contraction adherence
   ```

3. **Run a few manual tests**:
   ```bash
   python3 tools/generate_samples.py --limit 1
   ```
   
   Read a sample and ask: "Does this actually follow my skill instructions?" If the answer is "yes, obviously," your rubric isn't strict enough.

---

### Issue: Scores are very noisy (huge variance between iterations)

**Symptom:**

```
Iteration 1: 0.6420
Iteration 2: 0.7890  (huge jump)
Iteration 3: 0.5103  (huge drop)
Iteration 4: 0.8234  (huge jump)
```

**Cause:** Your LLM judge is inconsistent. This is common with cheaper models on subjective metrics.

**Fix:**

1. **Use a more consistent judge model**:
   ```yaml
   judge_provider: openai
   judge_model: gpt-4  # Pricier but more consistent
   judge_api_key_env: OPENAI_API_KEY
   ```

2. **Increase the `min_improvement` threshold** to filter noise:
   ```yaml
   min_improvement: 0.05  # Only keep changes with 5%+ improvement (instead of default 1%)
   ```

3. **Increase the number of test prompts** to average out variance:
   ```json
   // In prompts/prompts.json, add more prompts (target: 8-10)
   ```

4. **Make your rubrics more objective**:
   - Instead of "Is this high quality?" → "Does it follow these 3 specific rules?"
   - Objective criteria are easier for LLMs to judge consistently.

---

## Results & Output Issues

### Issue: `results.tsv` has no data or only baseline

**Symptom:**

```
$ cat results.tsv
run_id  composite_score  decision
baseline  0.6420        BASELINE
```

**Cause:** The loop ran the baseline but no iterations.

**Possible reasons:**

1. **Check iteration limit**:
   ```yaml
   # In config.yaml:
   max_iterations: 0  # This means unlimited, so it should keep going
   ```

2. **Check time limit**:
   ```yaml
   max_hours: 0  # Unlimited time
   ```

3. **Check cost limit**:
   ```yaml
   max_cost_usd: 0  # Unlimited cost
   ```

4. **Check convergence window**:
   ```yaml
   convergence_window: 0  # 0 means disabled, so it shouldn't stop early
   ```

If all limits are 0, the loop should run indefinitely. If it stopped after baseline, check the console output for errors.

**Fix:** Re-run with explicit iteration limit:

```bash
python3 tools/run_loop.py --iterations 5
```

---

### Issue: Experiments complete but `SKILL.md.best` isn't updated

**Symptom:**

```
Iteration 1: 0.7500 → 0.7850 (+0.0350)  KEEP
# But SKILL.md.best hasn't changed
```

**Cause:** The decision was KEEP, but the file copy didn't happen.

**Fix:** Manually sync:

```bash
cp SKILL.md SKILL.md.best
```

This shouldn't happen in normal operation. If it does repeatedly, check file permissions:

```bash
ls -la SKILL.md SKILL.md.best
```

---

### Issue: Dashboard won't start

**Symptom:**

```
$ python3 tools/dashboard_server.py
Error: Module 'dash' not found
```

**Cause:** Dashboard dependencies aren't installed.

**Fix:**

```bash
pip install dash plotly pandas
python3 tools/dashboard_server.py
```

The dashboard is optional; if you don't need it, ignore this error.

---

## Performance Issues

### Issue: Loop is very slow (1 minute per iteration)

**Symptom:**

```
[1/3] Generating samples...
  [1/5] Generating: intro_email... (wait 20-30 seconds per sample)
```

**Cause:** You're using a slow model or hitting rate limits.

**Options:**

1. **Switch to a faster model**:
   ```yaml
   model: gemini-2.5-flash  # Faster and cheaper than gpt-4o
   ```

2. **Reduce test prompts** (temporarily, for testing):
   ```bash
   # In prompts/prompts.json, keep only 3-4 critical prompts
   ```

3. **Check API rate limits**:
   - Gemini: 60 requests per minute (free tier)
   - OpenAI: varies by plan
   - Anthropic: varies by plan
   
   If you're hitting limits, wait or upgrade your plan.

---

### Issue: Out of memory or timeout errors

**Symptom:**

```
Error: Timeout: experiment_runner.py took > 300 seconds
```

**Cause:** One evaluation is taking too long (bad prompts, overloaded API, etc.).

**Fix:**

1. **Increase timeout** in `tools/experiment_runner.py`:
   ```python
   timeout=600  # 10 minutes instead of 5
   ```

2. **Reduce `max_concurrent`** to run fewer parallel calls:
   ```yaml
   max_concurrent: 1  # Serial instead of parallel
   ```

3. **Check your test prompts**: Are they very long? Reduce prompt length.

---

## Resuming Interrupted Runs

### Issue: "I stopped the loop mid-run — can I resume?"

**Answer:** Yes, but not automatically.

The loop is **idempotent by iteration number**: if you run `exp_001` twice, it overwrites the first result. So:

1. **Check `results.tsv`** to see which iterations completed:
   ```bash
   tail results.tsv
   ```

2. **Figure out the next iteration number**, e.g., if the last line is `exp_005`, the next is `exp_006`.

3. **Resume manually**:
   ```bash
   python3 tools/run_loop.py --iterations 20  # Run another 15 iterations (up to 20 total)
   ```

The loop reads `results.tsv`, finds the best score, loads `SKILL.md.best`, and continues from there.

---

## Getting Help

If you're stuck:

1. **Check the logs** in `.tmp/`:
   ```bash
   tail -100 .tmp/experiment_runner.log
   ```

2. **Enable debug mode** (if supported by your version):
   ```bash
   DEBUG=1 python3 tools/run_loop.py --iterations 1
   ```

3. **Verify your API key** by testing the provider's CLI directly (see Configuration Issues above).

4. **Re-run setup wizard** to regenerate config from scratch:
   ```bash
   rm config.yaml .env
   python3 setup.py
   ```

5. **Check the issue tracker** on GitHub for similar problems.
