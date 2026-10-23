"""The bounded agent loop: the model picks each step and when to stop; our bound
ends the run from outside (Lecture 1, slide 20).

Owner: Aman Panigrahi. Used by U4 (nonstandard_request), U5 (operator_review),
and U6 (bulk_change) when plan.bulk_change is off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.bounds import Bound


@dataclass
class LoopResult:
    text: str                                         # what the operator reads
    actions: list[str] = field(default_factory=list)  # one line per effect
    end_reason: str = ""                              # one of the names in agent/bounds.py
    steps: int = 0


def run_loop(session_id: str, *, use_case: str, system: str, user_content: str,
             tools: list[str], bound: Bound, request_id: str | None = None) -> LoopResult:
    raise NotImplementedError("run_loop: not built yet")
