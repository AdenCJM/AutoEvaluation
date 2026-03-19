# Always-On Mode (GitHub Actions)

Run AutoEvaluation on a schedule so your skill improves in the background.

## Setup

1. Copy the workflow into your repo:
   ```bash
   mkdir -p .github/workflows
   cp examples/github-actions/optimise.yml .github/workflows/optimise.yml
   ```

2. Push to GitHub

3. Add your API key as a repository secret:
   - Go to **Settings > Secrets > Actions**
   - Add a secret called `LLM_API_KEY` with your provider's API key

4. The workflow runs daily at 2am UTC by default. You can also trigger it manually from the Actions tab.

## What it does

Each run:
- Checks out the repo
- Installs the right SDK for your provider (reads `config.yaml`)
- Runs N iterations of the optimisation loop (default: 10)
- Commits the updated `SKILL.md.best` and `results.tsv` back to the repo

## Customise the schedule

Edit the cron expression in `optimise.yml`:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'    # daily at 2am UTC
    - cron: '0 */6 * * *'  # every 6 hours
    - cron: '0 2 * * 1'    # weekly on Monday
```

## Requirements

- `config.yaml` must exist in the repo root (run `python3 setup.py` first)
- `LLM_API_KEY` secret must be set in your repo settings
