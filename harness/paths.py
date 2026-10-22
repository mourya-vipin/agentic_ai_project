"""Where the project keeps what it writes. Read at call time, so a test or a worker can move them.

    state_dir()    the live sandbox and the channel's thread store; never committed
    results_dir()  run.py's run records and their traces; commit them
    traces_dir()   the console sessions' traces

run.py gives its runs a state directory of their own (state/runs/), so an
experiment never touches the data the app shows. Keep every file your sandbox
writes under state_dir(): that is what makes a sweep safe to run in parallel.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness import ROOT

STATE_ENV = "PROJECT_STATE_DIR"
RESULTS_ENV = "PROJECT_RESULTS_DIR"
TRACES_ENV = "PROJECT_TRACES_DIR"


def _dir(env: str, default: Path) -> Path:
    path = Path(os.environ.get(env) or default)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    return _dir(STATE_ENV, ROOT / "state")


def results_dir() -> Path:
    return _dir(RESULTS_ENV, ROOT / "results")


def traces_dir() -> Path:
    return _dir(TRACES_ENV, ROOT / "traces")
