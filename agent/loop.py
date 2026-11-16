"""The bounded agent loop: the model picks each step and when to stop; our bound
ends the run from outside (Lecture 1, slide 20).

Owner: Aman Panigrahi. Used by U4 (nonstandard_request), U5 (operator_review),
and U6 (bulk_change) when plan.bulk_change is off.

Reused from Aman's Assignment 1 (amanpanigrahi2305/agentic-ai-assignment1,
agent/loop.py): the bound checked in code before every model call, each assistant
turn appended unchanged, and all of a step's tool results sent back in one user
turn. Changed to fit: every tool call goes through our registry (which validates,
keys and traces it), the bound and end reasons are ours (agent/bounds.py), the token
budget counts this loop's own tokens, and the repeat check, the protocol-error stop
and the progress lines are new.

Before every model call, in this order: the step limit, the token budget (tokens
since this loop started, from the metered client), the wall clock. The first one
reached ends the loop with its own reason; the model's retries after an error count
as steps like any other. The loop ends early when a tool result carries an end
reason (critique_held), or on the second protocol error in the loop.

Milestone 2. The caller opens the run (agent/checkpoints.py) and gives its key. Before
each model call, the loop also does these two steps:
  - It reads the stop flag (agent/control.py). A stopped run ends with "stopped" and
    makes no more model calls. When the hook refuses a tool call because the run is
    stopped, the loop ends with "stopped" at once, and the step makes no more calls.
  - It builds the call with context.assemble. A call that cannot fit ends the
    loop with budget_unfit. After a compaction, the loop keeps the compacted messages,
    so that the compaction holds. It does not keep the notes or the memory, because
    assemble adds them again to each call.
Each tool call carries the run key and origin "agent". The text of a send passes
disclosure.stamp_call before the registry.

Before the calls of a step run, the loop saves them with their arguments (a write-ahead
record), and after the step it saves the run again (checkpoints.save_step): the messages,
and a record of each call with its arguments, its key and its end reason. A write that
fails with a transient or protocol error takes the three exits (agent/exits.py). carry_on() continues
a saved run from there, after an approval, a reply, /resume or the operator's words
(router.resume calls it). A new run and a resumed run use the same loop (_drive).

A run that asks a stranger (channel_ask_template) carries on with its turn. When the model
ends the turn, the run is saved as waiting for the reply, with the thread and a deadline
(bounds.reply_deadline), and the LoopResult names the thread (waiting_for).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent import bounds, checkpoints, context, control, disclosure, exits
from agent.bounds import Bound
from agent.detectors import RepeatTracker
from agent.registry import AWAITING_APPROVAL, CallContext, get_registry
from harness import runtime
from harness.client import tool_result

PROGRESS_EVERY = 3        # steps between the progress lines the console shows (Lecture 1, slide 34)
MAX_PROTOCOL_ERRORS = 2   # the second protocol error in one loop ends it
NOT_RUN = "Not run: the operator stopped this run before this call."
STOP_LIFTED = ("[from our code] The operator lifted the stop on this run. A call that the stop "
               "refused did not run: make it again if it is still needed.")
RESTARTED = ("[from our code] The process stopped during this run (a crash, or a restart of the app). The run "
             "carries on from its last saved step. Each write that it made is done once: do not make it again.")


@dataclass
class LoopResult:
    text: str                                         # what the operator reads
    actions: list[str] = field(default_factory=list)  # one line per effect
    end_reason: str = ""                              # one of the names in agent/bounds.py
    steps: int = 0
    waiting_for: str | None = None                    # the thread of a stranger the run waits for


def run_loop(session_id: str, *, use_case: str, system: str, user_content: str,
             tools: list[str], bound: Bound, request_id: str | None = None,
             run_key: str | None = None, memory: list | None = None) -> LoopResult:
    """A new run of the loop, from its task. `memory` is what memory.for_call chose for the run:
    each call of the run carries it, at the end of the call (context.assemble)."""
    return _drive(session_id, use_case=use_case, system=system, tools=tools, bound=bound, request_id=request_id,
                  run_key=run_key, messages=[{"role": "user", "content": user_content}], step=0, actions=[],
                  memory=memory)


def carry_on(cp, event: dict, *, system: str, tools: list[str], bound: Bound,
             memory: list | None = None) -> LoopResult:
    """Continue a saved run of the loop after one event (router.resume). The run goes on
    from its saved messages, or from its task when no step was saved (the exit mode, or a
    crash before the first step). What the event adds:

        approval     each call that waited for the operator's approval is made again with
                     the same arguments. The gate lets that exact call through once, and its
                     result takes the place of the "waiting" result. A denial does not come here.
        reply        the stranger's reply, labelled as theirs (context.label)
        stop_lifted  a line from our code: the stop is lifted, and a call that the stop
                     refused did not run. Then the operator's new words, if any
        operator     the operator's words (the answer to an escalation)
        restart      the process stopped during the run. The step that was running when it
                     stopped is finished first: each write takes the three exits
                     (agent/exits.py), and each read is made again. Then a line from our code

    The bound is new: a resumed turn is a turn of its own."""
    findings = cp.findings or {}
    messages = [dict(m) for m in (cp.messages or [{"role": "user", "content": cp.task}])]
    step, actions = cp.current_step or 0, []
    if event.get("kind") == "approval":
        ended = _make_approved_calls(cp, messages, step, actions)
        if ended is not None:
            runtime.trace(cp.session_id).end(ended, f"step {step}: an approved call ended the turn again")
            return LoopResult(f"Stopped: {ended.replace('_', ' ')}.", actions, ended, step)
    elif event.get("kind") == "restart":
        messages, ended, held = _finish_the_step(cp, messages, actions)
        if ended is not None:
            runtime.trace(cp.session_id).end(ended, f"step {step}: the step that the stop of the process cut")
            return LoopResult(held or f"Stopped: {ended.replace('_', ' ')}.", actions, ended, step)
        messages = _with_text(messages, RESTARTED)
    else:
        messages = _with_text(messages, _event_text(event))
    return _drive(cp.session_id, use_case=cp.use_case, system=system, tools=tools, bound=bound,
                  request_id=findings.get("request_id"), run_key=cp.run_key, messages=messages, step=step,
                  actions=actions, cp=cp, memory=memory)


def _drive(session_id: str, *, use_case: str, system: str, tools: list[str], bound: Bound,
           request_id: str | None, run_key: str | None, messages: list[dict], step: int, actions: list[str],
           cp=None, memory: list | None = None) -> LoopResult:
    """The loop itself, for a new run and for a resumed one. After each step it saves the
    run (checkpoints.save_step): the record of each call and the messages."""
    client = runtime.client(session_id)
    trace = runtime.trace(session_id)
    registry = get_registry()
    repeats = RepeatTracker()
    done = bounds.ANSWERED if use_case == "operator_review" else bounds.DONE
    trace.event("bound", use_case=use_case, request_id=request_id, max_steps=bound.max_steps,
                token_budget=bound.token_budget, wall_clock_s=bound.wall_clock_s, from_step=step)
    if cp is None and run_key is not None:
        cp = checkpoints.load(run_key)          # the router saved the run when it opened it
    task = messages[0]["content"] if isinstance(messages[0].get("content"), str) else None
    first, start, start_tokens = step, time.monotonic(), client.total_tokens
    text, calls_made, protocol_errors = "", 0, 0
    asked = None                                    # the thread of a question that this turn sent

    def end(reason: str, detail: str, said: str) -> LoopResult:
        trace.end(reason, detail)
        return LoopResult(said, actions, reason, step)

    while True:
        used, elapsed = client.total_tokens - start_tokens, time.monotonic() - start
        if step - first >= bound.max_steps:
            return end(bounds.STEP_LIMIT, f"{bound.max_steps} model calls", _stopped("the step limit", text))
        if used >= bound.token_budget:
            return end(bounds.LOOP_TOKEN_BUDGET, f"{used:,} >= {bound.token_budget:,} tokens before step {step + 1}",
                       _stopped("the token budget", text))
        if elapsed >= bound.wall_clock_s:
            return end(bounds.WALL_CLOCK, f"{elapsed:.1f}s >= {bound.wall_clock_s:g}s before step {step + 1}",
                       _stopped("the time limit", text))
        if control.stopped(run_key):
            return end(control.END_STOPPED, f"{run_key} stopped by the operator before step {step + 1}",
                       f"Stopped by the operator before step {step + 1}." + (f" Last said: {text}" if text else ""))
        built = context.assemble("loop", system=system, session_id=session_id, run_key=run_key, task=task,
                                 history=messages, tool_names=tools, registry=registry, use_case=use_case,
                                 memory=memory)
        if built.unfit:
            return end(context.BUDGET_UNFIT, f"{built.tokens:,} > {context.budget():,} tokens before step {step + 1}",
                       _stopped("the context budget", text))
        messages = _kept(built, messages)

        step += 1
        response = client.create(system=built.system, messages=built.messages, tools=built.tools)
        trace.model_call(step, response, purpose=use_case, request_id=request_id)
        text = response.text or text
        calls = response.tool_calls
        if not calls and asked is not None:
            # the model asked a stranger and finished its turn: the run waits for the reply
            messages.append(response.assistant_message())
            wait = {"kind": "reply", "thread_id": asked, "deadline": bounds.reply_deadline()}
            if cp is not None:
                cp.messages = [dict(m) for m in messages]
                cp.status, cp.waiting_for = checkpoints.WAITING_REPLY, wait
                checkpoints.save(cp, mode="sync")
            trace.end(bounds.WAITING_REPLY, f"asked on {asked}; the deadline is {wait['deadline']}")
            return LoopResult(response.text, actions, bounds.WAITING_REPLY, step, waiting_for=asked)
        if not calls:
            return end(done, f"step {step}, stop_reason {response.stop_reason}", response.text)

        messages.append(response.assistant_message())
        results, ended, held = [], None, None
        record = {"n": step, "calls": []}
        for call in calls:
            name = registry.resolve(call.name) or call.name
            record["calls"].append({"id": call.id, "tool": name, "args": disclosure.stamp_call(name, call.input),
                                    "status": "pending"})
        if cp is not None:
            # before the calls run: the model's step and its arguments, so that a restart makes
            # each write with these arguments, never with new ones from the model
            checkpoints.save_step(cp, record, messages=messages)
        for call, saved in zip(calls, record["calls"]):
            name, args = saved["tool"], saved["args"]
            if ended == control.END_STOPPED:
                # the stop refused an earlier call of this step: the rest do not run, and each
                # still gets a result, so that the saved conversation stays whole for a resume
                results.append(tool_result(call, NOT_RUN, True))
                saved.update(status="not_run", key=None, is_error=True, end_reason="not_run")
                continue
            earlier = repeats.see(call.name, call.input, step)
            if earlier is not None:
                trace.event("repeat_call", step=step, tool=call.name, first_step=earlier["step"], hash=earlier["hash"])
            ctx = CallContext(session_id, step, "model", use_case, request_id=request_id, call_id=call.id,
                              run_key=run_key, origin="agent")
            result = registry.call(call.name, args, ctx)
            if result.is_error and result.error_class in exits.IN_DOUBT and exits.is_write(name):
                settled = exits.settle(name, args, ctx, first=result, registry=registry)    # the three exits
                result = settled.result
                if settled.outcome == exits.UNKNOWN and settled.packet_id:
                    held = _held(settled.packet_id)
            saved.update(status="done", key=result.key, is_error=result.is_error, end_reason=result.end_reason)
            calls_made += 1
            results.append(tool_result(call, result.content, result.is_error))
            if result.error_class == "protocol":
                protocol_errors += 1
                if protocol_errors >= MAX_PROTOCOL_ERRORS and ended is None:
                    ended = bounds.PROTOCOL_ERROR
            elif not result.is_error and result.key is not None:
                actions.append(action_line(name, call.input, result.data))
                if name == "channel_ask_template" and isinstance(result.data, dict):
                    asked = result.data.get("waiting_for") or result.data.get("thread_id") or asked
            if result.end_reason and ended is None:
                ended = result.end_reason
            if result.is_error and control.stopped(run_key):
                ended = control.END_STOPPED          # the hook refused the call: the operator stopped this run
        messages.append({"role": "user", "content": results})
        if cp is not None:
            checkpoints.save_step(cp, record, messages=messages)
        if ended == control.END_STOPPED:
            return end(ended, f"step {step}: the hook refused a call of the stopped run",
                       f"Stopped by the operator at step {step}." + (f" Last said: {text}" if text else ""))
        if held is not None:
            return end(ended or exits._escalated(), f"step {step}: a write in doubt, held for the operator", held)
        if ended is not None:
            return end(ended, f"step {step}", f"Stopped: {ended.replace('_', ' ')}. {text}".strip())
        if step % PROGRESS_EVERY == 0:
            trace.progress(f"{use_case}{' ' + request_id if request_id else ''}: step {step}, "
                           f"{calls_made} tool calls so far.")


def _make_approved_calls(cp, messages: list[dict], step: int, actions: list[str]) -> str | None:
    """Make again each call of the last saved step that waited for an approval, with its
    saved arguments, and put its result in place of the "waiting" result. Returns the end
    reason of a call that ends the turn again (a second approval, for example), or None."""
    registry, ended = get_registry(), None
    last = (cp.steps or [{}])[-1]
    for saved in [c for c in last.get("calls", []) if c.get("end_reason") == AWAITING_APPROVAL]:
        ctx = CallContext(cp.session_id, step, "model", cp.use_case, request_id=(cp.findings or {}).get("request_id"),
                          call_id=saved["id"], run_key=cp.run_key, origin="agent")
        result = registry.call(saved["tool"], saved["args"], ctx)
        if not result.is_error and result.key is not None:
            actions.append(action_line(saved["tool"], saved["args"], result.data))
        _put_result(messages, saved["id"], result)
        ended = ended or result.end_reason
    return ended


def _finish_the_step(cp, messages: list[dict], actions: list[str]) -> tuple[list[dict], str | None, str | None]:
    """After a restart: finish the step that was running when the process stopped. Its record
    holds the model's calls and their arguments, saved before they ran. A write takes the three
    exits: the journal's result if the write happened, or the same call again (the same key) if
    not, or a hold for the operator if no one can tell. A read is made again. Returns the
    messages with the step's results, the end reason of a call that ends the turn, and the
    operator's text for a hold."""
    last = (cp.steps or [None])[-1]
    pending = bool(last) and any(c.get("status") == "pending" for c in last.get("calls", []))
    if not pending or not messages or messages[-1].get("role") != "assistant":
        return messages, None, None                 # the step's results were saved: nothing is cut
    registry, results, ended, held = get_registry(), [], None, None
    runtime.trace(cp.session_id).event("step_finished_after_restart", run_key=cp.run_key, step=last.get("n"),
                                       calls=[c["tool"] for c in last["calls"]])
    for saved in last["calls"]:
        ctx = CallContext(cp.session_id, last.get("n") or 0, "model", cp.use_case,
                          request_id=(cp.findings or {}).get("request_id"), call_id=saved["id"],
                          run_key=cp.run_key, origin="agent")
        if exits.is_write(saved["tool"]):
            settled = exits.settle(saved["tool"], saved["args"], ctx, registry=registry)
            result = settled.result
            if settled.outcome == exits.UNKNOWN and settled.packet_id:
                held = _held(settled.packet_id)
        else:
            result = registry.call(saved["tool"], saved["args"], ctx)
        if not result.is_error and result.key is not None:
            actions.append(action_line(saved["tool"], saved["args"], result.data))
        block = {"type": "tool_result", "tool_use_id": saved["id"], "content": result.content}
        if result.is_error:
            block["is_error"] = True
        results.append(block)
        saved.update(status="done", key=result.key, is_error=result.is_error, end_reason=result.end_reason)
        ended = ended or result.end_reason
    messages = messages + [{"role": "user", "content": results}]
    checkpoints.save_step(cp, last, messages=messages)
    return messages, ended, held


