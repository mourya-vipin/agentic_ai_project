"""The stop, and its three commands: cancel, resume, rollback (Part 2, task 6).

Owner: Shruti Tewaney.

The flag lives outside the agent and outside the sandbox, in state_dir()/control.sqlite3,
so a stop survives a restart and no model call or tool can lift it. policy/hook.py reads
stopped(run_key) before every tool call, first, so every later call of a stopped run is
refused, whoever makes it (the loop, the executor, a workflow, a second agent).

    stop(session_id, run_no=None)   flags one run, or every live run of the session
    stopped(run_key)                the hook's question, before every call
    cancel(run_key)                 ends the run for good; what it did stays
    resume(run_key)                 lifts the flag, recorded with its time, and carries on
    rollback(run_key)               undoes each write newest first, where it can be undone

Every command is written to the `log` table with its time, and to the session's trace.

Until agent/checkpoints.py (Aman, C3.1) lands, stop() without a run number flags the
whole session (run key "<session_id>#*"), since nothing yet lists a session's live
runs, and resume() and rollback() lift the flag and plan the compensation but have no
saved run to carry on or journal to read.
"""

from __future__ import annotations

import contextlib
import importlib
import sqlite3

from harness import runtime
from harness.paths import state_dir
from harness.trace import now
from harness.turns import Reply

DB_FILE = "control.sqlite3"
ANY_RUN = "*"                  # the run number of a session-wide flag

STOPPED, CANCELLED, LIFTED = "stopped", "cancelled", "lifted"

# end reasons (C6)
END_STOPPED, END_CANCELLED, END_ROLLED_BACK = "stopped", "cancelled", "rolled_back"

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    run_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, state TEXT NOT NULL,
    stopped_at TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_key TEXT NOT NULL,
    command TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
"""


@contextlib.contextmanager
def _connect():
    """The control file: commits on success, always closes. A stop arrives from another
    thread while a turn runs, so the file waits for a lock rather than failing."""
    conn = sqlite3.connect(state_dir() / DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def run_key(session_id: str, run_no: int | str) -> str:
    """C3.1's run key."""
    return f"{session_id}#{run_no}"


def split_key(key: str) -> tuple[str, str]:
    """(session_id, run number) of a run key; the session ID may itself hold '#'."""
    session_id, _, run_no = key.rpartition("#")
    if not session_id:
        raise ValueError(f"not a run key: {key!r} (it is <session_id>#<run number>)")
    return session_id, run_no


def _module(name: str):
    """A teammate's module, or None while it is not built yet."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None


def _log(conn, key: str, command: str, detail: str = "") -> str:
    at = now()
    conn.execute("INSERT INTO log (at, run_key, command, detail) VALUES (?, ?, ?, ?)", (at, key, command, detail))
    return at


def _set(conn, key: str, state: str) -> str:
    session_id, _ = split_key(key)
    at = now()
    conn.execute("INSERT INTO flags (run_key, session_id, state, stopped_at, changed_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(run_key) DO UPDATE SET state = excluded.state, changed_at = excluded.changed_at, "
                 "stopped_at = CASE WHEN excluded.state = 'stopped' THEN excluded.stopped_at ELSE flags.stopped_at END",
                 (key, session_id, state, at, at))
    return at


def _state(conn, key: str) -> str | None:
    row = conn.execute("SELECT state FROM flags WHERE run_key = ?", (key,)).fetchone()
    return row["state"] if row else None


# -- the flag -----------------------------------------------------------------------------

def stop(session_id: str, run_no: int | None = None) -> list[str]:
    """Flag one run, or every live run of the session, as stopped, and withdraw any
    approval they wait on. Returns the run keys flagged. A cancelled run stays cancelled."""
    if run_no is not None:
        keys = [run_key(session_id, run_no)]
    else:
        checkpoints = _module("agent.checkpoints")
        live = [cp.run_key for cp in checkpoints.live(session_id)] if checkpoints is not None else None
        keys = live if live else [run_key(session_id, ANY_RUN)]
    flagged = []
    with _connect() as conn:
        for key in keys:
            if _state(conn, key) == CANCELLED:
                continue
            at = _set(conn, key, STOPPED)
            _log(conn, key, "stop", at)
            flagged.append(key)
    approvals = _module("agent.approvals")
    for key in flagged:
        if approvals is not None and hasattr(approvals, "withdraw"):
            approvals.withdraw(key)
    runtime.trace(session_id).event("stop_flag", run_keys=flagged)   # the line's own `at` is the stop's time
    return flagged


def stopped(key: str | None) -> bool:
    """Whether a call of this run must be refused: its own flag, or its session's, is set."""
    if not key:
        return False
    session_id, _ = split_key(key)
    with _connect() as conn:
        rows = conn.execute("SELECT state FROM flags WHERE run_key IN (?, ?)",
                            (key, run_key(session_id, ANY_RUN))).fetchall()
    return any(r["state"] in (STOPPED, CANCELLED) for r in rows)


def state(key: str) -> str | None:
    """The run's own flag: stopped, cancelled, lifted, or None if it was never stopped."""
    with _connect() as conn:
        return _state(conn, key)


