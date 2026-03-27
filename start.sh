#!/usr/bin/env bash
# AutoEvaluation — zero-friction entry point
# Installs deps, runs setup if needed, then starts the optimisation loop.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo ""
echo "================================================"
echo "  AutoEvaluation"
echo "================================================"
echo ""

# ── 1. Virtual environment ───────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  uv venv --quiet
  echo "  ✓ Virtual environment created"
fi

# ── 2. Install dependencies ──────────────────────────────────────
echo "Installing dependencies..."
uv pip install -q pyyaml google-genai openai anthropic
echo "  ✓ Dependencies installed"
echo ""

# ── 3. Activate venv ─────────────────────────────────────────────
source .venv/bin/activate

# ── 4. Run setup if config.yaml is missing ───────────────────────
if [ ! -f "config.yaml" ]; then
  echo "No config.yaml found — running setup wizard..."
  echo ""
  python3 setup.py
  echo ""
fi

# ── 5. Check API key ─────────────────────────────────────────────
# Read the key env var name from config.yaml, check env + .env, prompt if missing.
python3 - <<'PYEOF'
import sys, os, getpass, yaml
from pathlib import Path

cfg = yaml.safe_load(Path("config.yaml").read_text())
key_env = cfg.get("api_key_env", "GEMINI_API_KEY")

# Load .env into environ
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

if not os.environ.get(key_env):
    print(f"  {key_env} not found. Enter your API key:")
    api_key = getpass.getpass("  > ").strip()
    if not api_key:
        print("  Error: API key required.", file=sys.stderr)
        sys.exit(1)
    with open(".env", "a") as f:
        f.write(f"\n{key_env}={api_key}\n")
    print(f"  ✓ Saved to .env")
else:
    print(f"  ✓ {key_env} found")
PYEOF

echo ""

# ── 6. Start the optimisation loop ──────────────────────────────
echo "Starting optimisation loop..."
echo ""

if command -v claude &>/dev/null; then
  echo "  Mode: Claude Code  (claude -p program.md)"
  echo "  Stop: Ctrl+C in this terminal"
  echo ""
  claude -p program.md
else
  echo "  Mode: Headless  (python3 tools/run_loop.py)"
  echo "  Tip:  Install Claude Code for guided optimisation: npm install -g @anthropic-ai/claude-code"
  echo "  Stop: Ctrl+C"
  echo ""
  python3 tools/run_loop.py
fi
