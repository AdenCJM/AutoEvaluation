# AutoEvaluation — New User Test Report

**Date:** 2026-03-28  
**Tested from:** Fresh clone → full optimization run  
**Provider used:** OpenAI (gpt-4o) — the README defaults to Gemini but the user had OpenAI available  

---

## Test Flow

### 1. Clone ✅
```bash
git clone https://github.com/AdenCJM/AutoEvaluation.git
```
Clean, fast, no issues.

### 2. Copy Example Files ✅
```bash
cp examples/writing-style/SKILL.md SKILL.md
cp examples/writing-style/config.yaml config.yaml
cp examples/writing-style/prompts.json prompts/prompts.json
cp examples/writing-style/eval_deterministic.py tools/eval_deterministic.py
```
All files present, instructions match README exactly.

### 3. Create `.env` ✅
```bash
echo "OPENAI_API_KEY=your-key" > .env
```
Had to manually update `config.yaml` to switch from `gemini` → `openai` provider since the example config hardcodes Gemini.

### 4. Run `start.sh` ⚠️
```bash
./start.sh
```

| Step | Result |
|---|---|
| Virtual env creation | ✅ Created with `uv` |
| Dependency install | ✅ Installed via `uv pip` |
| Config detection | ✅ Found existing config.yaml |
| API key detection | ✅ `OPENAI_API_KEY found` |
| API key validation | ✅ `API key valid` |
| Start optimization | ⚠️ See below |

> [!WARNING]
> **Claude Code detection issue:** `start.sh` detected Claude Code was installed and ran `claude -p program.md`. However, Claude just printed the contents of `program.md` and exited — it didn't actually execute the optimization loop. A new user with Claude Code installed but wanting headless mode would be stuck.
>
> The script assumes `claude -p` will execute the program, but `claude -p` in current versions appears to just print/describe the file rather than run it as instructions.

**Workaround:** Run the loop directly:
```bash
source .venv/bin/activate
python3 tools/run_loop.py --iterations 3
```

### 5. Headless Optimization Loop ✅

Ran `python3 tools/run_loop.py --iterations 3` — **worked perfectly**.

| Experiment | Score | Decision | Change |
|---|---|---|---|
| baseline | 0.8358 | — | Starting point |
| exp_001 | 0.7768 | DISCARD | Added opener variety guidance |
| exp_002 | 0.8597 | **KEEP** | Clarified parallel structure guidance |
| exp_003 | 0.8400 | DISCARD | Added opener variety emphasis |

**Final result:**
- Baseline: **0.8358** → Best: **0.8597** (+0.0239)
- 1 of 3 changes kept
- Total time: 3m 21s
- Total cost: $0.04
- `SKILL.md.best` saved with optimized version

### 6. Dashboard ✅
```bash
python3 tools/dashboard_server.py
```
Server starts on `http://localhost:8050`, returns HTTP 200. Working.

### 7. Output Artifacts ✅

All expected files were created:

| File | Status |
|---|---|
| `results.tsv` | ✅ Full experiment history |
| `SKILL.md.best` | ✅ Best-scoring skill version |
| `.tmp/run-summary.md` | ✅ Clean summary report |
| `.tmp/samples/*/` | ✅ Generated samples per experiment |
| `.tmp/evals/*/` | ✅ Full evaluation data |

---

## Issues Found

### 🔴 Critical: `claude -p program.md` doesn't work as expected
- **Impact:** Any user who has Claude Code installed will hit this — `start.sh` auto-detects Claude Code and uses it, but the `claude -p` command doesn't run the optimization loop
- **Fix suggestion:** Either document this limitation or add a `--headless` flag to `start.sh` to force `python3 tools/run_loop.py`

### 🟡 Medium: Example config hardcodes Gemini
- **Impact:** Users with OpenAI or Anthropic keys need to manually edit `config.yaml` after copying the example
- **Fix suggestion:** The README says to set `GEMINI_API_KEY` in `.env` but doesn't mention you need to change `config.yaml` if using a different provider. Add a note or auto-detect from `.env`.

### 🟢 Minor: No `.env` template auto-copy
- **Impact:** Very minor — `start.sh` could copy `.env.example` → `.env` if `.env` doesn't exist, with a prompt to fill in the key
- **Current UX:** Acceptable, the inline API key prompt works fine as a fallback

---

## Overall Assessment

| Category | Rating |
|---|---|
| Clone → first run | ⭐⭐⭐⭐ (4/5) |
| Documentation accuracy | ⭐⭐⭐⭐ (4/5) |
| Error messages | ⭐⭐⭐⭐⭐ (5/5) |
| Loop execution & output | ⭐⭐⭐⭐⭐ (5/5) |
| Terminal UX (progress bars, scores) | ⭐⭐⭐⭐⭐ (5/5) |
| Dashboard | ⭐⭐⭐⭐ (4/5) |

> [!TIP]
> **Bottom line:** The headless path (`python3 tools/run_loop.py`) is excellent. Clean output, clear progress, intuitive scoring display. The main friction point is the Claude Code detection in `start.sh` — either fix the `claude -p` integration or default to headless mode.
