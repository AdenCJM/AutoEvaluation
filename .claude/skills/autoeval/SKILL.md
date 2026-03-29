---
name: autoeval
description: Run the AutoEvaluation optimisation loop to iteratively improve a SKILL.md file through automated testing and scoring. Trigger when the user types /autoeval, says "run the eval loop", "optimise my skill", "start AutoEvaluation", "improve my prompts", "run a few iterations", or wants to test and refine LLM instructions. Also trigger when the user is in an AutoEvaluation project and asks to start, resume, or check on an optimisation run. Always use this skill rather than trying to run the loop manually.
---

# AutoEvaluation

Autonomous skill optimisation through iterative evaluation and hill-climbing.

This skill has **three phases**: interactive setup → dashboard launch → autopilot execution.

---

## Phase 1: Interactive Setup

Before running anything, have a conversation with the user to understand what they want to optimise. **Do not run setup.py** — instead, ask questions directly and generate the config yourself.

### Step 1.1: Check if already configured

Look for `config.yaml` and `results.tsv` in the current directory.

- If **both exist** and `results.tsv` has data rows: use AskUserQuestion:

  Question: "I found an existing run with N experiments. What would you like to do?"
  Header: "Existing run"
  Options:
  - A) Resume — pick up where I left off (Recommended)
  - B) Start fresh — set up a new skill

  If A → skip to Phase 2
  If B → continue with setup questions below

- If **config.yaml exists** but no `results.tsv`: use AskUserQuestion:

  Question: "I found a config for [skill name] but no results yet. How would you like to proceed?"
  Header: "Config found"
  Options:
  - A) Use this config — start running (Recommended)
  - B) Set up from scratch — reconfigure

- If neither exists → continue with setup questions

### Step 1.2: Understand the skill

Use AskUserQuestion:

Question: "Do you have existing skill instructions to paste, or would you like me to draft them from a description?"
Header: "Skill content"
Options:
- A) I'll paste my instructions
- B) Draft them for me

If A → ask the user to paste their skill content.
If B → ask them to describe their skill, draft a SKILL.md, show it to them, then use AskUserQuestion:

  Question: "Does this capture what you want?"
  Header: "Skill draft"
  Options:
  - A) Yes, looks good (Recommended)
  - B) Let me revise it

  If B → ask what to change, update the draft, and repeat.

Extract a short `skill_name` (snake-case, e.g. `writing-style`) and a one-line `skill_description` from the conversation.

### Step 1.3: Define success metrics

Before presenting options, read the skill content and reason about what good output looks like for this specific skill. Then write a short recommendation (2–3 sentences) explaining which 2–3 metrics you'd suggest and why, given what the skill is trying to do. For example:

- A writing-style skill → human-sounding + tone consistency + brevity
- A sales email skill → persuasiveness + task accuracy + tone consistency
- A code review skill → technical accuracy + task accuracy + brevity
- A customer support skill → tone consistency + task accuracy + human-sounding

Show this recommendation in plain text first, then present the question so the user can confirm or deviate:

Use AskUserQuestion with multiSelect:

Question: "Which metrics should I optimise for? (I've suggested a starting point above — you can confirm or pick your own.)"
Header: "Metrics"
multiSelect: true
Options:
- Human-sounding — reads like a human, not obviously AI
- Task accuracy — follows the skill instructions correctly
- Tone consistency — maintains the right tone throughout
- Brevity — concise without losing meaning
- Technical accuracy — facts, code, or technical details are correct
- Persuasiveness — convinces or motivates the reader
- Creativity — original and engaging output
- Custom — I'll describe my own metric

If they choose "Custom", ask:
> "What's the metric called, and what does a score of 1 (worst) vs 5 (best) look like?"

Map their choices to metric objects with name, weight, and rubric. Distribute weights evenly unless they indicate some matter more than others.

### Step 1.4: LLM Provider

Check `.env` in the project root for existing API keys (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY).

- If a key is found: use AskUserQuestion:

  Question: "I found a [Provider] API key in your .env. Which provider should I use?"
  Header: "LLM provider"
  Options:
  - A) Use [Provider] — already configured (Recommended)
  - B) Use a different provider

- If no key is found (or user chose a different provider): use AskUserQuestion:

  Question: "Which LLM provider do you want to use for evaluation?"
  Header: "LLM provider"
  Options:
  - A) Gemini (Google) — cheapest, recommended (Recommended)
  - B) OpenAI (GPT-4o)
  - C) Anthropic (Claude)

