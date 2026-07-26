# Changelog

All notable user-facing changes to AutoEvaluation are recorded here, newest first.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); this project
doesn't yet cut version numbers or GitHub releases; entries are dated instead.

Tracking starts at the entry below — earlier history lives in `git log` and in
`walkthrough.md`, which keeps a real new-user test report (with its original
findings preserved, not rewritten) rather than a changelog.

## 2026-07-26

### Fixed
- `docs/GETTING_STARTED.md` step 1 no longer tells new users to run a bare
  `pip install -r requirements.txt`, which fails with `externally-managed-environment`
  on stock Homebrew/Debian Python. It now creates and activates a `.venv` first,
  matching what `start.sh` already does.
- The bundled zero-API-key demo (`python3 autoeval.py demo`) now explains *why*
  an experiment's rationale, instruction diff, and judge feedback are empty
  instead of showing a bare "unavailable" — the demo replays a historical
  score-only run with no `decision.json` artifacts behind it.
- `autoeval init --help` now reports itself as `autoeval init` instead of
  leaking the underlying `setup.py` wrapper's program name.

### Added
- `CONTRIBUTING.md`, `SECURITY.md`, and Dependabot config (pinned GitHub Actions
  to commit SHAs).
- This changelog.
