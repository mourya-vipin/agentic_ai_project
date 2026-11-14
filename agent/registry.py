"""The tool registry: every tool call goes through it, the model's and our code's.

Owner: Vipin Mourya.

    registry = get_registry()
    tools = registry.definitions(["directory_get_employee", "access_grant"])
    result = registry.call("access_grant", args, CallContext(session_id, step, "model", "nonstandard_request"))

call() does, in order (Lecture 3, slides 22 to 42):

    1. protocol    an unknown tool, a tool this caller may not use, or arguments that
                   are not an object: logged, and the model gets a short generic answer
    2. boundary    the arguments against the tool's rules (agent/tools/rules.py) and its
                   definition's schema; a rejection is an execution error with its repair fact
    3. hook        policy/hook.py's policy_hook: the stop, the action class, the policy's
                   columns, stated limits and the disclosure line; a hook that raises
                   denies the call, except AskApproval, which hands it to agent/approvals.py
                   and ends the turn awaiting_approval
    4. key         derived for every write from the intended action, never from the model
    5. run         a transient failure is retried in code, with backoff, up to MAX_ATTEMPTS
    6. result      checked against the tool's declared shape before the model sees it
    7. cap         what the model reads is at most MAX_CHARS, and says so when cut
    8. record      the trace's tool_call line, with the key, attempts, latency and cost

Callers never write a tool_call line of their own: call() traces each call.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
import uuid
from dataclasses import asdict, dataclass

import channel
from agent import mcp_client, notes
from agent.errors import EndRun, ToolError
from agent.tools import definitions as _rewrite
from agent.tools import impl, rules
from harness import runtime
from harness.paths import RESULTS_ENV, STATE_ENV, TRACES_ENV, results_dir, state_dir, traces_dir
from harness.schema import errors as schema_errors
from policy.hook import AskApproval, Denied, policy_hook
from sandbox import access, db

CALLERS = ("model", "workflow", "executor")
GENERIC_PROTOCOL = "No tool by that name ran. Use only the tools in your definitions."
DENIED = "Denied before running: the pre-call check failed. Nothing was changed; do not retry this call."
BAD_RESULT = "The tool returned a result in an unexpected form, so nothing more is known. Do not assume it worked."
MAX_ATTEMPTS = 3          # a transient failure: the first try and two retries
BACKOFF_S = 0.05          # waits 0.05 s, then 0.1 s
MAX_CHARS = 4000          # the most one call returns to the model
REPAIR_CHARS = 400        # the most an error's repair fact takes


AWAITING_APPROVAL = "awaiting_approval"      # the end reason of a turn whose call waits for the operator
TRANSPORTS = ("mcp", "local")                # local: in this process, for tests that replace a sandbox function
ORIGINS = ("agent", "workflow", "operator_command", "program")


def allow_all(tool: str, args: dict, ctx) -> None:
    """Milestone 1's pre-call hook: it allows every call. Tests that measure the registry
    alone use it; every other registry runs policy/hook.py's policy_hook."""


@dataclass
class CallContext:
    session_id: str               # what runtime gives the turn
    step: int                     # the loop's step, or the workflow's step number
    caller: str                   # one of CALLERS
    use_case: str                 # "standard_grant", "nonstandard_request", ...
    plan_step: int | None = None  # set by the executor only
    request_id: str | None = None
    call_id: str | None = None    # the model's tool_use id, when the model made the call
    run_key: str | None = None    # f"{session_id}#{run_no}" (agent/checkpoints.py); set by every caller
    origin: str = "agent"         # one of ORIGINS: who asked for the call
    action_class: str | None = None   # filled in by the hook; logged on the tool_call line
    approved: bool = False        # set by the hook only, when an approval covers this exact call


