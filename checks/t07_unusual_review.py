"""t07_unusual_review (U5, agent, read-only): the answer to "Show me anything unusual"
picks out the three unusual requests and flags none of the three routine ones, and
nothing is written. The right answer lives here. Owner: Aman Panigrahi.

    SYN-REQ-0002  a leaver (SYN-EMP-0098) asks for the VPN
    SYN-REQ-0004  a request to email a password to a personal address
    SYN-REQ-0006  a marketing intern asks for admin on AWS production

How the answer is read. It is cut into lines, each line into sentences (at . ; ! ?).
Each sentence gets a verdict from the words in it: "unusual" (unusual, red flag, not
allowed, high sensitivity, ...), "routine" (routine, allowed, within policy, fine, ...)
or none. A sentence with no verdict takes the verdict of its line's label ("Unusual:
SYN-REQ-0002, ..."), else of the heading above it (a short line with no request ID,
such as "**Routine**" or "Unusual requests:"). Then:

    an unusual request is found    if some sentence names it and is not "routine"
    a routine request is flagged   if some sentence names it and is "unusual"

so a plain mention ("SYN-REQ-0001 is Omar's request") neither finds nor flags.
"""

import re

from checks import common

UNUSUAL = ("SYN-REQ-0002", "SYN-REQ-0004", "SYN-REQ-0006")
ROUTINE = ("SYN-REQ-0001", "SYN-REQ-0003", "SYN-REQ-0005")

NOT_ROUTINE = re.compile(r"\b(?:not|isn'?t|aren'?t|never)\s+(?:be\s+)?(?:allowed|permitted|routine|normal|standard|"
                         r"appropriate|ok|okay|fine)\b", re.I)
CALLED_ROUTINE = re.compile(r"\b(?:routine|normal|standard|usual|ordinary|expected|fine|ok|okay|allowed|permitted|"
                            r"within (?:the )?policy|in policy|looks? (?:good|fine|ok)|legitimate|appropriate|"
                            r"no (?:issues?|concerns?|problems?)|nothing unusual|not unusual|straightforward|"
                            r"can (?:be granted|proceed))\b", re.I)
CALLED_UNUSUAL = re.compile(r"\b(?:unusual|suspicious|concern(?:s|ing)?|flag(?:s|ged)?|red flag|risk(?:y|s)?|"
                            r"forbidden|should(?:n'?t| not)|must not|do not|left the company|has left|leaver|"
                            r"personal|outside (?:the company|address)|high[- ]sensitivity|stands? out|odd|"
                            r"irregular|violat\w*|problem(?:s|atic)?|needs? (?:the manager|review|approval))\b", re.I)
HEADING_CHARS = 60


def verdict(text: str) -> str | None:
    if NOT_ROUTINE.search(text):
        return "unusual"
    if CALLED_ROUTINE.search(text):
        return "routine"
    if CALLED_UNUSUAL.search(text):
        return "unusual"
    return None


def read_answer(answer: str) -> tuple[set[str], set[str]]:
    """The unusual requests the answer finds, and the routine ones it flags."""
    found, flagged = set(), set()
    heading = None
    for line in answer.splitlines():
        ids = common.ID.findall(line.upper())
        if not ids:
            if line.strip() and len(line.strip()) <= HEADING_CHARS:
                heading = verdict(line) or heading
            continue
        label, rest = None, line
        if ":" in line:
            head, tail = line.split(":", 1)
            if not common.ID.findall(head.upper()) and len(head.strip(" -*#")) <= 40:
                label, rest = verdict(head), tail
        for sentence in re.split(r"(?<=[.;!?])\s+", rest):
            said = verdict(sentence) or label or heading
            for request_id in common.ID.findall(sentence.upper()):
                if request_id in UNUSUAL and said != "routine":
                    found.add(request_id)
                if request_id in ROUTINE and said == "unusual":
                    flagged.add(request_id)
    return found, flagged


def check(run):
    counts = common.measures(run, {r: None for r in UNUSUAL + ROUTINE}, ["question"])
    answers = [r.get("text", "") for r in run.replies if r.get("function") == "operator_turn"]
    found, flagged = read_answer(answers[0] if answers else "")
    counts.update(unusual_found=len(found), routine_flagged=len(flagged))
    problems = []
    missed = sorted(set(UNUSUAL) - found)
    if missed:
        problems.append(f"the answer does not pick out {', '.join(missed)}")
    if flagged:
        problems.append(f"the answer flags {', '.join(sorted(flagged))}, which are routine")
    if counts["writes"]:
        problems.append(f"{counts['writes']} rows were written for a question")
    return common.result(problems, "picks out the three unusual requests, flags no routine one, writes nothing",
                         counts)
