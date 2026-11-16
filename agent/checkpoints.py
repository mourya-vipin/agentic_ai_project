"""The run store: one checkpoint for each run, in state_dir()/runs.sqlite3.

Owner: Aman Panigrahi.

A checkpoint holds the state of one run: the task, the plan, the status of each step,
the journal of completed calls, the findings and the current step. It does not hold
memory. The memory store is a different module (agent/memory.py).

The run key is "<session_id>#<run_no>". In a run.py run, the session ID is the run ID.
In the app, one console session can have many runs, and each new run has the next
number. The same key names the wait, the stop flag and the hold of the run.

    new_run(session_id, use_case, task)       make a run with the next run number
    save(cp, mode=...)                        write the checkpoint: "sync", "async" or "exit"
    load(run_key)                             read the checkpoint, or None
    live(session_id)                          the runs that are not done, failed or cancelled
    crashed(session_id)                       the runs that a stopped process left as running
    waiting(kind)                             the runs that wait for a reply, an approval, ...
    runs_of(session_id, limit)                the runs of one session, newest first (the console's panel)
    resume(run_key, event)                    continue a run from its checkpoint
    journaled(run_key, key)                   the recorded result of a completed call, or None
    journal_of(run_key)                       the whole journal of the run, without a flush
    record_completed(run_key, key, ...)       add one completed call to the journal
    end_turn(cp, end_reason)                  save the run when its turn ends: done, paused or ended
    save_step(cp, step, messages=...)         save the run after one step of its path
    mode_for(use_case)                        the durability mode of the use case (DURABILITY)

The durability modes (Lecture 4, slide 12):

    sync    Write the checkpoint before the next step starts. This mode is the slowest and the safest.
    async   Write the checkpoint in a background thread. A crash before the write loses that step.
    exit    Write the checkpoint only when the run stops: it ends, fails or waits for a person.
            A crash before that loses the whole run.

The journal is a table of its own. The registry adds an entry after each successful
write, and save() only adds entries. Because of this, the save of an old copy
of a checkpoint cannot remove an entry that the registry wrote.

Nothing resumes a run at start-up. A run continues only after one of these calls:
the next turn of the operator, a reply from a stranger, an approval, or /resume.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness import runtime
from harness.paths import state_dir
from harness.trace import now
from harness.turns import Reply

DB_FILE = "runs.sqlite3"

RunKey = str                    # f"{session_id}#{run_no}". In run.py, session_id is the run ID.

RUNNING = "running"
WAITING_REPLY, WAITING_APPROVAL, HELD, STOPPED = "waiting_reply", "waiting_approval", "held", "stopped"
DONE, FAILED, CANCELLED = "done", "failed", "cancelled"
PAUSED = (WAITING_REPLY, WAITING_APPROVAL, HELD, STOPPED)     # the run waits; the next call continues it
ENDED = (DONE, FAILED, CANCELLED)                             # the run cannot continue
STATUSES = (RUNNING, *PAUSED, *ENDED)

MODES = ("sync", "async", "exit")
WAIT_KINDS = ("reply", "approval", "escalation", "stop")
EVENT_KINDS = ("reply", "approval", "stop_lifted", "operator", "restart")

# The process that saves: each save writes it, so a run that is "running" but was last saved
# by another process stopped with that process (a crash, or a restart of the app). The
# token is new for each process, so a new process with the same PID is not the old one.
PROCESS = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

# A console run keeps the last 6 messages of its conversation: 3 exchanges, as the
# router reads them (agent/router.py, HISTORY_TURNS). The task stays whole in `task`.
CONVERSATION_TURNS = 6

# The function that continues a run of each use case: resume(cp, event) -> Reply. The
# router opens and ends the runs of the workflows and of the loop, so it also continues
# them (router.resume). A U6 run on the planned path continues in the executor.
RESUMERS = {
    "standard_grant": "agent.router:resume",
    "onboarding": "agent.router:resume",
    "offboarding": "agent.router:resume",
    "nonstandard_request": "agent.router:resume",
    "operator_review": "agent.router:resume",
    "bulk_change": "agent.router:resume",
}
PLANNED_RESUMER = "agent.executor:resume"

# The durability mode of each use case (Lecture 4, slide 12; M2.md, Part 1, task 2).
# sync:  a run that can do an irreversible step. U2 issues a credential, U4 can send,
#        and U6 can send manager notes.
# async: a run whose writes all have a key and can be undone (U1, U3). A replay of a lost
#        step gets the same key, and the sandbox keeps the first result of each key.
# exit:  a run that only reads (U5). A crash loses only the answer, and the operator asks again.
DURABILITY = {
    "standard_grant": "async",
    "onboarding": "sync",
    "offboarding": "async",
    "nonstandard_request": "sync",
    "operator_review": "exit",
    "bulk_change": "sync",
}

# The status that each end reason (trace.end) gives the run. Each name comes from the
# module that ends the turn: agent/bounds.py, agent/executor.py, agent/registry.py and
# agent/control.py. An end reason that is not here gives "failed".
ENDS_DONE = ("done", "answered", "workflow_done", "plan_done", "nothing_to_do", "unsupported_instruction",
             "approval_denied")
ENDS_PAUSED = {                       # end reason -> (status, wait kind)
    "awaiting_approval": (WAITING_APPROVAL, "approval"),
    "waiting_reply": (WAITING_REPLY, "reply"),
    "stopped": (STOPPED, "stop"),
    "escalated": (HELD, "escalation"),
}
ENDS_CANCELLED = ("cancelled", "replaced")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_no INTEGER NOT NULL,
    use_case TEXT NOT NULL, status TEXT NOT NULL, wait_kind TEXT, state TEXT NOT NULL,
    updated_at TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, UNIQUE (session_id, run_no));
CREATE TABLE IF NOT EXISTS journal (
    run_key TEXT NOT NULL, key TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL,
    result TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (run_key, key));
"""

