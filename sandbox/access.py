"""The mock identity API: who belongs to which group. Owner: Vipin Mourya.

It enforces its own idempotency, as a real identity provider's API would: a write
that carries a key it has seen returns the first result and changes nothing
(Lecture 3, slide 38). A write with no key (the operator's, by hand) always runs.

A test can make it fail on purpose: while a `faults` row for "access_grant" or
"access_revoke" has remaining > 0, that call raises TransientError before
writing anything, and the count goes down by one.

It does not apply the role policy: the operator may grant anything by hand. The
agent's access_grant tool checks catalog.policy_for() before it calls grant().
"""

from __future__ import annotations

import json

from harness.trace import now as wall_now
from sandbox import catalog, clock, db, directory


class TransientError(Exception):
    """The identity API is briefly unavailable; the same call may succeed if retried."""


class NotFound(ValueError):
    """The employee or the group does not exist."""


MEMBERSHIP_COLUMNS = ("m.id AS membership_id, m.employee_id, m.group_id, g.name AS group_name, g.system_id, "
                      "g.sensitivity, g.protected, m.granted_at, m.expires_at, m.removed_at, m.request_id, "
                      "m.removed_request_id, m.actor, m.stamp")


def memberships(employee_id: str, active_only: bool = True) -> list[dict]:
    sql = (f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships m JOIN groups g ON g.id = m.group_id "
           "WHERE m.employee_id = ?")
    if active_only:
        sql += " AND m.removed_at IS NULL"
    with db.connect() as conn:
        return conn.execute(sql + " ORDER BY m.group_id, m.id", (str(employee_id).strip().upper(),)).fetchall()


def members(group_id: str) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships m JOIN groups g ON g.id = m.group_id "
                            "WHERE m.group_id = ? AND m.removed_at IS NULL ORDER BY m.employee_id",
                            (str(group_id).strip().upper(),)).fetchall()


def by_request(request_id: str) -> list[dict]:
    """Every membership granted or removed under this request, with "change": granted | removed."""
    with db.connect() as conn:
        granted = conn.execute(f"SELECT {MEMBERSHIP_COLUMNS}, 'granted' AS change FROM memberships m "
                               "JOIN groups g ON g.id = m.group_id WHERE m.request_id = ?", (request_id,)).fetchall()
        removed = conn.execute(f"SELECT {MEMBERSHIP_COLUMNS}, 'removed' AS change FROM memberships m "
                               "JOIN groups g ON g.id = m.group_id WHERE m.removed_request_id = ?",
                               (request_id,)).fetchall()
    return granted + removed


def changes_by_stamp(stamp: str) -> list[dict]:
    """Every membership granted or removed under this stamp (a run, or a console
    session), with "change": granted | removed and "under_request", the request it was
    made under (None for an operator's bulk change). For the critique's check."""
    with db.connect() as conn:
        granted = conn.execute(f"SELECT {MEMBERSHIP_COLUMNS}, 'granted' AS change, m.request_id AS under_request "
                               "FROM memberships m JOIN groups g ON g.id = m.group_id WHERE m.stamp = ?",
                               (stamp,)).fetchall()
        removed = conn.execute(f"SELECT {MEMBERSHIP_COLUMNS}, 'removed' AS change, m.removed_request_id AS "
                               "under_request FROM memberships m JOIN groups g ON g.id = m.group_id "
                               "WHERE m.removed_stamp = ?", (stamp,)).fetchall()
    return granted + removed


def grant(employee_id, group_id, *, request_id, key, stamp, actor, expires_at=None) -> dict:
    """{"status": "granted" | "already_member", "membership_id": int}; a repeated key
    returns the first result."""
    employee_id, group_id = str(employee_id).strip().upper(), str(group_id).strip().upper()
    with db.connect() as conn:
        first = _seen(conn, key)
        if first is not None:
            return first
        _maybe_fail(conn, "access_grant")
        _require(employee_id, group_id)
        row = conn.execute("SELECT id FROM memberships WHERE employee_id = ? AND group_id = ? AND removed_at IS NULL",
                           (employee_id, group_id)).fetchone()
        if row:
            result = {"status": "already_member", "membership_id": row["id"]}
        else:
            cur = conn.execute("INSERT INTO memberships (employee_id, group_id, granted_at, expires_at, request_id, "
                               "actor, stamp, key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (employee_id, group_id, clock.now(), expires_at, request_id, actor, stamp, key))
            result = {"status": "granted", "membership_id": cur.lastrowid}
            db.audit(conn, actor=actor, action="grant", stamp=stamp, employee_id=employee_id, group_id=group_id,
                     request_id=request_id, detail={"expires_at": expires_at, "key": key})
        _remember(conn, key, "access_grant", result, stamp)
        return result


def revoke(employee_id, group_id, *, request_id, key, stamp, actor, reason) -> dict:
    """{"status": "removed" | "not_a_member" | "protected", ...}; a protected group is
    never removed; a repeated key returns the first result."""
    employee_id, group_id = str(employee_id).strip().upper(), str(group_id).strip().upper()
    with db.connect() as conn:
        first = _seen(conn, key)
        if first is not None:
            return first
        _maybe_fail(conn, "access_revoke")
        group = _require(employee_id, group_id)
        row = conn.execute("SELECT id FROM memberships WHERE employee_id = ? AND group_id = ? AND removed_at IS NULL",
                           (employee_id, group_id)).fetchone()
        if row is None:
            result = {"status": "not_a_member"}
        elif group["protected"]:
            result = {"status": "protected", "membership_id": row["id"]}
        else:
            conn.execute("UPDATE memberships SET removed_at = ?, removed_request_id = ?, removed_stamp = ? WHERE id = ?",
                         (clock.now(), request_id, stamp, row["id"]))
            result = {"status": "removed", "membership_id": row["id"]}
            db.audit(conn, actor=actor, action="revoke", stamp=stamp, employee_id=employee_id, group_id=group_id,
                     request_id=request_id, detail={"reason": reason, "key": key})
        _remember(conn, key, "access_revoke", result, stamp)
        return result


def set_fault(tool: str, times: int) -> None:
    """Make the next `times` calls of this API's `tool` fail as transient (for tests)."""
    with db.connect() as conn:
        conn.execute("INSERT INTO faults (tool, remaining) VALUES (?, ?) "
                     "ON CONFLICT(tool) DO UPDATE SET remaining = excluded.remaining", (tool, int(times)))


def _seen(conn, key) -> dict | None:
    if not key:
        return None
    row = conn.execute("SELECT result_json FROM identity_keys WHERE key = ?", (key,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def _remember(conn, key, tool: str, result: dict, stamp: str) -> None:
    if key:
        conn.execute("INSERT INTO identity_keys (key, tool, result_json, stamp, at) VALUES (?, ?, ?, ?, ?)",
                     (key, tool, json.dumps(result), stamp, wall_now()))


def _maybe_fail(conn, tool: str) -> None:
    row = conn.execute("SELECT remaining FROM faults WHERE tool = ?", (tool,)).fetchone()
    if row and row["remaining"] > 0:
        conn.execute("UPDATE faults SET remaining = remaining - 1 WHERE tool = ?", (tool,))
        conn.commit()           # the count goes down even though the call fails
        raise TransientError(f"the identity API is briefly unavailable ({tool}); retry the same call")


def _require(employee_id: str, group_id: str) -> dict:
    if directory.get_employee(employee_id) is None:
        raise NotFound(f"no employee {employee_id}")
    group = catalog.get_group(group_id)
    if group is None:
        raise NotFound(f"no group {group_id}")
    return group
