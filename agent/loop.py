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
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent import bounds
from agent.bounds import Bound
from agent.detectors import RepeatTracker
from agent.registry import CallContext, get_registry
from harness import runtime
from harness.client import tool_result

PROGRESS_EVERY = 3        # steps between the progress lines the console shows (Lecture 1, slide 34)
MAX_PROTOCOL_ERRORS = 2   # the second protocol error in one loop ends it


@dataclass
class LoopResult:
    text: str                                         # what the operator reads
    actions: list[str] = field(default_factory=list)  # one line per effect
    end_reason: str = ""                              # one of the names in agent/bounds.py
    steps: int = 0


def run_loop(session_id: str, *, use_case: str, system: str, user_content: str,
             tools: list[str], bound: Bound, request_id: str | None = None) -> LoopResult:
    client = runtime.client(session_id)
    trace = runtime.trace(session_id)
    registry = get_registry()
    definitions = registry.definitions(tools)          # the same order on every request
    repeats = RepeatTracker()
    done = bounds.ANSWERED if use_case == "operator_review" else bounds.DONE
    trace.event("bound", use_case=use_case, request_id=request_id, max_steps=bound.max_steps,
                token_budget=bound.token_budget, wall_clock_s=bound.wall_clock_s)

    messages = [{"role": "user", "content": user_content}]
    actions: list[str] = []
    start, start_tokens = time.monotonic(), client.total_tokens
    text, step, calls_made, protocol_errors = "", 0, 0, 0

    def end(reason: str, detail: str, said: str) -> LoopResult:
        trace.end(reason, detail)
        return LoopResult(said, actions, reason, step)

    while True:
        used, elapsed = client.total_tokens - start_tokens, time.monotonic() - start
        if step >= bound.max_steps:
            return end(bounds.STEP_LIMIT, f"{bound.max_steps} model calls", _stopped("the step limit", text))
        if used >= bound.token_budget:
            return end(bounds.LOOP_TOKEN_BUDGET, f"{used:,} >= {bound.token_budget:,} tokens before step {step + 1}",
                       _stopped("the token budget", text))
        if elapsed >= bound.wall_clock_s:
            return end(bounds.WALL_CLOCK, f"{elapsed:.1f}s >= {bound.wall_clock_s:g}s before step {step + 1}",
                       _stopped("the time limit", text))

        step += 1
        response = client.create(system=system, messages=messages, tools=definitions)
        trace.model_call(step, response, purpose=use_case, request_id=request_id)
        text = response.text or text
        calls = response.tool_calls
        if not calls:
            return end(done, f"step {step}, stop_reason {response.stop_reason}", response.text)

        messages.append(response.assistant_message())
        results, ended = [], None
        for call in calls:
            earlier = repeats.see(call.name, call.input, step)
            if earlier is not None:
                trace.event("repeat_call", step=step, tool=call.name, first_step=earlier["step"], hash=earlier["hash"])
            ctx = CallContext(session_id, step, "model", use_case, request_id=request_id, call_id=call.id)
            result = registry.call(call.name, call.input, ctx)
            calls_made += 1
            results.append(tool_result(call, result.content, result.is_error))
            if result.error_class == "protocol":
                protocol_errors += 1
                if protocol_errors >= MAX_PROTOCOL_ERRORS and ended is None:
                    ended = bounds.PROTOCOL_ERROR
            elif not result.is_error and result.key is not None:
                actions.append(action_line(registry.resolve(call.name) or call.name, call.input, result.data))
            if result.end_reason and ended is None:
                ended = result.end_reason
        messages.append({"role": "user", "content": results})
        if ended is not None:
            return end(ended, f"step {step}", f"Stopped: {ended.replace('_', ' ')}. {text}".strip())
        if step % PROGRESS_EVERY == 0:
            trace.progress(f"{use_case}{' ' + request_id if request_id else ''}: step {step}, "
                           f"{calls_made} tool calls so far.")


def _stopped(limit: str, text: str) -> str:
    return f"Stopped at {limit} before finishing." + (f" Last said: {text}" if text else "")


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
    return f"{tool}: {data.get('status', 'done')}"
