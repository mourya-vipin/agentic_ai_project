"""Limits the operator states at the console, parsed by code into rules the hook reads
(Lecture 5, slide 17). Owner: Vipin Mourya.

Said in chat, a limit is only text that compaction can drop; parsed here, it is a rule
the hook checks before every call. Never a model: the patterns below are the parser.

STUB until phase 1 (C3.8, due Tue 17 Nov): the signatures are final, so callers can
code against them now; from_text() parses nothing yet and active() returns no rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Rule:
    id: str
    session_id: str
    text: str             # the operator's words, kept as said
    blocks: dict          # e.g. {"action_class": "reply_send/*"} or {"group_id": "SYN-GRP-AWS-PROD"}
    until: str | None     # "operator_lifts" or an ISO time
    stated_at: str


def from_text(session_id: str, text: str) -> Rule | None:
    """The rule an operator's message states, stored durably, or None if it states none."""
    return None


def active(session_id: str) -> list[Rule]:
    """The rules in force for this console session, oldest first."""
    return []


def blocks(rule: Rule, action_class: str, args: dict) -> bool:
    """Whether the rule forbids this call."""
    return False


def lift(session_id: str, rule_id: str) -> None:
    """The operator lifts a rule; it stays in the store, marked lifted, with the time."""