# The fields of a checkpoint that go in the `state` column, as JSON.
STATE_FIELDS = ("task", "plan", "steps", "findings", "current_step", "waiting_for", "conversation", "messages",
                "process")


@dataclass
class Checkpoint:
    run_key: RunKey
    session_id: str
    run_no: int
    use_case: str
    status: str                       # one of STATUSES
    task: str                         # the operator's request, whole (never summarized)
    plan: dict | None = None          # the plan as validated (U6)
    steps: list[dict] = field(default_factory=list)   # [{"n", "status", "tool", "args", "key", "action_class"}]
    completed: dict = field(default_factory=dict)     # key -> {"tool", "args", "result", "at"}: the journal
    findings: dict = field(default_factory=dict)
    current_step: int | None = None
    waiting_for: dict | None = None   # {"kind": "reply", "thread_id", "deadline"} | {"kind": "approval", "approval_id"}
                                      # | {"kind": "escalation", "packet_id"} | {"kind": "stop"}
    conversation: list[dict] | None = None            # console runs only, trimmed to CONVERSATION_TURNS
    messages: list[dict] | None = None                # the loop's own conversation, so that its resume carries it on
    process: str = ""                                 # the process that saved it last (PROCESS)
    updated_at: str = ""


def key_of(session_id: str, run_no: int) -> RunKey:
    """The run key of run `run_no` in this session."""
    return f"{session_id}#{run_no}"


# -- the file -------------------------------------------------------------------------------

def _path() -> Path:
    return state_dir() / DB_FILE


@contextlib.contextmanager
def _connect(path: Path | None = None, *, write: bool = False):
    """Open the run store. It commits if the block has no error, and it always closes.
    A stop can come from another thread while a turn runs. Because of this, the
    connection waits for a lock and does not fail at once. A write takes the lock at
    the start, so that two writers cannot block each other."""
    conn = sqlite3.connect(path or _path(), timeout=10)
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
    """Delete the run store. Call it when the sandbox is reset (make app, run.py), so that
    no saved run uses records that are gone."""
    _writer.discard()
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(_path()) + suffix).unlink(missing_ok=True)


# -- the background writer, for the async mode --------------------------------------------

_seq_lock = threading.Lock()
_last_seq = 0


