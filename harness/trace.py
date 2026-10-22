"""The session-scoped trace: one JSON object per line, one file per session.

Get the writer for a session from the harness:

    from harness import runtime
    trace = runtime.trace(session_id)

A console session's trace is traces/<session_id>.jsonl. A run.py run is one
session, and its trace sits beside its record: results/<arm>/<run_id>.trace.jsonl.
The harness writes the lines that frame each turn: the operator's message
(`operator`), a reply from the outside channel (`outside`), the agent's reply
(`reply`), and an `error` when the agent raised. Your code writes everything in
between:

    trace.model_call(step, response, purpose="draft")     # after every model call; any purpose, or none
    trace.tool_call(step, call, result, plan_step=None)   # after every tool call, recorded in full
    trace.plan(version, plan)                             # your plan, as data, in whatever shape it has
    trace.event("critique", verdict="revise", round=2)    # any other kind of line, with any fields
    trace.progress("Asked the requester for the date.")   # a line the console shows the operator
    trace.end("plan_rejected", detail="")                 # once per loop: why it stopped, in your own name

The names are yours: the purposes, the event kinds and their fields, the end
reasons. An end reason is a short name (letters, digits and _, a letter first, at
most 40 characters); its line carries it as `terminal_reason`, as in Assignments 1
and 2.
Every line carries the session, the run (in a run.py run) and the time. A run's
record copies every tool_call line in full, every reply, and every end reason in
order (`end_reasons`).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness.formats import END_REASON_MAX, RESERVED_TYPES

_END_REASON = re.compile(rf"^[A-Za-z][A-Za-z0-9_]{{0,{END_REASON_MAX - 1}}}$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def now() -> str:
    """The time now, in UTC, as the records and traces write it."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


class SessionTrace:
    def __init__(self, path: Path, session_id: str, run_id: str | None = None):
        self.path = Path(path)
        self.session_id = session_id
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- for your code ----------------------------------------------------------------
    def model_call(self, step: int, response, purpose: str | None = None, **fields) -> None:
        """Record one model call. `response` is what ModelClient.create returned;
        `purpose` is your own word for what the call was for, or None."""
        if purpose is not None and not isinstance(purpose, str):
            raise ValueError(f"purpose is text, such as 'draft', or None; not {purpose!r}")
        self.write({
            "type": "model_call", "step": step, "purpose": purpose,
            "model": response.model, "stop_reason": response.stop_reason,
            "latency_s": round(response.latency_s, 3), "usage": response.usage.as_dict(),
            "cost_usd": response.cost_usd,
            "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in response.tool_calls],
            "text": response.text[:2000], **fields,
        })

    def tool_call(self, step: int, call, result, plan_step=None, **fields) -> None:
        """Record one tool call in full: its id, name, input and output. `call` is a
        ToolCall (.id, .name, .input) or a dict with those keys (an id is made up if it
        has none); `result` has .content, .is_error and .latency_s, or is the output itself."""
        get = call.get if isinstance(call, dict) else (lambda key: getattr(call, key, None))
        output = result if isinstance(result, str) else getattr(result, "content", result)
        self.write({
            "type": "tool_call", "step": step, "plan_step": plan_step,
            "id": get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": get("name"), "input": get("input") or {},
            "output": output, "is_error": bool(getattr(result, "is_error", False)),
            "latency_s": round(float(getattr(result, "latency_s", 0.0) or 0.0), 3), **fields,
        })

    def plan(self, version, plan, **fields) -> None:
        """Record a version of your plan, as data. Its shape is yours."""
        _no_clash(fields, {"version", "plan"})
        self.write({"type": "plan", "version": version, "plan": plan, **fields})

    def event(self, kind: str, **fields) -> None:
        """Record a line whose type is `kind`, your own name, with any fields."""
        if not isinstance(kind, str) or not kind.isidentifier():
            raise ValueError(f"event kind must be a plain name such as 'off_plan', not {kind!r}")
        if kind in RESERVED_TYPES:
            raise ValueError(f"{kind!r} is a line the harness writes; use another kind")
        _no_clash(fields)
        self.write({"type": kind, **fields})

    def progress(self, text: str) -> None:
        """A line of progress the console shows the operator while the turn runs."""
        self.write({"type": "progress", "text": str(text)})

    def end(self, reason: str, detail: str = "") -> None:
        """Record why your loop stopped, in a short name your team chooses (plan_rejected,
        turn_limit, waiting_for_reply). Call it from your own code, never from the model's words."""
        if not isinstance(reason, str) or not _END_REASON.match(reason):
            raise ValueError(f"an end reason is a short name such as 'plan_rejected': letters, digits and _, "
                             f"starting with a letter, at most {END_REASON_MAX} characters; not {reason!r}")
        self.write({"type": "end", "terminal_reason": reason, "detail": detail})

    # -- for the harness ----------------------------------------------------------------
    def write(self, record: dict) -> None:
        line = {"at": now(), "session_id": self.session_id, **({"run_id": self.run_id} if self.run_id else {}),
                **record}
        text = json.dumps(line, default=str) + "\n"
        with _lock_for(self.path):
            with self.path.open("a") as fh:
                fh.write(text)

    def lines(self) -> list[dict]:
        return read_trace(self.path) if self.path.exists() else []


def _no_clash(fields: dict, more: set = frozenset()) -> None:
    clash = ({"type", "at", "session_id", "run_id"} | set(more)) & set(fields)
    if clash:
        raise ValueError(f"fields may not be named {sorted(clash)}: the line already has them")


def read_trace(path: Path) -> list[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"type": "unreadable", "text": line[:300]})
    return out
