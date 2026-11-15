"""The operator's commands, read by code before any model sees the message (Part 2, task 6).

Owner: Shruti Tewaney.

    /stop            stop every live run of this session
    /stop <n>        stop run n of this session
    /cancel <n>      end run n for good; what it did stays
    /resume <n>      lift run n's stop and carry on from its saved state
    /rollback <n>    undo run n's writes, newest first, where they can be undone
    /answer <id> <text>   answer an escalation's packet (ESC-0001), from any console session
    /packet <id>     open an escalation's packet, one held over the day's cap included

service.operator_turn calls handle() first. A message that is a command is
answered here and never queued, routed or shown to a model; any other message gets
None and goes on as usual. A command with a missing or malformed run number gets its
usage back, still without a model.
"""

from __future__ import annotations

import re

from agent import control
from harness import runtime
from harness.turns import Reply

COMMANDS = ("stop", "cancel", "resume", "rollback")
PACKET_COMMANDS = ("answer", "packet")
PACKET = re.compile(r"^\s*/(?P<name>answer|packet)\b\s*(?P<id>ESC-\d{1,6})?\s*(?P<text>.*)$", re.IGNORECASE | re.DOTALL)
NEEDS_RUN = ("cancel", "resume", "rollback")
COMMAND = re.compile(r"^\s*/(?P<name>[a-z]+)\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
RUN_NO = re.compile(r"^\s*#?(?P<n>\d{1,6})\s*$")

USAGE = {
    "stop": "/stop stops every run of this session; /stop <run number> stops one.",
    "cancel": "/cancel <run number> ends that run for good.",
    "resume": "/resume <run number> lifts that run's stop and carries on.",
    "rollback": "/rollback <run number> undoes that run's writes, newest first.",
    "answer": "/answer <packet ID, such as ESC-0001> <your answer> answers a held run's question.",
    "packet": "/packet <packet ID, such as ESC-0001> shows what a held run needs.",
}


def parse(text: str) -> tuple[str, int | None, bool] | None:
    """(command, run number or None, well formed) for one of our commands, else None."""
    match = COMMAND.match(text or "")
    if not match or match["name"].lower() not in COMMANDS:
        return None
    name, rest = match["name"].lower(), match["rest"].strip()
    if not rest:
        return name, None, name not in NEEDS_RUN
    number = RUN_NO.match(rest)
    return (name, int(number["n"]), True) if number else (name, None, False)


def handle(session_id: str, text: str) -> Reply | None:
    """Answer a command, or return None so the message goes on to the inbox and the router."""
    packet = PACKET.match(text or "")
    if packet:
        return _packet_command(session_id, packet["name"].lower(), packet["id"], packet["text"].strip())
    parsed = parse(text)
    if parsed is None:
        return None
    name, run_no, ok = parsed
    trace = runtime.trace(session_id)
    trace.event("operator_command", command=name, run_no=run_no, well_formed=ok)
    if not ok:
        return Reply(f"Nothing was done. {USAGE[name]}")
    if name == "stop":
        keys = control.stop(session_id, run_no)
        trace.end(control.END_STOPPED, ", ".join(keys))
        if not keys:
            return Reply(f"Run {run_no} was cancelled already; nothing to stop.")
        what = f"run {run_no}" if run_no is not None else "every run of this session"
        return Reply(f"Stopped {what}: no further tool call will run until you /resume, /cancel or /rollback it.",
                     actions=[f"stopped {k}" for k in keys])
    key = control.run_key(session_id, run_no)
    return {"cancel": control.cancel, "resume": control.resume, "rollback": control.rollback}[name](key)


def _packet_command(session_id: str, name: str, packet_id: str | None, rest: str) -> Reply:
    from agent import escalation

    ok = bool(packet_id) and (name == "packet" or bool(rest))
    runtime.trace(session_id).event("operator_command", command=name, packet_id=packet_id, well_formed=ok)
    if not ok:
        return Reply(f"Nothing was done. {USAGE[name]}")
    if name == "packet":
        return escalation.open_packet(packet_id)
    return escalation.answer(packet_id, rest, session_id=session_id)
