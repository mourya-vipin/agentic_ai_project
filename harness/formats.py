"""The shared names: how a run ended, and the trace lines the harness writes itself.

The rest is yours to name. Your end reasons (trace.end), your event kinds and their
fields (trace.event), your model calls' purposes and your plan's shape are your
team's design; the harness checks none of them.
"""

from __future__ import annotations

# How a run.py run ended, as its record says (record["outcome"]). The runner
# decides it, not your code.
OUTCOMES = (
    "completed",      # every scripted turn was played; the check decides pass or fail
    "crashed",        # your agent raised; the record keeps the error
    "token_budget",   # the harness's hard ceiling stopped the run
    "not_built",      # agent/service.py raised NotImplementedError
    "quota",          # the vendor's daily quota stopped the run, and the sweep
    "rate_limited",   # the vendor still refused after every retry
)

# A run with one of these outcomes is not finished: it never counts, and running
# the same command again redoes it.
UNFINISHED = ("not_built", "quota", "rate_limited")

# An end reason, trace.end(reason): a short name your team chooses, such as
# plan_rejected. Letters, digits and _, starting with a letter, at most 40 characters.
END_REASON_MAX = 40

# Names from Assignment 2, for code you reuse from it. Nothing in the harness checks them.
CRITIQUE_MODES = ("off", "self", "external")
NOTES_MODES = ("off", "on")
PLAN_STATUSES = ("pending", "done", "failed")
FILL = "$fill"   # in a plan, an argument the executor fills in at run time

# Line types the harness writes, or writes for you through trace.model_call,
# trace.tool_call, trace.plan, trace.progress and trace.end; trace.event cannot use them.
RESERVED_TYPES = ("session_start", "run_start", "run_end", "sandbox", "operator", "outside", "reply",
                  "approval", "error", "model_call", "tool_call", "plan", "progress", "end")
