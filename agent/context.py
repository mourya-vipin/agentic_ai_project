"""What each model call carries, decided by our code, not the API (Lecture 2, slides 23 to 25;
Lecture 4, Section C). Owner: Vipin Mourya.

    assembled = assemble("loop", system=prompt, session_id=sid, run_key=rk, task=request,
                         history=messages, tool_names=tools, notes=False, memory=None, stranger_texts=[])
    response = client.create(system=assembled.system, messages=assembled.messages, tools=assembled.tools)

Every caller (the loop, the router, the planner, the executor and the reviser) builds its
calls here, so one rule decides what goes in, and the trace says what was left out.

Tokens are counted as characters / 4, tool definitions included, against context_budget. The
rule for when the candidates do not fit is assemble()'s docstring; compaction runs past
compaction_threshold. Notes (agent/notes.py) go in loop, executor and answer calls, never the
planner's.
"""

from __future__ import annotations

import json
import re
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
NO_OUTSIDE_TEXT = ("planner",)       # never notes, memory or a stranger's words
NOTES_KINDS = ("loop", "executor", "answer")   # the calls that do the work or answer the operator
DEFAULT_BUDGET = 8000
DEFAULT_THRESHOLD = 4000
KEEP_STEPS = 2                       # the latest steps, kept word for word through a compaction
MAX_IDS = 12
IDS = re.compile(r"\bSYN-[A-Z]+-[A-Z0-9-]+\b")
BUDGET_UNFIT = "budget_unfit"        # the end reason when a call cannot fit even after compaction


@dataclass
class Assembled:
    system: str
    messages: list[dict]
    tools: list[dict]
    tokens: int                                          # the estimate, definitions included
    left_out: list[dict] = field(default_factory=list)   # each {"what", "why", "id"}
    compacted: bool = False                              # keep these messages: the compaction holds
    unfit: bool = False                                  # still over the budget: end the turn BUDGET_UNFIT


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


def threshold() -> int:
    """compaction_threshold: past this many tokens of conversation, compaction runs."""
    return int(runtime.switch("compaction_threshold", DEFAULT_THRESHOLD))


def _blocks(message: dict) -> list:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _tool_names(messages: list[dict]) -> dict[str, str]:
    """tool_use id -> tool name, so a cleared result can say what it was."""
    return {b["id"]: b.get("name", "tool") for m in messages for b in _blocks(m) if b.get("type") == "tool_use"}


def _ids_in(text: str) -> list[str]:
    return list(dict.fromkeys(IDS.findall(str(text or ""))))[:MAX_IDS]


def _cleared(block: dict, name: str) -> dict:
    """A tool result shortened to the IDs it held, which the agent can fetch again."""
    ids = _ids_in(block.get("content"))
    text = (f"[cleared to save room: the result of {name}"
            + (f", which named {', '.join(ids)}" if ids else "") + "; call the tool again if you need it]")
    return {**block, "content": text}


def _with_results(message: dict, change) -> dict:
    blocks = _blocks(message)
    if not any(b.get("type") == "tool_result" for b in blocks):
        return message
    return {**message, "content": [change(b) if b.get("type") == "tool_result" else b for b in blocks]}


def _recent_start(messages: list[dict]) -> int:
    """Where the recent steps, kept word for word, begin: an assistant turn, so pairs stay whole."""
    starts = [i for i, m in enumerate(messages) if i > 0 and m.get("role") == "assistant"]
    return starts[-KEEP_STEPS] if len(starts) >= KEEP_STEPS else (starts[0] if starts else len(messages))


def _transcript(messages: list[dict]) -> str:
    names = _tool_names(messages)
    out = []
    for m in messages:
        if isinstance(m.get("content"), str):
            out.append(f"{m['role']}: {m['content']}")
            continue
        for b in _blocks(m):
            if b.get("type") == "text":
                out.append(f"{m['role']}: {b.get('text', '')}")
            elif b.get("type") == "tool_use":
                out.append(f"assistant called {b.get('name')}({json.dumps(b.get('input') or {})})")
            elif b.get("type") == "tool_result":
                out.append(f"result of {names.get(b.get('tool_use_id'), 'tool')}: {str(b.get('content'))[:600]}")
    return "\n".join(out)


def _summarize_with_model(session_id: str, text: str) -> str:
    response = runtime.client(session_id).create(
        system=("Summarize these earlier steps of an IT access desk agent's work for the agent itself: what it "
                "looked up, what it changed (with every request, person and group ID), and what is still open. "
                "Keep every ID. Write facts only, no advice."),
        messages=[{"role": "user", "content": text[: budget() * 3]}], tools=[])
    return (response.text or "").strip()


def compact(messages: list[dict], *, session_id: str, run_key: str | None, summarize=None) -> tuple[list[dict], list[str]]:
    """Lecture 2, slide 31: first clear old tool results (no model call); if the conversation is still
    past the threshold, summarize the older turns with a model call. The task (the first message) is
    never summarized, and the last KEEP_STEPS steps stay word for word. Returns (messages, stages run)."""
    if len(messages) < 2:
        return messages, []
    trace, stages = runtime.trace(session_id), []
    before = tokens(messages)
    cut = _recent_start(messages)
    names = _tool_names(messages)
    cleared = [messages[0]] + [_with_results(m, lambda b: _cleared(b, names.get(b.get("tool_use_id"), "tool")))
                               for m in messages[1:cut]] + messages[cut:]
    if cleared != messages:
        stages.append("cleared")
        messages = cleared
    if tokens(messages) > threshold() and cut > 1:
        older = messages[1:cut]
        summary = (summarize or (lambda text: _summarize_with_model(session_id, text)))(_transcript(older))
        task = messages[0]
        head = task["content"] if isinstance(task["content"], str) else _transcript([task])
        merged = {"role": "user", "content": f"{head}\n\n[Summary of the earlier steps, written by a model call to save "
                                             f"room; the request above is as it was given]\n{summary}"}
        messages = [merged] + messages[cut:]
        stages.append("summarized")
    if stages:
        trace.event("compaction", run_key=run_key, stages=stages, tokens_before=before, tokens_after=tokens(messages),
                    threshold=threshold(), kept_steps=KEEP_STEPS)
    return messages, stages


