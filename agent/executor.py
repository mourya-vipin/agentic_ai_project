"""The executor for the planned agent: it runs the plan's steps in order, fills
"$fill" arguments, checks each step's outcome and the checkpoint, and refuses any
call off the plan (Lecture 2, slide 16).

Owner: Shruti Tewaney.
"""

from __future__ import annotations

from agent.bounds import Bound
from agent.loop import LoopResult


def run_planned(session_id: str, operator_text: str, *, bound: Bound) -> LoopResult:
    raise NotImplementedError("run_planned: not built yet")
