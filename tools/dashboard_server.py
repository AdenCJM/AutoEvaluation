"""
Live Tracking Dashboard
========================
Single-file Python HTTP server that serves a live-updating dashboard
for the optimisation loop. No external dependencies beyond Python stdlib
and PyYAML. Chart.js loaded from CDN.

Reads metric names from config.yaml so it works for any use case.

Usage:
    python3 tools/dashboard_server.py
    python3 tools/dashboard_server.py --port 8050 --tsv results.tsv

Then open http://localhost:8050 in your browser.
"""

import argparse
import csv
import json
import sys
import yaml
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from utils import PROJECT_ROOT

DEFAULT_TSV = PROJECT_ROOT / "results.tsv"


def load_config():
    """Load config.yaml and extract metric info."""
    cfg_path = PROJECT_ROOT / "config.yaml"
    if not cfg_path.exists():
        return {"metric_names": [], "metric_labels": {}, "metric_directions": {}}
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    names = []
    labels = {}
    directions = {}
    for m in cfg.get("deterministic_metrics", []):
        names.append(m["name"])
        labels[m["name"]] = m["name"].replace("_", " ").title()
        directions[m["name"]] = m.get("direction", "higher_is_better")
    for m in cfg.get("llm_judge_dimensions", []):
        names.append(m["name"])
        labels[m["name"]] = m["name"].replace("_", " ").title()
        directions[m["name"]] = m.get("direction", "higher_is_better")

    return {"metric_names": names, "metric_labels": labels, "metric_directions": directions}


def read_tsv(tsv_path: str, metric_config: dict) -> dict:
    """Read results.tsv and return structured data."""
    path = Path(tsv_path)
    metric_names = metric_config["metric_names"]

    if not path.exists():
        return {"runs": [], "best": None, "latest": None, "first": None,
                "metric_names": metric_names, "metric_labels": metric_config["metric_labels"]}

    runs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            header_fields = reader.fieldnames or []

            if not metric_names:
                skip = {"run_id", "timestamp", "composite_score", "change_description", "decision"}
                metric_names = [c for c in header_fields if c not in skip]

            for row in reader:
                try:
                    run = {
                        "run_id": row.get("run_id", ""),
                        "timestamp": row.get("timestamp", ""),
                        "composite_score": float(row.get("composite_score", 0)),
                        "change_description": row.get("change_description", ""),
                        "decision": row.get("decision", ""),
                    }
                    for m in metric_names:
                        run[m] = float(row.get(m, 0))
                    runs.append(run)
                except (ValueError, TypeError):
                    continue
    except Exception:
        return {"runs": [], "best": None, "latest": None, "first": None,
                "metric_names": metric_names, "metric_labels": metric_config.get("metric_labels", {})}

    best = max(runs, key=lambda r: r["composite_score"]) if runs else None
    latest = runs[-1] if runs else None
    first = runs[0] if runs else None

    labels = metric_config.get("metric_labels", {})
    if not labels:
        labels = {m: m.replace("_", " ").title() for m in metric_names}

    return {
        "runs": runs,
        "best": best,
        "latest": latest,
        "first": first,
        "metric_names": metric_names,
        "metric_labels": labels,
        "metric_directions": metric_config.get("metric_directions", {}),
    }


def read_status() -> dict:
    """Read .tmp/run_status.json written by run_loop.py."""
    status_path = PROJECT_ROOT / ".tmp" / "run_status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoEvaluation Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=Instrument+Serif&display=swap" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/style.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

/* ── Design Tokens (Dark-first, per DESIGN.md) ── */
:root {
    --bg-page: #0C0A09;
    --bg-card: #1C1917;
    --bg-card-hover: #292524;
    --bg-header: #1C1917;
    --border: rgba(255,255,255,0.08);
    --border-light: rgba(255,255,255,0.05);
    --border-strong: rgba(255,255,255,0.14);
    --text-primary: #FAFAF7;
    --text-secondary: #A8A29E;
    --text-muted: #78716C;
    --accent: #D4A015;
    --accent-bright: #E8B828;
    --accent-dim: #B8890F;
    --accent-light: rgba(212,160,21,0.08);
    --accent-border: rgba(212,160,21,0.25);
    --green: #16A34A;
    --green-light: rgba(22,163,74,0.10);
    --green-border: rgba(22,163,74,0.25);
    --green-text: #4ADE80;
    --red: #DC2626;
    --red-light: rgba(220,38,38,0.10);
    --red-border: rgba(220,38,38,0.25);
    --amber: #D97706;
    --amber-light: rgba(217,119,6,0.10);
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.20);
    --shadow-md: 0 2px 8px rgba(0,0,0,0.25);
    --shadow-lg: 0 4px 16px rgba(0,0,0,0.30);
    --font-display: 'Instrument Serif', Georgia, serif;
    --font-body: 'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'Geist Mono', 'SF Mono', 'Fira Code', monospace;
}

