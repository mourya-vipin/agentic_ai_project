"""Messages mid-task: what the operator says while a run of the session is live (Part 2, task 7).

Owner: Aman Panigrahi. The store is state_dir()/inbox.sqlite3, one row for each message.

operator_turn (agent/service.py) takes a message in this order: a command first (agent/commands.py,
never queued), then the stated limits (policy/limits.py), then, if a run of the session is live
in this process, inbox.post. Our code takes each message one of four ways, from its words, never
a model's judgement:

    carry on     it adds to the task ("also ...", "and ...", "one more thing", "... as well"):
                 the run keeps its work and reads the message before its next model call
    start over   it replaces the task ("instead", "forget that", "scratch that", "never mind",
                 "change of plan"): the run ends "replaced" before its next model call, the
                 router starts a fresh run with the message, and the operator sees what the
                 old run did, and the /rollback that undoes it
    queue        anything else, a question too ("how far along are you?"): it waits, and the
                 router takes it as a new message when the turn ends
    reject       the queue already holds MAX_QUEUED messages: the operator knows at once

    live_run(session_id)        the run of the session that runs in this process now, or None
    post(session_id, text, cp)  take one message: the Reply says which way
    take(run_key)               the loop, before each model call: the messages that add to or
                                replace the run's task (each one only once)
    after_turn(session_id)      the router, when the turn ends: the replacement, then the queue
    reset()                     delete the store (the sandbox's reset calls it)

A path that does not call take() (a workflow has no model call; the planned path until its
executor calls it) gets no message mid-run: after_turn hands it on as a new message instead.

A run that waits for a stranger's reply or for an approval has not finished either, though no
turn of it runs now (waiting_run). A message to it takes the same ways, from the same words:

    carry on     kept for that run (status "for_run"): when the reply or the answer resumes it,
                 the run reads the message before its next model call, as a live run does
    start over   the waiting run ends "replaced" at once, its approvals withdrawn; the router
                 starts a fresh run with the message, and the operator sees what the old run did
    queue        the router answers it now: the run makes no call while it waits, and a wait
                 can last days, so a queue behind it would have no bound on its response time

    waiting_run(session_id)              the run of the session that waits for a reply or an approval
    post_to_waiting(session_id, text, cp)   (way, Reply or None) for one message to that run
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path

from harness import runtime
from harness.paths import state_dir
from harness.trace import now
from harness.turns import Reply

DB_FILE = "inbox.sqlite3"
ADD, REPLACE, QUEUE, REJECT = "carry_on", "start_over", "queue", "reject"
MAX_QUEUED = 5
FOR_RUN = "for_run"            # a message that adds to the task of a run that waits, kept until it resumes
WAITS = {"reply": "a reply", "approval": "your approval"}   # the waits a message can reach

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_key TEXT NOT NULL,
    text TEXT NOT NULL, way TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL);
"""

_REPLACE = re.compile(r"\b(instead|forget (that|it|this|the last)|scratch that|never ?mind|change of plan|"
                      r"start over|do this one instead)\b", re.I)
_ADD = re.compile(r"^\s*(also|and also|and|plus|one more( thing)?|in addition|additionally)\b|\b(as well|too)\s*[.!]?\s*$",
                  re.I)


def _path() -> Path:
    return state_dir() / DB_FILE


@contextlib.contextmanager
def _connect(*, write: bool = False):
    conn = sqlite3.connect(_path(), timeout=10)
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
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(_path()) + suffix).unlink(missing_ok=True)


def way_of(text: str) -> str:
    """How our code takes the message: start over, carry on, or queue."""
    if _REPLACE.search(text or ""):
        return REPLACE
    if _ADD.search(text or ""):
        return ADD
    return QUEUE


def live_run(session_id: str):
    """The run of the session that runs now, in this process (checkpoints.PROCESS), or None.
    A run that another process left as running is a crashed run, not a live one."""
    from agent import checkpoints
    runs = [cp for cp in checkpoints.live(session_id) if cp.status == checkpoints.RUNNING
            and cp.process == checkpoints.PROCESS]
    return runs[-1] if runs else None


