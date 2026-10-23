"""The conversation state of each console session, in memory: within one session
the conversation is the state (Milestone 1).

Owner: Aman Panigrahi.
"""

from __future__ import annotations

_turns: dict[str, list[dict]] = {}


def history(session_id: str) -> list[dict]:
    return list(_turns.get(session_id, []))


def add(session_id: str, role: str, text: str) -> None:
    _turns.setdefault(session_id, []).append({"role": role, "text": text})
