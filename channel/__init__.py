"""The outside channel: a local thread store, in SQLite.

A thread is one conversation with one stranger, such as an email thread or a
ticket. Its ID is yours to choose: a message ID, a ticket number, an address.

Asking a stranger, the one way to build it:

    import channel
    channel.send(thread_id, "Which branch should it go to?")       # ask on the thread
    return Reply("Asked; waiting for the answer.", waiting_for=thread_id)   # and end the turn

The answer reaches your agent through agent.service.on_outside_reply(thread_id,
text): in the app when someone writes on the threads page, and in run.py when the
test task file's scripted stranger answers. Each reply is handled once.

A reply the operator sends by hand, from one of your product's pages, is the
operator's, not the agent's:

    channel.send(thread_id, text, sender="operator")    # stored and shown as the operator's

    channel.replies(thread_id, since=0)    # read the stranger's messages after message `since`

replies() only reads; it takes nothing. If you want a turn to wait in-process
instead, there is

    channel.wait_for_reply(thread_id, timeout_s)   # the next reply, or None after timeout_s

A reply that wait_for_reply takes is not delivered again to on_outside_reply. In
run.py the scripted stranger answers it at once; in the app it takes the next
reply written on the thread while it waits. Either way the run's or the session's
trace shows the reply, and in run.py the time waited is recorded as waiting.

Every message is stamped with the run (in a run.py run) and the console session
that were active when it was written, with `seeded` when your sandbox's seed wrote
it (the task's input, not part of the exchange), and with `handled_by` once a reply
has been handled ("on_outside_reply" or "wait_for_reply").

The rest is for the harness, your sandbox's seed and the app: receive() is a
message arriving from outside; deliver() claims it for on_outside_reply, unless a
turn waiting on its thread takes it first; seeding() marks what the seed writes;
messages(), threads() and session_of() read the store; reset() empties it. Your
tools may read the store too, with messages() and threads(), such as to find the
threads with new messages.

Keeping this store is the expected choice. If you replace it, with a local mailbox
for example, implement the whole interface of this file: every function above,
Message and its fields, and the two calls to the scripted strangers (send() reports
each message to _script.sent; wait_for_reply asks _script.answer before it
waits). The runner, the checks' helpers and the app's pages use all of them.
"""

from __future__ import annotations

import contextlib
import fnmatch
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass

from harness import runtime
from harness.paths import state_dir
from harness.trace import now

AGENT = "agent"                  # the sender name of every message your agent sends
OPERATOR = "operator"            # the sender name of a reply the operator sends by hand
OURS = (AGENT, OPERATOR)         # the senders on your side; anyone else is the stranger
DELIVERED = "on_outside_reply"   # handled_by: handed to agent.service.on_outside_reply
WAITED = "wait_for_reply"        # handled_by: taken by a turn waiting in wait_for_reply
POLL_S = 0.05
_lock = threading.Lock()
_waiting: dict[str, int] = {}    # thread ID -> turns in this process waiting on it now
_seeding = False                 # True while the sandbox's seed runs


@dataclass(frozen=True)
class Message:
    id: int
    thread_id: str
    sender: str                  # "agent", or "operator" for a reply sent by hand; anything else is the stranger
    text: str
    at: str
    run_id: str | None
    session_id: str | None
    seeded: bool = False         # written by the sandbox's seed: the input, not the exchange
    handled_by: str | None = None   # a reply already handled: "on_outside_reply" or "wait_for_reply"

    @property
    def from_outside(self) -> bool:
        return self.sender not in OURS

    def as_dict(self) -> dict:
        return asdict(self)


@contextlib.contextmanager
def _db():
    """A connection that commits on success and always closes."""
    conn = sqlite3.connect(state_dir() / "channel.sqlite3", timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, sender TEXT NOT NULL,
            text TEXT NOT NULL, at TEXT NOT NULL, run_id TEXT, session_id TEXT,
            seeded INTEGER NOT NULL DEFAULT 0, handled_by TEXT)""")
        with conn:
            yield conn
    finally:
        conn.close()


def _row(r) -> Message:
    return Message(*r[:7], seeded=bool(r[7]), handled_by=r[8])


def _insert(thread_id: str, sender: str, text: str, handled_by: str | None = None) -> Message:
    run = runtime.current_run()
    session = runtime.current_session() or (run.run_id if run else None)
    with _lock, _db() as conn:
        cur = conn.execute("INSERT INTO messages (thread_id, sender, text, at, run_id, session_id, seeded, handled_by) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (str(thread_id), sender, str(text), now(), run.run_id if run else None, session,
                            int(_seeding), handled_by))
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row(row)


# -- for your agent -------------------------------------------------------------------

def send(thread_id: str, text: str, sender: str = AGENT) -> Message:
    """Write on a thread, as the agent. A product page that sends a reply by hand,
    such as a pending reply the operator approved there, passes sender="operator":
    the message is stored and shown as the operator's. Returns the message as stored."""
    if sender not in OURS:
        raise ValueError(f"send writes as {AGENT!r} or {OPERATOR!r}, not {sender!r}; a stranger's message "
                         "arrives through receive()")
    message = _insert(thread_id, sender, text)
    _script.sent(message)
    return message


