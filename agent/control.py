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
    rollback(run_key, only=None)    undoes each write newest first, where it can be undone, or only
                                    the numbers the operator picks from undo_list(run_key)

Every command is written to the `log` table with its time, and to the session's trace.

stop() without a run number flags every live run of the session (agent/checkpoints.py:
live), or the whole session (run key "<session_id>#*") when none is saved yet. resume()
carries the run on through checkpoints.resume; rollback() reads the run's journal, which
the registry writes after every successful write, and its undo calls pass the hook's stop
check as the operator's command (origin="operator_command").
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

# the end reasons this module writes
END_STOPPED, END_CANCELLED, END_ROLLED_BACK = "stopped", "cancelled", "rolled_back"

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    run_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, state TEXT NOT NULL,
    stopped_at TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_key TEXT NOT NULL,
    command TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS undone (
    run_key TEXT NOT NULL, undo_id TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (run_key, undo_id));
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


def reset() -> None:
    """Delete the control file, when the sandbox is reset (make app, run.py)."""
    from pathlib import Path
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(state_dir() / DB_FILE) + suffix).unlink(missing_ok=True)


def run_key(session_id: str, run_no: int | str) -> str:
    """The run key agent/checkpoints.py gives a run: "<session_id>#<run number>"."""
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


def flags(state: str | None = None) -> list[dict]:
    """Every run's flag, or those in one state (stopped, cancelled, lifted), oldest stop first."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM flags" + (" WHERE state = ?" if state else "") + " ORDER BY stopped_at",
                            (state,) if state else ()).fetchall()
    return [dict(r) for r in rows]


def history(key: str | None = None) -> list[dict]:
    """The command log, oldest first: every stop, cancel, resume and rollback with its time."""
    with _connect() as conn:
        rows = conn.execute("SELECT at, run_key, command, detail FROM log"
                            + (" WHERE run_key = ?" if key else "") + " ORDER BY id", (key,) if key else ()).fetchall()
    return [dict(r) for r in rows]


def notice(key: str, task: str, *, resumed: bool = False) -> None:
    """The operator's notice that a run is live, naming it and how to stop it: before the run's
    first call, and again when a held or waiting run resumes (the stop's first condition, watched)."""
    session_id, run_no = split_key(key)
    what = " ".join(str(task or "").split())[:120].rstrip(". ")
    trace = runtime.trace(session_id)
    trace.progress(f"Run {run_no} {'resumed' if resumed else 'is live'}: {what}. /stop {run_no} stops it.")
    trace.event("run_notice", run_key=key, resumed=resumed)


# -- the three commands -------------------------------------------------------------------

def cancel(key: str) -> Reply:
    """End the run for good. What it already did stays; rollback undoes it."""
    session_id, run_no = split_key(key)
    with _connect() as conn:
        if _state(conn, key) == CANCELLED:
            return Reply(f"Run {run_no} was already cancelled.")
        _set(conn, key, CANCELLED)
        _log(conn, key, "cancel")
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(key) if checkpoints is not None else None
    if cp is not None and cp.status not in checkpoints.ENDED:
        cp.status, cp.waiting_for = checkpoints.CANCELLED, None
        checkpoints.save(cp, mode="sync")
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
    """The undo of each journaled write (the checkpoint's `completed`), newest first. A write with
    none gets {"tool": None, "why": ...}, so rollback can print "none" for it."""
    entries = sorted(completed.items(), key=lambda kv: kv[1].get("at") or "", reverse=True)
    out = []
    for journal_key, entry in entries:
        tool, args, result = entry.get("tool"), dict(entry.get("args") or {}), entry.get("result") or {}
        undo = {"of": tool, "at": entry.get("at"), "id": str(journal_key)}
        if tool == "access_grant":
            out.append({**undo, "tool": "access_revoke", "args": {
                "employee_id": args.get("employee_id"), "group_id": args.get("group_id"), "reason": "rollback"}})
        elif tool == "access_revoke":
            out.append({**undo, "tool": "access_grant", "args": {
                "employee_id": args.get("employee_id"), "group_id": args.get("group_id")}})
        elif tool == "access_revoke_all":
            for group in result.get("removed") or []:
                out.append({**undo, "id": f"{journal_key}:{group}", "tool": "access_grant", "args": {
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


def undo_list(key: str) -> list[dict]:
    """The undo of each of the run's journaled writes that no rollback has handled yet, newest
    first, numbered from 1 as the operator picks them (/rollback <n> 2 3). An entry with
    "tool": None has no undo and prints "none"."""
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(key) if checkpoints is not None else None
    if cp is None:
        return []
    with _connect() as conn:
        done = {r["undo_id"] for r in conn.execute("SELECT undo_id FROM undone WHERE run_key = ?", (key,))}
    left = [u for u in compensations(cp.completed or {}) if u["id"] not in done]
    return [{**u, "n": n} for n, u in enumerate(left, start=1)]


def describe(undo: dict) -> str:
    """One line of the undo list, as the operator reads it before picking."""
    if undo["tool"] is None:
        return f"{undo['n']}. {undo['of']}: none ({undo['why']})"
    args = undo["args"]
    return f"{undo['n']}. {undo['of']} of {args.get('group_id')} for {args.get('employee_id')} → {undo['tool']}"


def rollback_list(key: str) -> Reply:
    """/rollback <n> list: the numbered undo list, and nothing done."""
    _, run_no = split_key(key)
    left = undo_list(key)
    if not left:
        return Reply(f"Run {run_no} has nothing left to undo.")
    return Reply("\n".join([f"Run {run_no} wrote, newest first; nothing is undone yet:", *map(describe, left),
                            f"/rollback {run_no} undoes them all; /rollback {run_no} 1 3 undoes only those."]))


def rollback(key: str, only: list[int] | None = None) -> Reply:
    """Apply each write's compensation, newest first, through the registry with
    origin="operator_command", printing "none" where there is none. With `only`, apply just
    those numbers of undo_list(key), the operator's pick; the others stay, and a later rollback
    can still undo them. The run stays stopped. Each undo is done once: a second rollback
    undoes only what no rollback has handled."""
    session_id, run_no = split_key(key)
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(key) if checkpoints is not None else None
    if cp is None:
        return Reply(f"Run {run_no} has no saved journal, so there is nothing to roll back.")
    left = undo_list(key)
    if only is not None:
        unknown = sorted(set(only) - {u["n"] for u in left})
        if unknown or not only:
            return Reply(f"Nothing was undone: run {run_no} has no undo numbered "
                         f"{', '.join(map(str, unknown)) or '(none given)'}. /rollback {run_no} list shows them.")
        left = [u for u in left if u["n"] in set(only)]
    last = next((h["at"] for h in reversed(history(key)) if h["command"] == "rollback"), None)
    if last is not None and not left:
        return Reply(f"Run {run_no} was rolled back at {last}, and it has written nothing since; nothing changed.")
    with _connect() as conn:
        if _state(conn, key) not in (STOPPED, CANCELLED):
            _set(conn, key, STOPPED)        # nothing else of the run may run while it is undone
            _log(conn, key, "stop", "before rollback")
    from agent.registry import CallContext, get_registry
    registry, lines, handled = get_registry(), [], []
    for n, undo in enumerate(left, start=1):
        if undo["tool"] is None:
            lines.append(f"{undo['of']}: none ({undo['why']})")
            handled.append(undo["id"])
            continue
        ctx = CallContext(session_id, n, "workflow", cp.use_case, run_key=key, origin="operator_command")
        result = registry.call(undo["tool"], undo["args"], ctx)
        lines.append(f"{undo['of']} → {undo['tool']} {undo['args'].get('group_id')}: "
                     + ("failed: " + result.content[:120] if result.is_error else "done"))
        if not result.is_error:
            handled.append(undo["id"])
    with _connect() as conn:
        at = _log(conn, key, "rollback", ("only " + ", ".join(map(str, only)) + ": " if only else "") + "; ".join(lines))
        conn.executemany("INSERT OR IGNORE INTO undone (run_key, undo_id, at) VALUES (?, ?, ?)",
                         [(key, u, at) for u in handled])
    runtime.trace(session_id).end(END_ROLLED_BACK, key)
    head = f"Rolled back run {run_no}" + (f", only {', '.join(map(str, only))}" if only else "") + ", newest first:"
    return Reply(head + "\n" + ("\n".join(lines) or "nothing to undo"), actions=lines)
