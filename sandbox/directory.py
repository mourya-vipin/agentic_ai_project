"""The staff directory (reads only). Owner: Vipin Mourya."""

from __future__ import annotations

from sandbox import db

COLUMNS = "id, name, email, dept, title, role, manager_id, employment, status, start_date, end_date"


def get_employee(employee_id: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute(f"SELECT {COLUMNS} FROM employees WHERE id = ?", (str(employee_id).strip().upper(),)).fetchone()


def by_email(email: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute(f"SELECT {COLUMNS} FROM employees WHERE lower(email) = lower(?)",
                            (str(email).strip(),)).fetchone()


def search_employees(query: str, dept: str | None = None, limit: int = 10) -> list[dict]:
    """People whose name, email, ID or title contains the query (case-insensitive),
    in ID order, at most `limit`."""
    like = f"%{str(query).strip().lower()}%"
    sql = (f"SELECT {COLUMNS} FROM employees WHERE (lower(name) LIKE ? OR lower(email) LIKE ? OR lower(id) LIKE ? "
           "OR lower(title) LIKE ?)")
    args: list = [like, like, like, like]
    if dept:
        sql += " AND lower(dept) = lower(?)"
        args.append(dept)
    with db.connect() as conn:
        return conn.execute(sql + " ORDER BY id LIMIT ?", (*args, int(limit))).fetchall()


def count_matching(query: str, dept: str | None = None) -> int:
    """How many people match, so a capped result can say how many it left out."""
    return len(search_employees(query, dept, limit=10_000))


def manager_of(employee_id: str) -> dict | None:
    person = get_employee(employee_id)
    return get_employee(person["manager_id"]) if person and person["manager_id"] else None


def contractors_ended(before: str) -> list[dict]:
    """Contractors whose contract ended before `before` (a date) and who still hold
    any active membership: the ones whose access should go."""
    with db.connect() as conn:
        return conn.execute(
            f"SELECT {COLUMNS} FROM employees e WHERE employment = 'contractor' AND end_date IS NOT NULL "
            "AND end_date < ? AND EXISTS (SELECT 1 FROM memberships m WHERE m.employee_id = e.id "
            "AND m.removed_at IS NULL) ORDER BY id", (before,)).fetchall()
