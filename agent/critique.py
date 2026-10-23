"""The critique loop on reply_draft: a check on an outside signal, the sandbox's
records, before a drafted reply is written (Lecture 2, slide 43).

Owner: Shruti Tewaney. reply_draft's implementation calls review_draft() when
critique.requester_reply is "external". On a fail while a round remains, the tool
writes nothing and returns the reason to the author; at MAX_ROUNDS it writes nothing
and ends the run with critique_held. Until it is built, every draft passes.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ROUNDS = 2
CRITIQUE_HELD = "critique_held"


@dataclass
class Verdict:
    passed: bool
    reason: str       # the outside fact that failed, in one sentence
    round: int


def review_draft(*, recipient_employee_id: str, request_id: str | None, body: str,
                 stamp: str, round: int) -> Verdict:
    return Verdict(True, "not checked yet: the verifier is not built", round)