/* ── Light Mode Override ── */
[data-theme="light"] {
    --bg-page: #FAFAF7;
    --bg-card: #FFFFFF;
    --bg-card-hover: #F5F5F0;
    --bg-header: #FFFFFF;
    --border: rgba(0,0,0,0.08);
    --border-light: rgba(0,0,0,0.04);
    --border-strong: rgba(0,0,0,0.14);
    --text-primary: #1C1917;
    --text-secondary: #57534E;
    --text-muted: #A8A29E;
    --accent: #B8890F;
    --accent-bright: #D4A015;
    --accent-dim: #96700A;
    --accent-light: rgba(184,137,15,0.08);
    --accent-border: rgba(184,137,15,0.25);
    --green: #16A34A;
    --green-light: rgba(22,163,74,0.08);
    --green-border: rgba(22,163,74,0.20);
    --green-text: #065F46;
    --red-light: rgba(220,38,38,0.08);
    --red-border: rgba(220,38,38,0.20);
    --amber-light: rgba(217,119,6,0.08);
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 2px 8px rgba(0,0,0,0.06);
    --shadow-lg: 0 4px 16px rgba(0,0,0,0.08);
}

body {
    font-family: var(--font-body);
    background: var(--bg-page);
    color: var(--text-primary);
    min-height: 100vh;
    padding-bottom: 50px;
}

/* ── Grain Texture Overlay ── */
body::after {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    pointer-events: none;
    z-index: 9999;
    opacity: 0.025;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
    background-repeat: repeat;
    background-size: 256px 256px;
}

/* ── Header ────────────────────────────────────── */
.header {
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: var(--shadow-sm);
}
.header-left h1 {
    font-family: var(--font-display);
    font-size: 24px;
    font-weight: 400;
    color: var(--accent);
}
.header-left .subtitle {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 2px;
}

/* ── Hero Stats ────────────────────────────────── */
.hero-stats {
    display: flex;
    gap: 36px;
}
.hero-stat { text-align: right; }
.hero-stat .label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-muted);
    margin-bottom: 4px;
}
.hero-stat .value {
    font-family: var(--font-display);
    font-size: 32px;
    font-weight: 400;
    letter-spacing: -0.5px;
    color: var(--text-primary);
}
.hero-stat .value.green { color: var(--green); }
.hero-stat .value.accent { color: var(--accent); }
.hero-stat .sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.delta.positive { color: var(--green); font-weight: 600; }
.delta.negative { color: var(--red); font-weight: 600; }
.delta.neutral { color: var(--text-muted); }

/* ── Container ─────────────────────────────────── */
.container {
    padding: 24px 32px;
    max-width: 1440px;
    margin: 0 auto;
}
.section { margin-bottom: 28px; }
.section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
}
.section-title {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    color: var(--text-muted);
}

/* ── Cards ─────────────────────────────────────── */
.card {
    background: var(--bg-card);
    border-radius: 12px;
    padding: 20px;
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
}
.chart-container { position: relative; width: 100%; height: 280px; }

/* ── Metric Cards ──────────────────────────────── */
.metric-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
}
.metric-card {
    background: var(--bg-card);
    border-radius: 12px;
    padding: 16px 18px;
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
    transition: all 0.2s;
}
.metric-card:hover {
    border-color: var(--accent-border);
    box-shadow: var(--shadow-md);
}
.metric-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 6px;
}
.metric-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.metric-delta {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
}
.metric-delta.up { background: var(--green-light); color: var(--green); }
.metric-delta.down { background: var(--red-light); color: var(--red); }
.metric-delta.flat { background: var(--amber-light); color: var(--amber); }
.metric-value {
    font-family: var(--font-mono);
    font-size: 28px;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1;
    margin-bottom: 4px;
    letter-spacing: -0.5px;
}
.metric-value .pct { font-size: 18px; font-weight: 600; color: var(--text-muted); }

