# Architecture & Design

This document explains *why* AutoEvaluation works the way it does, the signal flow through the system, and the design trade-offs.

## The Core Insight

Unlike most prompt optimisers (DSPy, TextGrad, OpenAI's optimizer), AutoEvaluation treats prompts as **prose documents that an LLM can read, critique, and rewrite** — not as parameters to optimise computationally.

This approach is powerful because:
1. **No DSL needed** — you write plain Markdown, not Python code
2. **Reasoning is visible** — the modifier LLM explains why it made each change
3. **Changes are meaningful** — the system doesn't search a parameter space; it engages with the actual instruction text

## Signal Flow: The Optimisation Loop

```
┌─────────────────┐
│  SKILL.md       │  ← The prose instruction file being optimised
│  (current)      │
└────────┬────────┘
         │
         ├─→ Generate samples using current SKILL.md
         │   (5-10 test prompts × current skill → LLM outputs)
         │
         ├─→ Judge each sample
         │   (LLM evaluates blind: does it follow skill? quality?)
         │   → Per-metric scores (1–5 scale, normalised to 0–1)
         │
         ├─→ Aggregate scores
         │   → Composite score (weighted average of metrics)
         │
         ├─→ Find weaknesses
         │   (2–3 lowest-scoring metrics)
         │   (Read 2–3 failing samples + judge reasoning)
         │
         ├─→ Form hypothesis
         │   (LLM reads failures and asks: "Why did this metric fail?")
         │   ("The output was too long — skill doesn't set length limits")
         │
         ├─→ Modify SKILL.md
         │   (ONE targeted change based on the specific failure)
         │
         ├─→ Evaluate new SKILL.md
         │   (Regenerate samples → re-judge → new composite score)
         │
         ├─→ Decide: Keep or Revert?
         │   If new score > best score (above noise threshold):
         │     → cp SKILL.md SKILL.md.best
         │     → record as KEEP
         │   Else:
         │     → cp SKILL.md.best SKILL.md
         │     → record as DISCARD
         │
         └─→ Repeat until iteration/time/cost limit
```

## Why Hill-Climbing (and What It Trades Off)

AutoEvaluation uses **hill-climbing**: each iteration makes one change, keeps it if the score improves, and reverts if not. This is a greedy, local search algorithm.

### Strengths

- **Simple & transparent** — every decision is visible: "we kept this change because score improved from 0.73 to 0.78"
- **Works with noisy evaluation** — even with variance in LLM judging, a 5% improvement signal is real
- **Fast** — no exploration of alternative branches; just climb
- **Interpretable** — each change is a single, named modification to the prose

### Weaknesses

- **Local optima** — if you start with a bad skill, you may optimize "up" to a bad local maximum, not the global best
- **No exploration** — once you reject a change, you never revisit it, even if it would have been useful paired with a later change
- **Path-dependent** — the order in which changes are tried affects the final result

### Trade-off: Why This Choice?

Hill-climbing was chosen because:

1. **The problem is not convex** — prompt optimisation landscapes are non-convex with noisy evaluation, where gradient descent fails anyway
2. **Population methods are expensive** — genetic algorithms, simulated annealing, or evolutionary strategies would require 5–10× more evaluations (more API calls, higher cost)
3. **Transparency matters** — we want users to *see* what changed and *why* it changed, not just "the algorithm improved your score"
4. **Good enough works** — a 2–5% improvement on a real prompt (like the writing-style example: 0.9508 → 0.9692) is valuable, even if a global optimum exists elsewhere

**Future possibility:** The TODOS list exploration of alternative search algorithms (simulated annealing, evolutionary strategies) to escape local optima. This would be a Phase 2 investment, validated only if Phase 1 data shows we regularly get stuck.

## Key Design Decisions

### One Change Per Iteration

Each experiment modifies **exactly one thing** in `SKILL.md`. Why?

- **Attribution** — if the score changes, you know which edit caused it
- **Reversibility** — if something goes wrong, revert one change
- **Interpretability** — the user can read the change and understand the system's reasoning
- **Testing** — small changes are easier to validate than large refactors

### Noise Filtering (min_improvement threshold)

By default, changes are kept only if the score improvement exceeds 1% (configurable via `min_improvement`).

Why? Because LLM judges have variance. A score of 0.7214 vs 0.7205 might be random noise, not a real improvement. If we kept every tiny change, the skill would churn with no real progress.

The threshold is conservative (1% default) because:
- Small improvements are signal, not noise (0.73 → 0.75 is real)
- But we don't overfit to variance (0.73 → 0.7301 is ignored)

### Blind Evaluation (judge_sees_skill: false by default)

The judge evaluates samples **without seeing `SKILL.md`**. This avoids self-judging bias — the LLM can't optimize to please itself.

However, the `task_accuracy` metric (does the output follow the skill?) benefits from context. Optional `judge_sees_skill: true` lets the judge see the skill for that dimension only.

Trade-off:
- **Blind (default)**: Cleaner signal, less bias, but task_accuracy might miss context
- **Semi-blind**: Better task_accuracy, small bias risk, requires careful configuration

### Deterministic Metrics (Optional)

Most evaluations use **LLM-as-judge only**, but advanced users can add rule-based metrics (e.g., word count, banned words, response time).

Why optional?
- LLM judges are flexible and require no code
- Deterministic metrics require writing Python; they're not for everyone
- Mixing LLM and deterministic requires careful weight tuning

### Separate Judge Model (Optional)

By default, the same LLM that generates outputs also judges them. But you can configure a separate judge model (e.g., generate with Gemini, judge with GPT-4) to avoid self-judging bias.

Trade-off:
- **Single model (default)**: Cheaper, simpler, some bias risk
- **Separate model**: Better signal, costs more, requires two API keys

## Parallel Execution

The `max_concurrent` setting allows multiple LLM calls to run in parallel (generation and judgment).

Why?
- Generation is parallelisable (score 5 samples in parallel instead of sequentially)
- Evaluation is parallelisable (judge 5 samples in parallel instead of sequentially)

Trade-off:
- **Serial (default, max_concurrent=1)**: Cheaper, simpler
- **Parallel (max_concurrent=4)**: 4× faster wall-clock time, higher API costs

## File Ownership

During an optimisation run, only `SKILL.md` is modified. All other files are immutable:

| File | Mutability | Why |
|------|-----------|-----|
| `SKILL.md` | Read/write during loop | The target of optimisation |
| `SKILL.md.best` | Write (tracked) | Copy of the best version seen |
| `results.tsv` | Append-only | Full experiment history, immutable for reproducibility |
| `config.yaml` | Read-only | Configuration is fixed during a run |
| `prompts/prompts.json` | Read-only | Test prompts are fixed during a run |
| `tools/` | Read-only | Evaluation harness is deterministic |
| `.tmp/samples/` | Write (intermediate) | Disposable: samples, logs, intermediate results |

This design ensures **reproducibility**: given the same config and prompts, the same sequence of experiments produces the same results.

## Cost Estimation

Every API call is tracked:
- Tokens in/out per call
- Per-provider pricing (Gemini $0.075/M input, $0.30/M output; OpenAI varies by model)
- Cumulative cost estimate printed at run end

The `max_cost_usd` limit stops the loop if estimated spend exceeds the budget.

Why estimate, not actual? Because the system doesn't control provider billing; it only knows what it sent. A conservative estimate (tokens × provider rates) helps users stay within budget.

## Convergence Detection

Optional: stop automatically after N iterations with no improvement above the noise threshold. Why?

- Human-run experiments might plateau and run indefinitely
- Automated runs might waste API budget on fruitless iterations
- Convergence detection (after 10 iterations with no improvement, stop) is a smart heuristic

But it's off by default because:
- Some optimisation targets have long plateaus followed by breakthroughs
- Users might prefer to let it run longer

## Why No Rollback to Earlier Good States?

The system keeps only `SKILL.md.best` (the single best version), not a version history. Why not keep the top 5 and try backtracking?

Trade-offs:
- **Current (single best)**: Simple, no path-dependency questions, fast
- **History with backtracking**: Might escape local optima, but complex, requires deciding when to backtrack

For Phase 1, the single-best approach is justified. If Phase 1 experiments show we regularly get stuck in local optima, Phase 2 can add history and backtracking logic.

## Integration Points

### Claude Code (`program.md`)

`program.md` encodes the loop as instructions for Claude Code. The LLM reads the file, runs the loop autonomously, modifies SKILL.md, and tracks results.

Why a separate file? Because:
- The loop logic is separate from the system logic
- Users can understand and modify loop behaviour without touching code
- Claude Code can run the loop hands-off

### GitHub Actions

Optional GitHub Actions workflow (in `examples/github-actions/`) lets you schedule the optimisation to run daily, commit improvements, and track results.

Why optional? Because:
- Setup requires secrets management (storing API keys in GitHub)
- Not all projects want automated optimisations running unsupervised
- Opt-in keeps the tool lightweight

### Dashboard

Optional live dashboard (`tools/dashboard_server.py`) plots score trends and per-metric cards.

Why optional? Because:
- Most users don't need real-time visualisation
- The loop works fine in headless mode without it
- Adding dependencies on web frameworks (Dash/Plotly) is a choice, not a requirement

## Future Directions (from TODOS)

**P3: Alternative search algorithms** — Explore simulated annealing, evolutionary strategies, or random restarts. The current hill-climbing is non-convex-unfriendly; population-based methods might find better optima.

**P3: Live public dashboard** — Turn the experiment into content: a public dashboard showing AutoEvaluation running in real-time.

**P1: Target-specific eval modules** — The current example uses writing-style metrics; a real launch target needs domain-specific metrics (task accuracy for code generation, coherence for storytelling, etc.).
