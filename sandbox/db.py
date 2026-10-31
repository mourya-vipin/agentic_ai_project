"""The sandbox's one SQLite file, state_dir()/access.sqlite3, its schema, and the
stamp every write carries. Owner: Vipin Mourya.

    with db.connect() as conn:        # commits on success, always closes
        conn.execute(...)

Every table has `synthetic` = 1 on every row: every record here is made up. Every
ID starts SYN-, and every secret starts FAKE-.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3

from harness import runtime
from harness.paths import state_dir
from harness.trace import now as wall_now

DB_FILE = "access.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clock (now TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, dept TEXT NOT NULL,
    title TEXT NOT NULL, role TEXT NOT NULL, manager_id TEXT, employment TEXT NOT NULL,
    status TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS systems (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_dept TEXT NOT NULL, sensitivity TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, system_id TEXT NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL,
    sensitivity TEXT NOT NULL, needs_manager INTEGER NOT NULL DEFAULT 0,
    protected INTEGER NOT NULL DEFAULT 0, max_days INTEGER,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS role_policy (
    role TEXT NOT NULL, group_id TEXT NOT NULL, kind TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (role, group_id));

CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, group_id TEXT NOT NULL,
    granted_at TEXT NOT NULL, expires_at TEXT, removed_at TEXT, request_id TEXT,
    removed_request_id TEXT, actor TEXT NOT NULL, stamp TEXT NOT NULL, removed_stamp TEXT, key TEXT,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY, employee_id TEXT NOT NULL, system_id TEXT NOT NULL, kind TEXT NOT NULL,
    secret TEXT NOT NULL, created_at TEXT NOT NULL, rotated_at TEXT, stamp TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, source TEXT NOT NULL, requester_id TEXT,
    subject_id TEXT, text TEXT NOT NULL, received_at TEXT NOT NULL, status TEXT NOT NULL,
    route TEXT, note TEXT, stamp TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, action TEXT, request_id TEXT, employee_id TEXT,
    group_id TEXT, thread_id TEXT, body TEXT, reason TEXT, status TEXT NOT NULL,
    created_at TEXT NOT NULL, closed_at TEXT, stamp TEXT NOT NULL, closed_stamp TEXT, key TEXT UNIQUE,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS identity_keys (
    key TEXT PRIMARY KEY, tool TEXT NOT NULL, result_json TEXT NOT NULL, stamp TEXT NOT NULL,
    at TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS notices_sent (
    key TEXT PRIMARY KEY, thread_id TEXT NOT NULL, message_id INTEGER, template_id TEXT NOT NULL,
    stamp TEXT NOT NULL, at TEXT NOT NULL, synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    employee_id TEXT, group_id TEXT, request_id TEXT, stamp TEXT NOT NULL, detail TEXT,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS faults (
    tool TEXT PRIMARY KEY, remaining INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'transient',
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS request_claims (
    request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, at TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 1);

CREATE INDEX IF NOT EXISTS memberships_by_employee ON memberships (employee_id, removed_at);
CREATE INDEX IF NOT EXISTS memberships_by_group ON memberships (group_id, removed_at);
-- one active membership per person and group, so two grants at once cannot make two
CREATE UNIQUE INDEX IF NOT EXISTS memberships_one_active ON memberships (employee_id, group_id)
    WHERE removed_at IS NULL;
"""

TABLES = ("clock", "employees", "systems", "groups", "role_policy", "memberships", "credentials", "requests",
          "pending_actions", "identity_keys", "notices_sent", "audit_log", "faults", "request_claims")


def remove() -> None:
    """Delete the file (reset)."""
    file = path()
    _ready.discard(str(file))
    if file.exists():
        file.unlink()


def path():
    """Where the file is, read at call time: run.py moves state_dir() per run."""
    return state_dir() / DB_FILE


_ready: set[str] = set()      # database files whose schema this process has created


@contextlib.contextmanager
def connect():
    """A connection whose rows read as dicts; commits on success and always closes."""
    file = path()
    fresh = not file.exists() or str(file) not in _ready
    conn = sqlite3.connect(file, timeout=10)
    conn.row_factory = _dict_row
    try:
        if fresh:
            conn.executescript(SCHEMA)
            _ready.add(str(file))
        with conn:
            yield conn
    finally:
        conn.close()


def _dict_row(cursor, row) -> dict:
    return {c[0]: v for c, v in zip(cursor.description, row)}


def current_stamp() -> str:
    """The run's ID in a run.py run, the console session's ID in the app, and
    "manual" for a write made by hand on a product page."""
    run = runtime.current_run()
    return run.run_id if run else (runtime.current_session() or "manual")


def audit(conn, *, actor: str, action: str, stamp: str, employee_id=None, group_id=None, request_id=None,
          detail=None) -> None:
    """One line of the audit log, in the caller's transaction."""
    conn.execute("INSERT INTO audit_log (at, actor, action, employee_id, group_id, request_id, stamp, detail) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (wall_now(), actor, action, employee_id, group_id, request_id, stamp,
                  None if detail is None else json.dumps(detail, default=str)))


def next_id(conn, table: str, prefix: str) -> str:
    """The next ID in a series such as SYN-PEND-0001, from the highest one stored."""
    row = conn.execute(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
                       (f"{prefix}%",)).fetchone()
    number = int(row["id"][len(prefix):]) + 1 if row else 1
    return f"{prefix}{number:04d}"