def history(key: str | None = None) -> list[dict]:
    """The command log, oldest first: every stop, cancel, resume and rollback with its time."""
    with _connect() as conn:
        rows = conn.execute("SELECT at, run_key, command, detail FROM log"
                            + (" WHERE run_key = ?" if key else "") + " ORDER BY id", (key,) if key else ()).fetchall()
    return [dict(r) for r in rows]


# -- the three commands -------------------------------------------------------------------

def cancel(key: str) -> Reply:
    """End the run for good. What it already did stays; rollback undoes it."""
    session_id, run_no = split_key(key)
    with _connect() as conn:
        if _state(conn, key) == CANCELLED:
            return Reply(f"Run {run_no} was already cancelled.")
        _set(conn, key, CANCELLED)
        _log(conn, key, "cancel")
    runtime.trace(session_id).end(END_CANCELLED, key)
    return Reply(f"Run {run_no} is cancelled. What it already did stays; /rollback {run_no} undoes what can be undone.",
                 actions=[f"cancelled run {run_no}"])


def resume(key: str, new_input: str | None = None) -> Reply:
    """Lift the stop, recorded with its time, and carry on from the saved state. A stop
    of the whole session is lifted with it."""
    session_id, run_no = split_key(key)
    with _connect() as conn:
        own = _state(conn, key)
        if own == CANCELLED:
            return Reply(f"Run {run_no} was cancelled, so it cannot be resumed.")
        lifted = [k for k in (key, run_key(session_id, ANY_RUN)) if _state(conn, k) == STOPPED]
        if not lifted:
            return Reply(f"Run {run_no} is not stopped.")
        for k in lifted:
            _set(conn, k, LIFTED)
            _log(conn, k, "resume", new_input or "")
    runtime.trace(session_id).event("stop_lifted", run_keys=lifted)
    checkpoints = _module("agent.checkpoints")
    if checkpoints is None or checkpoints.load(key) is None:
        return Reply(f"The stop on run {run_no} is lifted. There is no saved state to carry on from.",
                     actions=[f"lifted the stop on run {run_no}"])
    return checkpoints.resume(key, {"kind": "stop_lifted", "new_input": new_input})


# -- rollback -----------------------------------------------------------------------------

def compensations(completed: dict) -> list[dict]:
    """The undo of each journaled write (C3.1's `completed`), newest first. A write with
    none gets {"tool": None, "why": ...}, so rollback can print "none" for it."""
    entries = sorted(completed.values(), key=lambda e: e.get("at") or "", reverse=True)
    out = []
    for entry in entries:
        tool, args, result = entry.get("tool"), dict(entry.get("args") or {}), entry.get("result") or {}
        undo = {"of": tool, "at": entry.get("at")}
        if tool == "access_grant":
            out.append({**undo, "tool": "access_revoke", "args": {
                "employee_id": args.get("employee_id"), "group_id": args.get("group_id"), "reason": "rollback"}})
        elif tool == "access_revoke":
            out.append({**undo, "tool": "access_grant", "args": {
                "employee_id": args.get("employee_id"), "group_id": args.get("group_id")}})
        elif tool == "access_revoke_all":
            for group in result.get("removed") or []:
                out.append({**undo, "tool": "access_grant", "args": {
                    "employee_id": result.get("employee_id") or args.get("employee_id"), "group_id": group}})
            if not result.get("removed"):
                out.append({**undo, "tool": None, "why": "nothing was removed"})
        else:
            why = {"reply_send": "a sent message cannot be recalled",
                   "channel_ask_template": "a sent message cannot be recalled",
                   "notices_send_template": "a sent message cannot be recalled",
                   "requests_set_status": "the journal does not hold the status before",
                   "credentials_issue": "a credential is rotated by a person, not undone"}.get(
                tool, "the operator dismisses a pending record on /pending")
            out.append({**undo, "tool": None, "why": why})
    return out


def rollback(key: str) -> Reply:
    """Apply each write's compensation, newest first, through the registry with
    origin="operator_command", printing "none" where there is none. The run stays stopped."""
    session_id, run_no = split_key(key)
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(key) if checkpoints is not None else None
    if cp is None:
        return Reply(f"Run {run_no} has no saved journal, so there is nothing to roll back.")
    with _connect() as conn:
        if _state(conn, key) not in (STOPPED, CANCELLED):
            _set(conn, key, STOPPED)        # nothing else of the run may run while it is undone
            _log(conn, key, "stop", "before rollback")
    from agent.registry import CallContext, get_registry
    registry, lines = get_registry(), []
    for n, undo in enumerate(compensations(cp.completed or {}), start=1):
        if undo["tool"] is None:
            lines.append(f"{undo['of']}: none ({undo['why']})")
            continue
        ctx = CallContext(session_id, n, "workflow", cp.use_case, run_key=key, origin="operator_command")
        result = registry.call(undo["tool"], undo["args"], ctx)
        lines.append(f"{undo['of']} → {undo['tool']} {undo['args'].get('group_id')}: "
                     + ("failed: " + result.content[:120] if result.is_error else "done"))
    with _connect() as conn:
        _log(conn, key, "rollback", "; ".join(lines))
    runtime.trace(session_id).end(END_ROLLED_BACK, key)
    return Reply(f"Rolled back run {run_no}, newest first:\n" + ("\n".join(lines) or "nothing to undo"),
                 actions=lines)
