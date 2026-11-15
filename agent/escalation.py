"""The escalation: a run that stops to ask the operator while it runs, on a signal named in
advance (Part 2, task 5; Lecture 5, slides 34 to 38). Owner: Shruti Tewaney.

The four decisions, made before the agent runs (slide 37):

    the trigger   one of SIGNALS, set by our code, never the model's "low confidence":
                  ambiguous_write       a write whose outcome the three exits cannot settle
                                        (agent/exits.py, Aman), on U2, U4 and U6
                  memory_contradiction  a stored preference contradicts the request (U4)
                  blocked_three_times   the same step refused three times
                  An empty search is "not determined" and retried; doubt about a reversible,
                  visible action proceeds. Neither escalates.
    the packet    what the run attempted, why it stopped, what it needs from the operator:
                  three non-empty fields, shown on the console as a progress line.
    the hold      the run checkpoints as held (waiting_for: escalation) and nothing proceeds.
                  No answer within TIMEOUT_HOURS ends it held_timeout, never approved. An
                  answer, in this or a later console session (/answer <packet> <text>),
                  resumes it in its own session, so its replayed writes keep their keys.
    the budget    DAILY_CAP asks a day, escalations and approvals counted together against
                  the one reviewer's minutes. Past it, a signal still holds its run but sends
                  no packet: the run waits in the queue marked over the cap, until the
                  operator opens it from the console (/packet <id>).

    escalate(run_key, signal, *, attempted, why, needs) -> packet_id   (C3.4)
    answer(packet_id, text)        the operator's answer: logged, then the run resumes
    expire(now=None)               ends every packet past its deadline: held_timeout
    held()                         the packets still waiting, for the queue page
"""

from __future__ import annotations

import contextlib
import importlib
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from harness import runtime
from harness.paths import state_dir
from harness.trace import now
from harness.turns import Reply

DB_FILE = "escalations.sqlite3"
SIGNALS = ("ambiguous_write", "memory_contradiction", "blocked_three_times")
PENDING, ANSWERED, TIMED_OUT = "pending", "answered", "timed_out"

# end reasons (C6)
ESCALATED, HELD_TIMEOUT = "escalated", "held_timeout"

# The hold: an escalation waits at most one working day for the operator.
TIMEOUT_HOURS = 8
# The budget: asks a day, packets and approvals together. Shruti's Assignment 5 replay asked
# 14 a day at 42.9 s each (reports/LAB_05.md, L80-81): about ten minutes of one reviewer.
DAILY_CAP = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
    id TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, run_key TEXT NOT NULL, session_id TEXT NOT NULL,
    signal TEXT NOT NULL, attempted TEXT NOT NULL, why TEXT NOT NULL, needs TEXT NOT NULL,
    status TEXT NOT NULL, sent INTEGER NOT NULL, created_at TEXT NOT NULL, deadline TEXT NOT NULL,
    answered_at TEXT, answer TEXT, answered_in TEXT);
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
    """Delete the escalations file, when the sandbox is reset (make app, run.py)."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(state_dir() / DB_FILE) + suffix).unlink(missing_ok=True)


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None


def normal_id(packet_id: str) -> str:
    """ESC-2, esc-0002 and ESC-0002 name the same packet."""
    match = re.fullmatch(r"ESC-(\d{1,6})", str(packet_id or "").strip().upper())
    return f"ESC-{int(match[1]):04d}" if match else str(packet_id or "").strip().upper()


def _today(at: str) -> str:
    return at[:10]


def asks_today(at: str | None = None) -> int:
    """Today's asks of the reviewer: the packets sent and the approvals put up (UTC day)."""
    day = _today(at or now())
    with _connect() as conn:
        packets = conn.execute("SELECT COUNT(*) AS n FROM packets WHERE sent = 1 AND substr(created_at, 1, 10) = ?",
                               (day,)).fetchone()["n"]
    approvals = _module("agent.approvals")
    asked = 0
    if approvals is not None:
        with approvals._connect() as conn:
            asked = conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE substr(requested_at, 1, 10) = ?",
                                 (day,)).fetchone()["n"]
    return packets + asked


def packet_text(row) -> str:
    return (f"Run {row['run_key']} is held and waits for you ({row['id']}, {row['signal']}).\n"
            f"What it attempted: {row['attempted']}\n"
            f"Why it stopped: {row['why']}\n"
            f"What it needs from you: {row['needs']}\n"
            f"Answer with /answer {row['id']} <your answer>. Nothing proceeds until you do; "
            f"with no answer by {row['deadline']} the run ends held, never approved.")


