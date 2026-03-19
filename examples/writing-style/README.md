# Writing Style Example

This example shows how to use AutoEvaluation to optimise a writing style skill that makes AI output sound human-written.

## Files

- `SKILL.md` — The skill instructions (Australian English, anti-AI patterns, natural tone)
- `prompts.json` — 10 test prompts across different writing genres (emails, blog posts, reports, etc.)
- `eval_deterministic.py` — Custom deterministic metrics (banned words, em dashes, contractions, AU spelling, sentence variety)
- `config.yaml` — Example config with both deterministic and LLM judge metrics

## Config

This example uses both **deterministic metrics** (rule-based checks) and **LLM judge** dimensions. Most use cases can get by with just LLM judge dimensions — the deterministic evals are optional.

See `config.yaml` for the full setup.

## To use this example

```bash
cp examples/writing-style/SKILL.md SKILL.md
cp examples/writing-style/prompts.json prompts/prompts.json
cp examples/writing-style/eval_deterministic.py tools/eval_deterministic.py
cp examples/writing-style/config.yaml config.yaml
# Then edit .env with your API key and run:
#   python3 tools/run_loop.py
# Or with Claude Code: claude -p program.md
```
