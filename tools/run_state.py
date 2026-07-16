"""Crash-safe state and single-process locking for the optimisation loop."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from utils import PROJECT_ROOT

LOCK_PATH = PROJECT_ROOT / ".tmp" / "run.lock"
STATE_PATH = PROJECT_ROOT / ".tmp" / "run_state.json"


def atomic_write_text(path: Path, content: str) -> None:
    """Write content beside its destination, then atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_state(phase: str, **fields) -> None:
    payload = {
        "phase": phase,
        "pid": os.getpid(),
        "updated_at": time.time(),
        **fields,
    }
    atomic_write_text(STATE_PATH, json.dumps(payload, indent=2) + "\n")


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def run_lock():
    """Prevent concurrent loops from mutating the same project state."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, json.dumps({"pid": os.getpid(), "started_at": time.time()}).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                existing = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
                owner = int(existing.get("pid", 0))
            except (OSError, ValueError, json.JSONDecodeError):
                owner = 0
            if _pid_is_alive(owner):
                raise RuntimeError(
                    f"another optimisation loop is already running (pid {owner}); "
                    f"lock: {LOCK_PATH}"
                )
            LOCK_PATH.unlink(missing_ok=True)

    try:
        yield
    finally:
        try:
            current = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if int(current.get("pid", 0)) == os.getpid():
            LOCK_PATH.unlink(missing_ok=True)
