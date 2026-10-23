"""The plan validator, run before any step (Lecture 2, slide 18): every tool exists,
no step depends on a later one, every step has an observable outcome, the step count
is within the plan's limit, and the orphan check.

Owner: Shruti Tewaney.
"""

from __future__ import annotations


def validate(plan: dict, tool_names: list[str], max_steps: int) -> list[tuple[int | None, str, str]]:
    """Every problem as (step_id, rule, message); an empty list means valid."""
    raise NotImplementedError("validate: not built yet")
