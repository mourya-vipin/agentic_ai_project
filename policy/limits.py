"""Limits the operator states at the console, parsed by code into rules the hook reads
(Lecture 5, slide 17; Part 2, task 3). Owner: Vipin Mourya.

    rule = limits.from_text(session_id, "Send nothing until I have read it.")   # a Rule, or None
    limits.active(session_id)          # the rules in force, which policy_hook checks before every call
    limits.lift_from_text(session_id, "You can send now.")                     # the rule IDs lifted

Said in chat, a limit is only text that compaction can drop; parsed here, it is a rule the hook
checks before every call, whatever the context window holds. Never a model: the patterns below
are the parser, so a limit they do not recognise is not a rule, and from_text says so with None.
Rules are the operator's: one stated in any console session holds in every session until it
expires or is lifted, and lives in the sandbox (state_dir()/access.sqlite3, table stated_limits),
so it survives a restart (make serve) and make app's reset clears it.

What the patterns recognise (case-insensitive):

    sends      "send nothing", "don't send anything", "no messages", "hold all replies"
    grants     "no grants", "don't grant anything", "no grants to SYN-GRP-…", "nobody gets <group>"
    removals   "don't remove anything", "no removals", "no revocations from <group>"
    how long   "today" (to the end of the sandbox's day), "this week" (to the end of its Sunday);
               anything else, such as "until I have read it", holds until the operator lifts it
    lifting    "lift L2", "lift all limits", "you can send now", "go ahead and send"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

from harness import runtime
from sandbox import catalog, clock, db

OPERATOR_LIFTS = "operator_lifts"
SEND_CLASSES = ("reply_send/*", "ask_stranger/*", "notices_send_template/*", "manager_note/*")

_SENDS = re.compile(r"\b(send nothing|don'?t send|do not send|no (messages|replies|emails|notices)|"
                    r"hold (all )?(messages|replies|sends)|nothing (goes|is sent) out)\b", re.I)
_GRANTS = re.compile(r"\b(no grants?|don'?t grant|do not grant|nobody gets|no one gets|grant nothing)\b", re.I)
_REMOVALS = re.compile(r"\b(no (removals?|revocations?)|don'?t (remove|revoke)|do not (remove|revoke)|"
                       r"remove nothing)\b", re.I)
_TODAY = re.compile(r"\btoday\b", re.I)
_WEEK = re.compile(r"\bthis week\b", re.I)
_GROUP_ID = re.compile(r"\bSYN-GRP-[A-Z0-9-]+\b", re.I)
_AFTER = re.compile(r"\b(?:nobody gets|no one gets|grants? (?:of|to|for)|grant(?:ing)?|removals? (?:of|from)|"
                    r"revocations? (?:of|from)|remove|revoke)\s+(.+?)(?=\s+(?:today|this week|without|until|to anyone|"
                    r"for anyone|unless)\b|[.,;!]|$)", re.I)
_LIFT_ID = re.compile(r"\blift\s+(?:limit\s+|rule\s+)?(L\d+)\b", re.I)
_LIFT_ALL = re.compile(r"\blift (all )?(the )?(limits|rules)\b", re.I)
_LIFT_SENDS = re.compile(r"\b(you can send( now)?|go ahead and send|sending is fine( now)?)\b", re.I)


@dataclass
class Rule:
    id: str
    session_id: str
    text: str             # the operator's words, kept as said
    blocks: dict          # {"classes": [...action-class patterns], "group_id": optional}
    until: str | None     # "operator_lifts" or an ISO time
    stated_at: str


# -- parsing -------------------------------------------------------------------------------

def _group_in(text: str) -> str | None:
    """The group a limit names: by its ID, or by the words after its verb matched to a group's ID
    or name with the punctuation taken out ("AWS-Prod admin" is SYN-GRP-AWSPROD-ADMIN)."""
    found = _GROUP_ID.search(text)
    if found:
        return found.group(0).upper()
    after = _AFTER.search(text)
    if not after:
        return None
    phrase = re.sub(r"\b(the|a|an|any|group|access|to|of)\b", " ", after.group(1).lower())
    squeezed = re.sub(r"[^a-z0-9]", "", phrase)
    if not squeezed or squeezed in ("anything", "anyone", "nothing"):
        return None
    for group in catalog.search_groups("", limit=1000):
        tail = re.sub(r"[^a-z0-9]", "", group["id"].lower().removeprefix("syn-grp-"))
        name = re.sub(r"[^a-z0-9]", "", group["name"].lower())
        if squeezed in (tail, name) or (len(squeezed) >= 6 and (squeezed in name or name.startswith(squeezed))):
            return group["id"]
    return None


def _until(text: str) -> str:
    today = date.fromisoformat(clock.today())
    if _TODAY.search(text):
        return f"{today.isoformat()}T23:59:59Z"
    if _WEEK.search(text):
        return f"{(today + timedelta(days=6 - today.weekday())).isoformat()}T23:59:59Z"
    return OPERATOR_LIFTS


def parse(text: str) -> dict | None:
    """What a message would block, or None if it states no limit our patterns know."""
    if _SENDS.search(text):
        return {"classes": list(SEND_CLASSES)}
    if _GRANTS.search(text):
        group = _group_in(text)
        return {"classes": ["access_grant/*"], **({"group_id": group} if group else {})}
    if _REMOVALS.search(text):
        group = _group_in(text)
        return {"classes": ["access_revoke/*"], **({"group_id": group} if group else {})}
    return None


# -- the store -------------------------------------------------------------------------------

def _row(r) -> Rule:
    return Rule(r["id"], r["session_id"], r["text"], json.loads(r["blocks"]), r["until"], r["stated_at"])


def from_text(session_id: str, text: str) -> Rule | None:
    """The rule an operator's message states, stored durably, or None if it states none.
    Called on every operator message, at the start of a turn or mid-task."""
    blocks_ = parse(str(text or ""))
    if blocks_ is None:
        return None
    stated_at = clock.now()
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM stated_limits").fetchone()["n"] + 1
        rule = Rule(f"L{n}", str(session_id), str(text).strip(), blocks_, _until(text), stated_at)
        conn.execute("INSERT INTO stated_limits (id, session_id, text, blocks, until, stated_at, stamp) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (rule.id, rule.session_id, rule.text, json.dumps(rule.blocks),
                                                      rule.until, rule.stated_at, db.current_stamp()))
    runtime.trace(session_id).event("limit_stated", rule_id=rule.id, text=rule.text, blocks=rule.blocks,
                                    until=rule.until)
    return rule


def active(session_id: str | None = None) -> list[Rule]:
    """The rules in force, oldest first: not lifted, and not past their time."""
    now = clock.now()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM stated_limits WHERE lifted_at IS NULL ORDER BY rowid").fetchall()
    return [_row(r) for r in rows if r["until"] == OPERATOR_LIFTS or r["until"] > now]


def blocks(rule: Rule, action_class: str, args: dict) -> bool:
    """Whether the rule forbids this call."""
    if not any(action_class == c or (c.endswith("/*") and action_class.startswith(c[:-1]))
               for c in rule.blocks.get("classes", [])):
        return False
    group = rule.blocks.get("group_id")
    return group is None or str(args.get("group_id") or "").upper() == group


def lift(session_id: str, rule_id: str) -> None:
    """The operator lifts a rule; it stays in the store, marked lifted, with the time."""
    with db.connect() as conn:
        conn.execute("UPDATE stated_limits SET lifted_at = ?, lifted_by = ? WHERE id = ? AND lifted_at IS NULL",
                     (clock.now(), str(session_id), str(rule_id).upper()))
    runtime.trace(session_id).event("limit_lifted", rule_id=str(rule_id).upper())


def lift_from_text(session_id: str, text: str) -> list[str]:
    """The rules an operator's message lifts, by ID, all of them, or every send limit."""
    text = str(text or "")
    live = active(session_id)
    if _LIFT_ALL.search(text):
        chosen = [r.id for r in live]
    elif _LIFT_SENDS.search(text):
        chosen = [r.id for r in live if set(r.blocks.get("classes", [])) & set(SEND_CLASSES)]
    else:
        chosen = [m.upper() for m in _LIFT_ID.findall(text) if m.upper() in {r.id for r in live}]
    for rule_id in chosen:
        lift(session_id, rule_id)
    return chosen
