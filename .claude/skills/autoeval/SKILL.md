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

- If **both exist** and `results.tsv` has data rows: ask the user
  > "I found an existing run with N experiments. Want me to **resume** where you left off, or **start fresh** with a new skill?"
  - If resume → skip to Phase 2
  - If start fresh → continue with setup questions below

- If **config.yaml exists** but no `results.tsv`: ask
  > "I found a config for [skill name] but no results yet. Want me to **use this config**, or **set up from scratch**?"

- If neither exists → continue with setup questions

### Step 1.2: Understand the skill

Ask the user:

> **What skill do you want to improve?**
>
> This is a set of instructions that tells an LLM how to behave. For example:
> - Writing style rules for blog posts
> - Sales email tone and structure
> - Code review feedback guidelines
> - Customer support response templates
> - Technical documentation standards

Wait for their response. Based on what they say, follow up:

> **Can you paste your skill instructions, or describe what you want and I'll draft them for you?**

If they paste content, use it directly. If they describe it, draft a SKILL.md for them and show it, asking "Does this capture what you want?"

Extract a short `skill_name` (snake-case, e.g. `writing-style`) and a one-line `skill_description` from the conversation.

### Step 1.3: Define success metrics

Ask the user:

> **What observable, quantifiable metrics matter most for this skill?**
>
> Here are common ones — pick 2-4, or describe your own:
>
> 1. **Human-sounding** — Does the output read like a human wrote it? (vs obviously AI-generated)
> 2. **Task accuracy** — Does it follow the skill instructions correctly?
> 3. **Tone consistency** — Does it maintain the right tone throughout?
> 4. **Brevity** — Is it concise without losing meaning?
> 5. **Technical accuracy** — Are facts, code, or technical details correct?
> 6. **Persuasiveness** — Does it convince or motivate the reader?
> 7. **Creativity** — Is the output original and engaging?
> 8. **Custom** — Describe your own metric and what 1 (worst) vs 5 (best) looks like
>
> Just list the numbers or names, e.g. "1, 2, and 4" or "human-sounding, accuracy, and brevity"

Wait for their response. Map their choices to metric objects with name, weight, and rubric. Distribute weights evenly unless they indicate some matter more than others.

If they choose "Custom", ask:
> "What's the metric called, and what does a score of 1 (worst) vs 5 (best) look like?"

### Step 1.4: LLM Provider

Check `.env` in the project root for existing API keys (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY).

- If a key is found: ask
  > "I found a [Provider] API key in your .env. Want me to use that, or a different provider?"

- If no key is found: ask
  > "Which LLM provider do you want to use for evaluation?
  > 1. **Gemini** (Google) — cheapest, recommended
  > 2. **OpenAI** (GPT-4o)
  > 3. **Anthropic** (Claude)
  >
  > Then I'll need your API key."

After they specify the provider, if the key isn't in `.env`, ask:
> "Paste your [Provider] API key and I'll save it to .env:"

### Step 1.5: Run duration

Ask:

> **How many iterations should I run?**
>
> - **10** is a quick test (5-15 min)
> - **20-30** is a solid optimisation run (30-60 min)
> - **50+** is a deep run (1-2 hours)
>
> You can always stop me early or extend later.

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

After generating, show the AI-generated test prompts to the user:

> "I've generated these test scenarios for your skill:
>
> 1. [prompt summary]
> 2. [prompt summary]
> ...
>
> Look good, or want me to add/change any?"

Wait for confirmation before proceeding.

---

## Phase 2: Dashboard Launch

The dashboard gives the user a live view of progress. Always offer it before starting the loop.

### Step 2.1: Start the dashboard server

Run the dashboard in the background:
```bash
python3 tools/dashboard_server.py --port 8050 &
```

### Step 2.2: Ask about opening the browser

Ask the user:

> **I've started the live tracking dashboard. Want me to open it in your browser?**
>
> 📊 http://localhost:8050
>
> You can watch scores, metrics, and experiment history update in real time while I work.

If they say yes:
```bash
open http://localhost:8050
```

If they say no, tell them they can open it anytime at that URL.

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
>
> Want me to show you which changes were kept and what each one improved?

If the user wants to deploy the optimised skill:
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
