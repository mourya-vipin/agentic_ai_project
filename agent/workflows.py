"""The workflows: our code picks each step and ends the run (Lecture 1, slide 19).

Owner: Aman Panigrahi. U1 standard_grant, U2 onboarding, U3 offboarding. Every step
calls the registry, which traces it.
"""

from __future__ import annotations

from agent.loop import LoopResult


def standard_grant(session_id: str, request_id: str) -> LoopResult:
    raise NotImplementedError("standard_grant: not built yet")


def onboarding(session_id: str, request_id: str) -> LoopResult:
    raise NotImplementedError("onboarding: not built yet")


def offboarding(session_id: str, request_id: str) -> LoopResult:
    raise NotImplementedError("offboarding: not built yet")
