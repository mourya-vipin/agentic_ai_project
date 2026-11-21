"""Memory across sessions, with provenance (Lecture 4, Section D; Part 1, task 7).

Owner: Aman Panigrahi. The store is state_dir()/memory.sqlite3. It stays after `make serve`,
and the sandbox's reset (make app, run.py) clears it.

What it keeps, and what it does not:

    kept      what the operator states for later: a preference or a standing rule ("from now
              on, ..."), and a fact about a record that the sandbox does not hold ("remember
              that Victor Lam's legal hold stays until Legal says it can go")
    not kept  who is in which group, and a person's role: the sandbox holds them, our tools
              read them fresh, and a copy goes stale

    Record                         id, kind, text, subject, source, written_at, derived,
                                   confirmed, supersedes (and superseded_by, last_used)
    write(record)                  the one write policy: insert, merge or supersede; returns the ID
    from_text(session_id, text)    the record that an operator's message states, written, or None
    read_for_session(session_id)   what a new session shows the operator first ("memory_read")
    for_call(subjects, use_case)   what one model call gets, by the read rule below
    reset()                        delete the store (the sandbox's reset calls it)

The write policy (write):
    how    insert a new record. The same kind, subject and text as an active record: merge
           (no copy is made; the record keeps its first written_at, and is marked used now, so
           eviction keeps it). A fact about a subject that has an active fact: supersede (the
           new record names the old one, and the old one names the new one). Never an update in
           place. Two preferences have no subject to compare, so our code cannot tell that they
           disagree: both stay, each call lists them newest first with their dates, and the call
           says that the newer one holds (agent/context.py).
    with   its provenance: who said it (source), when (written_at), whether code derived it
           (derived), whether a person confirmed it (confirmed), and the session and run.
    when   at the end of the operator's turn that states it, never in the middle of a run on a
           guess. A stranger's text is kept as source "stranger", not derived and not confirmed,
           so it never enters a model call. Private data (a credential) is never kept.
    size   at most MAX_RECORDS active records. Past that, the record used longest ago goes, and
           the trace says which (memory_evicted).

The read rule (for_call), what enters one model call: each confirmed record of the operator
whose subject is one of the call's subjects (the request's people and group), or that has no
subject, newest first, at most MAX_IN_CALL. Never a stranger's record. The planner never gets
memory (agent/context.py). Each read writes what it returned and what it skipped to the trace.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness import runtime
from harness.paths import state_dir
from harness.trace import now

DB_FILE = "memory.sqlite3"
KINDS = ("preference", "fact")
SOURCES = ("operator", "stranger", "system", "private_data")
MAX_RECORDS = 200
MAX_IN_CALL = 5
MAX_SHOWN = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
    subject TEXT, source TEXT NOT NULL, written_at TEXT NOT NULL, derived INTEGER NOT NULL,
    confirmed INTEGER NOT NULL, supersedes TEXT, superseded_by TEXT, session_id TEXT, run_key TEXT,
    last_used TEXT);
"""

_REMEMBER = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind)\b[\s:,]*(?:that\s+)?(?P<body>.+)$",
                       re.I | re.S)
# a standing rule opens a sentence of the operator's own: at the start, or after . ! ? or a new line
_STANDING = re.compile(r"(?:^|[.!?]\s+|\n\s*)(?:(?:and|also|please|so)\s+)?"
                       r"(?:from now on|going forward|in future|from today on|every time)\b", re.I)
# someone else's words in an operator's message, such as a requester's text that the operator
# pastes (Vipin Mourya's reviews): a quotation, also across lines; a line that starts with ">" or
# with two spaces or a tab; and what follows "... wrote:" or "... said:", to the end of its paragraph
_QUOTE = re.compile(r'"[^"]*"|“[^”]*”')
_QUOTED_LINE = re.compile(r"^(?:[ \t]*>|[ \t]{2,}(?=\S)).*$", re.M)
_WROTE = re.compile(r"(?<!\bI )(?<!\bwe )\b((?:wrote|writes|said|says)[ \t]*:)\s*[\s\S]*?(?=\n[ \t]*\n|\Z)", re.I)
SHORT_QUOTE = 5           # a quotation of at most this many words, such as a name, is the operator's own term
_FACT = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind)\b[\s:,]*that\b", re.I)
_IDS = re.compile(r"\bSYN-(?:EMP|GRP)-[A-Z0-9-]+\b", re.I)
_SECRET = re.compile(r"\bFAKE-[A-Z0-9-]+|\bpassword\b|\bsecret\b", re.I)