def _held(packet_id: str) -> str:
    """What the operator reads when a write in doubt holds the run (the third exit)."""
    from agent import escalation
    return escalation.hold_reply(packet_id).text


def _put_result(messages: list[dict], call_id: str, result) -> None:
    """Put a new result in place of the old result of one call, in the last user turn that has it."""
    block = {"type": "tool_result", "tool_use_id": call_id, "content": result.content}
    if result.is_error:
        block["is_error"] = True
    for m in reversed(messages):
        content = m.get("content")
        if m.get("role") == "user" and isinstance(content, list) and any(
                b.get("type") == "tool_result" and b.get("tool_use_id") == call_id for b in content):
            m["content"] = [block if b.get("type") == "tool_result" and b.get("tool_use_id") == call_id else b
                            for b in content]
            return


def _event_text(event: dict) -> str:
    """What the event adds to the conversation, with the name of who wrote it."""
    kind = event.get("kind")
    if kind == "reply":
        return context.label({"sender": event.get("sender") or "outside", "thread_id": event.get("thread_id"),
                              "text": event.get("text", "")})
    if kind == "stop_lifted":
        new = str(event.get("new_input") or "").strip()
        return STOP_LIFTED + (f"\n[from the operator] {new}" if new else "")
    return f"[from the operator] {event.get('text', '')}"


