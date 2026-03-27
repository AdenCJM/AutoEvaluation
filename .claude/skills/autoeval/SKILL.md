---
name: autoeval
description: Run the AutoEvaluation optimisation loop to iteratively improve a SKILL.md file through automated testing and scoring. Trigger when the user types /autoeval, says "run the eval loop", "optimise my skill", "start AutoEvaluation", "improve my prompts", "run a few iterations", or wants to test and refine LLM instructions. Also trigger when the user is in an AutoEvaluation project and asks to start, resume, or check on an optimisation run. Always use this skill rather than trying to run the loop manually.
---

# AutoEvaluation

Runs the interactive optimisation loop for the current project.

## Step 1: Confirm you're in the right place

Check for these files in the current directory:
- `program.md` — loop instructions
- `SKILL.md` — the skill being optimised
- `config.yaml` — project configuration (may not exist yet on first run)

If `program.md` is missing, this isn't an AutoEvaluation project. Tell the user and offer:
> "This doesn't look like an AutoEvaluation project. You can clone one with:
> `git clone https://github.com/AdenCJM/AutoEvaluation.git`
> Then open that folder in Claude Code and run `/autoeval` again."

## Step 2: Verify setup before running

Check these three things:

**config.yaml** — If missing, ask the user what they want to optimise and run `python3 setup.py` on their behalf. The wizard takes 2 minutes and generates the config interactively. If they want to skip prompts: `python3 setup.py --defaults`.

**SKILL.md content** — If it still contains placeholder text ("Replace this file with your skill instructions"), ask the user to describe or paste their skill. You can help them draft it.

**API key** — Read `config.yaml` for `api_key_env` (e.g. `GEMINI_API_KEY`). Check `.env` for that key. If it's missing, ask the user to add it: `echo "GEMINI_API_KEY=your-key" >> .env`.

Once all three are confirmed, proceed.

## Step 3: Run the loop

Read `program.md` in full and follow its instructions exactly. Do not summarise or skip steps.

The loop:
1. Runs a baseline experiment to establish the starting score
2. Analyses the weakest metrics in the results
3. Forms a hypothesis and makes one targeted change to `SKILL.md`
4. Re-evaluates and compares against the best score
5. Keeps improvements, reverts failures
6. Reports after each experiment (see below)
7. Repeats until the limits in `config.yaml` are reached

## Step 4: Report after every experiment

After each experiment completes, output a brief summary before continuing:

```
Iteration N — exp_NNN
Hypothesis: [one sentence]
Change: [what you changed in SKILL.md]
Score: [previous best] → [new score] ([+/-delta])  [✓ KEEP / ✗ DISCARD]
```

If the user sends a message during the run, read it and adjust your next hypothesis accordingly — e.g. "focus on the tone metric", "try removing rules", "stop, that's enough". Otherwise continue autonomously without waiting for input.

## Step 5: Final summary

When the run ends (limits reached or user says stop):

```
Optimisation complete
Ran N iterations in X hours · Best score: Y (was Z, +delta)
Best version saved to SKILL.md.best
```

Offer to show a breakdown of which changes were kept and what each one improved.