@dataclass
class ToolResult:
    content: str                     # JSON text the model reads (capped, and says so when cut)
    data: dict | list | None = None  # the parsed result, for code callers
    is_error: bool = False
    error_class: str | None = None   # "transient" | "permanent" | "protocol"
    retryable: bool = False
    key: str | None = None
    latency_s: float = 0.0
    end_reason: str | None = None    # set when the call must end the loop: "critique_held", "protocol_error"


def canonical(value):
    """A value in one standard form, so the same intended action always hashes the same."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text.upper() if text.upper().startswith("SYN-") else text.lower()


def derive_key(tool: str, args: dict, ctx: CallContext) -> str:
    """Lecture 3, slide 38: from the run (in the app, the console session), the plan step
    it serves, the tool and the arguments that name the effect; never from text the
    model writes, so a reworded retry gets the same key."""
    effect = {name: canonical(args.get(name)) for name in impl.EFFECT_ARGS[tool]}
    material = [db.current_stamp(), "-" if ctx.plan_step is None else str(ctx.plan_step), tool, effect]
    if ctx.run_key is not None:
        # one run's writes, not the whole console session's: a later run that makes the same
        # change again gets a new key, and a replay of this run gets the same one (M1 CHANGES)
        material.append(ctx.run_key)
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]


class Registry:
    def __init__(self, definitions: list[dict] | None = None, aliases: dict[str, str] | None = None,
                 apply_switches: bool = True, transport: str = "mcp"):
        if transport not in TRANSPORTS:
            raise ValueError(f"transport is one of {TRANSPORTS}, not {transport!r}")
        self.transport = transport
        self._tool_state: dict = {}      # what a tool counts across calls (the critique's rounds), kept here
        self._offered: dict[str, list[str]] = {}   # run -> the tools its use case is offered (deferred loading)
        self._found: dict[str, list[str]] = {}     # run -> the tools its searches found, in order
        self.served_by: int | None = None
        self._definitions = definitions if definitions is not None else _rewrite.DEFINITIONS
        self._aliases = aliases if aliases is not None else _rewrite.ALIASES
        self._by_canonical = {self._aliases.get(d["name"], d["name"]): d for d in self._definitions}
        self.hooks = [policy_hook]
        # False sends the definitions exactly as written: the first draft is measured as it was,
        # never with the rewrite's strict mode or examples on it.
        self.apply_switches = apply_switches

    # -- deferred loading (Lecture 3, slides 24 and 25) ---------------------------------------------

    def offer(self, scope: str, tool_names: list[str]) -> None:
        """The tools a run's use case is offered: all a search may find for it."""
        self._offered[scope] = [self.resolve(n) or n for n in tool_names]

    def found(self, scope: str) -> list[str]:
        """The tools this run's searches have found so far, in the order found."""
        return list(self._found.get(scope, []))

    def resolve(self, name: str) -> str | None:
        """The canonical name for a first-draft or canonical name, or None."""
        canon = self._aliases.get(name, name)
        return canon if canon in impl.TOOLS else None

    def definitions(self, names: list[str] | None = None) -> list[dict]:
        """The tool definitions the model reads, always in the same order (sorted by
        the name the model sees). `names` are canonical names; None means every tool a
        model may call."""
        wanted = list(self._by_canonical) if names is None else [self.resolve(n) or n for n in names]
        chosen = [self._by_canonical[n] for n in wanted if n in self._by_canonical]
        if not self.apply_switches:
            return sorted(({k: v for k, v in d.items() if k != "examples"} for d in chosen), key=lambda d: d["name"])
        return sorted((_as_sent(d, self._aliases.get(d["name"], d["name"])) for d in chosen), key=lambda d: d["name"])

    def call(self, name: str, args: dict, ctx: CallContext) -> ToolResult:
        start = time.monotonic()
        self.served_by = None
        canon = self.resolve(name)
        # 1. protocol: for us, not the model (slide 34)
        if canon is None or ctx.caller not in impl.TOOLS[canon][1] or not isinstance(args, dict):
            runtime.trace(ctx.session_id).event("protocol_error", tool=name, caller=ctx.caller,
                                                use_case=ctx.use_case, step=ctx.step)
            result = ToolResult(GENERIC_PROTOCOL, is_error=True, error_class="protocol")
            return self._record(name, canon, args if isinstance(args, dict) else {"_raw": args}, ctx, result, start)
        run, _, writes = impl.TOOLS[canon]
        # 2. boundary
        fixed, problems = rules.check_args(canon, args)
        definition = self._by_canonical.get(canon)
        if ctx.caller == "model" and definition is not None:
            named = {p.split(" ", 1)[0] for p in problems}       # a field the rules already named is said once
            problems += [e for e in schema_errors(fixed, definition["input_schema"])     # after the case is normalized
                         if e.split(":", 1)[0].split("[", 1)[0] not in named]
        if problems:
            message = (f"{canon} did not run: " + "; ".join(dict.fromkeys(p.rstrip(". ") for p in problems))
                       + ". Fix the arguments and "
                       "call again; the same arguments will fail the same way.")
            result = ToolResult(_cap(message, REPAIR_CHARS), is_error=True, error_class="permanent")
            return self._record(name, canon, args, ctx, result, start, rejected=True)
        # 3. the pre-call hooks, failing closed: any exception but AskApproval denies the call
        for hook in self.hooks:
            try:
                hook(canon, fixed, ctx)
            except AskApproval as ask:
                approvals = _approvals()
                if approvals is None:
                    # until the gate is built, the call runs unapproved: every gated effect's
                    # tool refuses without ctx.approved, as in Milestone 1 (C3.2)
                    runtime.trace(ctx.session_id).event("approval_unavailable", tool=canon,
                                                        action_class=ask.action_class, run_key=ctx.run_key)
                    continue
                approval_id = approvals.request(ctx, canon, fixed, ask.action_class, ask.facts)
                result = ToolResult(f"Waiting for the operator's approval {approval_id}. Nothing has run; end the "
                                    "turn here.", end_reason=AWAITING_APPROVAL)
                return self._record(name, canon, args, ctx, result, start, awaiting=approval_id)
            except Exception as exc:  # any other failure of the hook denies the call
                runtime.trace(ctx.session_id).event("hook_denied", tool=canon, hook=getattr(hook, "__name__", "hook"),
                                                    action_class=ctx.action_class, run_key=ctx.run_key,
                                                    error=f"{type(exc).__name__}: {exc}"[:300])
                # a denial with a next step says it; a hook that broke gets the generic answer
                message = exc.repair if isinstance(exc, Denied) and exc.repair else \
                    exc.message if isinstance(exc, ToolError) else DENIED
                return self._record(name, canon, args, ctx,
                                    ToolResult(_cap(message, REPAIR_CHARS), is_error=True, error_class="permanent"),
                                    start, denied=True)
        # 4. the key, derived here for every write
        key = derive_key(canon, fixed, ctx) if writes else None
        # 5. run, retrying transient failures in code (slide 36)
        attempts = 0
        while True:
            attempts += 1
            try:
                data = self._run(canon, run, fixed, ctx, key)
                break
            except mcp_client.ServerError as exc:
                runtime.trace(ctx.session_id).event("mcp_error", tool=canon, error=str(exc)[:300])
                result = ToolResult(GENERIC_PROTOCOL, is_error=True, error_class="protocol", key=key)
                return self._record(name, canon, args, ctx, result, start, attempts=attempts)
            except access.TransientError as exc:
                if attempts < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_S * 2 ** (attempts - 1))
                    continue
                result = ToolResult(_cap(f"{exc}. Tried {attempts} times; a retry later can work.", REPAIR_CHARS),
                                    is_error=True, error_class="transient", retryable=True, key=key)
                return self._record(name, canon, args, ctx, result, start, attempts=attempts)
            except EndRun as exc:
                result = ToolResult(_cap(exc.message, REPAIR_CHARS), is_error=True, error_class=exc.error_class,
                                    key=key, end_reason=exc.end_reason)
                return self._record(name, canon, args, ctx, result, start, attempts=attempts)
            except ToolError as exc:
                message = exc.message if exc.retryable or "same way" in exc.message else \
                    f"{exc.message} A retry of the same call will fail the same way."
                result = ToolResult(_cap(message, REPAIR_CHARS), is_error=True, error_class=exc.error_class,
                                    retryable=exc.retryable, key=key)
                return self._record(name, canon, args, ctx, result, start, attempts=attempts)
            except (access.NotFound, KeyError, ValueError) as exc:
                message = exc.args[0] if exc.args else str(exc)
                result = ToolResult(_cap(f"{message}. The same arguments will fail the same way.", REPAIR_CHARS),
                                    is_error=True, error_class="permanent", key=key)
                return self._record(name, canon, args, ctx, result, start, attempts=attempts)
        # 6. the result, against its declared shape
        bad = rules.check_result(canon, data)
        if bad:
            runtime.trace(ctx.session_id).event("result_invalid", tool=canon, problems=bad)
            result = ToolResult(BAD_RESULT, is_error=True, error_class="permanent", key=key)
            return self._record(name, canon, args, ctx, result, start, attempts=attempts)
        # 7. the cap; then the session's notes, outside the conversation (agent/notes.py)
        result = ToolResult(_cap(json.dumps(data, default=str), MAX_CHARS), data=data, key=key)
        if writes:
            try:
                notes.after_call(ctx, canon, fixed, data)
            except Exception as exc:           # the notes never fail the call they record
                runtime.trace(ctx.session_id).event("notes_error", tool=canon, error=f"{type(exc).__name__}: {exc}"[:200])
        return self._record(name, canon, args, ctx, result, start, attempts=attempts)

    def _run(self, canon: str, run, fixed: dict, ctx: CallContext, key: str | None):
        """Run the tool: on the MCP server (the app and run.py), or in this process for a
        test that replaces a sandbox function (transport="local"). Either way it returns the
        data or raises the error the tool raised, so the steps around it are the same."""
        if canon in impl.REGISTRY_TOOLS:
            from agent import codecalls
            return codecalls.run(fixed.get("program"), ctx, self, self._offered.get(_scope(ctx), []))
        if canon == "tools_search":
            fixed = {**fixed, "_offered": self._offered.get(_scope(ctx), [])}   # never seen by the model
        if self.transport == "local":
            return self._searched(canon, ctx, run(fixed, ctx, key))
        reply = mcp_client.shared().call(canon, fixed, self._context(ctx, key))
        self._tool_state = reply.get("state") or {}
        trace = runtime.trace(ctx.session_id)
        for line in reply.get("trace") or []:
            kind = line.pop("type", "tool_event")
            trace.event(kind, **{k: v for k, v in line.items() if k not in ("at", "session_id", "run_id")})
        self.served_by = reply.get("pid")
        if reply["ok"]:
            _tell_script(canon, fixed, reply["data"])
            return self._searched(canon, ctx, reply["data"])
        error = reply["error"]
        if error["type"] == "transient":
            raise access.TransientError(error["message"])
        if error["type"] == "end_run":
            raise EndRun(error["message"], error["end_reason"])
        if error["type"] == "tool":
            exc = ToolError(error["message"])
            exc.error_class, exc.retryable = error.get("error_class") or "permanent", bool(error.get("retryable"))
            raise exc
        raise ValueError(error["message"])

    def _searched(self, canon: str, ctx: CallContext, data):
        """After a search, remember what it found for this run, and log it so misses can be counted."""
        if canon == "tools_search" and isinstance(data, dict):
            scope = _scope(ctx)
            names = [f["name"] for f in data.get("found") or []]
            known = self._found.setdefault(scope, [])
            known += [n for n in names if n not in known]
            runtime.trace(ctx.session_id).event("tool_search", query=data.get("query"), found=names,
                                                matched=bool(names), offered=len(self._offered.get(scope, [])),
                                                run_key=ctx.run_key)
        return data

    def _context(self, ctx: CallContext, key: str | None) -> dict:
        """Everything the call needs, carried with it: the server keeps nothing between calls."""
        run = runtime.current_run()
        return {
            "key": key,
            "ctx": asdict(ctx),
            "run": {"run_id": run.run_id, "arm": run.arm, "task_id": run.task_id, "sweep": run.sweep,
                    "backend": run.backend} if run else None,
            "session": runtime.current_session(),
            "paths": {STATE_ENV: str(state_dir()), RESULTS_ENV: str(results_dir()), TRACES_ENV: str(traces_dir())},
            "switches": runtime.switches(),
            "state": self._tool_state,
        }

    def _record(self, asked: str, canon: str | None, args: dict, ctx: CallContext, result: ToolResult,
                start: float, attempts: int = 0, rejected: bool = False, denied: bool = False,
                awaiting: str | None = None) -> ToolResult:
        """The call record (Lecture 3, slide 42), as the trace's tool_call line."""
        result.latency_s = time.monotonic() - start
        runtime.trace(ctx.session_id).tool_call(
            ctx.step, {"id": ctx.call_id or f"call_{uuid.uuid4().hex[:12]}", "name": canon or asked, "input": args},
            result, plan_step=ctx.plan_step, asked_as=asked, caller=ctx.caller, use_case=ctx.use_case,
            key=result.key, error_class=result.error_class, attempts=attempts, rejected=rejected, denied=denied,
            action_class=ctx.action_class, origin=ctx.origin, run_key=ctx.run_key, approved=ctx.approved,
            awaiting_approval=awaiting, transport=self.transport, server_pid=self.served_by, cost_usd=0.0, summary=result.content[:200])
        return result


