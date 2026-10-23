"""Routing: tell a new task from a question at the console, and send each request
to its use case's path (Lecture 1, slide 19).

Owner: Aman Panigrahi. Trusted data first (the thread, the requester's directory
record); a model call, traced as a step of its own, only where code cannot tell.
The switches control.<use_case> say which path each use case takes.
"""

from __future__ import annotations

from harness.turns import Reply


def handle_operator(session_id: str, text: str) -> Reply:
    """One operator message: a new task (process requests, a bulk change) or a
    question about work already done."""
    raise NotImplementedError("handle_operator: routing is not built yet")
