# Current Benchmark Protocol

`sample-results.tsv` is a genuine historical product run, but it predates the
current statistical engine. Do not use it as evidence for hierarchical
bootstrap or final-test claims.

For a publishable benchmark:

1. Create at least 30 prompts before the run. Use the deterministic split:
   50% train, 30% validation, 20% untouched final test.
2. Use at least three replicates per prompt and a separately configured judge.
3. Pin exact model versions where the provider offers snapshots. Record model
   IDs, rubric, config, date, token usage, and estimated cost.
4. Run at least five independent optimisation campaigns from the same initial
   skill. Never reuse a campaign's final-test result to steer another campaign.
5. Report every campaign, including failures. Show baseline, selected training
   score, validation score, untouched final-test delta, uncertainty, number of
   attempted/kept edits, calls, elapsed time, and cost.
6. Publish the config, prompt IDs/splits, result TSVs, and aggregate JSON files
   needed to reproduce the analysis. Keep API keys out of the repository.

The benchmark is complete only when final-test results exist for every planned
campaign. Until then, describe the repository as an experimental optimiser,
not a proven guarantee of prompt improvement.