def _with_text(messages: list[dict], text: str) -> list[dict]:
    """The conversation with the text at its end: in the last user turn, or in a new one."""
    last = messages[-1] if messages else None
    if last is None or last.get("role") != "user":
        return messages + [{"role": "user", "content": text}]
    content = last["content"]
    joined = f"{content}\n\n{text}" if isinstance(content, str) else list(content) + [{"type": "text", "text": text}]
    return messages[:-1] + [{**last, "content": joined}]


def _stopped(limit: str, text: str) -> str:
    return f"Stopped at {limit} before finishing." + (f" Last said: {text}" if text else "")


def _kept(built: context.Assembled, messages: list[dict]) -> list[dict]:
    """The conversation for the next step. After a compaction, it is the compacted
    conversation, so that the compaction holds. assemble puts the notes and the memory in
    the last message, which is always a user turn here. The loop keeps that message as it
    was, without them."""
    if not built.compacted:
        return messages
    return built.messages[:-1] + messages[-1:]


def action_line(tool: str, args: dict, data) -> str:
    """One line per effect, for the operator, such as
    "granted SYN-GRP-JIRA-WRITE to SYN-EMP-0031"."""
    data = data if isinstance(data, dict) else {}
    person = args.get("employee_id") or args.get("recipient_employee_id") or data.get("employee_id")
    group = args.get("group_id") or data.get("group_id")
    if tool == "access_grant":
        return f"{data.get('status', 'granted')} {group} to {person}"
    if tool == "access_revoke":
        return f"{data.get('status', 'removed')} {group} from {person}"
    if tool == "access_revoke_all":
        kept = data.get("kept_protected") or []
        return f"removed {', '.join(data.get('removed') or []) or 'nothing'} from {person}" + \
            (f" (kept {', '.join(kept)})" if kept else "")
    if tool == "access_propose_change":
        return f"proposed {args.get('action')} {group} for {person}: {data.get('pending_id')}, waiting for the operator"
    if tool == "reply_draft":
        return f"drafted a reply to {person}: {data.get('pending_id')}, for the operator to send"
    if tool == "manager_confirmation_draft":
        return (f"drafted a confirmation request to {data.get('manager_id')} for {group} for {person}: "
                f"{data.get('pending_id')}")
    if tool == "requests_set_status":
        return f"set {args.get('request_id')} to {args.get('status')}"
    if tool == "channel_ask_template":
        return (f"asked {data.get('recipient_employee_id')} on {data.get('thread_id')} to confirm {group} for "
                f"{person}: waiting for the reply")
    return f"{tool}: {data.get('status', 'done')}"
