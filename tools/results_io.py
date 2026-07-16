"""
Results TSV IO
===============
Header-based read/append/update for results.tsv, shared by experiment_runner,
run_loop, and dashboard_server so the schema lives in exactly one place.

Column layout: BASE_COLUMNS + metric columns (config-driven) + TAIL_COLUMNS
+ EXTRA_COLUMNS. EXTRA_COLUMNS were added in July 2026; older files are
migrated in place by extending the header line (old rows simply read as
blank for the new columns).
"""

import csv
import os
import uuid
from pathlib import Path

BASE_COLUMNS = ["run_id", "timestamp", "composite_score"]
TAIL_COLUMNS = ["change_description", "decision"]
EXTRA_COLUMNS = ["composite_stddev", "n_samples", "judge_errors", "holdout_composite"]


def expected_header(metric_names: list[str]) -> list[str]:
    return BASE_COLUMNS + list(metric_names) + TAIL_COLUMNS + EXTRA_COLUMNS


def read_header(tsv_path: Path) -> list[str]:
    """Return the header columns of an existing results.tsv (empty if absent)."""
    if not tsv_path.exists():
        return []
    first = tsv_path.read_text(encoding="utf-8").split("\n", 1)[0]
    return first.split("\t") if first else []


def read_rows(tsv_path: Path) -> list[dict]:
    """Read all data rows as dicts keyed by header column. Missing trailing
    values (rows written under an older header) come back as None."""
    if not tsv_path.exists():
        return []
    with open(tsv_path, encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f, delimiter="\t")]


def _atomic_write(tsv_path: Path, content: str) -> None:
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = tsv_path.with_name(f".{tsv_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, tsv_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def ensure_header(tsv_path: Path, metric_names: list[str]) -> list[str]:
    """Create the file with the current header, or migrate an existing header
    to include any missing EXTRA_COLUMNS. Returns the effective header."""
    if not tsv_path.exists():
        header = expected_header(metric_names)
        _atomic_write(tsv_path, "\t".join(header) + "\n")
        return header

    header = read_header(tsv_path)
    missing = [c for c in EXTRA_COLUMNS if c not in header]
    if missing:
        lines = tsv_path.read_text(encoding="utf-8").split("\n")
        lines[0] = "\t".join(header + missing)
        _atomic_write(tsv_path, "\n".join(lines))
        header = header + missing
    return header


def append_row(tsv_path: Path, values: dict, metric_names: list[str]) -> None:
    """Atomically append one row in the file's actual header order."""
    header = ensure_header(tsv_path, metric_names)
    row = "\t".join(str(values.get(col, "")) for col in header)
    existing = tsv_path.read_text(encoding="utf-8")
    _atomic_write(tsv_path, existing + row + "\n")


def update_last_row(tsv_path: Path, updates: dict) -> None:
    """Update columns of the last data row by name (atomic write)."""
    if not tsv_path.exists():
        return
    lines = tsv_path.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return
    header = lines[0].split("\t")
    parts = lines[-1].split("\t")
    # Pad rows written under an older, shorter header
    parts += [""] * (len(header) - len(parts))
    for col, val in updates.items():
        if col in header:
            parts[header.index(col)] = str(val)
    lines[-1] = "\t".join(parts)
    _atomic_write(tsv_path, "\n".join(lines) + "\n")


def best_composite(tsv_path: Path) -> float:
    """Best confirmed composite (BASELINE/KEEP only; 0.0 if no data).

    Candidate scores marked DISCARD or left incomplete must never become the
    incumbent merely because their raw score was high.
    """
    best = 0.0
    for row in read_rows(tsv_path):
        if (row.get("decision") or "").strip() not in {"BASELINE", "KEEP"}:
            continue
        try:
            best = max(best, float(row.get("composite_score") or 0.0))
        except (TypeError, ValueError):
            continue
    return best


def latest_run_id(tsv_path: Path) -> str | None:
    rows = read_rows(tsv_path)
    if not rows:
        return None
    return rows[-1].get("run_id") or None


def main():
    """CLI for the Claude-Code-driven loop (program.md): update the last row's
    decision/holdout columns without hand-editing the TSV."""
    import argparse

    parser = argparse.ArgumentParser(description="Update the last row of results.tsv")
    parser.add_argument("--tsv", default="results.tsv")
    parser.add_argument("--decision", choices=["KEEP", "DISCARD", "BASELINE"])
    parser.add_argument("--holdout-composite", default=None)
    args = parser.parse_args()

    updates = {}
    if args.decision:
        updates["decision"] = args.decision
    if args.holdout_composite is not None:
        updates["holdout_composite"] = args.holdout_composite
    if not updates:
        parser.error("nothing to update — pass --decision and/or --holdout-composite")

    update_last_row(Path(args.tsv), updates)
    print(f"Updated last row of {args.tsv}: {updates}")


if __name__ == "__main__":
    main()