def _next_seq() -> int:
    """A number that increases with each save. It starts from the clock, so that the
    saves of a new process come after the saves of the process before it."""
    global _last_seq
    with _seq_lock:
        _last_seq = max(time.time_ns(), _last_seq + 1)
        return _last_seq


class _Writer:
    """One background thread that writes the async saves: only the newest save of each run."""

    def __init__(self):
        self._pending: dict[tuple[str, str], dict] = {}     # (file, run_key) -> row
        self._cond = threading.Condition()
        self._busy = False
        self._thread: threading.Thread | None = None

    def put(self, row: dict) -> None:
        with self._cond:
            self._pending[(row["path"], row["run_key"])] = row
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="checkpoint-writer", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def drop(self, path: str, run_key: RunKey) -> None:
        """Remove the async save of this run that is not written yet: a sync save replaces it."""
        with self._cond:
            self._pending.pop((path, run_key), None)

    def discard(self) -> None:
        with self._cond:
            self._pending.clear()

    def flush(self) -> None:
        """Wait until the thread writes every async save."""
        with self._cond:
            while self._pending or self._busy:
                self._cond.wait(timeout=1)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait()
                _, row = self._pending.popitem()
                self._busy = True
            try:
                _write(row)
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()


_writer = _Writer()
atexit.register(_writer.flush)      # a normal exit writes the async saves; a crash does not


def flush() -> None:
    """Write every async save now. load(), live() and waiting() call it first."""
    _writer.flush()


# -- rows -----------------------------------------------------------------------------------

def _json(value, what: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"the checkpoint field {what} is not JSON ({exc}): a checkpoint holds "
                         "only JSON values, never open files or connections") from exc


def _row(cp: Checkpoint) -> dict:
    state = {name: getattr(cp, name) for name in STATE_FIELDS}
    return {"path": str(_path()), "run_key": cp.run_key, "session_id": cp.session_id, "run_no": cp.run_no,
            "use_case": cp.use_case, "status": cp.status,
            "wait_kind": (cp.waiting_for or {}).get("kind"),
            "state": _json(state, "state"),
            "journal": [(key, entry.get("tool") or "", _json(entry.get("args") or {}, f"completed[{key}].args"),
                         _json(entry.get("result"), f"completed[{key}].result"), entry.get("at") or cp.updated_at)
                        for key, entry in (cp.completed or {}).items()],
            "updated_at": cp.updated_at, "seq": _next_seq()}


def _write(row: dict) -> None:
    """Write one checkpoint. A save with a lower sequence number than the stored one
    changes nothing, so an old async save cannot replace a newer save."""
    with _connect(Path(row["path"]), write=True) as conn:
        conn.execute(
            "INSERT INTO runs (run_key, session_id, run_no, use_case, status, wait_kind, state, updated_at, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_key) DO UPDATE SET "
            "use_case = excluded.use_case, status = excluded.status, wait_kind = excluded.wait_kind, "
            "state = excluded.state, updated_at = excluded.updated_at, seq = excluded.seq "
            "WHERE excluded.seq > runs.seq",
            (row["run_key"], row["session_id"], row["run_no"], row["use_case"], row["status"], row["wait_kind"],
             row["state"], row["updated_at"], row["seq"]))
        conn.executemany("INSERT OR IGNORE INTO journal (run_key, key, tool, args, result, at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", [(row["run_key"], *entry) for entry in row["journal"]])


def _journal(conn, run_key: RunKey) -> dict:
    rows = conn.execute("SELECT key, tool, args, result, at FROM journal WHERE run_key = ? ORDER BY at, key",
                        (run_key,)).fetchall()
    return {r["key"]: {"tool": r["tool"], "args": json.loads(r["args"]), "result": json.loads(r["result"]),
                       "at": r["at"]} for r in rows}


def _from_row(row, journal: dict) -> Checkpoint:
    state = json.loads(row["state"])
    return Checkpoint(run_key=row["run_key"], session_id=row["session_id"], run_no=row["run_no"],
                      use_case=row["use_case"], status=row["status"], completed=journal,
                      updated_at=row["updated_at"], **{name: state[name] for name in STATE_FIELDS if name in state})


