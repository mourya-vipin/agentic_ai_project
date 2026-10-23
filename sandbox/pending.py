"""Pending records: what sits below the approval line, written down for the operator
to do by hand on the product page (Milestone 1's way of not automating it).
Owner: Vipin Mourya.

    proposed_change       a grant or removal that needs a person first
    reply_draft           a reply whose text the model wrote; the operator sends it
    manager_confirmation  a drafted request for the manager's agreement (asking is Milestone 2's)
    credential_handover   a new starter's credential, handed over outside our channel

A record carries the key it was written under; a repeated key returns the first
record's ID and writes nothing.
"""

from __future__ import annotations

from harness.trace import now as wall_now
from sandbox import clock, db

KINDS = ("proposed_change", "reply_draft", "manager_confirmation", "credential_handover")


def create(kind, *, request_id, employee_id, key, stamp, action=None, group_id=None,
           thread_id=None, body=None, reason=None) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}, not {kind!r}")
    with db.connect() as conn:
        if key:
            row = conn.execute("SELECT id FROM pending_actions WHERE key = ?", (key,)).fetchone()
            if row:
                return row["id"]
        pid = db.next_id(conn, "pending_actions", "SYN-PEND-")
        conn.execute("INSERT INTO pending_actions (id, kind, action, request_id, employee_id, group_id, thread_id, "
                     "body, reason, status, created_at, stamp, key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                     (pid, kind, action, request_id, employee_id, group_id, thread_id, body, reason, clock.now(),
                      stamp, key))
        db.audit(conn, actor="system", action=f"pending_{kind}", stamp=stamp, employee_id=employee_id,
                 group_id=group_id, request_id=request_id, detail={"pending_id": pid, "action": action})
    return pid


def get(pending_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM pending_actions WHERE id = ?", (str(pending_id).strip().upper(),)).fetchone()


def open_items(kind: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if kind:
            return conn.execute("SELECT * FROM pending_actions WHERE status = 'open' AND kind = ? ORDER BY id",
                                (kind,)).fetchall()
        return conn.execute("SELECT * FROM pending_actions WHERE status = 'open' ORDER BY id").fetchall()


def by_request(request_id: str) -> list[dict]:
    with db.connect() as conn:
        return conn.execute("SELECT * FROM pending_actions WHERE request_id = ? ORDER BY id",
                            (str(request_id).strip().upper(),)).fetchall()


def close(pending_id, status, *, stamp) -> None:
    """done or dismissed, by the operator on the product page."""
    if status not in ("done", "dismissed"):
        raise ValueError(f"a pending record closes as done or dismissed, not {status!r}")
    with db.connect() as conn:
        cur = conn.execute("UPDATE pending_actions SET status = ?, closed_at = ?, closed_stamp = ? "
                           "WHERE id = ? AND status = 'open'", (status, wall_now(), stamp, str(pending_id).strip().upper()))
        if cur.rowcount == 0:
            raise KeyError(f"no open pending record {pending_id}")
        db.audit(conn, actor="operator", action=f"pending_{status}", stamp=stamp, detail={"pending_id": pending_id})