@dataclass
class Record:
    kind: str
    text: str
    source: str
    subject: str | None = None
    derived: bool = False
    confirmed: bool = False
    supersedes: str | None = None
    id: str = ""
    written_at: str = ""
    superseded_by: str | None = None
    session_id: str | None = None
    run_key: str | None = None
    last_used: str | None = None


SWITCHES = {"preference": "memory.preferences", "fact": "memory.facts"}     # one switch per kind kept


def enabled(kind: str = "preference") -> bool:
    """Whether the switch of this kind (SWITCHES) is on. Off keeps that kind out: it is not
    written, and no read returns it."""
    return str(runtime.switch(SWITCHES[kind], "on")) == "on"


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
    """Delete the store. The sandbox's reset calls it (sandbox/seed.py: AGENT_STORES)."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(_path()) + suffix).unlink(missing_ok=True)


def _record(row) -> Record:
    return Record(kind=row["kind"], text=row["text"], source=row["source"], subject=row["subject"],
                  derived=bool(row["derived"]), confirmed=bool(row["confirmed"]), supersedes=row["supersedes"],
                  id=row["id"], written_at=row["written_at"], superseded_by=row["superseded_by"],
                  session_id=row["session_id"], run_key=row["run_key"], last_used=row["last_used"])


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split()).rstrip(".")


def _trace(session_id: str | None, event: str, **fields) -> None:
    runtime.trace(session_id).event(event, **fields)


# -- the write policy ---------------------------------------------------------------------------

def write(record: Record) -> str | None:
    """The one write policy (see the module's docstring). Returns the record's ID, or None
    when the policy declines it. Each write and each decline writes a trace line."""
    sid = record.session_id
    if record.kind not in KINDS:
        raise ValueError(f"the kind is {record.kind!r}; use one of {', '.join(KINDS)}")
    if record.source not in SOURCES:
        raise ValueError(f"the source is {record.source!r}; use one of {', '.join(SOURCES)}")
    if not enabled(record.kind):
        _trace(sid, "memory_declined", why=f"{SWITCHES[record.kind]} is off", source=record.source)
        return None
    if record.source == "private_data" or _SECRET.search(record.text or ""):
        _trace(sid, "memory_declined", why="private data is never kept", source=record.source)
        return None
    if not str(record.text or "").strip():
        _trace(sid, "memory_declined", why="no text", source=record.source)
        return None
    if record.source == "stranger":
        record.derived, record.confirmed = False, False       # a stranger's words: theirs, never confirmed
    record.written_at = record.written_at or now()
    record.subject = str(record.subject).upper() if record.subject else None
    with _connect(write=True) as conn:
        active = conn.execute("SELECT * FROM records WHERE superseded_by IS NULL AND kind = ? AND "
                              "COALESCE(subject, '') = ? ORDER BY n", (record.kind, record.subject or "")).fetchall()
        same = [r for r in active if _norm(r["text"]) == _norm(record.text) and r["source"] == record.source]
        if same:
            # said again: the record keeps its first written_at (its age) and is marked as used now,
            # so eviction keeps it; nothing in the record is rewritten
            conn.execute("UPDATE records SET last_used = ? WHERE id = ?", (record.written_at, same[0]["id"]))
            _trace(sid, "memory_write", id=same[0]["id"], how="merge", record_kind=record.kind, subject=record.subject,
                   source=record.source)
            return same[0]["id"]
        if record.supersedes is None and record.kind == "fact" and record.subject:
            older = [r for r in active if r["source"] == "operator"]
            record.supersedes = older[-1]["id"] if older and record.source == "operator" else None
        n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 AS n FROM records").fetchone()["n"]
        record.id = record.id or f"M{n}"
        conn.execute("INSERT INTO records (id, kind, text, subject, source, written_at, derived, confirmed, "
                     "supersedes, session_id, run_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (record.id, record.kind, record.text.strip(), record.subject, record.source, record.written_at,
                      int(record.derived), int(record.confirmed), record.supersedes, sid, record.run_key))
        if record.supersedes:
            conn.execute("UPDATE records SET superseded_by = ? WHERE id = ?", (record.id, record.supersedes))
        evicted = _evict(conn)
    _trace(sid, "memory_write", id=record.id, how="supersede" if record.supersedes else "insert", record_kind=record.kind,
           subject=record.subject, source=record.source, confirmed=record.confirmed, supersedes=record.supersedes)
    for gone in evicted:
        _trace(sid, "memory_evicted", id=gone, why=f"more than {MAX_RECORDS} active records; used longest ago")
    return record.id


def _evict(conn) -> list[str]:
    rows = conn.execute("SELECT id FROM records WHERE superseded_by IS NULL "
                        "ORDER BY COALESCE(last_used, written_at), n").fetchall()
    gone = [r["id"] for r in rows[:max(0, len(rows) - MAX_RECORDS)]]
    for record_id in gone:
        conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    return gone


def from_text(session_id: str, text: str, *, run_key: str | None = None) -> Record | None:
    """The record that an operator's message states for later, written, or None. Our code
    reads it, never a model: a message that starts "remember", "note" or "keep in mind", or one
    with a sentence that starts "from now on", "going forward", "in future" or "every time".
    Someone else's words are left out first (_others): the rule must be in the operator's own
    words, and the record keeps only those, with "…" where the other words were. A short
    quotation, such as a name, stays in the record. "Remember that ..." about a named person or
    group is a fact; anything else is a preference. A question is never a record."""
    text = str(text or "").strip()
    if not text or text.endswith("?"):
        return None
    kept = _others(text)                            # the record's text
    own = _QUOTE.sub(" ", kept).strip()             # what is read for a rule: no quotation at all
    if _REMEMBER.match(own) is None and not _STANDING.search(own):
        return None
    said = _REMEMBER.match(kept)
    body = (said.group("body") if said else kept).strip()
    subject = _subject(body)
    kind = "fact" if _FACT.match(kept) and subject else "preference"
    record = Record(kind=kind, text=body, source="operator", subject=subject, derived=False, confirmed=True,
                    session_id=session_id, run_key=run_key)
    record_id = write(record)
    return record if record_id else None


def _others(text: str) -> str:
    """The text with someone else's words put as "…": a quoted or pasted line, what follows
    "... wrote:", and a quotation of more than SHORT_QUOTE words."""
    text = _QUOTED_LINE.sub("…", text)
    text = _WROTE.sub(lambda m: f"{m.group(1)} …", text)
    text = _QUOTE.sub(lambda m: m.group(0) if len(m.group(0).split()) <= SHORT_QUOTE else '"…"', text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _subject(text: str) -> str | None:
    """The person or group that the text is about: an ID, or a full name from the directory or
    the catalog. None when it names neither, or more than one."""
    ids = list(dict.fromkeys(i.upper() for i in _IDS.findall(text)))
    if len(ids) == 1:
        return ids[0]
    if ids:
        return None
    from sandbox import db
    lowered = " ".join(text.lower().split())
    with db.connect() as conn:
        named = [r["id"] for r in conn.execute("SELECT id, name FROM employees").fetchall()
                 if re.search(rf"\b{re.escape(r['name'].lower())}\b", lowered)]
        if not named:
            named = [r["id"] for r in conn.execute("SELECT id, name FROM groups").fetchall()
                     if re.search(rf"\b{re.escape(r['name'].lower())}\b", lowered)]
    return named[0] if len(named) == 1 else None


# -- the reads ----------------------------------------------------------------------------------

def _active() -> list[Record]:
    with _connect() as conn:
        return [_record(r) for r in conn.execute("SELECT * FROM records WHERE superseded_by IS NULL "
                                                 "ORDER BY written_at DESC, n DESC").fetchall()]


def held() -> list[Record]:
    """The records that the store holds now (not superseded), newest first, for the console's
    panel. It writes no trace line, so the panel can read it at each refresh. A kind whose
    switch is off is not in it."""
    return [r for r in _active() if enabled(r.kind)]


def all_records() -> list[Record]:
    """Every record, the superseded ones too, oldest first (for the tests and the write-up)."""
    with _connect() as conn:
        return [_record(r) for r in conn.execute("SELECT * FROM records ORDER BY n").fetchall()]


def read_for_session(session_id: str) -> list[Record]:
    """What a new console session shows the operator before its first model call: each active
    record, newest first, at most MAX_SHOWN. A stranger's record is shown as theirs. Writes
    memory_read with what it returned and what it skipped."""
    records = [r for r in _active() if enabled(r.kind)]
    shown, skipped = records[:MAX_SHOWN], records[MAX_SHOWN:]
    _trace(session_id, "memory_read", purpose="session_start", returned=[r.id for r in shown],
           skipped=[{"id": r.id, "why": "past the first ten"} for r in skipped], enabled=enabled(),
           facts_enabled=enabled("fact"))
    return shown


def for_call(session_id: str, subjects, use_case: str | None = None, *, run_key: str | None = None) -> list[Record]:
    """The records that one model call gets (the read rule in the module's docstring), marked as
    used. Writes memory_read with what it returned and what it skipped, and why."""
    if not any(enabled(kind) for kind in KINDS):
        return []
    wanted = {str(s).upper() for s in (subjects or []) if s}
    returned, skipped = [], []
    for r in _active():
        if not enabled(r.kind):
            skipped.append({"id": r.id, "why": f"{SWITCHES[r.kind]} is off"})
        elif r.source != "operator" or not r.confirmed:
            skipped.append({"id": r.id, "why": f"source {r.source}, not confirmed by the operator"})
        elif r.subject and r.subject not in wanted:
            skipped.append({"id": r.id, "why": f"its subject {r.subject} is not in this call"})
        elif len(returned) >= MAX_IN_CALL:
            skipped.append({"id": r.id, "why": f"past the {MAX_IN_CALL} newest"})
        else:
            returned.append(r)
    if returned:
        with _connect(write=True) as conn:
            conn.executemany("UPDATE records SET last_used = ? WHERE id = ?", [(now(), r.id) for r in returned])
    _trace(session_id, "memory_read", purpose="call", run_key=run_key, use_case=use_case, subjects=sorted(wanted),
           returned=[r.id for r in returned], skipped=skipped)
    return returned


def age(record: Record, at: str | None = None) -> str:
    """How old the record is, in words, for the operator."""
    try:
        then = datetime.fromisoformat(record.written_at.replace("Z", "+00:00"))
        moment = datetime.fromisoformat((at or now()).replace("Z", "+00:00"))
    except ValueError:
        return "of unknown age"
    then = then if then.tzinfo else then.replace(tzinfo=timezone.utc)
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    days = (moment - then).days
    return "written today" if days < 1 else f"written {days} day{'s' if days != 1 else ''} ago"


def as_line(record: Record) -> str:
    return f"[{record.id}, {record.kind}, from the {record.source}, {age(record)}] {record.text}"


def seed_record(item: dict) -> str | None:
    """A record that a test task gives (input kind "memory"), written with its own provenance,
    because the sandbox's reset clears the store before each run."""
    fields = {k: item[k] for k in ("kind", "text", "source", "subject", "derived", "confirmed", "supersedes",
                                   "written_at", "session_id") if k in item}
    return write(Record(**{"kind": "preference", "source": "operator", "confirmed": True, **fields}))


def to_dict(record: Record) -> dict:
    return asdict(record)
