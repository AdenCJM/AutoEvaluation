# AutoEvaluation Optimisation Run

The crash-safe headless driver is the single executable source of truth for the
optimisation loop. Do not reproduce its state transitions manually.

## Before starting

1. Read `config.yaml`, `SKILL.md`, and `prompts/prompts.json`.
2. Confirm the API-key environment variable named by `api_key_env` exists in
   `.env` or the process environment. Never print the value.
3. Use about 30 diverse prompts. Generated configurations split them into 50%
   train, 30% validation (`holdout_fraction`), and 20% untouched final test
   (`final_test_fraction`). Fewer than eight training prompts run in degraded
   threshold mode and must not be described as statistically significant.
4. Do not edit `SKILL.md`, prompts, config, results, or tools while the driver is
   active. The driver owns the mutation lock and recovery journal.

If setup is incomplete, run `python3 setup.py` or use
`python3 tools/generate_config.py` with the API key already stored in `.env`.

## Start

Optionally run the dashboard in another terminal:

```bash
python3 tools/dashboard_server.py --port 8050
```

Then run:

```bash
python3 tools/run_loop.py
```

CLI limits may override config for the current segment:

```bash
python3 tools/run_loop.py --iterations 10
python3 tools/run_loop.py --hours 2.5
```

For scheduled segments that should preserve the untouched final test for a
later campaign-finalising run:

```bash
python3 tools/run_loop.py --iterations 10 --skip-final-test
```

Run once without `--skip-final-test` only when the campaign is complete. After
that result is consumed, create a fresh campaign with new final-test prompts
before further optimisation.

## What the driver guarantees

- one process mutates campaign state at a time;
- interrupted candidates and partial promotions recover to the confirmed best;
- experiment outputs are isolated and promoted only when complete;
- generation, judging, and modification usage is priced by the serving model;
- prompt and replicate variance is resampled hierarchically;
- alpha spending bounds repeated-test false positives across resumes;
- validation gates KEEPs while the final-test split never affects selection;
- every experiment is reported and recorded through `results_io.py`.

Accept user steering between experiments, but never bypass these guarantees with
manual copies, TSV edits, or direct `SKILL.md` rewrites during a run.
