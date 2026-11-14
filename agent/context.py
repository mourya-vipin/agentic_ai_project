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
DEFERRED_ABOVE = 10      # below about ten tools, every definition in every request (Lecture 3, slide 24)
SEARCH_TOOL = "tools_search"
PROGRAM_TOOL = "code_run"
# The tools every case of a use case needs, sent whole even when loading is deferred: those called in
# nearly every run of it in Milestone 1's baseline and Pass 1 arms (U4: 14 to 18 of 18 runs; U6 on
# the loop: 2 or 3 of 3). The rest are found with tools_search.
CORE = {
    "nonstandard_request": ("access_grant", "policy_check_access", "reply_draft", "requests_set_status"),
    "bulk_change": ("access_propose_change", "access_revoke_all", "directory_list_contractors_ended", "reply_draft"),
}
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


def loading() -> str:
    """tools.loading: all | deferred."""
    return str(runtime.switch("tools.loading", "all"))


def offered_tools(registry, kind: str, use_case: str | None, session_id: str, run_key: str | None,
                  tool_names: list[str] | None) -> tuple[list[str] | None, list[str]]:
    """(the definitions this call carries, those left for tools_search). With tools.loading=deferred and
    more than DEFERRED_ABOVE tools offered, a call carries its use case's core tools, the search tool
    and what its searches found; a search finds only tools in tool_names."""
    if tool_names is None:
        return None, []
    registry.offer(run_key or session_id, list(tool_names))
    if use_case and str(runtime.switch(f"code_calls.{use_case}", "off")) == "on" and kind in ("loop", "answer"):
        tool_names = list(tool_names) + [PROGRAM_TOOL]       # the program calls the tools offered above
    if loading() != "deferred" or kind not in ("loop", "executor") or len(tool_names) <= DEFERRED_ABOVE:
        return list(tool_names), []
    found = registry.found(run_key or session_id)
    keep = [n for n in tool_names if n in CORE.get(use_case or "", ()) or n in found]
    return keep + [SEARCH_TOOL], [n for n in tool_names if n not in keep]


def assemble(kind: str, *, system: str, session_id: str, run_key: str | None, task: str | None,
             history: list[dict], tool_names: list[str] | None, notes: bool = False,
             memory: list | None = None, stranger_texts: list[dict] | None = None,
             registry=None, use_case: str | None = None) -> Assembled:
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
    names, deferred = offered_tools(registry, kind, use_case, session_id, run_key, tool_names)
    left_out += [{"what": "definition", "why": "deferred: tools_search finds it", "id": n} for n in deferred]
    tools = registry.definitions(names) if names is not None else []
    count = tokens(system) + tokens(messages) + tokens(tools)
    out = Assembled(system, messages, tools, count, left_out)
    trace = runtime.trace(session_id)
    trace.event("context", call_kind=kind, run_key=run_key, tokens=count, budget=budget(),
                over_budget=count > budget(), left_out=left_out)
    return out