/* ── Start/End Endpoint Bar ────────────────────── */
.endpoint-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 10px 0 4px;
}
.endpoint-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-muted);
    min-width: 32px;
}
.endpoint-value {
    font-size: 12px;
    font-weight: 700;
    min-width: 36px;
}
.endpoint-value.start-val { color: var(--red); }
.endpoint-value.end-val { color: var(--green); }
.endpoint-track {
    flex: 1;
    height: 6px;
    background: var(--bg-card-hover);
    border-radius: 9999px;
    position: relative;
    overflow: hidden;
}
.endpoint-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.6s ease;
}
.endpoint-fill.high { background: linear-gradient(90deg, var(--accent-dim), var(--accent)); }
.endpoint-fill.mid { background: linear-gradient(90deg, var(--accent-dim), var(--accent-bright)); }
.endpoint-fill.low { background: linear-gradient(90deg, #fca5a5, var(--red)); }
.endpoint-start-marker {
    position: absolute;
    top: -3px;
    width: 2px;
    height: 14px;
    background: var(--red);
    border-radius: 1px;
    opacity: 0.7;
}

/* ── Layout ────────────────────────────────────── */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 18px; }
.radar-container { position: relative; height: 360px; }

/* ── Change Log ────────────────────────────────── */
.change-log { max-height: 400px; overflow-y: auto; }
.change-entry {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-light);
    transition: background 0.15s;
}
.change-entry:hover { background: var(--bg-card-hover); }
.change-entry:last-child { border-bottom: none; }
.change-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
}
.change-id {
    font-size: 12px;
    font-weight: 700;
    color: var(--accent);
}
.change-score {
    display: flex;
    align-items: center;
    gap: 6px;
}
.change-score .before {
    font-size: 12px;
    color: var(--text-muted);
    text-decoration: line-through;
}
.change-score .arrow { font-size: 11px; color: var(--text-muted); }
.change-score .after {
    font-size: 13px;
    font-weight: 700;
    color: var(--green);
}
.change-score .gain {
    font-size: 11px;
    font-weight: 600;
    color: var(--green);
    background: var(--green-light);
    padding: 1px 6px;
    border-radius: 4px;
}
.change-desc {
    font-size: 13px;
    color: var(--text-secondary);
    line-height: 1.5;
    white-space: normal;
    word-wrap: break-word;
    overflow-wrap: break-word;
}