def _as_sent(definition: dict, canonical_name: str) -> dict:
    """A definition as the model receives it: its examples rendered into the description
    when examples.<tool> is on (Lecture 3, slide 18), and strict: true when strict.<tool>
    is on (slides 20 to 22). Every other field is sent as it is written."""
    sent = {k: v for k, v in definition.items() if k != "examples"}
    examples = definition.get("examples")
    if examples and runtime.switch(f"examples.{canonical_name}", "off") == "on":
        first, *rest = examples
        text = f" Example, a typical call: {json.dumps(first)}."
        text += "".join(f" The less usual case: {json.dumps(e)}." for e in rest)
        sent["description"] = definition["description"] + text
    if runtime.switch(f"strict.{canonical_name}", "off") == "on":
        sent["strict"] = True
    return sent


def _scope(ctx: CallContext) -> str:
    """The run a call belongs to: its run key, or its session until callers set one."""
    return ctx.run_key or ctx.session_id


def _tell_script(tool: str, args: dict, data) -> None:
    """A send ran in the server's process, so tell this process's scripted strangers
    (run.py's channel script) that the agent wrote on the thread, as channel.send would."""
    if not isinstance(data, dict) or data.get("status") != "sent" or not args.get("thread_id"):
        return
    script = getattr(channel, "_script", None)
    if script is None or not script.people:
        return
    for message in channel.messages(str(args["thread_id"])):
        if message.id == data.get("message_id"):
            script.sent(message)


def _approvals():
    """Shruti Tewaney's agent/approvals.py, or None until it lands (C3.2)."""
    try:
        return importlib.import_module("agent.approvals")
    except ModuleNotFoundError as exc:
        if exc.name != "agent.approvals":
            raise
        return None


def _cap(text: str, limit: int) -> str:
    """At most `limit` characters, saying so when cut."""
    if len(text) <= limit:
        return text
    note = f" … [cut: showed {limit} of {len(text)} characters; narrow the query to see the rest]"
    return text[: limit - len(note)] + note


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
