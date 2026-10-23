"""The detectors (Lecture 2, slides 49 to 52): the repeat check on every agent-path
run, the orphan check on every plan, and the diff of a plan against what ran.

Owner: Shruti Tewaney. RepeatTracker.see() returns None until it is built, so the
loop can call it from the start.
"""

from __future__ import annotations


class RepeatTracker:
    def see(self, tool: str, args: dict) -> dict | None:
        """The earlier call's {"step": n, "hash": h} if this call repeats one exactly
        (same tool, same canonical arguments), else None. The caller writes
        trace.event("repeat_call", ...)."""
        return None
