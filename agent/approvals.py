"""The approval gate: what waits for the operator, the screen they read, their answer, and
the decision log (Part 2, task 4). Owner: Shruti Tewaney.

    request(ctx, tool, args, action_class, facts)   the registry hands a gated call here (C3.2)
    covers(run_key, action_class, args)             the hook asks: has the operator approved this exact call?
    pending()                                       what the approvals page lists (service.pending_approvals)
    decide(approval_id, approved, note, who)        the answer: logged, then the run resumes
    withdraw(run_key)                               a stop takes the run's waiting approvals off the page
    log(run_key=None)                               every decision, for the ask counts (Part 2, task 9)

A gated call never runs while it waits: the hook raised AskApproval before the tool, so the
sandbox is unchanged until the operator answers. request() stores the call in
state_dir()/approvals.sqlite3, checkpoints the run as waiting_approval, and the registry
ends the turn awaiting_approval. decide() resumes the run as a stranger's reply does
(checkpoints.resume), so an answer given after a restart still lands. The path then makes
the same call again; covers() finds the approval for exactly that call, once, and the hook
lets it run with ctx.approved set.

The screen shows information, not a recommendation, in this order: the dry run (the
effect exactly as it would be applied), then facts our code computed from the records,
then what still checks the output. The model's rationale is not shown. Each line says
whether our code computed it or the stranger asserted it.

An approval covers one call: the same run, action class and arguments, used once.
Approving at the plan (approval.bulk_change = plan) is not built yet.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from harness import runtime
from harness.paths import state_dir
from harness.trace import now
from harness.turns import Reply

DB_FILE = "approvals.sqlite3"
PENDING, APPROVED, DENIED, WITHDRAWN = "pending", "approved", "denied", "withdrawn"

# end reasons (C6)
AWAITING_APPROVAL, APPROVAL_DENIED = "awaiting_approval", "approval_denied"

TEXT_CHARS = 600          # the most of a stranger's message the screen quotes

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, run_key TEXT, session_id TEXT NOT NULL,
    tool TEXT NOT NULL, action_class TEXT NOT NULL, args TEXT NOT NULL, screen TEXT NOT NULL,
    status TEXT NOT NULL, requested_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT,
    note TEXT NOT NULL DEFAULT '', used_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL, run_key TEXT, action_class TEXT NOT NULL,
    shown TEXT NOT NULL, decision TEXT NOT NULL, who TEXT NOT NULL, seconds REAL,
    setting TEXT, at TEXT NOT NULL);
"""


@contextlib.contextmanager
def _connect(*, write: bool = False):
    conn = sqlite3.connect(state_dir() / DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    finally:
        conn.close()


def reset() -> None:
    """Delete the approvals file, when the sandbox is reset (make app, run.py)."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(state_dir() / DB_FILE) + suffix).unlink(missing_ok=True)


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None


def canonical(args: dict) -> str:
    """The arguments as compared: sorted JSON, without the registry's private fields (`_text`)."""
    return json.dumps({k: v for k, v in (args or {}).items() if not str(k).startswith("_")},
                      sort_keys=True, default=str)


def _seconds(since: str, until: str) -> float:
    return round((datetime.fromisoformat(until) - datetime.fromisoformat(since)).total_seconds(), 3)


def _setting() -> str | None:
    value = runtime.switch("approval.bulk_change", None)
    return None if value is None else str(value)


# -- the registry and the hook ------------------------------------------------------------

def request(ctx, tool: str, args: dict, action_class: str, facts: dict) -> str:
    """Put a gated call up for the operator and checkpoint its run as waiting for it.
    The same call of the same run, already waiting, gets the same approval: never two."""
    args_key = canonical(args)
    with _connect(write=True) as conn:
        row = conn.execute("SELECT id FROM approvals WHERE status = ? AND run_key IS ? AND action_class = ? "
                           "AND args = ?", (PENDING, ctx.run_key, action_class, args_key)).fetchone()
        if row is not None:
            approval_id, new = row["id"], False
        else:
            n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 AS n FROM approvals").fetchone()["n"]
            approval_id, new = f"APR-{n:04d}", True
            conn.execute("INSERT INTO approvals (id, n, run_key, session_id, tool, action_class, args, screen, status, "
                         "requested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (approval_id, n, ctx.run_key, ctx.session_id, tool, action_class, args_key,
                          json.dumps(screen(tool, args, action_class, facts)), PENDING, now()))
    runtime.trace(ctx.session_id).event("approval_requested", approval_id=approval_id, tool=tool,
                                        action_class=action_class, run_key=ctx.run_key, new=new)
    _checkpoint_waiting(ctx.run_key, approval_id)
    return approval_id


