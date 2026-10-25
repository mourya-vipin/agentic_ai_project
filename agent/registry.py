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
    3. hook        each pre-call hook may deny the call; a hook that raises denies it
    4. key         derived for every write from the intended action, never from the model
    5. run         a transient failure is retried in code, with backoff, up to MAX_ATTEMPTS
    6. result      checked against the tool's declared shape before the model sees it
    7. cap         what the model reads is at most MAX_CHARS, and says so when cut
    8. record      the trace's tool_call line, with the key, attempts, latency and cost

Callers never write a tool_call line of their own: call() traces each call.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass

from agent.errors import EndRun, ToolError
from agent.tools import definitions as _rewrite
from agent.tools import impl, rules
from harness import runtime
from harness.schema import errors as schema_errors
from sandbox import access, db

CALLERS = ("model", "workflow", "executor")
GENERIC_PROTOCOL = "No tool by that name ran. Use only the tools in your definitions."
DENIED = "Denied before running: the pre-call check failed. Nothing was changed; do not retry this call."
BAD_RESULT = "The tool returned a result in an unexpected form, so nothing more is known. Do not assume it worked."
MAX_ATTEMPTS = 3          # a transient failure: the first try and two retries
BACKOFF_S = 0.05          # waits 0.05 s, then 0.1 s
MAX_CHARS = 4000          # the most one call returns to the model
REPAIR_CHARS = 400        # the most an error's repair fact takes


def allow_all(tool: str, args: dict, ctx) -> None:
    """Milestone 1's pre-call hook: it allows every call. Milestone 2 gives it a policy."""


@dataclass
class CallContext:
    session_id: str               # what runtime gives the turn
    step: int                     # the loop's step, or the workflow's step number
    caller: str                   # one of CALLERS
    use_case: str                 # "standard_grant", "nonstandard_request", ...
    plan_step: int | None = None  # set by the executor only
    request_id: str | None = None
    call_id: str | None = None    # the model's tool_use id, when the model made the call


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
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]


class Registry:
    def __init__(self, definitions: list[dict] | None = None, aliases: dict[str, str] | None = None):
        self._definitions = definitions if definitions is not None else _rewrite.DEFINITIONS
        self._aliases = aliases if aliases is not None else _rewrite.ALIASES
        self._by_canonical = {self._aliases.get(d["name"], d["name"]): d for d in self._definitions}
        self.hooks = [allow_all]

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
        return sorted((_as_sent(d, self._aliases.get(d["name"], d["name"])) for d in chosen), key=lambda d: d["name"])

    def call(self, name: str, args: dict, ctx: CallContext) -> ToolResult:
        start = time.monotonic()
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
        # 3. the pre-call hook, failing closed
        for hook in self.hooks:
            try:
                hook(canon, fixed, ctx)
            except Exception as exc:  # any failure of the hook denies the call
                runtime.trace(ctx.session_id).event("hook_denied", tool=canon, hook=getattr(hook, "__name__", "hook"),
                                                    error=f"{type(exc).__name__}: {exc}"[:300])
                return self._record(name, canon, args, ctx, ToolResult(DENIED, is_error=True, error_class="permanent"),
                                    start, denied=True)
        # 4. the key, derived here for every write
        key = derive_key(canon, fixed, ctx) if writes else None
        # 5. run, retrying transient failures in code (slide 36)
        attempts = 0
        while True:
            attempts += 1
            try:
                data = run(fixed, ctx, key)
                break
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
        # 7. the cap
        result = ToolResult(_cap(json.dumps(data, default=str), MAX_CHARS), data=data, key=key)
        return self._record(name, canon, args, ctx, result, start, attempts=attempts)

    def _record(self, asked: str, canon: str | None, args: dict, ctx: CallContext, result: ToolResult,
                start: float, attempts: int = 0, rejected: bool = False, denied: bool = False) -> ToolResult:
        """The call record (Lecture 3, slide 42), as the trace's tool_call line."""
        result.latency_s = time.monotonic() - start
        runtime.trace(ctx.session_id).tool_call(
            ctx.step, {"id": ctx.call_id or f"call_{uuid.uuid4().hex[:12]}", "name": canon or asked, "input": args},
            result, plan_step=ctx.plan_step, asked_as=asked, caller=ctx.caller, use_case=ctx.use_case,
            key=result.key, error_class=result.error_class, attempts=attempts, rejected=rejected, denied=denied,
            cost_usd=0.0, summary=result.content[:200])
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
