"""The critique loop on reply_draft: a check on an outside signal, the sandbox's
records, before a drafted reply is written (Lecture 2, slide 43).

Owner: Shruti Tewaney. reply_draft's implementation calls review_draft() when
critique.requester_reply is "external". On a fail while a round remains, the tool
writes nothing and returns the reason to the author; at MAX_ROUNDS it writes nothing
and ends the run with critique_held.

The check is a verification, not an opinion: it reads what this run (or console
session) actually did, from the identity store and the pending records, never a
test task's check or its expected answer, and fails a draft that

    1. says a group was granted that no membership of this case's shows granted;
    2. says a group was removed that this case did not remove;
    3. says a group is waiting for approval with no open proposal or confirmation;
       (and, for a group it says was kept, one no one in the case still holds)
    4. leaves out a group the case granted, removed or proposed, or, on an
       operator's bulk change, a person whose access changed;
    5. names a request other than the one it answers, or a person outside the case.

A group is recognised by its ID (SYN-GRP-...) or its full catalog name; the claim
about it is the nearest claim word in its sentence (granted, removed, waiting, kept),
unless negated. Ported in spirit from Shruti's Assignment 2 (agent/critique.py:
verify), which checked a draft's file against what the run had seen.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from sandbox import access, catalog, directory, pending, requests

MAX_ROUNDS = 2
CRITIQUE_HELD = "critique_held"
MAX_REASONS = 3            # the reason names at most this many facts, most important first

GROUP_ID = re.compile(r"SYN-GRP-[A-Z0-9]+(?:-[A-Z0-9]+)*", re.IGNORECASE)
REQUEST_ID = re.compile(r"SYN-REQ-\d{4}", re.IGNORECASE)
EMPLOYEE_ID = re.compile(r"SYN-EMP-\d{4}", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.;!?\n]")
CLAIMS = (
    ("granted", re.compile(r"\b(granted|added|given|gave you|provisioned|now ha(?:ve|s) access)\b", re.I)),
    ("removed", re.compile(r"\b(removed|revoked|taken away|took away|deprovisioned|no longer ha(?:ve|s))\b", re.I)),
    ("waiting", re.compile(r"\b(waiting|pending|awaits?|awaiting|approval|proposed|proposal|"
                           r"needs? (?:the |your )?(?:manager|operator)|confirm(?:ation)?)\b", re.I)),
    ("kept", re.compile(r"\b(kept|keeps?|remains?|retained|still ha(?:ve|s)|stays?|left in place)\b", re.I)),
)
NEGATED = re.compile(r"(\bnot|n't|\bnever|\bno)\s+(been\s+|yet\s+|be\s+)?$", re.I)
WAITING_KINDS = ("proposed_change", "manager_confirmation")


@dataclass
class Verdict:
    passed: bool
    reason: str       # the outside fact that failed, in one sentence
    round: int
    changed: int = 0  # characters changed since this case's last draft


_last: dict[tuple, str] = {}     # (stamp, recipient, request) -> the last draft reviewed


def review_draft(*, recipient_employee_id: str, request_id: str | None, body: str,
                 stamp: str, round: int) -> Verdict:
    slot = (stamp, recipient_employee_id, request_id)
    changed = _changed(_last.get(slot), body)
    _last[slot] = body
    failures = check_draft(recipient_employee_id=recipient_employee_id, request_id=request_id, body=body,
                           stamp=stamp)
    if failures:
        return Verdict(False, " ".join(failures[:MAX_REASONS]), round, changed)
    return Verdict(True, "Every group and person the draft names matches the records.", round, changed)


def _changed(before: str | None, after: str) -> int:
    if before is None:
        return 0
    ops = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")


# -- the case: what the records say this run did ------------------------------------------

def _case(recipient: str, request_id: str | None, stamp: str) -> dict:
    changes = access.changes_by_stamp(stamp)
    if request_id:
        req = requests.get(request_id) or {}
        changes = [c for c in changes if c["under_request"] == request_id]
        waiting = [p for p in pending.by_request(request_id)
                   if p["stamp"] == stamp and p["kind"] in WAITING_KINDS and p["status"] == "open"]
        people = {recipient, req.get("requester_id"), req.get("subject_id")}
        people |= {(directory.manager_of(p) or {}).get("id") for p in list(people) if p}
        affected = sorted({c["employee_id"] for c in changes} | {p["employee_id"] for p in waiting})
        text = req.get("text") or ""
    else:
        # An operator's bulk change: what this run did to the recipient's own reports.
        def reports(employee_id):
            return (directory.get_employee(employee_id) or {}).get("manager_id") == recipient
        changes = [c for c in changes if c["under_request"] is None and reports(c["employee_id"])]
        waiting = [p for kind in WAITING_KINDS for p in pending.open_items(kind)
                   if p["stamp"] == stamp and p["request_id"] is None and reports(p["employee_id"])]
        affected = sorted({c["employee_id"] for c in changes} | {p["employee_id"] for p in waiting})
        people, text = {recipient}, ""
    people = {p for p in people | set(affected) if p}
    active = {m["group_id"] for person in affected or [recipient] for m in access.memberships(person)}
    return {"granted": {c["group_id"] for c in changes if c["change"] == "granted"},
            "removed": {c["group_id"] for c in changes if c["change"] == "removed"},
            "waiting": {p["group_id"]: p["id"] for p in waiting if p["group_id"]},
            "active": active, "affected": affected, "people": people, "request_text": text.lower(),
            "bulk": request_id is None}


# -- the draft: which groups it names, and what it says about each ---------------------------

def _mentions(body: str, groups: dict[str, dict]) -> list[tuple[str, int, int]]:
    found = [(m.group(0).upper(), m.start(), m.end()) for m in GROUP_ID.finditer(body)]
    lower = body.lower()
    for gid, group in groups.items():
        name = (group.get("name") or "").lower()
        start = lower.find(name) if name else -1
        while start >= 0:
            found.append((gid, start, start + len(name)))
            start = lower.find(name, start + 1)
    return found


def _claim(body: str, start: int, end: int) -> str | None:
    """The claim word nearest the mention in its sentence, or None."""
    left = max((m.end() for m in CLAUSE_END.finditer(body, 0, start)), default=0)
    right = next((m.start() for m in CLAUSE_END.finditer(body, end)), len(body))
    best, distance = None, None
    for kind, pattern in CLAIMS:
        for m in pattern.finditer(body, left, right):
            if NEGATED.search(body[max(left, m.start() - 20):m.start()]):
                continue
            d = m.start() - end if m.start() >= end else start - m.end()
            if distance is None or d < distance:
                best, distance = kind, d
    return best


def check_draft(*, recipient_employee_id: str, request_id: str | None, body: str, stamp: str) -> list[str]:
    """Every fact the draft gets wrong, one sentence each, in rule order; [] when it fits."""
    case = _case(recipient_employee_id, request_id, stamp)
    groups = {g["id"]: g for g in catalog.search_groups("", limit=1000)}
    under = f" under {request_id}" if request_id else ""
    wrong: list[str] = []
    mentioned: set[str] = set()
    for gid, start, end in sorted(_mentions(body, groups), key=lambda x: x[1]):
        if gid not in groups:
            wrong.append(f"The draft names {gid}, which is not a group in the catalog.")
            continue
        mentioned.add(gid)
        claim = _claim(body, start, end)
        pend = case["waiting"].get(gid)
        if claim == "granted" and gid not in case["granted"]:
            wrong.append(f"The draft says {gid} was granted, but "
                         + (f"it is waiting for approval ({pend})." if pend
                            else f"no membership of it was granted{under} in this run."))
        elif claim == "removed" and gid not in case["removed"]:
            wrong.append(f"The draft says {gid} was removed, but "
                         + (f"its removal is waiting for approval ({pend})." if pend
                            else "it is still active." if gid in case["active"]
                            else f"this run removed no membership of it{under}."))
        elif claim == "waiting" and pend is None:
            wrong.append(f"The draft says {gid} is waiting for approval, but "
                         + ("it was granted." if gid in case["granted"] else "it was removed."
                            if gid in case["removed"] else "no proposal or confirmation for it was written in this run."))
        elif claim == "kept" and gid not in case["active"]:
            wrong.append(f"The draft says {gid} was kept, but no one in this case still holds it.")
    for kind, ids in (("granted", case["granted"]), ("removed", case["removed"]),
                      ("waiting for approval", set(case["waiting"]))):
        for gid in sorted(ids - mentioned):
            wrong.append(f"The draft does not mention {gid}, which is {kind} in this case; name it by its ID.")
    lower = body.lower()
    if case["bulk"]:
        for person_id in case["affected"]:
            person = directory.get_employee(person_id) or {}
            if person_id.lower() not in lower and (person.get("name") or "").lower() not in lower:
                wrong.append(f"The draft does not name {person.get('name')} ({person_id}), whose access changed.")
    for rid in sorted({m.upper() for m in REQUEST_ID.findall(body)} - ({request_id} if request_id else set())):
        wrong.append(f"The draft names {rid}, but it answers "
                     + (f"{request_id}." if request_id else "no request: leave request IDs out."))
    for pid in sorted({m.upper() for m in EMPLOYEE_ID.findall(body)} - case["people"]):
        person = directory.get_employee(pid)
        if person and person["name"].lower() in case["request_text"]:
            continue                       # named in the request itself: the requester knows them
        wrong.append(f"The draft names {pid}, who is not part of this case; leave them out.")
    return wrong