/* ── History Table ────────────────────────────── */
.history-table { max-height: 360px; overflow-y: auto; }
table { width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 12px; }
th {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 2px solid var(--border);
    color: var(--text-muted);
    font-weight: 600;
    position: sticky;
    top: 0;
    background: var(--bg-card);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-secondary);
}
tr:hover td { background: var(--bg-card-hover); }
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.badge.keep { background: var(--green-light); color: var(--green-text); border: 1px solid var(--green-border); }
.badge.discard { background: var(--red-light); color: var(--red); border: 1px solid var(--red-border); }
.badge.baseline { background: var(--accent-light); color: var(--accent); border: 1px solid var(--accent-border); }
.score-cell { font-family: var(--font-mono); font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.desc-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Elapsed Time ─────────────────────────────── */
.elapsed-card {
    background: var(--accent-light);
    border: 1px solid var(--accent-border);
    border-radius: 10px;
    padding: 14px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
}
.elapsed-left { display: flex; align-items: center; gap: 10px; }
.elapsed-icon { font-size: 20px; }
.elapsed-label { font-size: 12px; color: var(--accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.elapsed-value { font-size: 18px; font-weight: 800; color: var(--accent); }
.elapsed-details { display: flex; gap: 24px; }
.elapsed-detail { text-align: right; }
.elapsed-detail .label { font-size: 10px; color: var(--text-muted); font-weight: 500; text-transform: uppercase; }
.elapsed-detail .val { font-size: 14px; font-weight: 700; color: var(--text-secondary); }

/* ── Run Progress ─────────────────────────────── */
.run-progress-card {
    background: var(--green-light);
    border: 1px solid var(--green-border);
    border-radius: 10px;
    padding: 14px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
    gap: 20px;
}
.run-progress-card.hidden { display: none; }
.run-progress-left { display: flex; align-items: center; gap: 10px; flex: 1; }
.run-pulse {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--green-text);
    animation: pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.85); }
}
.run-progress-label { font-size: 12px; color: var(--green-text); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.run-progress-bar-wrap { flex: 1; background: var(--border); border-radius: 4px; height: 6px; overflow: hidden; min-width: 80px; }
.run-progress-bar { height: 100%; background: var(--green-text); border-radius: 4px; transition: width 0.4s ease; }
.run-progress-details { display: flex; gap: 20px; }
.run-progress-detail { text-align: right; }
.run-progress-detail .label { font-size: 10px; color: var(--text-muted); font-weight: 500; text-transform: uppercase; }
.run-progress-detail .val { font-size: 14px; font-weight: 700; color: var(--green-text); }

/* ── Status Bar ────────────────────────────────── */
.status-bar {
    background: var(--bg-header);
    padding: 10px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1px solid var(--border);
    font-size: 12px;
    color: var(--text-muted);
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 100;
}
.status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    margin-right: 8px;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
.status-bar button {
    background: var(--bg-card-hover);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 5px 14px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: background 0.18s;
}
.status-bar button:hover { background: var(--accent-light); color: var(--accent); }
.theme-toggle {
    background: var(--bg-card-hover);
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 6px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: all 0.18s;
}
.theme-toggle:hover { border-color: var(--accent-border); color: var(--accent); }

.empty-state {
    text-align: center;
    padding: 100px 20px;
    color: var(--text-muted);
}
.empty-state h2 {
    font-size: 22px;
    color: var(--text-secondary);
    margin-bottom: 10px;
}
.empty-state code {
    background: var(--accent-light);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 13px;
    color: var(--accent);
}

@media (max-width: 1100px) {
    .three-col { grid-template-columns: 1fr; }
    .two-col { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
    .metric-grid { grid-template-columns: repeat(2, 1fr); }
    .container { padding: 16px; }
    .hero-stats { gap: 16px; }
}
</style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <h1>AutoEvaluation</h1>
        <div class="subtitle">Autonomous Skill Optimisation</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px;">
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">&#9680;</button>
    <div class="hero-stats">
        <div class="hero-stat">
            <div class="label">Best Score</div>
            <div class="value green" id="hdr-best">&mdash;</div>
            <div class="sub" id="hdr-best-run"></div>
        </div>
        <div class="hero-stat">
            <div class="label">Latest</div>
            <div class="value accent" id="hdr-latest">&mdash;</div>
            <div class="sub"><span class="delta neutral" id="hdr-delta"></span></div>
        </div>
        <div class="hero-stat">
            <div class="label">Experiments</div>
            <div class="value" id="hdr-runs">0</div>
            <div class="sub" id="hdr-keep-rate"></div>
        </div>
    </div>
    </div>
</div>

<div class="container" id="main-content">
    <div class="empty-state" id="empty-state">
        <h2>Waiting for first experiment&hellip;</h2>
        <p>Run <code>python3 tools/experiment_runner.py --run-id baseline</code> to start</p>
    </div>

    <div id="dashboard" style="display:none;">

        <!-- Run Progress Card (shown only during active run) -->
        <div class="run-progress-card hidden" id="runProgressCard">
            <div class="run-progress-left">
                <div class="run-pulse"></div>
                <div style="flex:1">
                    <div class="run-progress-label">Running</div>
                    <div class="run-progress-bar-wrap">
                        <div class="run-progress-bar" id="runProgressBar" style="width:0%"></div>
                    </div>
                </div>
            </div>
            <div class="run-progress-details">
                <div class="run-progress-detail">
                    <div class="label">Iteration</div>
                    <div class="val" id="runProgressIter">&mdash;</div>
                </div>
                <div class="run-progress-detail">
                    <div class="label">ETA</div>
                    <div class="val" id="runProgressEta">&mdash;</div>
                </div>
                <div class="run-progress-detail">
                    <div class="label">Cost</div>
                    <div class="val" id="runProgressCost">&mdash;</div>
                </div>
            </div>
        </div>

        <!-- Elapsed Time Bar -->
        <div class="elapsed-card" id="elapsedCard">
            <div class="elapsed-left">
                <div class="elapsed-icon">&#9202;</div>
                <div>
                    <div class="elapsed-label">Elapsed Time</div>
                    <div class="elapsed-value" id="elapsedValue">&mdash;</div>
                </div>
            </div>
            <div class="elapsed-details">
                <div class="elapsed-detail">
                    <div class="label">Started</div>
                    <div class="val" id="elapsedStart">&mdash;</div>
                </div>
                <div class="elapsed-detail">
                    <div class="label">Last Run</div>
                    <div class="val" id="elapsedLast">&mdash;</div>
                </div>
                <div class="elapsed-detail">
                    <div class="label">Avg Per Run</div>
                    <div class="val" id="elapsedAvg">&mdash;</div>
                </div>
            </div>
        </div>

        <!-- Composite Score Trend -->
        <div class="section">
            <div class="section-header">
                <div class="section-title">Composite Score Trend</div>
            </div>
            <div class="card">
                <div class="chart-container">
                    <canvas id="compositeChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Per-Metric Scores -->
        <div class="section">
            <div class="section-header">
                <div class="section-title">Per-Metric Scores</div>
            </div>
            <div class="metric-grid" id="metricGrid"></div>
        </div>

        <!-- Bottom: Radar + Change Log + History -->
        <div class="section">
            <div class="three-col">
                <div>
                    <div class="section-header">
                        <div class="section-title">Baseline &rarr; Best</div>
                    </div>
                    <div class="card">
                        <div class="radar-container">
                            <canvas id="radarChart"></canvas>
                        </div>
                    </div>
                </div>
                <div>
                    <div class="section-header">
                        <div class="section-title">What the System Learned</div>
                    </div>
                    <div class="card change-log" id="changeLog">
                        <div style="padding:20px;text-align:center;color:var(--text-muted)">No improvements yet</div>
                    </div>
                </div>
                <div>
                    <div class="section-header">
                        <div class="section-title">Experiment History</div>
                    </div>
                    <div class="card history-table">
                        <table>
                            <thead>
                                <tr><th>Run</th><th>Score</th><th>&Delta;</th><th>Decision</th><th>Description</th></tr>
                            </thead>
                            <tbody id="historyBody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="status-bar">
    <span><span class="status-dot" id="statusDot"></span><span id="statusText">Connecting&hellip;</span></span>
    <button id="pauseBtn" onclick="togglePause()">Pause</button>
</div>

<script>
// Theme toggle
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute("data-theme");
    const next = current === "light" ? null : "light";
    if (next) { html.setAttribute("data-theme", next); localStorage.setItem("ae-theme", next); }
    else { html.removeAttribute("data-theme"); localStorage.removeItem("ae-theme"); }
    updateChartColors();
}
(function() {
    const saved = localStorage.getItem("ae-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
})();

const POLL_MS = 10000;
let METRIC_NAMES = [];
let METRIC_LABELS = {};
let METRIC_DIRECTIONS = {};

let polling = true;
let compositeChart = null;
let radarChart = null;
let lastDataHash = "";

function pct(v) { return (v * 100).toFixed(1) + '%'; }
function pctInt(v) { return Math.round(v * 100) + '%'; }

function togglePause() {
    polling = !polling;
    document.getElementById("pauseBtn").textContent = polling ? "Pause" : "Resume";
    document.getElementById("statusDot").style.background = polling ? "var(--green)" : "var(--amber)";
    if (polling) poll();
}

async function fetchData() {
    const res = await fetch("/api/results");
    return res.json();
}

function dataHash(data) {
    const runStatus = data.run_status ? data.run_status.status + "_" + (data.run_status.current_iteration || 0) : "";
    return data.runs.length + "_" + (data.latest ? data.latest.composite_score : 0) + "_" + runStatus;
}

function formatDuration(ms) {
    const s = Math.floor(ms / 1000);
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    if (m < 60) return m + 'm ' + (s % 60) + 's';
    const h = Math.floor(m / 60);
    return h + 'h ' + (m % 60) + 'm';
}

function formatTime(ts) {
    if (!ts) return '\u2014';
    try {
        const d = new Date(ts + 'Z');
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch(e) { return ts; }
}

function updateElapsed(data) {
    if (!data.runs.length) return;
    const firstTs = data.first ? data.first.timestamp : null;
    const lastTs = data.latest ? data.latest.timestamp : null;

    document.getElementById("elapsedStart").textContent = formatTime(firstTs);
    document.getElementById("elapsedLast").textContent = formatTime(lastTs);

    if (firstTs && lastTs) {
        const start = new Date(firstTs + 'Z');
        const end = new Date(lastTs + 'Z');
        const elapsed = end - start;
        document.getElementById("elapsedValue").textContent = formatDuration(elapsed);

        if (data.runs.length > 1) {
            const avg = elapsed / (data.runs.length - 1);
            document.getElementById("elapsedAvg").textContent = formatDuration(avg);
        }
    }
}

function updateHeader(data) {
    document.getElementById("hdr-runs").textContent = data.runs.length;

    if (data.best) {
        document.getElementById("hdr-best").textContent = pctInt(data.best.composite_score);
        document.getElementById("hdr-best-run").textContent = data.best.run_id;
    }
    if (data.latest) {
        document.getElementById("hdr-latest").textContent = pctInt(data.latest.composite_score);
        if (data.first) {
            const delta = data.latest.composite_score - data.first.composite_score;
            const el = document.getElementById("hdr-delta");
            const sign = delta >= 0 ? "+" : "";
            el.textContent = sign + (delta * 100).toFixed(1) + "% from start";
            el.className = "delta " + (delta > 0 ? "positive" : delta < 0 ? "negative" : "neutral");
        }
    }

    const keeps = data.runs.filter(r => r.decision.toLowerCase() === "keep").length;
    const total = data.runs.filter(r => r.decision && r.decision.toLowerCase() !== "baseline").length;
    if (total > 0) {
        document.getElementById("hdr-keep-rate").textContent =
            keeps + "/" + total + " kept (" + Math.round(keeps/total*100) + "%)";
    }
}

function getPointColors(runs) {
    return runs.map(r => {
        const d = (r.decision || '').toLowerCase();
        if (d === "keep" || d === "baseline") return getComputedStyle(document.documentElement).getPropertyValue('--green').trim();
        if (d === "discard") return getComputedStyle(document.documentElement).getPropertyValue('--red').trim();
        return getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim();
    });
}

function createCompositeChart(data) {
    const ctx = document.getElementById("compositeChart").getContext("2d");
    const labels = data.runs.map(r => r.run_id);
    const scores = data.runs.map(r => r.composite_score * 100);
    const colors = getPointColors(data.runs);

    // Trend line (linear regression)
    const n = scores.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
    scores.forEach((y, x) => { sumX += x; sumY += y; sumXY += x*y; sumX2 += x*x; });
    const denom = n * sumX2 - sumX * sumX;
    const slope = denom ? (n * sumXY - sumX * sumY) / denom : 0;
    const intercept = denom ? (sumY - slope * sumX) / n : scores[0] || 0;
    const trendLine = scores.map((_, i) => slope * i + intercept);

    compositeChart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "Composite Score",
                    data: scores,
                    borderColor: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),
                    backgroundColor: "rgba(212,160,21,0.06)",
                    pointBackgroundColor: colors,
                    pointRadius: 6,
                    pointHoverRadius: 9,
                    pointBorderWidth: 2,
                    pointBorderColor: getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim(),
                    tension: 0.2,
                    borderWidth: 2.5,
                    fill: true,
                },
                {
                    label: "Trend",
                    data: trendLine,
                    borderColor: "rgba(217,119,6,0.5)",
                    borderDash: [8, 5],
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim(),
                    borderColor: getComputedStyle(document.documentElement).getPropertyValue('--border-strong').trim() || "rgba(255,255,255,0.14)",
                    borderWidth: 1,
                    titleColor: getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim(),
                    bodyColor: getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim(),
                    callbacks: {
                        label: ctx => ctx.datasetIndex === 0 ? ctx.parsed.y.toFixed(1) + '%' : 'Trend: ' + ctx.parsed.y.toFixed(1) + '%',
                        afterLabel: ctx => {
                            if (ctx.datasetIndex === 0) {
                                const run = data.runs[ctx.dataIndex];
                                return (run.decision || '') + (run.change_description ? ": " + run.change_description : "");
                            }
                        }
                    }
                }
            },
            scales: {
                y: {
                    min: 0, max: 100,
                    grid: { color: getComputedStyle(document.documentElement).getPropertyValue('--border').trim() },
                    ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim(), callback: v => v + '%', stepSize: 20 }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim(), maxRotation: 45, font: { size: 11 } }
                }
            }
        }
    });
}