def _checkpoint_waiting(run_key: str | None, approval_id: str) -> None:
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(run_key) if checkpoints is not None and run_key else None
    if cp is None:
        return
    cp.status = checkpoints.WAITING_APPROVAL
    cp.waiting_for = {"kind": "approval", "approval_id": approval_id}
    checkpoints.save(cp, mode="sync")


def covers(run_key: str | None, action_class: str, args: dict) -> bool:
    """Whether the operator approved exactly this call of this run and it has not run yet.
    A yes covers one call: it is used here, so a second call asks again."""
    if not run_key:
        return False
    with _connect(write=True) as conn:
        row = conn.execute("SELECT id FROM approvals WHERE status = ? AND used_at IS NULL AND run_key = ? "
                           "AND action_class = ? AND args = ? ORDER BY n LIMIT 1",
                           (APPROVED, run_key, action_class, canonical(args))).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE approvals SET used_at = ? WHERE id = ?", (now(), row["id"]))
    return True


# -- the operator's side ------------------------------------------------------------------

def pending() -> list[dict]:
    """What waits for the operator, oldest first: the approval's ID, its console session,
    then the screen in the order it is read."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM approvals WHERE status = ? ORDER BY n", (PENDING,)).fetchall()
    return [{"id": r["id"], "session_id": r["session_id"], **json.loads(r["screen"]),
             "waiting since": r["requested_at"]} for r in rows]


def get(approval_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (str(approval_id).strip().upper(),)).fetchone()
    return dict(row) if row else None


def decide(approval_id: str, approved: bool, note: str = "", who: str | None = None) -> Reply:
    """Record the operator's answer and the decision, then resume the run that waits on it.
    `who` is "operator", or "scripted" for a run.py run's scripted answer."""
    who = who or ("scripted" if runtime.current_run() is not None else "operator")
    decision = APPROVED if approved else DENIED
    at = now()
    with _connect(write=True) as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (str(approval_id).strip().upper(),)).fetchone()
        if row is None:
            return Reply(f"There is no approval {approval_id}.")
        if row["status"] != PENDING:
            return Reply(f"Approval {row['id']} is {row['status']} already; nothing changed.")
        conn.execute("UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, note = ? WHERE id = ?",
                     (decision, at, who, note or "", row["id"]))
        _log(conn, row, decision, who, at)
    trace = runtime.trace(row["session_id"])
    trace.event("approval_decided", approval_id=row["id"], decision=decision, who=who,
                seconds=_seconds(row["requested_at"], at), action_class=row["action_class"], run_key=row["run_key"])
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(row["run_key"]) if checkpoints is not None and row["run_key"] else None
    if cp is not None and cp.status not in checkpoints.ENDED:
        return checkpoints.resume(row["run_key"], {"kind": "approval", "approval_id": row["id"],
                                                   "approved": bool(approved), "note": note or ""})
    if not approved:
        trace.end(APPROVAL_DENIED, row["id"])
        return Reply(f"Denied {row['id']}: {row['action_class']} did not run.", actions=[f"denied {row['id']}"])
    return Reply(f"Approved {row['id']}, but no saved run waits on it, so nothing ran.",
                 actions=[f"approved {row['id']}"])


def withdraw(run_key: str) -> list[str]:
    """Take the run's waiting approvals off the page, as a stop does. Each is logged; a
    resumed run that makes the call again asks again."""
    at = now()
    with _connect(write=True) as conn:
        rows = conn.execute("SELECT * FROM approvals WHERE status = ? AND run_key = ? ORDER BY n",
                            (PENDING, run_key)).fetchall()
        for row in rows:
            conn.execute("UPDATE approvals SET status = ?, decided_at = ?, decided_by = 'stop' WHERE id = ?",
                         (WITHDRAWN, at, row["id"]))
            _log(conn, row, WITHDRAWN, "stop", at)
    return [r["id"] for r in rows]


