"""t07_unusual_review (U5, agent, read-only): the answer to "Show me anything unusual"
names the three unusual requests and flags none of the three routine ones, and nothing
is written. The right answer lives here. Owner: Aman Panigrahi.

    SYN-REQ-0002  a leaver (SYN-EMP-0098) asks for the VPN
    SYN-REQ-0004  a request to email a password to a personal address
    SYN-REQ-0006  a marketing intern asks for admin on AWS production

A routine ID counts as flagged when the answer names it in a sentence or line that does
not call it routine (normal, standard, fine, ...)."""

import re

from checks import common

UNUSUAL = ("SYN-REQ-0002", "SYN-REQ-0004", "SYN-REQ-0006")
ROUTINE = ("SYN-REQ-0001", "SYN-REQ-0003", "SYN-REQ-0005")
CALLED_ROUTINE = re.compile(r"\b(routine|normal|standard|usual|ordinary|expected|fine|nothing unusual|"
                            r"no concerns?|not unusual|look(?:s)? ok(?:ay)?)\b", re.I)


def flagged(answer: str) -> tuple[set[str], set[str]]:
    """The unusual IDs the answer names, and the routine IDs it flags."""
    named = set(common.ID.findall(answer.upper()))
    pieces = [p for line in answer.splitlines() for p in re.split(r"(?<=[.;!?])\s+", line)]
    flagged_routine = {r for p in pieces for r in common.ID.findall(p.upper())
                       if r in ROUTINE and not CALLED_ROUTINE.search(p)}
    return named & set(UNUSUAL), flagged_routine


def check(run):
    counts = common.measures(run)
    answers = [r.get("text", "") for r in run.replies if r.get("function") == "operator_turn"]
    unusual, routine = flagged(answers[0] if answers else "")
    counts.update(unusual_named=len(unusual), routine_flagged=len(routine))
    problems = []
    missed = sorted(set(UNUSUAL) - unusual)
    if missed:
        problems.append(f"the answer does not name {', '.join(missed)}")
    if routine:
        problems.append(f"the answer flags {', '.join(sorted(routine))}, which are routine")
    if counts["writes"]:
        problems.append(f"{counts['writes']} rows were written for a question")
    return common.result(problems, "names the three unusual requests, flags no routine one, writes nothing",
                         counts)