function updateCompositeChart(data) {
    if (!compositeChart) { createCompositeChart(data); return; }
    const labels = data.runs.map(r => r.run_id);
    const scores = data.runs.map(r => r.composite_score * 100);

    const n = scores.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
    scores.forEach((y, x) => { sumX += x; sumY += y; sumXY += x*y; sumX2 += x*x; });
    const denom = n * sumX2 - sumX * sumX;
    const slope = denom ? (n * sumXY - sumX * sumY) / denom : 0;
    const intercept = denom ? (sumY - slope * sumX) / n : scores[0] || 0;
    const trendLine = scores.map((_, i) => slope * i + intercept);

    compositeChart.data.labels = labels;
    compositeChart.data.datasets[0].data = scores;
    compositeChart.data.datasets[0].pointBackgroundColor = getPointColors(data.runs);
    compositeChart.data.datasets[1].data = trendLine;
    compositeChart.update("none");
}

function createMetricGrid(data) {
    const grid = document.getElementById("metricGrid");
    grid.innerHTML = "";

    METRIC_NAMES.forEach(m => {
        const card = document.createElement("div");
        card.className = "metric-card";

        const dir = METRIC_DIRECTIONS[m] || 'higher_is_better';
        const isLower = dir === 'lower_is_better';
        const latest = data.latest ? data.latest[m] : 0;
        const first = data.first ? data.first[m] : 0;
        const delta = latest - first;
        const deltaSign = delta >= 0 ? "+" : "";

        // For lower_is_better: going down is good (green), going up is bad (red)
        let deltaClass;
        if (isLower) {
            deltaClass = delta < -0.01 ? "up" : delta > 0.01 ? "down" : "flat";
        } else {
            deltaClass = delta > 0.01 ? "up" : delta < -0.01 ? "down" : "flat";
        }

        // For lower_is_better: lower fill = better, so invert for bar display
        const barVal = isLower ? (1 - latest) : latest;
        const barClass = barVal >= 0.8 ? "high" : barVal >= 0.5 ? "mid" : "low";
        const startPct = Math.round(first * 100);
        const endPct = Math.round(latest * 100);
        const startMarkerLeft = Math.min(first * 100, 100);
        const dirLabel = isLower ? '<span style="font-size:10px;color:var(--text-muted);font-weight:500"> (lower is better)</span>' : '';

        card.innerHTML = `
            <div class="metric-top">
                <div class="metric-name">${METRIC_LABELS[m] || m}${dirLabel}</div>
                <div class="metric-delta ${deltaClass}">${deltaSign}${(delta*100).toFixed(1)}%</div>
            </div>
            <div class="metric-value" id="mv-${m}">${endPct}<span class="pct">%</span></div>
            <div class="endpoint-bar">
                <div class="endpoint-label">Start</div>
                <div class="endpoint-value start-val">${startPct}%</div>
                <div class="endpoint-track">
                    <div class="endpoint-start-marker" style="left:${startMarkerLeft}%"></div>
                    <div class="endpoint-fill ${barClass}" style="width:${endPct}%"></div>
                </div>
                <div class="endpoint-value end-val">${endPct}%</div>
                <div class="endpoint-label">Now</div>
            </div>
        `;
        grid.appendChild(card);
    });
}