def _log(conn, row, decision: str, who: str, at: str) -> None:
    conn.execute("INSERT INTO decisions (approval_id, run_key, action_class, shown, decision, who, seconds, setting, at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (row["id"], row["run_key"], row["action_class"], row["screen"], decision, who,
                  _seconds(row["requested_at"], at), _setting(), at))


def log(run_key: str | None = None) -> list[dict]:
    """The decision log, oldest first: the action class, the facts shown, the decision, who
    decided, the seconds from put-up to answer, the approval.bulk_change setting, the run key."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM decisions" + (" WHERE run_key = ?" if run_key else "") + " ORDER BY id",
                            (run_key,) if run_key else ()).fetchall()
    return [{**dict(r), "shown": json.loads(r["shown"])} for r in rows]


# -- the screen ---------------------------------------------------------------------------

def screen(tool: str, args: dict, action_class: str, facts: dict) -> dict:
    """The approval screen of one call, in reading order: the dry run, the computed facts,
    what still checks it. Keys are the labels the page shows; "(computed)" marks what our code
    read from the records, "(their words)" what a stranger wrote."""
    from sandbox import directory

    out: dict = {"action": f"{action_class} ({tool})"}
    if tool == "access_grant":
        person = directory.get_employee(str(args.get("employee_id"))) or {}
        group = _group(str(args.get("group_id")))
        out["dry run"] = (f"Add {person.get('id')} ({person.get('name')}) to {group.get('id')} "
                          f"({group.get('name')})" + (f", until {args['expires_at']}" if args.get("expires_at") else "")
                          + (f", on request {args['request_id']}" if args.get("request_id") else "") + ".")
        policy = facts.get("policy") or {}
        if policy:
            out["role policy (computed)"] = f"{policy.get('decision')}: {policy.get('reason')}"
        out["already holds it (computed)"] = "yes" if facts.get("already_holds") else "no"
        members = _member_count(group.get("id"))
        out["reach (computed)"] = (f"one person; {group.get('id')} has {members} member(s) now, sensitivity "
                                   f"{group.get('sensitivity')}, system {group.get('system_id')}")
        if action_class == "access_grant/manager_confirmed" or action_class == "access_grant/high_sensitivity":
            manager = directory.manager_of(str(person.get("id"))) or {}
            out["manager's answer (computed)"] = (f"YES on record from {manager.get('id')} ({manager.get('name')}), "
                                                  "read by code from their reply; the operator still decides")
    elif tool == "reply_send":
        recipient = directory.get_employee(str(args.get("recipient_employee_id"))) or {}
        out["dry run"] = (f"Send to {recipient.get('id')} ({recipient.get('name')}, {recipient.get('email')}):\n"
                          + str(facts.get("text_as_sent") or args.get("body") or ""))
        out["reach (computed)"] = "one person; a sent message cannot be recalled"
    else:
        out["dry run"] = f"{tool} with {canonical(args)}"
    request = _request(args.get("request_id"))
    if request is not None:
        requester = directory.get_employee(str(request.get("requester_id"))) if request.get("requester_id") else None
        out["the request (computed)"] = (f"{request['id']}, from {request.get('source')}"
                                         + (f", by {requester['id']} ({requester['name']})" if requester else "")
                                         + f", status {request.get('status')}")
        out["the request's text (their words)"] = " ".join(str(request.get("text") or "").split())[:TEXT_CHARS]
    if tool == "reply_send":
        out["what still checks it"] = _critique_line()
    out["secrets (computed)"] = "none: the hook refuses any text or record that carries a credential's secret"
    return out


def _group(group_id: str) -> dict:
    from sandbox import db
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id.strip().upper(),)).fetchone()
    return dict(row) if row else {"id": group_id}


def _member_count(group_id: str | None) -> int:
    from sandbox import access
    return len(access.members(group_id)) if group_id else 0


def _request(request_id) -> dict | None:
    from sandbox import requests
    return requests.get(str(request_id)) if request_id else None


def _critique_line() -> str:
    if str(runtime.switch("critique.requester_reply", "off")) == "external":
        return ("the critique, when it is sent: it checks the groups the text says were granted, removed or "
                "waiting against the records; not tone, and not claims of approval")
    return "nothing after this screen: the critique is off (critique.requester_reply)"
