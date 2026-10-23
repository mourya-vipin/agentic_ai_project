"""The tool registry: every tool call goes through it, the model's and our code's.

Owner: Vipin Mourya.

    registry = get_registry()
    tools = registry.definitions(["directory_get_employee", "access_grant"])
    result = registry.call("access_grant", args, CallContext(session_id, step, "model", "nonstandard_request"))

This is the plain dispatch of Part 1, task 4: it resolves a tool's name (the
first-draft name the model reads, or the canonical name our code uses), refuses a
tool the caller may not use, derives the key of every write, runs the tool and
traces the call in full. Part 3 adds, behind the same interface, boundary
validation, the pre-call hook, retries of transient failures and result checks.

Callers never write a tool_call line of their own: call() traces each call.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass

from agent.errors import EndRun, ToolError
from agent.tools import definitions_v1, impl
from harness import runtime
from sandbox import access, db

CALLERS = ("model", "workflow", "executor")
GENERIC_PROTOCOL = "No tool by that name ran. Use only the tools in your definitions."


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
        self._definitions = definitions if definitions is not None else definitions_v1.DEFINITIONS
        self._aliases = aliases if aliases is not None else definitions_v1.ALIASES
        self._by_canonical = {self._aliases.get(d["name"], d["name"]): d for d in self._definitions}

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
        return sorted((dict(d) for d in chosen), key=lambda d: d["name"])

    def call(self, name: str, args: dict, ctx: CallContext) -> ToolResult:
        start = time.monotonic()
        canon = self.resolve(name)
        if canon is None or ctx.caller not in impl.TOOLS[canon][1] or not isinstance(args, dict):
            runtime.trace(ctx.session_id).event("protocol_error", tool=name, caller=ctx.caller,
                                                use_case=ctx.use_case, step=ctx.step)
            result = ToolResult(GENERIC_PROTOCOL, is_error=True, error_class="protocol")
            return self._record(name, canon, args if isinstance(args, dict) else {"_raw": args}, ctx, result, start)
        run, _, writes = impl.TOOLS[canon]
        key = derive_key(canon, args, ctx) if writes else None
        try:
            data = run(args, ctx, key)
            result = ToolResult(json.dumps(data, default=str), data=data, key=key)
        except EndRun as exc:
            result = ToolResult(exc.message, is_error=True, error_class=exc.error_class, key=key,
                                end_reason=exc.end_reason)
        except ToolError as exc:
            result = ToolResult(exc.message, is_error=True, error_class=exc.error_class, retryable=exc.retryable,
                                key=key)
        except access.TransientError as exc:
            result = ToolResult(f"{exc} A retry can work.", is_error=True, error_class="transient", retryable=True,
                                key=key)
        except (access.NotFound, KeyError, ValueError) as exc:
            message = exc.args[0] if exc.args else str(exc)
            result = ToolResult(f"{message}. A retry with the same arguments will fail the same way.", is_error=True,
                                error_class="permanent", key=key)
        return self._record(name, canon, args, ctx, result, start)

    def _record(self, asked: str, canon: str | None, args: dict, ctx: CallContext, result: ToolResult,
                start: float) -> ToolResult:
        """The call record (Lecture 3, slide 42), as the trace's tool_call line."""
        result.latency_s = time.monotonic() - start
        runtime.trace(ctx.session_id).tool_call(
            ctx.step, {"id": ctx.call_id or f"call_{uuid.uuid4().hex[:12]}", "name": canon or asked, "input": args},
            result, plan_step=ctx.plan_step, asked_as=asked, caller=ctx.caller, use_case=ctx.use_case,
            key=result.key, error_class=result.error_class, cost_usd=0.0, summary=result.content[:200])
        return result


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