function updateMetricGrid(data) {
    // Rebuild every time since structure is simple and data-driven
    createMetricGrid(data);
}

function createRadarChart(data) {
    const ctx = document.getElementById("radarChart").getContext("2d");
    const labels = METRIC_NAMES.map(m => METRIC_LABELS[m] || m);
    const firstVals = METRIC_NAMES.map(m => data.first ? data.first[m] * 100 : 0);
    const bestVals = METRIC_NAMES.map(m => data.best ? data.best[m] * 100 : 0);

    radarChart = new Chart(ctx, {
        type: "radar",
        data: {
            labels,
            datasets: [
                {
                    label: "Baseline",
                    data: firstVals,
                    borderColor: "rgba(220,38,38,0.6)",
                    backgroundColor: "rgba(220,38,38,0.06)",
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: "rgba(220,38,38,0.6)",
                },
                {
                    label: "Best",
                    data: bestVals,
                    borderColor: "rgba(5,150,105,0.8)",
                    backgroundColor: "rgba(5,150,105,0.06)",
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: "rgba(5,150,105,0.8)",
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: "#4b5563", font: { size: 12 } }
                },
                tooltip: {
                    backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim(),
                    borderColor: getComputedStyle(document.documentElement).getPropertyValue('--border-strong').trim() || "rgba(255,255,255,0.14)",
                    borderWidth: 1,
                    titleColor: getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim(),
                    bodyColor: getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim(),
                    callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.r.toFixed(1) + '%' }
                }
            },
            scales: {
                r: {
                    min: 0, max: 100,
                    grid: { color: getComputedStyle(document.documentElement).getPropertyValue('--border').trim() },
                    angleLines: { color: "#e5e7eb" },
                    pointLabels: { color: "#4b5563", font: { size: 11 } },
                    ticks: { display: false, stepSize: 25 }
                }
            }
        }
    });
}

