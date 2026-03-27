#!/usr/bin/env bash
# AutoEvaluation — zero-friction entry point
# Installs deps, runs setup if needed, validates API key, then starts the loop.
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
  if command -v uv &>/dev/null; then
    uv venv --quiet
  elif command -v python3 &>/dev/null; then
    python3 -m venv .venv
  elif command -v python &>/dev/null; then
    python -m venv .venv
  else
    echo "  Error: No Python found. Install Python 3.10+ first."
    exit 1
  fi
  echo "  ✓ Virtual environment created"
fi

# ── 2. Activate venv ─────────────────────────────────────────────
source .venv/bin/activate

# ── 3. Install dependencies ──────────────────────────────────────
echo "Installing dependencies..."
if command -v uv &>/dev/null; then
  uv pip install -q pyyaml google-genai openai anthropic
else
  pip install -q pyyaml google-genai openai anthropic
fi
echo "  ✓ Dependencies installed"
echo ""

# ── 4. Run setup if config.yaml is missing ───────────────────────
if [ ! -f "config.yaml" ]; then
  echo "No config.yaml found — running setup wizard..."
  echo "  Tip: Use 'python3 setup.py --defaults' to skip prompts"
  echo ""
  python3 setup.py
  echo ""
fi

# ── 5. Check API key ─────────────────────────────────────────────
# Read the key env var name from config.yaml, check env + .env, prompt if missing.
# Then validate the key with a tiny API call.
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
    os.environ[key_env] = api_key
    print(f"  ✓ Saved to .env")
else:
    print(f"  ✓ {key_env} found")

# Validate the API key with a tiny call
print("  Validating API key...", end="", flush=True)
sys.path.insert(0, "tools")
try:
    from model_client import ModelClient
    client = ModelClient(
        provider=cfg.get("provider", "gemini"),
        model=cfg.get("model", "gemini-2.5-flash"),
        api_key_env=key_env,
    )
    response = client.generate("Respond with OK.", "Say OK.", max_tokens=8)
    if response and len(response.strip()) > 0:
        print(" ✓ API key valid")
    else:
        print(" ✗ Empty response — check your key and model", file=sys.stderr)
        sys.exit(1)
except Exception as e:
    err_name = type(e).__name__
    print(f" ✗ {err_name}: {e}", file=sys.stderr)
    print(f"\n  Your {key_env} appears invalid or the model is unreachable.", file=sys.stderr)
    print(f"  Fix the key in .env and try again.", file=sys.stderr)
    sys.exit(1)
PYEOF

echo ""

# ── 6. Install /autoeval skill ───────────────────────────────────
SKILL_SRC=".claude/skills/autoeval/SKILL.md"
SKILL_DEST="$HOME/.claude/skills/autoeval/SKILL.md"
if [ -f "$SKILL_SRC" ] && [ ! -f "$SKILL_DEST" ]; then
  mkdir -p "$HOME/.claude/skills/autoeval"
  cp "$SKILL_SRC" "$SKILL_DEST"
  echo "  ✓ /autoeval skill installed (type /autoeval in Claude Code to start a run)"
  echo ""
fi

# ── 7. Start the optimisation loop ──────────────────────────────
echo "Starting optimisation loop..."
echo ""
echo "  Dashboard: python3 tools/dashboard_server.py (http://localhost:8050)"
echo ""

echo "  Mode: Headless  (python3 tools/run_loop.py)"
echo "  Stop: Ctrl+C"
echo ""
python3 tools/run_loop.py
