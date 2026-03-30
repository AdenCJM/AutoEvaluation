---
name: autoeval
description: Run the AutoEvaluation optimisation loop to iteratively improve a SKILL.md file through automated testing and scoring. Trigger when the user types /autoeval, says "run the eval loop", "optimise my skill", "start AutoEvaluation", "improve my prompts", "run a few iterations", or wants to test and refine LLM instructions. Also trigger when the user is in an AutoEvaluation project and asks to start, resume, or check on an optimisation run. Always use this skill rather than trying to run the loop manually.
---

# AutoEvaluation

Autonomous skill optimisation through iterative evaluation and hill-climbing.

This skill has **three phases**: interactive setup → dashboard launch → autopilot execution.

## Interaction Rules

**All user-facing questions MUST use the `AskUserQuestion` tool** — never ask questions as plain text in chat. This tool renders clickable button options in the UI, which is the expected user experience.

When this document says "use AskUserQuestion", it means: call the `AskUserQuestion` tool with a `questions` array containing objects with `question`, `header`, `options` (each with `label` and `description`), and `multiSelect` fields.

If you recommend a specific option, make it the first option and append "(Recommended)" to its label.

---

## Phase 1: Interactive Setup

Before running anything, have a conversation with the user to understand what they want to optimise. **Do not run setup.py** — instead, ask questions directly and generate the config yourself.

### Step 1.1: Check if already configured

Look for `config.yaml` and `results.tsv` in the current directory.

- If **both exist** and `results.tsv` has data rows, call the `AskUserQuestion` tool. Example of the exact tool parameters (all subsequent questions in this document follow the same pattern):

  ```json
  {
    "questions": [{
      "question": "I found an existing run with N experiments. What would you like to do?",
      "header": "Existing run",
      "multiSelect": false,
      "options": [
        { "label": "Resume (Recommended)", "description": "Pick up where I left off" },
        { "label": "Start fresh", "description": "Set up a new skill from scratch" }
      ]
    }]
  }
  ```

  If Resume → skip to Phase 2
  If Start fresh → continue with setup questions below

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

Before presenting options, read the skill content and reason about what good output looks like for this specific skill. **Prefer metrics that are quantifiable** — where a judge can point to specific, observable evidence in the output (e.g. "does the output contain X?", "is the word count under Y?", "are the instructions followed?"). Vague qualitative feels (e.g. "overall vibe") produce inconsistent scores and make optimisation noisy. An LLM judge can still score softer metrics like tone, but the rubric must describe *observable signals*, not just impressions.

Write a short recommendation (2–3 sentences) explaining which 2–3 metrics you'd suggest and why, given what the skill is trying to do. Call out concretely what makes each metric measurable for this specific skill. For example:

- A writing-style skill → task accuracy (did it follow the style rules?) + brevity (measurable by output length relative to input) + human-sounding (judge looks for specific AI tells)
- A sales email skill → task accuracy (did it hit the required elements: hook, offer, CTA?) + persuasiveness (judge scores whether the argument is logically structured) + tone consistency (judge checks against a defined voice rubric)
- A code review skill → technical accuracy (are the flagged issues real?) + task accuracy (does it follow the review format?) + brevity (is feedback concise, not padded?)
- A customer support skill → task accuracy (did it resolve the issue or ask the right clarifying question?) + tone consistency (empathetic and on-brand per a rubric) + human-sounding (no AI clichés)

Show this recommendation in plain text first, then present the question so the user can confirm or deviate:

Use AskUserQuestion with multiSelect:

Question: "Which metrics should I optimise for? (I've suggested a starting point above — you can confirm or pick your own.)"
Header: "Metrics"
multiSelect: true
Options:
- Task accuracy — did the output follow the skill's instructions? (most quantifiable — judge checks against specific rules)
- Brevity — is it concise without losing meaning? (measurable via output length and information density)
- Technical accuracy — are facts, code, or technical details correct? (judge verifies specific claims)
- Human-sounding — reads like a human wrote it, not an AI (judge looks for specific AI tells and clichés)
- Tone consistency — maintains the right tone throughout (requires a clear tone rubric to score reliably)
- Persuasiveness — argument is logically structured and motivates action (judge scores reasoning quality)
- Creativity — original and non-generic output (harder to quantify — use only if novelty is core to the skill)
- Custom — I'll define my own metric with clear scoring criteria

If they choose "Custom", ask:
> "What's the metric called? Describe what a judge would look for — what specific, observable signals make this a 1 (worst) vs 5 (best)?"

Map their choices to metric objects with name, weight, and rubric. Write rubrics that describe **observable signals**, not feelings — e.g. "Score 5 if the output contains a clear call to action in the final sentence and uses second-person voice throughout" not "Score 5 if it feels persuasive". Distribute weights evenly unless they indicate some matter more than others.

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