function updateRadarChart(data) {
    if (!radarChart) { createRadarChart(data); return; }
    radarChart.data.datasets[0].data = METRIC_NAMES.map(m => data.first ? data.first[m] * 100 : 0);
    radarChart.data.datasets[1].data = METRIC_NAMES.map(m => data.best ? data.best[m] * 100 : 0);
    radarChart.update("none");
}

function updateChangeLog(data) {
    const log = document.getElementById("changeLog");
    const keeps = data.runs.filter(r => (r.decision || '').toLowerCase() === "keep" && r.change_description);

    if (keeps.length === 0) {
        log.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">No improvements yet</div>';
        return;
    }

    // Show most recent first, find previous score for each
    log.innerHTML = [...keeps].reverse().map(r => {
        const idx = data.runs.indexOf(r);
        const prevScore = idx > 0 ? data.runs[idx - 1].composite_score : r.composite_score;
        const gain = r.composite_score - prevScore;
        const gainStr = gain > 0 ? '+' + (gain * 100).toFixed(1) + '%' : '';

        return `<div class="change-entry">
            <div class="change-header">
                <div class="change-id">${r.run_id}</div>
                <div class="change-score">
                    <span class="before">${pctInt(prevScore)}</span>
                    <span class="arrow">&rarr;</span>
                    <span class="after">${pctInt(r.composite_score)}</span>
                    ${gainStr ? `<span class="gain">${gainStr}</span>` : ''}
                </div>
            </div>
            <div class="change-desc">${r.change_description}</div>
        </div>`;
    }).join('');
}