def _check(cp: Checkpoint, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"the mode is {mode!r}; use one of {', '.join(MODES)}")
    if cp.status not in STATUSES:
        raise ValueError(f"the status is {cp.status!r}; use one of {', '.join(STATUSES)}")
    if cp.waiting_for is not None and cp.waiting_for.get("kind") not in WAIT_KINDS:
        raise ValueError(f"waiting_for has the kind {cp.waiting_for.get('kind')!r}; "
                         f"use one of {', '.join(WAIT_KINDS)}")
    if cp.run_key != key_of(cp.session_id, cp.run_no):
        raise ValueError(f"the run key {cp.run_key!r} does not agree with {key_of(cp.session_id, cp.run_no)!r}")


# -- the functions that the paths call ------------------------------------------------------

def new_run(session_id: str, use_case: str, task: str) -> Checkpoint:
    """Make a run with the next run number of its session, and write it at once (sync).
    The stop and the queue can then find the run before its first step."""
    at = now()
    state = _json({"task": task, "process": PROCESS}, "task")
    with _connect(write=True) as conn:
        cur = conn.execute(
            "INSERT INTO runs (run_key, session_id, run_no, use_case, status, wait_kind, state, updated_at, seq) "
            "SELECT ? || '#' || n, ?, n, ?, ?, NULL, ?, ?, ? "
            "FROM (SELECT COALESCE(MAX(run_no), 0) + 1 AS n FROM runs WHERE session_id = ?)",
            (session_id, session_id, use_case, RUNNING, state, at, _next_seq(), session_id))
        run_no = conn.execute("SELECT run_no FROM runs WHERE rowid = ?", (cur.lastrowid,)).fetchone()["run_no"]
    cp = Checkpoint(key_of(session_id, run_no), session_id, run_no, use_case, RUNNING, task, updated_at=at,
                    process=PROCESS)
    runtime.trace(session_id).event("run_opened", run_key=cp.run_key, use_case=use_case)
    return cp


def save(cp: Checkpoint, *, mode: str) -> None:
    """Write the checkpoint in its durability mode. In the exit mode, a save writes
    nothing while the status is "running". When the run stops (it waits or it ends), the
    checkpoint also goes into the trace: state/ is not committed, but the trace is."""
    _check(cp, mode)
    if cp.conversation is not None:
        cp.conversation = list(cp.conversation)[-CONVERSATION_TURNS:]
    cp.updated_at = now()
    cp.process = PROCESS
    row = _row(cp)
    if mode == "exit" and cp.status == RUNNING:
        return
    if mode == "async":
        _writer.put(row)
    else:
        _writer.drop(row["path"], cp.run_key)
        _write(row)
    if cp.status != RUNNING:
        _trace_stop(cp)


def _trace_stop(cp: Checkpoint) -> None:
    with _connect() as conn:
        journal = _journal(conn, cp.run_key)
    last = {**asdict(cp), "completed": {**journal, **(cp.completed or {})},
            "messages": len(cp.messages or [])}           # the count: each call is in the trace already
    kind = "checkpoint_final" if cp.status in ENDED else "checkpoint_paused"
    runtime.trace(cp.session_id).event(kind, run_key=cp.run_key, checkpoint=last)


def load(run_key: RunKey) -> Checkpoint | None:
    """The checkpoint of the run, with its full journal, or None."""
    flush()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_key = ?", (run_key,)).fetchone()
        if row is None:
            return None
        return _from_row(row, _journal(conn, run_key))


def live(session_id: str) -> list[Checkpoint]:
    """The runs of the session that are not done, failed or cancelled, by run number."""
    flush()
    marks = ", ".join("?" for _ in ENDED)
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM runs WHERE session_id = ? AND status NOT IN ({marks}) ORDER BY run_no",
                            (session_id, *ENDED)).fetchall()
        return [_from_row(r, _journal(conn, r["run_key"])) for r in rows]


def crashed(session_id: str) -> list[Checkpoint]:
    """The runs of the session that are "running", but that another process saved last:
    that process stopped during the run (a crash, or a restart of the app). By run number."""
    return [cp for cp in live(session_id) if cp.status == RUNNING and cp.process and cp.process != PROCESS]


