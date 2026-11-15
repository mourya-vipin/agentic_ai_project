"""The conversation of each console session, in state_dir()/sessions.sqlite3.

Owner: Aman Panigrahi.

Each message of the operator and each reply of the agent is one row. The rows stay
after the app stops, so an earlier session can continue after `make serve` (Milestone 2,
Submitting). In a run.py run, the session ID is the run ID.

    history(session_id)          the messages of the session, oldest first: {"role", "text"}
    add(session_id, role, text)  add one message; the role is "operator" or "agent"
    reset()                      delete every session (when the sandbox is reset)

The app shows the history of a session from its trace (app/main.py). This store is
what the agent reads, so the agent knows the conversation after a restart too.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from harness.paths import state_dir
from harness.trace import now

DB_FILE = "sessions.sqlite3"
ROLES = ("operator", "agent")

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS turns_by_session ON turns (session_id, id);
"""


def _path() -> Path:
    return state_dir() / DB_FILE


@contextlib.contextmanager
def _connect():
    """Open the session store. It commits if the block has no error, and it always
    closes. Two console tabs can write at the same time, so the connection waits for a lock."""
    conn = sqlite3.connect(_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def history(session_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT role, text FROM turns WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return [{"role": r["role"], "text": r["text"]} for r in rows]


def add(session_id: str, role: str, text: str) -> None:
    if role not in ROLES:
        raise ValueError(f"the role is {role!r}; use one of {', '.join(ROLES)}")
    with _connect() as conn:
        conn.execute("INSERT INTO turns (session_id, role, text, at) VALUES (?, ?, ?, ?)",
                     (session_id, role, str(text), now()))


def reset() -> None:
    """Delete the session store."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(_path()) + suffix).unlink(missing_ok=True)
