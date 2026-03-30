# TODOS

## P1 — Before launch

### Write eval module for real target prompt
**What:** Write a deterministic eval module (or decide to skip deterministic metrics and rely on LLM judge only) for the real public experiment target prompt.
**Why:** The eval portability config change is just plumbing. The actual work is writing metrics that make sense for the chosen prompt. Without this, the experiment either uses the writing-style metrics (meaningless for a different prompt) or skips deterministic scoring entirely.
**Effort:** M (human) / S (CC: ~30 min once target is known)
**Blocked by:** Target prompt selection (critical blocker in CEO plan)
**Added:** 2026-03-28, CEO review

### Audience seeding strategy
**What:** Before launch day, identify 5-10 people in the AI/ML community who would find AutoEvaluation interesting. Send them the thread/post directly. Warm outreach, not cold.
**Why:** A Twitter thread with no followers reaches nobody. HN front page is not controllable. Seeding with 5-10 people who retweet/comment gives the content initial momentum. This is Hormozi's "warm outreach" play: existing contacts, community members, people who've tweeted about prompt engineering or LLM evals.
**Effort:** S (human: ~1 hour of research + DMs)
**Blocked by:** Nothing. Can start now.
**Added:** 2026-03-28, CEO review (outside voice finding)

## P3 — Research / future

### Alternative search algorithms
**What:** Evaluate simulated annealing, evolutionary strategies, or random restarts as alternatives to pure hill-climbing. The current search space is non-convex with noisy evaluation, which is the worst case for greedy hill-climbing.
**Why:** Hill-climbing keeps the first improvement it finds and never backtracks. It never explores. A population-based or temperature-based approach could find better optima. The outside voice from the CEO review flagged this as a real limitation.
**Effort:** L (human: ~2 weeks research + implementation) / M (CC: ~3-4 hours)
**Blocked by:** Nothing technically. Needs experimentation to validate whether alternative algorithms actually outperform hill-climbing on prompt optimisation tasks.
**Added:** 2026-03-30, eng review (outside voice finding)

## Deferred — After launch

### Live public dashboard (10x vision)
**What:** A public-facing dashboard showing AutoEvaluation running in real-time. Visitors watch the score curve climb live.
**Why:** This turns the experiment into content that generates itself. But it requires hosting, real-time updates, and a public server. Too much scope for Phase 1.
**Effort:** L-XL
**Added:** 2026-03-28, CEO review

### Proof bomb (multiple experiments)
**What:** Run AutoEvaluation against 5-10 well-known prompts, publish "We Let an AI Rewrite N Popular Prompts" as a roundup piece.
**Why:** More data points = more credible. Better viral hook. Natural follow-up if Phase 1 content gets traction.
**Effort:** L (3-5x API budget, 3-5x content packaging)
**Added:** 2026-03-28, CEO review