def escalate(run_key: str, signal: str, *, attempted: str, why: str, needs: str) -> str:
    """Hold the run on a named signal and put the packet up for the operator (C3.4). The
    caller ends the turn with ESCALATED and makes no further write."""
    if signal not in SIGNALS:
        raise ValueError(f"the signal is {signal!r}; use one of {', '.join(SIGNALS)} (never low confidence)")
    fields = {"attempted": attempted, "why": why, "needs": needs}
    empty = [k for k, v in fields.items() if not str(v or "").strip()]
    if empty:
        raise ValueError(f"the packet's {', '.join(empty)} is empty: say what the run attempted, why it stopped "
                         "and what it needs")
    session_id = run_key.rpartition("#")[0] or run_key
    at = now()
    deadline = (datetime.fromisoformat(at) + timedelta(hours=TIMEOUT_HOURS)).isoformat(timespec="milliseconds")
    over = asks_today(at) >= DAILY_CAP
    with _connect(write=True) as conn:
        n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 AS n FROM packets").fetchone()["n"]
        packet_id = f"ESC-{n:04d}"
        conn.execute("INSERT INTO packets (id, n, run_key, session_id, signal, attempted, why, needs, status, sent, "
                     "created_at, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (packet_id, n, run_key, session_id, signal, str(attempted).strip(), str(why).strip(),
                      str(needs).strip(), PENDING, 0 if over else 1, at, deadline))
        row = conn.execute("SELECT * FROM packets WHERE id = ?", (packet_id,)).fetchone()
    _hold(run_key, packet_id)
    trace = runtime.trace(session_id)
    trace.event("escalation", packet_id=packet_id, run_key=run_key, signal=signal, over_cap=over,
                attempted=row["attempted"], why=row["why"], needs=row["needs"], deadline=deadline)
    if not over:
        trace.progress(packet_text(row))
    return packet_id


def _hold(run_key: str, packet_id: str) -> None:
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(run_key) if checkpoints is not None else None
    if cp is None:
        return
    cp.status = checkpoints.HELD
    cp.waiting_for = {"kind": "escalation", "packet_id": packet_id}
    checkpoints.save(cp, mode="sync")


def hold_reply(packet_id: str) -> Reply:
    """What the turn that escalated says to the operator."""
    row = get(packet_id)
    if row is None:
        return Reply(f"There is no escalation {packet_id}.")
    if not row["sent"]:
        return Reply(f"Run {row['run_key']} is held ({row['id']}). Today's asks are past the cap ({DAILY_CAP}), so it "
                     f"waits in the queue: open it with /packet {row['id']}.", actions=[f"held {row['run_key']}"])
    return Reply(packet_text(row), actions=[f"held {row['run_key']}"])


def get(packet_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM packets WHERE id = ?", (normal_id(packet_id),)).fetchone()
    return dict(row) if row else None


def open_packet(packet_id: str) -> Reply:
    """/packet <id>: the operator opens a packet, one over the cap included."""
    row = get(packet_id)
    return Reply(packet_text(row)) if row else Reply(f"There is no escalation {packet_id}.")


def held() -> list[dict]:
    """The packets still waiting for the operator, oldest first, with the over-the-cap mark."""
    expire()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM packets WHERE status = ? ORDER BY n", (PENDING,)).fetchall()
    return [dict(r) for r in rows]


def answer(packet_id: str, text: str, session_id: str | None = None) -> Reply:
    """The operator's answer to a packet: recorded, then the held run resumes in its own
    session (so its keys and its trace stay its own), from its checkpoint."""
    if not str(text or "").strip():
        return Reply(f"Nothing was done: write your answer after /answer {packet_id}.")
    expire()
    at = now()
    with _connect(write=True) as conn:
        row = conn.execute("SELECT * FROM packets WHERE id = ?", (normal_id(packet_id),)).fetchone()
        if row is None:
            return Reply(f"There is no escalation {packet_id}.")
        if row["status"] != PENDING:
            return Reply(f"Escalation {row['id']} is {row['status'].replace('_', ' ')} already; nothing changed.")
        conn.execute("UPDATE packets SET status = ?, answered_at = ?, answer = ?, answered_in = ? WHERE id = ?",
                     (ANSWERED, at, str(text).strip(), session_id, row["id"]))
    seconds = round((datetime.fromisoformat(at) - datetime.fromisoformat(row["created_at"])).total_seconds(), 3)
    runtime.trace(row["session_id"]).event("escalation_answered", packet_id=row["id"], run_key=row["run_key"],
                                           seconds=seconds, answered_in=session_id)
    checkpoints = _module("agent.checkpoints")
    cp = checkpoints.load(row["run_key"]) if checkpoints is not None else None
    if cp is None or cp.status in checkpoints.ENDED:
        return Reply(f"Recorded your answer to {row['id']}; no saved run waits on it.")
    with runtime.in_session(row["session_id"]):
        return checkpoints.resume(row["run_key"], {"kind": "operator", "text": str(text).strip(),
                                                   "packet_id": row["id"]})


def expire(at: str | None = None) -> list[str]:
    """End every packet past its deadline: the run ends held_timeout, never approved."""
    at = at or now()
    with _connect(write=True) as conn:
        rows = conn.execute("SELECT * FROM packets WHERE status = ? AND deadline <= ?", (PENDING, at)).fetchall()
        for row in rows:
            conn.execute("UPDATE packets SET status = ? WHERE id = ?", (TIMED_OUT, row["id"]))
    checkpoints = _module("agent.checkpoints")
    for row in rows:
        cp = checkpoints.load(row["run_key"]) if checkpoints is not None else None
        if cp is not None and cp.status not in checkpoints.ENDED:
            cp.status, cp.waiting_for = checkpoints.FAILED, None
            checkpoints.save(cp, mode="sync")
        trace = runtime.trace(row["session_id"])
        trace.event("escalation_timeout", packet_id=row["id"], run_key=row["run_key"], deadline=row["deadline"])
        trace.end(HELD_TIMEOUT, f"{row['id']}: no answer by {row['deadline']}; nothing was approved")
    return [r["id"] for r in rows]
