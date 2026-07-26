#!/usr/bin/env python3
"""Unified product CLI for AutoEvaluation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from campaigns import campaign_status, new_campaign  # noqa: E402


def _run(script: str, args: list[str], env: dict | None = None) -> int:
    try:
        run_env = {**os.environ, **env} if env else None
        return subprocess.call([sys.executable, str(PROJECT_ROOT / script), *args], cwd=PROJECT_ROOT, env=run_env)
    except KeyboardInterrupt:
        return 130


def main() -> None:
    # Setup owns a richer, evolving option surface. Forward everything after
    # `init` verbatim so callers do not have to learn a second wrapper schema.
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        raise SystemExit(_run("setup.py", sys.argv[2:], env={"AUTOEVAL_PROG_NAME": "autoeval init"}))

    parser = argparse.ArgumentParser(
        prog="autoeval",
        description="Optimise, inspect, finalise, and archive LLM instruction campaigns.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Configure a new project")

    run = sub.add_parser("run", help="Start or continue the active campaign")
    run.add_argument("--iterations", type=int, default=0)
    run.add_argument("--hours", type=float, default=0)
    run.add_argument("--measure-noise", type=int, default=0)

    sub.add_parser("status", help="Show active campaign state")
    sub.add_parser("finalize", help="Run the untouched final test and close the campaign")

    new = sub.add_parser("new", help="Archive the active campaign and start another")
    new.add_argument("name", nargs="?", default=None)
    new.add_argument("--from-current", action="store_true", help="Start from SKILL.md instead of SKILL.md.best")

    dashboard = sub.add_parser("dashboard", help="Open the active campaign dashboard")
    dashboard.add_argument("--port", type=int, default=8050)
    dashboard.add_argument("--open", action="store_true")

    demo = sub.add_parser("demo", help="Explore the bundled campaign without an API key")
    demo.add_argument("--port", type=int, default=8050)
    demo.add_argument("--open", action="store_true")

    sub.add_parser("report", help="Print the latest campaign report")
    benchmark = sub.add_parser("benchmark", help="Plan or run isolated repeated campaigns")
    benchmark.add_argument("--campaigns", type=int, default=3)
    benchmark.add_argument("--iterations", type=int, default=10)
    benchmark.add_argument("--execute", action="store_true", help="Perform paid model calls (default is dry-run)")
    args = parser.parse_args()

    if args.command == "run":
        # The product CLI owns an explicit campaign lifecycle. The hidden
        # compatibility flag keeps the low-level driver's historical direct
        # invocation semantics available to older runbooks.
        forwarded = ["--skip-final-test"]
        if args.iterations:
            forwarded += ["--iterations", str(args.iterations)]
        if args.hours:
            forwarded += ["--hours", str(args.hours)]
        if args.measure_noise:
            forwarded += ["--measure-noise", str(args.measure_noise)]
        raise SystemExit(_run("tools/run_loop.py", forwarded))
    if args.command == "status":
        status = campaign_status()
        print(json.dumps(status, indent=2))
        return
    if args.command == "finalize":
        raise SystemExit(_run("tools/run_loop.py", ["--finalize"]))
    if args.command == "new":
        archive, manifest = new_campaign(args.name, start_from_best=not args.from_current)
        if archive:
            print(f"Archived previous campaign to {archive.relative_to(PROJECT_ROOT)}")
        print(f"Created campaign {manifest['id']!r}. Run: python3 autoeval.py run")
        return
    if args.command in {"dashboard", "demo"}:
        cmd = [sys.executable, str(PROJECT_ROOT / "tools/dashboard_server.py"), "--port", str(args.port)]
        if args.command == "demo":
            cmd += [
                "--tsv", str(PROJECT_ROOT / "examples/writing-style/sample-results.tsv"),
                "--config", str(PROJECT_ROOT / "examples/writing-style/config.yaml"),
                "--demo",
            ]
        if args.open:
            cmd.append("--open")
        try:
            raise SystemExit(subprocess.call(cmd, cwd=PROJECT_ROOT))
        except KeyboardInterrupt:
            raise SystemExit(130)
    if args.command == "report":
        report = PROJECT_ROOT / ".tmp/run-summary.md"
        if not report.exists():
            print("No report yet. Run or finalize a campaign first.", file=sys.stderr)
            raise SystemExit(1)
        print(report.read_text(encoding="utf-8"))
        return
    if args.command == "benchmark":
        forwarded = ["--campaigns", str(args.campaigns), "--iterations", str(args.iterations)]
        if args.execute:
            forwarded.append("--execute")
        raise SystemExit(_run("tools/run_benchmark.py", forwarded))


if __name__ == "__main__":
    main()