function updateHistoryTable(data) {
    const tbody = document.getElementById("historyBody");
    const rows = [...data.runs].reverse();

    tbody.innerHTML = rows.map((r, i) => {
        const dc = (r.decision || '').toLowerCase();
        const badgeClass = dc === "keep" ? "keep" : dc === "discard" ? "discard" : "baseline";
        const nextRun = i < rows.length - 1 ? rows[i + 1] : null;
        const delta = nextRun ? r.composite_score - nextRun.composite_score : 0;
        const deltaStr = delta === 0 ? "\u2014" :
            (delta >= 0 ? "+" : "") + (delta * 100).toFixed(1) + "%";
        const deltaColor = delta > 0 ? "color:var(--green)" : delta < 0 ? "color:var(--red)" : "color:var(--text-muted)";

        return `<tr>
            <td style="font-weight:600;color:var(--text-primary)">${r.run_id}</td>
            <td class="score-cell">${pctInt(r.composite_score)}</td>
            <td style="${deltaColor};font-size:12px;font-weight:600">${deltaStr}</td>
            <td><span class="badge ${badgeClass}">${r.decision || "\u2014"}</span></td>
            <td class="desc-cell" title="${r.change_description}">${r.change_description || "\u2014"}</td>
        </tr>`;
    }).join("");
}

function formatDuration(seconds) {
    if (!seconds || seconds <= 0) return "\u2014";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return h + "h " + m + "m";
    return m + "m";
}

function updateRunProgress(data) {
    const card = document.getElementById("runProgressCard");
    const rs = data.run_status;
    if (!rs || rs.status !== "running") {
        card.classList.add("hidden");
        return;
    }
    card.classList.remove("hidden");
    const cur = rs.current_iteration || 0;
    const max = rs.max_iterations || 0;
    const pct = max > 0 ? Math.min(100, Math.round((cur / max) * 100)) : 0;
    document.getElementById("runProgressBar").style.width = pct + "%";
    document.getElementById("runProgressIter").textContent = max > 0 ? cur + "/" + max : cur;
    document.getElementById("runProgressEta").textContent = formatDuration(rs.eta_seconds);
    document.getElementById("runProgressCost").textContent = rs.cost_usd != null ? "$" + rs.cost_usd.toFixed(3) : "\u2014";
}

function updateDashboard(data) {
    if (data.metric_names && data.metric_names.length) METRIC_NAMES = data.metric_names;
    if (data.metric_labels) METRIC_LABELS = data.metric_labels;
    if (data.metric_directions) METRIC_DIRECTIONS = data.metric_directions;

    if (data.runs.length === 0) {
        document.getElementById("empty-state").style.display = "block";
        document.getElementById("dashboard").style.display = "none";
        updateRunProgress(data);
        return;
    }
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("dashboard").style.display = "block";
    updateRunProgress(data);

    updateHeader(data);
    updateElapsed(data);
    updateCompositeChart(data);
    updateMetricGrid(data);
    updateRadarChart(data);
    updateChangeLog(data);
    updateHistoryTable(data);
}

function updateChartColors() {
    // Destroy and recreate charts with new theme colors
    if (compositeChart) { compositeChart.destroy(); compositeChart = null; }
    if (radarChart) { radarChart.destroy(); radarChart = null; }
    lastDataHash = ""; // Force re-render
    poll();
}

async function poll() {
    if (!polling) return;
    try {
        const data = await fetchData();
        const hash = dataHash(data);
        if (hash !== lastDataHash) {
            updateDashboard(data);
            lastDataHash = hash;
        }
        const now = new Date().toLocaleTimeString();
        document.getElementById("statusText").textContent =
            "Updated " + now + " \u00b7 Polling every " + (POLL_MS/1000) + "s \u00b7 " + data.runs.length + " experiments";
    } catch (e) {
        document.getElementById("statusText").textContent =
            "Error: " + e.message + " \u00b7 Retrying\u2026";
    }
    setTimeout(poll, POLL_MS);
}

poll();
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    tsv_path = str(DEFAULT_TSV)
    metric_config = {}

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

        elif parsed.path == "/api/results":
            data = read_tsv(self.tsv_path, self.metric_config)
            data["run_status"] = read_status()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Live tracking dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port to serve on (default: 8050)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--tsv", default=str(DEFAULT_TSV), help="Path to results.tsv")
    args = parser.parse_args()

    metric_config = load_config()
    DashboardHandler.tsv_path = args.tsv
    DashboardHandler.metric_config = metric_config

    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    print(f"Reading data from: {args.tsv}")
    print(f"Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard.")
        server.server_close()


if __name__ == "__main__":
    main()