def runs_of(session_id: str, limit: int = 20) -> list[Checkpoint]:
    """The runs of one session, in every status, newest first: at most `limit` of them."""
    flush()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE session_id = ? ORDER BY run_no DESC LIMIT ?",
                            (session_id, int(limit))).fetchall()
        return [_from_row(r, _journal(conn, r["run_key"])) for r in rows]


def waiting(kind: str | None = None) -> list[Checkpoint]:
    """The runs that wait for something, of one kind (reply, approval, escalation, stop)
    or of all kinds. The oldest wait comes first."""
    if kind is not None and kind not in WAIT_KINDS:
        raise ValueError(f"the kind is {kind!r}; use one of {', '.join(WAIT_KINDS)}")
    flush()
    marks = ", ".join("?" for _ in ENDED)
    sql = f"SELECT * FROM runs WHERE wait_kind IS NOT NULL AND status NOT IN ({marks})"
    params: tuple = ENDED
    if kind is not None:
        sql, params = sql + " AND wait_kind = ?", (*ENDED, kind)
    with _connect() as conn:
        rows = conn.execute(sql + " ORDER BY updated_at, run_key", params).fetchall()
        return [_from_row(r, _journal(conn, r["run_key"])) for r in rows]


def journaled(run_key: RunKey, key: str) -> dict | None:
    """The recorded result of a completed call, {"tool", "args", "result", "at"}, or None.
    A replay that finds it uses the result and does not make the call again."""
    with _connect() as conn:
        return _journal(conn, run_key).get(key)


def journal_of(run_key: RunKey) -> dict:
    """The journal of the run, key -> {"tool", "args", "result", "at"}. The registry writes it at
    once (sync), so this reads it without a flush, and an async save of the run does not wait."""
    with _connect() as conn:
        return _journal(conn, run_key)