def replies(thread_id: str, since: int = 0) -> list[Message]:
    """The stranger's messages on this thread with an ID greater than `since`. It only reads."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE thread_id = ? AND sender NOT IN (?, ?) AND id > ? "
                            "ORDER BY id", (str(thread_id), *OURS, int(since or 0))).fetchall()
    return [_row(r) for r in rows]


def wait_for_reply(thread_id: str, timeout_s: float) -> Message | None:
    """Wait in this turn for the stranger's next reply on this thread: one newer than
    the agent's latest message there, not seeded and not handled yet. Returns it,
    or None after timeout_s seconds. The reply it takes is never delivered to
    on_outside_reply. In a run.py run, the time spent here is recorded as waiting."""
    thread_id = str(thread_id)
    start = time.monotonic()
    with _lock:
        _waiting[thread_id] = _waiting.get(thread_id, 0) + 1
    try:
        since = _last_agent_message(thread_id)
        found = _take(thread_id, since)
        if found is None:
            scripted = _script.answer(thread_id)
            if scripted is not None:
                found = _insert(thread_id, scripted[0], scripted[1], handled_by=WAITED)
        while found is None and time.monotonic() - start < timeout_s:
            time.sleep(min(POLL_S, max(0.0, timeout_s - (time.monotonic() - start))))
            found = _take(thread_id, since)
        if found is not None:
            _traced(found)
        return found
    finally:
        with _lock:
            _waiting[thread_id] -= 1
            if not _waiting[thread_id]:
                del _waiting[thread_id]
        run = runtime.current_run()
        if run is not None:
            run.add_wait(time.monotonic() - start)


# -- for the harness, the seed and the app ------------------------------------------------

def receive(thread_id: str, text: str, sender: str = "outside") -> Message:
    """A message arriving from outside on this thread. It is stored, not handled:
    harness.turns.outside_reply stores it, then delivers it."""
    if sender in OURS:
        raise ValueError(f"{sender!r} is a name on your side; give the stranger another")
    return _insert(thread_id, sender, text)


def deliver(message: Message) -> bool:
    """Claim a message from outside for agent.service.on_outside_reply. While a turn
    in this process waits on its thread (wait_for_reply), that turn has the first
    claim, until its wait ends. False when the message was taken or handled already:
    then nothing else may handle it."""
    while True:
        with _lock:
            waiting = _waiting.get(str(message.thread_id), 0)
        if not waiting:
            return _claim(message.id, DELIVERED)
        if _handled_by(message.id) is not None:
            return False
        time.sleep(POLL_S)


@contextlib.contextmanager
def seeding():
    """The harness wraps your sandbox's reset() and seed() in this: every message
    written meanwhile is marked seeded, the input and not part of the exchange."""
    global _seeding
    previous, _seeding = _seeding, True
    try:
        yield
    finally:
        _seeding = previous


def messages(thread_id: str, run_id: str | None = None) -> list[Message]:
    """Every message on a thread, oldest first; only one run's, if run_id is given."""
    query, args = "SELECT * FROM messages WHERE thread_id = ?", [str(thread_id)]
    if run_id is not None:
        query, args = query + " AND run_id = ?", args + [run_id]
    with _db() as conn:
        return [_row(r) for r in conn.execute(query + " ORDER BY id", args).fetchall()]


def threads() -> list[dict]:
    """One line per thread, newest activity first: its ID, message count, last message,
    and whether it waits for the stranger (the agent or the operator wrote last)."""
    with _db() as conn:
        rows = conn.execute("""SELECT m.thread_id, c.n, m.sender, m.text, m.at FROM messages m
            JOIN (SELECT thread_id, COUNT(*) AS n, MAX(id) AS last FROM messages GROUP BY thread_id) c
            ON m.id = c.last ORDER BY m.id DESC""").fetchall()
    return [{"thread_id": t, "messages": n, "last_sender": s, "last_text": x, "last_at": a,
             "waiting_for_outside": s in OURS} for t, n, s, x, a in rows]