def _append_text(messages: list[dict], text: str) -> list[dict]:
    """Put text at the end of the conversation: in the last user turn, or a new one after an assistant
    turn. The end, so the start of every call stays the same and the cached prefix still hits."""
    if not messages or messages[-1].get("role") != "user":
        return messages + [{"role": "user", "content": text}]
    last = messages[-1]
    if isinstance(last["content"], str):
        return messages[:-1] + [{**last, "content": f"{last['content']}\n\n{text}"}]
    return messages[:-1] + [{**last, "content": list(last["content"]) + [{"type": "text", "text": text}]}]


def _memory_text(memory: list | None) -> str:
    lines = []
    for r in memory or []:
        get = r.get if isinstance(r, dict) else (lambda k, r=r: getattr(r, k, None))
        lines.append(f"- [{get('kind')}, from {get('source')}, written {get('written_at')}] {get('text')}")
    return ("[Memory from earlier sessions, each with its source and age]\n" + "\n".join(lines)) if lines else ""


def assemble(kind: str, *, system: str, session_id: str, run_key: str | None, task: str | None,
             history: list[dict], tool_names: list[str] | None, notes: bool | None = None,
             memory: list | None = None, stranger_texts: list[dict] | None = None,
             registry=None, use_case: str | None = None, summarize=None) -> Assembled:
    """One call's input, within context_budget. The rule, in order (Part 1, task 6):

        1. always in, whole: the system prompt, the tool definitions, the task (the first message, as
           the operator or the request gave it) and the current step (the latest turn and its results)
        2. then the latest tool results, word for word (the last KEEP_STEPS steps)
        3. then the notes and memory, at the end of the call
        4. older turns are shortened: compaction past compaction_threshold clears their tool results
           to the IDs they held (large record sets go in only as IDs the agent can fetch again), then
           summarizes them with a model call
        5. still over the budget: memory is left out, then the notes; and if the call still cannot fit,
           unfit is set and the caller ends the turn budget_unfit

    Every piece's source is known: a stranger's text is labelled as theirs, the notes and the summary
    say who wrote them, and the trace's context line lists what was left out. The planner never gets
    notes, memory or a stranger's words. Callers keep the returned messages as their conversation,
    so a compaction holds."""
    if kind not in KINDS:
        raise ValueError(f"assemble: kind is one of {KINDS}, not {kind!r}")
    from agent import notes as notes_module

    left_out: list[dict] = []
    messages = [dict(m) for m in history]
    if task and (not messages or messages[0].get("role") != "user"):
        messages = [{"role": "user", "content": task}] + messages
    if registry is None:
        from agent.registry import get_registry
        registry = get_registry()
    names, deferred = offered_tools(registry, kind, use_case, session_id, run_key, tool_names)
    left_out += [{"what": "definition", "why": "deferred: tools_search finds it", "id": n} for n in deferred]
    tools = registry.definitions(names) if names is not None else []

    # what comes from outside the conversation, and who wrote it
    use_notes = notes_module.enabled() if notes is None else notes
    outside: list[tuple[str, str, str]] = []          # (what, text, id)
    if kind in NO_OUTSIDE_TEXT:
        if use_notes:
            left_out.append({"what": "notes", "why": "the planner never sees notes", "id": session_id})
        for record in memory or []:
            left_out.append({"what": "memory", "why": "the planner never sees memory",
                             "id": record.get("id") if isinstance(record, dict) else getattr(record, "id", None)})
        for s in stranger_texts or []:
            left_out.append({"what": "stranger_text", "why": "the planner never sees a stranger's words",
                             "id": s.get("message_id") or s.get("thread_id")})
    else:
        outside += [("stranger_text", label(s), s.get("message_id") or s.get("thread_id")) for s in stranger_texts or []]
        if use_notes and kind in NOTES_KINDS:
            text = notes_module.render(session_id)
            if text:
                outside.append(("notes", text, session_id))
        if memory:
            outside.append(("memory", _memory_text(memory), "memory"))

    # 4. compaction, past the threshold
    compacted = False
    if tokens(messages) > threshold():
        messages, stages = compact(messages, session_id=session_id, run_key=run_key, summarize=summarize)
        compacted = bool(stages)

    # 5. still over the budget: leave out memory, then the notes (a stranger's reply is the current step)
    fixed = tokens(system) + tokens(tools)
    for what in ("memory", "notes"):
        if fixed + tokens(messages) + sum(tokens(t) for _, t, _ in outside) <= budget():
            break
        for item in [o for o in outside if o[0] == what]:
            outside.remove(item)
            left_out.append({"what": what, "why": "over context_budget", "id": item[2]})
    for _, text, _ in outside:
        messages = _append_text(messages, text)
    count = fixed + tokens(messages)
    unfit = count > budget()
    out = Assembled(system, messages, tools, count, left_out, compacted=compacted, unfit=unfit)
    runtime.trace(session_id).event("context", call_kind=kind, run_key=run_key, tokens=count, budget=budget(),
                                    over_budget=unfit, compacted=compacted, left_out=left_out)
    return out
