# Contributing

Contributions are welcome when they improve the reliability, statistical integrity, or usability of AutoEvaluation.

Before opening a pull request, run `python -m pytest -q`, `python -m compileall -q setup.py tools tests`, and `bash -n start.sh`.

Do not commit API keys, unredacted prompts, provider outputs, or campaign artefacts. Changes to the acceptance protocol must explain their statistical and user-impact rationale.