After they specify the provider, if the key isn't in `.env`, ask:
> "Paste your [Provider] API key and I'll save it to .env:"

### Step 1.5: Run duration

Use AskUserQuestion:

Question: "How many iterations should I run? You can stop early or extend later."
Header: "Iterations"
Options:
- A) 10 — quick test (5–15 min)
- B) 20–30 — solid optimisation run (30–60 min)
- C) 50+ — deep run (1–2 hours)

### Step 1.6: Generate config files

Now create all the config files. You have two options:

**Option A (preferred):** Call the generate_config.py helper:
```bash
python3 tools/generate_config.py \
  --skill-name "<name>" \
  --skill-description "<description>" \
  --skill-content "<content>" \
  --provider <provider> \
  --api-key "<key>" \
  --metrics '<json_array>' \
  --iterations <N> \
  --generate-prompts
```

**Option B:** If generate_config.py doesn't exist or fails, write the files yourself:
- `config.yaml` — provider, model, api_key_env, metrics, iterations
- `SKILL.md` — the skill with YAML frontmatter
- `prompts/prompts.json` — test prompts (generate 5-8 diverse ones based on the skill)
- `.env` — API key
- `.claude/settings.json` — auto-approve rules

After generating, show the AI-generated test prompts and use AskUserQuestion:

Question: "I've generated these test scenarios for your skill — do they look good?"
Header: "Test prompts"
Options:
- A) Looks good, let's go (Recommended)
- B) I want to tweak them

If B → ask what to change, update the prompts, and confirm again before proceeding.

---

## Phase 2: Dashboard Launch

The dashboard gives the user a live view of progress. Always offer it before starting the loop.

### Step 2.1: Start the dashboard server

Run the dashboard in the background:
```bash
python3 tools/dashboard_server.py --port 8050 &
```

### Step 2.2: Ask about opening the browser

Use AskUserQuestion:

Question: "I've started the live tracking dashboard at http://localhost:8050. Want me to open it?"
Header: "Dashboard"
Options:
- A) Open it now (Recommended)
- B) I'll open it myself

If A:
```bash
open http://localhost:8050
```

### Step 2.3: Brief orientation

Tell the user:

> **Starting the optimisation now. Here's what will happen:**
>
> 1. I'll run a baseline evaluation to establish the starting score
> 2. Then I'll iterate: analyse weaknesses → modify the skill → re-evaluate → keep or revert
> 3. You can watch progress on the dashboard or just check back later
> 4. If you want to steer me (e.g. "focus on tone"), just send a message
>
> I'll run for **N iterations** unless you stop me.
> Starting now...

---

## Phase 3: Autopilot Execution

Run the optimisation loop headless. **Do not pause for input** — just run and let the user watch the dashboard.

### Step 3.1: Run the loop

Execute the headless loop:
```bash
python3 tools/run_loop.py
```

This will:
- Establish a baseline if one doesn't exist
- Iterate through analyse → modify → evaluate → decide cycles
- Print progress to the terminal
- Write status updates that the dashboard reads
- Stop when the configured iteration limit is reached

**Let this run to completion.** Do not interrupt it. The loop handles everything:
- Keeping improvements and reverting failures
- Trying radical changes after 5 consecutive discards
- Writing the final summary

### Step 3.2: Post-run

After `run_loop.py` finishes, read `.tmp/run-summary.md` if it exists and present the results:

> **Optimisation complete!**
>
> - Ran N iterations in X time
> - Score: [baseline] → [best] (+improvement)
> - [N] changes kept
> - Best version saved to `SKILL.md.best`

Use AskUserQuestion:

Question: "What would you like to do next?"
Header: "Post-run"
Options:
- A) Show me what changed — walk through kept improvements
- B) Deploy the best version to ~/.claude/skills/
- C) Run more iterations
- D) I'm done

If B:
```bash
mkdir -p ~/.claude/skills/<skill-name>
cp SKILL.md.best ~/.claude/skills/<skill-name>/SKILL.md
```

---

## Important Rules

- **Never modify files in `tools/` or `prompts/`** during a run
- **Never modify `program.md`** or `config.yaml` during a run
- If any tool errors during setup, read the error and fix it — don't just fail
- The interactive setup replaces `setup.py` — don't shell out to `setup.py` for Claude Code users
- If the user sends a message during Phase 3, note it but don't cancel the running loop
- Always start the dashboard before the loop so the user can track progress from the beginning
