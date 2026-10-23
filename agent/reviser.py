"""The reviser: repairs the plan only when the named checkpoint's check fails, from
step results alone, never changing a step already done (Lecture 2, slide 17).
Owner: Shruti Tewaney."""

from __future__ import annotations


def revise_plan(session_id: str, plan: dict, done_steps: list[int], results: dict, failure: str) -> dict:
    raise NotImplementedError("revise_plan: not built yet")
