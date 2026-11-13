"""What each model call carries, decided by our code, not the API (Lecture 2, slides 23 to 25;
Lecture 4, Section C). Owner: Vipin Mourya.

    assembled = assemble("loop", system=prompt, session_id=sid, run_key=rk, task=request,
                         history=messages, tool_names=tools, notes=False, memory=None, stranger_texts=[])
    response = client.create(system=assembled.system, messages=assembled.messages, tools=assembled.tools)

Every caller (the loop, the router, the planner, the executor and the reviser) builds its
calls here, so one rule decides what goes in, and the trace says what was left out.

Phase 0 (this version): every candidate goes in, with its source known. Stranger text is
labelled as theirs, and the planner never gets notes, memory or stranger text. Tokens are
counted as characters / 4, tool definitions included, against context_budget. The budget
rule (what is shortened, what goes in only as an ID) and compaction come in phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from harness import runtime

KINDS = ("planner", "executor", "loop", "router", "answer")
NO_OUTSIDE_TEXT = ("planner",)       # never notes, memory or a stranger's words (C3.5)
DEFAULT_BUDGET = 16000


@dataclass
class Assembled:
    system: str
    messages: list[dict]
    tools: list[dict]
    tokens: int                                          # the estimate, definitions included
    left_out: list[dict] = field(default_factory=list)   # each {"what", "why", "id"}


def tokens(value) -> int:
    """Our estimate: characters / 4, of the text as it is sent."""
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return (len(text) + 3) // 4


def budget() -> int:
    """context_budget: the most input tokens one call may carry."""
    value = runtime.switch("context_budget", DEFAULT_BUDGET)
    return int(value)


def label(stranger: dict) -> str:
    """A stranger's message as the model reads it: theirs, never the operator's."""
    sender = stranger.get("sender") or "outside"
    thread = stranger.get("thread_id") or "?"
    return f"[from {sender} on {thread}, not the operator] {stranger.get('text', '')}"


def assemble(kind: str, *, system: str, session_id: str, run_key: str | None, task: str | None,
             history: list[dict], tool_names: list[str] | None, notes: bool = False,
             memory: list | None = None, stranger_texts: list[dict] | None = None,
             registry=None) -> Assembled:
    if kind not in KINDS:
        raise ValueError(f"assemble: kind is one of {KINDS}, not {kind!r}")
    left_out: list[dict] = []
    messages = [dict(m) for m in history]
    extra: list[str] = []

    if kind in NO_OUTSIDE_TEXT:
        if notes:
            left_out.append({"what": "notes", "why": "the planner never sees notes", "id": session_id})
        for record in memory or []:
            left_out.append({"what": "memory", "why": "the planner never sees memory",
                             "id": getattr(record, "id", None)})
        for s in stranger_texts or []:
            left_out.append({"what": "stranger_text", "why": "the planner never sees a stranger's words",
                             "id": s.get("message_id") or s.get("thread_id")})
    else:
        extra += [label(s) for s in stranger_texts or []]
    if extra:
        messages.append({"role": "user", "content": "\n\n".join(extra)})

    if registry is None:
        from agent.registry import get_registry
        registry = get_registry()
    tools = registry.definitions(tool_names) if tool_names is not None else []
    count = tokens(system) + tokens(messages) + tokens(tools)
    out = Assembled(system, messages, tools, count, left_out)
    trace = runtime.trace(session_id)
    trace.event("context", call_kind=kind, run_key=run_key, tokens=count, budget=budget(),
                over_budget=count > budget(), left_out=left_out)
    return out
