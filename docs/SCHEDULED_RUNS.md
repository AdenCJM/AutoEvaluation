# Running AutoEvaluation unattended

This is a practical guide to the three ways to run the optimisation loop without babysitting it, plus a nightly regression sweep option. All supported paths delegate to `tools/run_loop.py`; they differ in where they run and how state is persisted.

| Option | Where it runs | Minimum interval | Local file access | Best for |
|---|---|---|---|---|
| `/loop` in Claude Code | Your machine, inside an open session | 1 minute | Full | Keep iterating tonight |
| Routines (`/schedule`) | Anthropic cloud, fresh clone each run | 1 hour | None (clone only) | Recurring nightly runs without a machine on |
| Cron + `run_loop.py` | Your server/always-on machine | Any (cron granularity) | Full | Always-works headless option |

## 1. `/loop` in Claude Code - session-scoped recurring runs

`/loop` fires a prompt or slash command on a recurring interval, but only while a Claude Code session is open (or backgrounded). The `/autoeval` skill ultimately delegates to the same locked headless driver.

```
/loop 30m /autoeval
```

This fires `/autoeval` every 30 minutes, with full local file access to `SKILL.md`, `config.yaml`, `results.tsv`, and `.tmp/` - exactly the file-ownership model in this repo's `CLAUDE.md`.

Characteristics:

- **1-minute minimum interval** - the fastest of the three scheduling surfaces.
- **Fires only while the session is open**, including backgrounded (agent-view) sessions without a terminal. Closing the terminal or ending the session stops it.
- **Recurring tasks expire after 7 days** regardless of activity. Fine for an overnight or weekend run; not suited to a "let it run for a month" campaign - nothing re-arms the task before expiry.
- `--resume`/`--continue` restores tasks that haven't expired yet.

**Use this when**: you want to start a run before bed and have it keep iterating overnight or over a weekend, without setting up any cloud or server infrastructure.

## 2. Routines via `/schedule` (research preview) - cloud-scheduled sessions

A Routine is a saved Claude Code session (prompt + repo + connectors) that Anthropic runs on managed cloud infrastructure on a cron schedule. Create or update one with `/schedule`.

Example - a nightly 10-iteration run:

```
/schedule nightly at 1am, run: cd AutoEvaluation && python3 autoeval.py run --iterations 10, then force-add the generated run artefacts, commit them to a claude/nightly-autoeval branch, and push
```

Or, staying closer to the conversational loop:

```
/schedule nightly at 1am, in the AutoEvaluation repo, run 10 optimisation iterations without consuming the final-test split, then force-add and commit the generated run artefacts
```

Characteristics:

- **1-hour minimum interval** (vs `/loop`'s 1 minute) - not suited to fast, tight iteration cadences, but fine for "once a night."
- **Fresh repo clone every run**, from the default branch. This matters because run state is gitignored for local use. The routine must use `git add -f` for `results.tsv`, `SKILL.md.best`, and the aggregate JSON files before committing. `config.yaml` must already be force-added once because it is non-secret configuration required by a fresh checkout.
- **Pushes are restricted to `claude/`-prefixed branches** unless unrestricted push is explicitly enabled for the routine. Plan for the routine to push to something like `claude/nightly-autoeval` and periodically merge or review that branch, rather than expecting it to land on `main` directly.
- **No permission prompts** - the routine runs the full session autonomously, so anything in `tools/` executes without a human in the loop. Treat `tools/` as trusted-as-is before scheduling a routine against it.
- **Personal daily routine-run cap** applies (metered overage available with usage credits) - a nightly single run comfortably fits under this, but don't stack many routines on tight schedules without checking the cap.

**The API-key question**: `.env` is gitignored, so it does not travel with the fresh clone a Routine gets. A Routine needs `ANTHROPIC_API_KEY` (or whichever provider key `config.yaml`'s `api_key_env` names) supplied through whatever secret-injection mechanism the Routines product exposes for a given session (for example a connector or environment-level secret) - check the current `/schedule` documentation for the exact mechanism at the time you set this up, since Routines are still in research preview and this detail is likely to firm up. Don't hardcode a key into the routine prompt or commit it to the branch it pushes.

**Use this when**: you want AutoEvaluation to run every night without your machine being on, and you're comfortable with a fresh-clone-plus-push model for getting results back out.

## 3. Plain cron + `run_loop.py` - the always-works headless option

If you have a server or an always-on machine, skip both Claude Code scheduling surfaces and just cron the headless driver directly. No research-preview caveats, no session/clone semantics - it runs exactly like a manual `python3 tools/run_loop.py` invocation, with full local file access and `.env` already in place.

```cron
# Run 10 optimisation iterations at 1am every night
0 1 * * * cd /path/to/AutoEvaluation && /usr/bin/python3 autoeval.py run --iterations 10 >> logs/nightly.log 2>&1
```

Characteristics:

- Works anywhere cron and Python 3.10+ are available - no Claude Code dependency, no cloud product, no research-preview surface.
- `.env` sits on disk as normal; no secret-injection question to solve.
- `results.tsv`, `SKILL.md.best`, and the aggregate JSON files update in place, same as an interactive run.
- You own uptime, log rotation, and failure alerting - none of that is provided for you the way Managed Agents' auto-pause-on-error behaviour is.

**Use this when**: you have a server or an always-on machine and want the simplest, most predictable unattended option with no dependency on Claude Code's cloud scheduling products.

## Nightly regression sweep (`tools/batch_sweep.py`)

Separate from optimisation, `tools/batch_sweep.py` re-scores the current `SKILL.md.best` against every prompt in `prompts/` (not just the fast iterate subset) using the Anthropic Batches API, at roughly 50% of synchronous cost. This is a regression check, not a hill-climb step - it doesn't modify `SKILL.md`, it validates that the best-known version still holds up on the full prompt set.

Run it nightly via cron:

```cron
0 3 * * * cd /path/to/AutoEvaluation && /usr/bin/python3 tools/batch_sweep.py >> logs/batch_sweep.log 2>&1
```

or as the payload of a Routine, using the same commit-and-push pattern as option 2 above.

Requires an Anthropic API key (Batches API is Anthropic-specific; other providers aren't supported by this script). Compare its output against `best_aggregate.json` to catch drift before it compounds across many optimisation iterations.

## Summary - which one to reach for

- **Tonight, one session, my machine**: `/loop 30m /autoeval`.
- **Every night, indefinitely, don't want my machine on**: a Routine via `/schedule`, with an explicit commit-and-push step and a plan for the API key.
- **I have a server**: cron + `python3 autoeval.py run --iterations N`, then explicitly run `python3 autoeval.py finalize` once when the campaign is finished.
- **Any time**: pair whichever of the above you pick with a nightly `tools/batch_sweep.py` regression check to catch drift on the full prompt set at half the API cost.
