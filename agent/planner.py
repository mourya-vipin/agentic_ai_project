"""The planner for U6 (bulk_change): it writes the whole plan and stops (Lecture 2,
slide 16). It sees the operator's instruction, the tool list, and record IDs and
counts from the sandbox, never a stranger's text.

Owner: Shruti Tewaney.
"""

from __future__ import annotations


def make_plan(session_id: str, operator_text: str, context: dict, feedback: list[str] | None = None) -> dict:
    raise NotImplementedError("make_plan: not built yet")
