"""The sandbox's one SQLite file, state_dir()/access.sqlite3, and the stamp every
write carries. Owner: Vipin Mourya."""

from __future__ import annotations

from harness import runtime

DB_FILE = "access.sqlite3"


def current_stamp() -> str:
    """The run's ID in a run.py run, the console session's ID in the app, and
    "manual" for a write made by hand on a product page."""
    run = runtime.current_run()
    return run.run_id if run else (runtime.current_session() or "manual")
