"""The agent path's bound, set from outside the model (Lecture 1, slide 20).

Owner: Aman Panigrahi. The loop (agent/loop.py) and the planned path
(agent/executor.py) check all three before every model call and write their values
into the trace. Each value's reason is below and in M1.md, Part 1, task 5. The
measured figures are the baseline's, from experiments/analysis/analyze.py, which
prints our bound against what each loop used.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bound:
    # Model calls per turn. t04's request turn makes 10 tool calls in each baseline run:
    # one a call, plus the answer, is 11, and 3 to spare. 12 was tight in Shruti's A1: it
    # stopped 24 of 60 agent runs. The model batches its calls, so the most in one
    # baseline loop was 7 (t05); in pass 1, 12 (t07, sweep 1).
    max_steps: int = 14
    # Tokens per turn, prompts and output. A typical loop at the step limit uses
    # 14 x 2,156 (the median first prompt) + 648.7 (the median growth a step) x 91 =
    # 89,216, so it can use all 14 steps, and a loop whose prompt grows faster stops
    # first. The most in one baseline loop was 29,088 (t04); the harness's ceiling is
    # 400,000.
    token_budget: int = 90_000
    # Seconds per turn. 14 calls at the baseline's 95th-percentile call (5.56 s) take
    # 78 s; the rest is room for tools and a slow call (the slowest took 14.79 s). The
    # longest baseline loop took 23.5 s (t06, planned).
    wall_clock_s: float = 120.0


# Our end reasons (trace.end), never "token_budget", which the runner uses.
DONE = "done"
STEP_LIMIT = "step_limit"
LOOP_TOKEN_BUDGET = "loop_token_budget"
WALL_CLOCK = "wall_clock"
ANSWERED = "answered"
WORKFLOW_DONE = "workflow_done"
WORKFLOW_REFUSED = "workflow_refused"
PROTOCOL_ERROR = "protocol_error"