def session_of(thread_id: str) -> str | None:
    """The console session (or run) that last wrote on this thread as the agent."""
    with _db() as conn:
        row = conn.execute("SELECT session_id FROM messages WHERE thread_id = ? AND sender = ? "
                           "AND session_id IS NOT NULL ORDER BY id DESC LIMIT 1", (str(thread_id), AGENT)).fetchone()
    return row[0] if row else None


def reset() -> None:
    """Empty the store."""
    with _lock:
        path = state_dir() / "channel.sqlite3"
        if path.exists():
            path.unlink()


def _last_agent_message(thread_id: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT MAX(id) FROM messages WHERE thread_id = ? AND sender = ?",
                           (str(thread_id), AGENT)).fetchone()
    return row[0] or 0


def _claim(message_id: int, by: str) -> bool:
    with _db() as conn:
        return conn.execute("UPDATE messages SET handled_by = ? WHERE id = ? AND handled_by IS NULL",
                            (by, message_id)).rowcount == 1


def _handled_by(message_id: int) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT handled_by FROM messages WHERE id = ?", (message_id,)).fetchone()
    return row[0] if row else None


def _take(thread_id: str, since: int) -> Message | None:
    """Claim the oldest reply a waiting turn may take, or None."""
    with _db() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE thread_id = ? AND sender NOT IN (?, ?) AND id > ? AND seeded = 0 "
            "AND handled_by IS NULL ORDER BY id", (thread_id, *OURS, since)).fetchall()]
        for message_id in ids:
            if conn.execute("UPDATE messages SET handled_by = ? WHERE id = ? AND handled_by IS NULL",
                            (WAITED, message_id)).rowcount == 1:
                return _row(conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone())
    return None


def _traced(message: Message) -> None:
    """The reply a waiting turn took, in that turn's trace (the run's, in run.py)."""
    session = runtime.current_session()
    if runtime.current_run() is None and session is None:
        return
    runtime.trace(session).write({"type": "outside", "thread_id": message.thread_id, "sender": message.sender,
                                  "text": message.text, "message_id": message.id, "handled_by": WAITED})


# -- the scripted people, in a run.py run ---------------------------------------------------

class ScriptedPerson:
    """One stranger from a task file's `outside` list."""

    def __init__(self, spec: dict):
        self.thread = str(spec["thread"])
        self.name = str(spec.get("who") or "outside")
        self.replies = [str(r) for r in spec.get("replies") or []]
        self.asked_again = spec.get("asked_again")
        self.never_asked = spec.get("never_asked")
        self.asked = 0      # how many times the agent waited on this person
        self.answered = 0   # how many answers they gave
        self.wrote_unasked = 0

    def matches(self, thread_id: str) -> bool:
        return fnmatch.fnmatchcase(str(thread_id), self.thread)

    def next_answer(self) -> str | None:
        self.asked += 1
        if self.asked <= len(self.replies):
            answer = self.replies[self.asked - 1]
        else:
            answer = None if self.asked_again is None else str(self.asked_again)
        if answer is not None:
            self.answered += 1
        return answer


class _Script:
    """The task's strangers while a run plays them; empty otherwise."""

    def __init__(self):
        self.people: list[ScriptedPerson] = []
        self.threads_written: dict[str, int] = {}

    def install(self, specs: list[dict]) -> list[ScriptedPerson]:
        self.people = [ScriptedPerson(s) for s in specs or []]
        self.threads_written = {}
        return self.people

    def clear(self) -> None:
        self.people = []
        self.threads_written = {}

    def person(self, thread_id: str) -> ScriptedPerson | None:
        return next((p for p in self.people if p.matches(thread_id)), None)

    def sent(self, message: Message) -> None:
        if self.people:
            self.threads_written[message.thread_id] = self.threads_written.get(message.thread_id, 0) + 1

    def answer(self, thread_id: str) -> tuple[str, str] | None:
        """(sender, text) of the scripted stranger's answer on this thread, or None."""
        person = self.person(thread_id)
        if person is None:
            return None
        text = person.next_answer()
        return None if text is None else (person.name, text)


_script = _Script()
