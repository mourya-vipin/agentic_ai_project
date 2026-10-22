"""The one path to the agent. The app's pages and run.py both call these, and these call
agent/service.py, so a scripted run takes exactly the path a person at the console takes.

    operator_turn(session_id, text)      the console page, and each operator turn in a task file
    outside_reply(thread_id, text)       the threads page, and each scripted stranger's answer
    pending_approvals()                  the approvals page, and the runner between turns
    decide_approval(id, approved, note)  the approvals page's buttons, and a task's scripted answers

Each one writes the turn into the session's trace (what came in, the reply, how
long it took, or the error) and looks up the agent's function at call time. In a
run.py run, the reply line also carries the turn's wait in channel.wait_for_reply
(`wait_s`) and the time the backend slept out a rate limit or an overload
(`throttled_s`), so the run record can give the agent's own time for each turn
(`turn_latency_s`).
agent/service.py imports Reply from here, so this module imports agent/service.py
only when a turn calls it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import channel
from harness import runtime


@dataclass
class Reply:
    """What the agent hands back for one turn. Every field is optional:
    Reply(waiting_for=thread_id) ends a turn that waits on a thread and says nothing."""
    text: str = ""                                     # what the operator reads
    actions: list[str] = field(default_factory=list)   # one line per action taken this turn
    waiting_for: str | None = None                     # a thread_id, when it waits for an outside reply

    def as_dict(self) -> dict:
        return {"text": self.text, "actions": list(self.actions), "waiting_for": self.waiting_for}


NOT_BUILT = ("The agent is not built yet: agent/service.py's {name} raises NotImplementedError. "
             "Build your agent behind it.")


def as_reply(value) -> Reply:
    """Accept a Reply, a plain string, or a dict with Reply's fields."""
    if isinstance(value, Reply):
        return value
    if value is None:
        return Reply("")
    if isinstance(value, str):
        return Reply(value)
    if isinstance(value, dict):
        return Reply(str(value.get("text", "")), list(value.get("actions") or []), value.get("waiting_for"))
    if all(hasattr(value, a) for a in ("text", "actions", "waiting_for")):
        return Reply(str(value.text), list(value.actions or []), value.waiting_for)
    raise TypeError(f"the agent returned {type(value).__name__}; return a Reply")


def _service():
    from agent import service
    return service


TURNS = ("operator_turn", "on_outside_reply")    # the turns a run record times (turn_latency_s)


def _call(trace, name: str, fn, *args) -> Reply:
    run = runtime.current_run()
    waited, throttled = (run.wait_s, run.throttled_s) if run else (0.0, 0.0)
    start = time.monotonic()
    try:
        reply = as_reply(fn(*args))
    except NotImplementedError as exc:
        trace.write({"type": "error", "kind": "not_built", "function": name,
                     "message": NOT_BUILT.format(name=name), "detail": str(exc)[:300]})
        raise
    except Exception as exc:
        trace.write({"type": "error", "kind": type(exc).__name__, "function": name, "message": str(exc)[:1000]})
        raise
    line = {"type": "reply", "function": name, **reply.as_dict(), "latency_s": round(time.monotonic() - start, 3)}
    if run is not None:
        line["wait_s"] = round(run.wait_s - waited, 3)     # time this turn spent in channel.wait_for_reply
        line["throttled_s"] = round(run.throttled_s - throttled, 3)   # time the backend slept out rate limits
    trace.write(line)
    return reply


def operator_turn(session_id: str, text: str) -> Reply:
    """One message from the operator, to agent.service.operator_turn."""
    trace = runtime.trace(session_id)
    trace.write({"type": "operator", "text": text})
    with runtime.in_session(session_id):
        return _call(trace, "operator_turn", _service().operator_turn, session_id, text)


def outside_reply(thread_id: str, text: str, sender: str = "outside") -> Reply | None:
    """A stranger's message on a thread: stored in the channel, then given once to
    agent.service.on_outside_reply. If a turn is waiting on the thread in
    channel.wait_for_reply, that turn takes the message instead, and this returns
    None: no reply is handled twice. It lands in the trace of the session that last
    wrote on the thread (in run.py, the run's)."""
    message = channel.receive(thread_id, text, sender=sender)
    if not channel.deliver(message):
        return None              # the waiting turn took it, and wrote it into its own trace
    run = runtime.current_run()
    session_id = run.run_id if run else (channel.session_of(thread_id) or f"thread-{thread_id}")
    trace = runtime.trace(session_id)
    trace.write({"type": "outside", "thread_id": thread_id, "sender": sender, "text": text,
                 "message_id": message.id, "handled_by": channel.DELIVERED})
    with runtime.in_session(session_id):
        return _call(trace, "on_outside_reply", _service().on_outside_reply, thread_id, text)


def pending_approvals() -> list[dict]:
    """What waits for the operator's answer, from agent.service.pending_approvals."""
    items = _service().pending_approvals() or []
    return [dict(item) for item in items]


def decide_approval(approval_id: str, approved: bool, note: str = "", session_id: str | None = None) -> Reply | None:
    """The operator's answer, to agent.service.decide_approval."""
    run = runtime.current_run()
    session_id = run.run_id if run else (session_id or f"approval-{approval_id}")
    trace = runtime.trace(session_id)
    trace.write({"type": "approval", "approval_id": approval_id, "approved": bool(approved), "note": note})
    with runtime.in_session(session_id):
        start = time.monotonic()
        try:
            result = _service().decide_approval(approval_id, bool(approved), note)
        except NotImplementedError:
            trace.write({"type": "error", "kind": "not_built", "function": "decide_approval",
                         "message": NOT_BUILT.format(name="decide_approval")})
            raise
        if result is None:
            return None
        reply = as_reply(result)
        trace.write({"type": "reply", "function": "decide_approval", **reply.as_dict(),
                     "latency_s": round(time.monotonic() - start, 3)})
        return reply