def record_completed(run_key: RunKey, key: str, *, tool: str, args: dict, result) -> None:
    """Add one completed call to the journal of the run, at once (sync). The registry
    calls it after each successful write. The first result of a key stays, as the
    target keeps the first result of a key."""
    entry = (key, tool, _json(args, "args"), _json(result, "result"), now())
    with _connect(write=True) as conn:
        conn.execute("INSERT OR IGNORE INTO journal (run_key, key, tool, args, result, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (run_key, *entry))


def mode_for(use_case: str) -> str:
    """The durability mode of the use case (DURABILITY). An unknown use case gets "sync"."""
    return DURABILITY.get(use_case, "sync")


def end_turn(cp: Checkpoint, end_reason: str, *, waiting_for: dict | None = None,
             mode: str | None = None) -> Checkpoint:
    """Save the run when its turn ends, with the status that the end reason gives:
    done, paused (the run waits for a reply, an approval or /resume) or ended.
    `waiting_for` adds the facts of the wait, for example the thread of a reply."""
    if end_reason in ENDS_PAUSED:
        cp.status, kind = ENDS_PAUSED[end_reason]
        cp.waiting_for = {**(waiting_for or {}), "kind": kind}
    else:
        cp.status = DONE if end_reason in ENDS_DONE else CANCELLED if end_reason in ENDS_CANCELLED else FAILED
        cp.waiting_for = None
    cp.findings = {**(cp.findings or {}), "end_reason": end_reason}
    save(cp, mode=mode or mode_for(cp.use_case))
    return cp


def save_step(cp: Checkpoint, step: dict, *, messages: list[dict] | None = None) -> Checkpoint:
    """Save the run after one step of its path, in the durability mode of its use case.
    `step` is the record of the step, and `messages` is the conversation of the loop.
    During the step, the approval gate, an escalation or /cancel can save a new status for
    the run (a wait, a hold or the end). That status stays, with its wait: a step does not
    write "running" over it. The store is read here without a flush, because those
    writers save at once (sync)."""
    steps = list(cp.steps or [])
    if steps and steps[-1].get("n") is not None and steps[-1].get("n") == step.get("n"):
        steps[-1] = step                 # the same step again: its record before the calls, now with their results
    else:
        steps.append(step)
    cp.steps = steps
    cp.current_step = step.get("n", cp.current_step)
    if messages is not None:
        cp.messages = [dict(m) for m in messages]
    with _connect() as conn:
        row = conn.execute("SELECT status, state FROM runs WHERE run_key = ?", (cp.run_key,)).fetchone()
    if row is not None and row["status"] != RUNNING:
        cp.status, cp.waiting_for = row["status"], json.loads(row["state"]).get("waiting_for")
    save(cp, mode=mode_for(cp.use_case))
    return cp


def resume(run_key: RunKey, event: dict) -> Reply:
    """Continue a run from its checkpoint, after one event:

        {"kind": "reply", "thread_id", "text"}                  a reply on the outside channel
        {"kind": "approval", "approval_id", "approved", "note"} the answer of the operator
        {"kind": "stop_lifted", "new_input"}                    /resume, after a stop
        {"kind": "operator", "text"}                            the next turn (for a run that a crash stopped)

    A reply or an approval continues only the run that waits for it. The path of the
    use case continues the run (RESUMERS). Until that path can do it, the run stays
    saved, and the reply tells the operator."""
    kind = (event or {}).get("kind")
    if kind not in EVENT_KINDS:
        raise ValueError(f"the event kind is {kind!r}; use one of {', '.join(EVENT_KINDS)}")
    cp = load(run_key)
    if cp is None:
        return Reply(f"Run {run_key} has no checkpoint, so it cannot continue.")
    trace = runtime.trace(cp.session_id)
    why = _refusal(cp, event)
    if why:
        trace.event("resume_refused", run_key=run_key, event_kind=kind, status=cp.status, why=why)
        return Reply(f"Run {cp.run_no} did not continue: {why}.")
    target, name = _resumer(cp)
    trace.event("resume", run_key=run_key, event_kind=kind, status=cp.status, current_step=cp.current_step,
                resumer=name if target else None)
    if target is None:
        return Reply(f"Run {cp.run_no} ({cp.use_case}) is saved at step {cp.current_step}, "
                     f"but its path cannot continue it yet ({name} is not built).")
    return target(cp, event)


def _refusal(cp: Checkpoint, event: dict) -> str:
    """Why this event must not continue this run, or "" if it can. Only /resume
    (stop_lifted) continues a stopped run. A reply that comes while the run is stopped
    stays on its thread, and the resume of the path can read it there."""
    if cp.status in ENDED:
        return f"it is {cp.status}"
    if event["kind"] != "stop_lifted" and _stop_flag(cp.run_key):
        return "it is stopped, and /resume continues it"
    wait = cp.waiting_for or {}
    if event["kind"] == "reply":
        if wait.get("kind") != "reply":
            return "it does not wait for a reply"
        if wait.get("thread_id") != event.get("thread_id"):
            return f"it waits for a reply on {wait.get('thread_id')}, not on {event.get('thread_id')}"
    if event["kind"] == "approval":
        if wait.get("kind") != "approval":
            return "it does not wait for an approval"
        if wait.get("approval_id") and wait.get("approval_id") != event.get("approval_id"):
            return f"it waits for the approval {wait.get('approval_id')}, not {event.get('approval_id')}"
    return ""


def _stop_flag(run_key: RunKey) -> bool:
    """Whether the operator stopped or cancelled the run (agent/control.py)."""
    from agent import control       # here, not at the top: control finds this module the same way
    return control.stopped(run_key)


def _resumer(cp: Checkpoint):
    """(the function that continues this run or None, and the name of that function).
    A U6 run continues in the executor if it has a plan, or if the router opened it on
    the planned path (findings["path"])."""
    planned = cp.use_case == "bulk_change" and (cp.plan or (cp.findings or {}).get("path") == "planned")
    name = PLANNED_RESUMER if planned else RESUMERS.get(cp.use_case, "")
    module_name, _, function = name.partition(":")
    if not module_name:
        return None, f"a path for {cp.use_case}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        return None, name
    return getattr(module, function, None), name
