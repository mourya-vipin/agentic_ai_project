"""Systems, groups and the role policy (reads only). Owner: Vipin Mourya.

The policy, in the order policy_for() applies it:

    forbidden       the person is not active (a leaver, or a contract that has ended),
                    the group is protected, or the person or group does not exist
    needs_manager   the group is high sensitivity, or is marked needs_manager, or the
                    person's role does not list it
    birthright      the role grants it to everyone in the role at onboarding
    allowed         the role lists it as grantable on request, without the manager
"""

from __future__ import annotations

from sandbox import clock, db, directory

GROUP_COLUMNS = ("g.id, g.system_id, s.name AS system_name, g.name, g.level, g.sensitivity, g.needs_manager, "
                 "g.protected, g.max_days")


def get_group(group_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute(f"SELECT {GROUP_COLUMNS} FROM groups g JOIN systems s ON s.id = g.system_id "
                            "WHERE g.id = ?", (str(group_id).upper(),)).fetchone()


def get_system(system_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute("SELECT id, name, owner_dept, sensitivity FROM systems WHERE id = ?",
                            (str(system_id).upper(),)).fetchone()


def search_groups(query: str = "", system_id: str | None = None, limit: int = 10) -> list[dict]:
    """Groups whose ID, name or system's name contains the query (case-insensitive)."""
    like = f"%{str(query).strip().lower()}%"
    sql = (f"SELECT {GROUP_COLUMNS} FROM groups g JOIN systems s ON s.id = g.system_id "
           "WHERE (lower(g.id) LIKE ? OR lower(g.name) LIKE ? OR lower(s.name) LIKE ?)")
    args: list = [like, like, like]
    if system_id:
        sql += " AND g.system_id = ?"
        args.append(str(system_id).upper())
    with db.connect() as conn:
        return conn.execute(sql + " ORDER BY g.id LIMIT ?", (*args, int(limit))).fetchall()


def birthright_groups(role: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT group_id FROM role_policy WHERE role = ? AND kind = 'birthright' ORDER BY group_id",
                            (role,)).fetchall()
    return [r["group_id"] for r in rows]


def policy_for(employee_id: str, group_id: str) -> dict:
    """{"decision": "birthright" | "allowed" | "needs_manager" | "forbidden", "reason": str}"""
    person = directory.get_employee(employee_id)
    group = get_group(group_id)
    if person is None:
        return {"decision": "forbidden", "reason": f"no employee {employee_id}"}
    if group is None:
        return {"decision": "forbidden", "reason": f"no group {group_id}"}
    if person["status"] == "leaver":
        return {"decision": "forbidden", "reason": f"{person['id']} has left the company"}
    if person["end_date"] and person["end_date"] < clock.today():
        return {"decision": "forbidden", "reason": f"{person['id']}'s contract ended on {person['end_date']}"}
    if group["protected"]:
        return {"decision": "forbidden", "reason": f"{group['id']} is set by Legal, never on request"}
    if group["sensitivity"] == "high":
        return {"decision": "needs_manager", "reason": f"{group['id']} is high sensitivity"}
    if group["needs_manager"]:
        return {"decision": "needs_manager", "reason": f"{group['id']} always needs the manager's agreement"}
    with db.connect() as conn:
        row = conn.execute("SELECT kind FROM role_policy WHERE role = ? AND group_id = ?",
                           (person["role"], group["id"])).fetchone()
    if row is None:
        return {"decision": "needs_manager", "reason": f"the role {person['role']} does not list {group['id']}"}
    return {"decision": row["kind"], "reason": f"the role {person['role']} lists {group['id']} as {row['kind']}"}
