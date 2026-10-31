"""Access requests: one per message the seed puts on the channel. Owner: Vipin Mourya.

A request's ID (SYN-REQ-0001, …) is given in the order the requests arrive, unless
the test task names it. Its thread is where it arrived: SYN-HR-… for the HR system's
feed, any other thread for an employee's own message.
"""

from __future__ import annotations

from sandbox import clock, db

STATUSES = ("new", "in_progress", "done", "partly_done", "pending_operator", "refused")
OPEN = ("new", "in_progress", "pending_operator")
HR_PREFIX = "SYN-HR-"


def source_of(thread_id: str) -> str:
    return "hr_feed" if str(thread_id).upper().startswith(HR_PREFIX) else "employee"


def create(thread_id, source, text, *, requester_id=None, subject_id=None, request_id=None, received_at=None,
           stamp="seed") -> str:
    with db.connect() as conn:
        rid = request_id or db.next_id(conn, "requests", "SYN-REQ-")
        conn.execute("INSERT INTO requests (id, thread_id, source, requester_id, subject_id, text, received_at, "
                     "status, stamp) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?)",
                     (rid, str(thread_id), source, requester_id, subject_id, str(text), received_at or clock.now(),
                      stamp))
    return rid


def get(request_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM requests WHERE id = ?", (str(request_id).strip().upper(),)).fetchone()


def by_thread(thread_id: str) -> dict | None:
    """The latest request that arrived on this thread."""
    with db.connect() as conn:
        return conn.execute("SELECT * FROM requests WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                            (str(thread_id),)).fetchone()


def all_requests(day: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if day:
            return conn.execute("SELECT * FROM requests WHERE substr(received_at, 1, 10) = ? ORDER BY id",
                                (day,)).fetchall()
        return conn.execute("SELECT * FROM requests ORDER BY id").fetchall()


def open_requests(day: str | None = None) -> list[dict]:
    return [r for r in all_requests(day) if r["status"] in OPEN]


def set_status(request_id, status, *, stamp, note="") -> dict:
    if status not in STATUSES:
        raise ValueError(f"status is one of {', '.join(STATUSES)}, not {status!r}")
    with db.connect() as conn:
        cur = conn.execute("UPDATE requests SET status = ?, note = ? WHERE id = ?", (status, note or None,
                                                                                     str(request_id).strip().upper()))
        if cur.rowcount == 0:
            raise KeyError(f"no request {request_id}")
        db.audit(conn, actor="system", action="set_status", stamp=stamp, request_id=str(request_id).strip().upper(),
                 detail={"status": status, "note": note})
        return conn.execute("SELECT * FROM requests WHERE id = ?", (str(request_id).strip().upper(),)).fetchone()


def set_route(request_id, route, *, stamp) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE requests SET route = ? WHERE id = ?", (route, str(request_id).strip().upper()))
        db.audit(conn, actor="system", action="set_route", stamp=stamp, request_id=str(request_id).strip().upper(),
                 detail={"route": route})


def claim(request_id, session_id) -> bool:
    """Whether this session may work on the request: the first session to claim it holds it
    until it releases it, so two console sessions at once do not both handle it."""
    rid, sid = str(request_id).strip().upper(), str(session_id)
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO request_claims (request_id, session_id, at) VALUES (?, ?, ?)",
                     (rid, sid, clock.now()))
        row = conn.execute("SELECT session_id FROM request_claims WHERE request_id = ?", (rid,)).fetchone()
    return row["session_id"] == sid


def release(request_id, session_id) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM request_claims WHERE request_id = ? AND session_id = ?",
                     (str(request_id).strip().upper(), str(session_id)))