def post(session_id: str, text: str, cp) -> Reply:
    """Take one message that came while the run `cp` is live, and tell the operator which way."""
    way = way_of(text)
    with _connect(write=True) as conn:
        waiting = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND status = 'new' "
                               "AND way = ?", (session_id, QUEUE)).fetchone()["n"]
        if way == QUEUE and waiting >= MAX_QUEUED:
            way = REJECT
        conn.execute("INSERT INTO messages (session_id, run_key, text, way, status, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (session_id, cp.run_key, str(text), way, "rejected" if way == REJECT else "new", now()))
    runtime.trace(session_id).event("midtask_message", run_key=cp.run_key, way=way, queued=waiting)
    said = {
        ADD: f"Run {cp.run_no} is working. It adds your message to its task, and reads it before its next step.",
        REPLACE: f"Run {cp.run_no} is working. Your message replaces its task: the run stops before its next step, "
                 "and a new run starts with your message. You will see what the old run did.",
        QUEUE: f"Run {cp.run_no} is working. Your message waits, and it is answered when the run's turn ends.",
        REJECT: f"Run {cp.run_no} is working, and {MAX_QUEUED} messages already wait, so this one is not kept. "
                "Send it again when the run ends, or /stop the run.",
    }[way]
    return Reply(said, [f"{way.replace('_', ' ')}: {cp.run_key}"])


def take(run_key: str | None) -> list[dict]:
    """The messages for this run that add to or replace its task, oldest first, each once.
    The loop calls it before each model call. A message kept for the run while it waited
    (status "for_run") comes too."""
    if not run_key:
        return []
    with _connect(write=True) as conn:
        rows = conn.execute("SELECT * FROM messages WHERE run_key = ? AND status IN ('new', ?) AND way IN (?, ?) "
                            "ORDER BY id", (run_key, FOR_RUN, ADD, REPLACE)).fetchall()
        conn.executemany("UPDATE messages SET status = 'taken' WHERE id = ?", [(r["id"],) for r in rows])
    return [dict(r) for r in rows]


def after_turn(session_id: str) -> list[dict]:
    """When a turn ends: the replacement that a run took (first), then each message that still
    waits (the queue, and a message for a path that did not take it), oldest first. Each is
    handed on once."""
    with _connect(write=True) as conn:
        replaced = conn.execute("SELECT * FROM messages WHERE session_id = ? AND status = 'taken' AND way = ? "
                                "ORDER BY id", (session_id, REPLACE)).fetchall()
        waiting = conn.execute("SELECT * FROM messages WHERE session_id = ? AND status = 'new' ORDER BY id",
                               (session_id,)).fetchall()
        conn.executemany("UPDATE messages SET status = 'handed_on' WHERE id = ?",
                         [(r["id"],) for r in [*replaced, *waiting]])
    return [dict(r) for r in [*replaced[-1:], *waiting]]


def waiting_run(session_id: str):
    """The latest run of the session that waits for a stranger's reply or for an approval, or
    None. A run that a stop or an escalation holds is not one: /resume and /answer carry it on."""
    from agent import checkpoints
    runs = [cp for cp in checkpoints.live(session_id)
            if cp.status in (checkpoints.WAITING_REPLY, checkpoints.WAITING_APPROVAL)]
    return runs[-1] if runs else None


def post_to_waiting(session_id: str, text: str, cp) -> tuple[str, Reply | None]:
    """Take one message to the run `cp`, which waits. Returns its way, and the Reply for the
    operator; None for the router to answer (a question, anything else). A replacement ends the
    run now: the caller starts the fresh run and lists what the old one did."""
    from agent import approvals, bounds, checkpoints
    way = way_of(text)
    wait = (cp.waiting_for or {}).get("kind") or ("reply" if cp.status == checkpoints.WAITING_REPLY else "approval")
    what = WAITS.get(wait, "something")
    trace = runtime.trace(session_id)
    if way == ADD:
        with _connect(write=True) as conn:
            conn.execute("INSERT INTO messages (session_id, run_key, text, way, status, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (session_id, cp.run_key, str(text), way, FOR_RUN, now()))
        trace.event("midtask_message", run_key=cp.run_key, way=way, waits_for=wait)
        return way, Reply(f"Run {cp.run_no} waits for {what}. Your message is added to its task: the run reads it "
                          "when it carries on.", [f"carry on: {cp.run_key}"])
    if way == REPLACE:
        withdrawn = approvals.withdraw(cp.run_key)
        cp.status, cp.waiting_for = checkpoints.CANCELLED, None
        checkpoints.save(cp, mode="sync")
        with _connect(write=True) as conn:
            conn.execute("INSERT INTO messages (session_id, run_key, text, way, status, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (session_id, cp.run_key, str(text), way, "handed_on", now()))
        trace.event("midtask_message", run_key=cp.run_key, way=way, waits_for=wait, withdrawn=withdrawn)
        trace.end(bounds.REPLACED, f"{cp.run_key} waited for {what}; the operator's message replaced its task")
        return way, None
    trace.event("midtask_message", run_key=cp.run_key, way=way, waits_for=wait, answered_now=True)
    return way, None
