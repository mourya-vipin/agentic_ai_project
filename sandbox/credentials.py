"""Fake credentials: initial passwords and tokens, every secret starting FAKE-.
Owner: Vipin Mourya.

No tool ever returns a secret. issue() returns the credential's ID only; reveal()
is for the operator's product page, for a hand-over outside our channel.
"""

from __future__ import annotations

import json

from harness.trace import now as wall_now
from sandbox import base_data, clock, db

PUBLIC = "id, employee_id, system_id, kind, created_at, rotated_at, stamp"


def issue(employee_id, system_id, *, key, stamp) -> str:
    """A new initial password for this person on this system; returns its ID. A
    repeated key returns the first ID and issues nothing."""
    with db.connect() as conn:
        if key:
            row = conn.execute("SELECT result_json FROM identity_keys WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row["result_json"])["credential_id"]
        credential_id = db.next_id(conn, "credentials", "SYN-CRED-")
        conn.execute("INSERT INTO credentials (id, employee_id, system_id, kind, secret, created_at, stamp) "
                     "VALUES (?, ?, ?, 'initial_password', ?, ?, ?)",
                     (credential_id, employee_id, system_id, base_data.fake_secret(credential_id, stamp),
                      clock.now(), stamp))
        db.audit(conn, actor="workflow", action="issue_credential", stamp=stamp, employee_id=employee_id,
                 detail={"credential_id": credential_id, "system_id": system_id})
        if key:
            conn.execute("INSERT INTO identity_keys (key, tool, result_json, stamp, at) VALUES (?, ?, ?, ?, ?)",
                         (key, "credentials_issue", json.dumps({"credential_id": credential_id}), stamp, wall_now()))
    return credential_id


def rotate(credential_id: str, *, stamp: str) -> None:
    """The undo path for an issued credential: replace its secret."""
    with db.connect() as conn:
        conn.execute("UPDATE credentials SET secret = ?, rotated_at = ? WHERE id = ?",
                     (base_data.fake_secret(credential_id, stamp, "rotated"), clock.now(), credential_id))
        db.audit(conn, actor="operator", action="rotate_credential", stamp=stamp, detail={"credential_id": credential_id})


def reveal(credential_id: str) -> str:
    """The secret, for the operator's product page only. Never wrapped by a tool."""
    with db.connect() as conn:
        row = conn.execute("SELECT secret FROM credentials WHERE id = ?", (credential_id,)).fetchone()
    if row is None:
        raise KeyError(f"no credential {credential_id}")
    return row["secret"]


def for_employee(employee_id: str) -> list[dict]:
    """The person's credentials: IDs, kinds and dates, never the secrets."""
    with db.connect() as conn:
        return conn.execute(f"SELECT {PUBLIC} FROM credentials WHERE employee_id = ? ORDER BY id",
                            (str(employee_id).upper(),)).fetchall()
