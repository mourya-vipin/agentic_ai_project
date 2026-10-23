"""The sandbox's fixed "now", so every run repeats. The seed writes it; every tool
and page reads the date from here, never from the machine's clock.
Owner: Vipin Mourya."""

from __future__ import annotations

from sandbox import db

NOW = "2026-10-21T09:00:00Z"      # a Wednesday


def now() -> str:
    with db.connect() as conn:
        row = conn.execute("SELECT now FROM clock LIMIT 1").fetchone()
    return row["now"] if row else NOW


def today() -> str:
    return now()[:10]


def set_now(value: str = NOW) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM clock")
        conn.execute("INSERT INTO clock (now) VALUES (?)", (value,))
