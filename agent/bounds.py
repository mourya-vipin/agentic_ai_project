"""The agent path's bound, set from outside the model (Lecture 1, slide 20).

Owner: Aman Panigrahi. The numbers are first values; their reasons go in M1.md,
Part 1, and in the comments below once the baseline confirms them. The loop checks
all three before every model call and writes their values into the trace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bound:
    max_steps: int = 14           # model calls per turn
    token_budget: int = 90_000    # tokens per turn, below the harness's 400,000 ceiling
    wall_clock_s: float = 120.0   # seconds per turn


# Our end reasons (trace.end), never "token_budget", which the runner uses.
DONE = "done"
STEP_LIMIT = "step_limit"
LOOP_TOKEN_BUDGET = "loop_token_budget"
WALL_CLOCK = "wall_clock"
ANSWERED = "answered"
WORKFLOW_DONE = "workflow_done"
WORKFLOW_REFUSED = "workflow_refused"
PROTOCOL_ERROR = "protocol_error"
