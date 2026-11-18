"""The three exits for a write in doubt (Lecture 4, Section B; Part 1, task 2).

Owner: Aman Panigrahi.

A write is in doubt when it failed with a transient error, when the server died during
it, or when the process stopped while it was running. Our code then takes one of three
exits, in this order:

    1. it happened       the run's journal has the write (the registry adds each write
                         after it succeeds), or a read of the target finds its effect:
                         the run uses the saved result and does not make the call again
    2. it did not        the call is made again with the same arguments, so it gets the
                         same key, and the target keeps the first result of a key
    3. no one can tell   the second try also fails and no read can tell: the run holds,
                         and the operator gets an escalation (ambiguous_write)

    replayed(run_key, tool, args)    the journal entry of this write in this run, or None
    confirm(tool, args, key=None)    True / False / None: is the effect in the target?
    settle(tool, args, ctx, ...)     the three exits for one write: a Settled

The journal and the keys come from the registry (agent/registry.py) and the run store
(agent/checkpoints.py). The reads are those of Milestone 1's "Did the effect happen?"
table: the memberships, the pending records, the request, the credentials and the
notices sent.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import checkpoints
from agent.registry import ToolResult, canonical, get_registry
from agent.tools import impl
from harness import runtime

HAPPENED, MADE, UNKNOWN, FAILED = "happened", "made", "unknown", "failed"
IN_DOUBT = ("transient", "protocol")       # the error classes after which a write may or may not have landed
PENDING_KINDS = {"access_propose_change": "proposed_change", "reply_draft": "reply_draft",       # sandbox/pending.py
                 "manager_confirmation_draft": "manager_confirmation",
                 "credentials_record_handover": "credential_handover"}


@dataclass
class Settled:
    outcome: str                     # HAPPENED, MADE, FAILED (the second try failed for good) or UNKNOWN
    result: ToolResult               # what the caller uses as the call's result
    packet_id: str | None = None     # the escalation, when the outcome is UNKNOWN


def is_write(tool: str) -> bool:
    entry = impl.TOOLS.get(tool)
    return bool(entry and entry[2])


def _effect(tool: str, args: dict) -> dict:
    return {name: canonical((args or {}).get(name)) for name in impl.EFFECT_ARGS.get(tool, ())}


def replayed(run_key: str | None, tool: str, args: dict) -> tuple[str, dict] | None:
    """(key, journal entry) of the same write in this run, matched by the arguments that name
    its effect, as the registry derives the key from them. None if the journal has none."""
    if not run_key or not is_write(tool):
        return None
    want = _effect(tool, args)
    for key, entry in checkpoints.journal_of(run_key).items():
        if entry.get("tool") == tool and _effect(tool, entry.get("args") or {}) == want:
            return key, entry
    return None


def confirm(tool: str, args: dict, key: str | None = None) -> bool | None:
    """Whether the effect of the write is in the target: True, False, or None when no read
    of ours can tell. Our code reads the sandbox here, never the model."""
    from sandbox import access, credentials, db, requests
    args = args or {}
    if key:
        with db.connect() as conn:
            for table in ("memberships", "pending_actions", "notices_sent"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE key = ?", (key,)).fetchone():
                    return True
    person, group, rid = args.get("employee_id"), args.get("group_id"), args.get("request_id")
    if tool in ("access_grant", "access_revoke") and person and group:
        held = any(m["group_id"] == str(group).upper() for m in access.memberships(person))
        return held if tool == "access_grant" else not held
    if tool == "requests_set_status" and rid:
        row = requests.get(rid)
        return None if row is None else row["status"] == str(args.get("status") or "").lower()
    if tool == "credentials_issue" and person:
        return any(c["system_id"] == str(args.get("system_id") or "SYN-SYS-SSO").upper()
                   for c in credentials.for_employee(person))
    if tool in PENDING_KINDS and rid:
        from sandbox import pending
        return any(p["kind"] == PENDING_KINDS[tool] and (not person or p["employee_id"] == str(person).upper())
                   for p in pending.by_request(rid))
    return None          # a send with no key, or a write that no read of ours covers


def settle(tool: str, args: dict, ctx, *, first: ToolResult | None = None, registry=None,
           read_first: bool = False) -> Settled:
    """The three exits for one write in doubt. `first` is the result of the first try, if
    there was one (no result when the process stopped during it). With `read_first`, a read of
    the target comes before the same call again: for a call that the operator approved, since
    the gate lets it through once, so a second call would ask again for a write that happened."""
    registry = registry or get_registry()
    trace = runtime.trace(ctx.session_id)
    found = replayed(ctx.run_key, tool, args)
    if found is not None:
        key, entry = found
        trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=key, outcome=HAPPENED, read="journal")
        return Settled(HAPPENED, _from_journal(key, entry))
    if read_first:
        from agent.registry import derive_key
        key = derive_key(tool, args, ctx)
        if confirm(tool, args, key):
            trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=key, outcome=HAPPENED, read="target")
            return Settled(HAPPENED, ToolResult("Our read of the target finds this write's effect, so it happened; "
                                                "do not make it again.", data=None, key=key))
    again = registry.call(tool, args, ctx)
    if not again.is_error:
        trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=again.key, outcome=MADE, read="same key")
        return Settled(MADE, again)
    if again.error_class not in IN_DOUBT:
        trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=again.key, outcome=FAILED,
                    read="same key", error=again.content[:200])
        return Settled(FAILED, again)
    seen = confirm(tool, args, again.key or (first.key if first else None))
    if seen is not None:
        outcome = HAPPENED if seen else FAILED
        trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=again.key, outcome=outcome, read="target")
        note = ("Our read of the target finds this write's effect, so it happened; do not make it again."
                if seen else f"{again.content} Our read of the target finds no effect: the write did not happen.")
        return Settled(outcome, ToolResult(note, data=None, is_error=not seen, key=again.key))
    packet = _escalate(tool, args, ctx, again)
    trace.event("write_settled", tool=tool, run_key=ctx.run_key, key=again.key, outcome=UNKNOWN, packet_id=packet)
    note = (f"No one can tell if this write happened, so the run holds for the operator ({packet}). "
            "Make no other write; end the turn here.")
    return Settled(UNKNOWN, ToolResult(note, is_error=True, key=again.key, end_reason=_escalated()), packet)


def _from_journal(key: str, entry: dict) -> ToolResult:
    import json
    data = entry.get("result")
    return ToolResult(json.dumps(data, default=str), data=data, key=key)


def _escalated() -> str:
    from agent import escalation
    return escalation.ESCALATED


def _escalate(tool: str, args: dict, ctx, result: ToolResult) -> str | None:
    """The third exit: hold the run, and put the packet up for the operator."""
    if not ctx.run_key:
        return None
    from agent import escalation
    effect = ", ".join(f"{k}={v}" for k, v in _effect(tool, args).items() if v)
    return escalation.escalate(
        ctx.run_key, "ambiguous_write",
        attempted=f"{tool} ({effect})",
        why=f"two tries failed with {result.error_class} errors, and no read of ours can tell if it landed",
        needs=f"look at the target for this effect, then answer: it happened, or it did not")
