#!/usr/bin/env bash
# AutoEvaluation — zero-friction entry point
# Installs deps, runs setup if needed, validates API key, then starts the loop.
# Now also launches the dashboard and optionally opens the browser.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo ""
echo "================================================"
echo "  AutoEvaluation"
echo "================================================"
echo ""

# ── 0. Python version check ──────────────────────────────────────
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "  Error: No Python found. Install Python 3.10+ first."
  exit 1
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
  echo "  Error: Python 3.10+ required (found $PY_VERSION)."
  echo "  Install a newer version: https://www.python.org/downloads/"
  exit 1
fi
echo "  ✓ Python $PY_VERSION"

# ── 1. Virtual environment ───────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  if command -v uv &>/dev/null; then
    uv venv --quiet
  else
    "$PYTHON" -m venv .venv
  fi
  echo "  ✓ Virtual environment created"
fi

# ── 2. Activate venv ─────────────────────────────────────────────
source .venv/bin/activate

# ── 3. Install dependencies ──────────────────────────────────────
# Always install pyyaml. Provider SDK is installed based on config (or all if no config yet).
PROVIDER_PKG=""
if [ -f "config.yaml" ]; then
  PROVIDER=$(python3 -c "import yaml; print(yaml.safe_load(open('config.yaml')).get('provider',''))" 2>/dev/null || echo "")
  case "$PROVIDER" in
    gemini)    PROVIDER_PKG="google-genai" ;;
    openai)    PROVIDER_PKG="openai" ;;
    anthropic) PROVIDER_PKG="anthropic" ;;
  esac
fi

echo "Installing dependencies..."
if [ -n "$PROVIDER_PKG" ]; then
  PKGS="pyyaml $PROVIDER_PKG"
else
  PKGS="pyyaml google-genai openai anthropic"
fi

if command -v uv &>/dev/null; then
  uv pip install -q $PKGS
else
  pip install -q $PKGS
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
python3 - <<'PYEOF'
import sys, os, getpass, yaml
from pathlib import Path
sys.path.insert(0, "tools")
from utils import load_env

cfg = yaml.safe_load(Path("config.yaml").read_text())
key_env = cfg.get("api_key_env", "GEMINI_API_KEY")

load_env(Path(".env"))

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

# ── 7. Start the dashboard ───────────────────────────────────────
DASHBOARD_PORT="${DASHBOARD_PORT:-8050}"
echo "Starting dashboard on http://localhost:${DASHBOARD_PORT}..."
python3 tools/dashboard_server.py --port "$DASHBOARD_PORT" &
DASHBOARD_PID=$!
echo "  ✓ Dashboard running (PID: $DASHBOARD_PID)"
echo ""

# Wait a moment for the server to start
sleep 1

# Open browser if --open flag is passed, or ask interactively
if [[ "${1:-}" == "--open" ]] || [[ "${1:-}" == "-o" ]]; then
  echo "  Opening dashboard in browser..."
  open "http://localhost:${DASHBOARD_PORT}" 2>/dev/null || true
elif [ -t 0 ]; then
  # Interactive terminal — ask the user
  read -p "  Open dashboard in browser? (y/n) [y]: " OPEN_BROWSER
  OPEN_BROWSER="${OPEN_BROWSER:-y}"
  if [[ "$OPEN_BROWSER" == "y" ]] || [[ "$OPEN_BROWSER" == "Y" ]]; then
    open "http://localhost:${DASHBOARD_PORT}" 2>/dev/null || true
  fi
fi
echo ""

# ── 8. Start the optimisation loop ──────────────────────────────
echo "Starting optimisation loop..."
echo ""
echo "  Dashboard: http://localhost:${DASHBOARD_PORT}"
echo "  Stop:      Ctrl+C"
echo ""

# Cleanup dashboard on exit
cleanup() {
  echo ""
  echo "  Stopping dashboard (PID: $DASHBOARD_PID)..."
  kill "$DASHBOARD_PID" 2>/dev/null || true
  wait "$DASHBOARD_PID" 2>/dev/null || true
  echo "  ✓ Dashboard stopped"
}
trap cleanup EXIT

python3 tools/run_loop.py
