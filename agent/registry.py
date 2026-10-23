"""The tool registry: every tool call goes through it, the model's and our code's.

Owner: Vipin Mourya. This file holds the interface the rest of the agent calls;
the body comes in two stages: a plain dispatch first (Part 1, task 4), then the
full registry (Part 3): boundary validation, a pre-call hook that fails closed,
retries of transient failures in code, a key on every write, result validation,
the call record, and the tools in a fixed order.

    registry = get_registry()
    tools = registry.definitions(["directory_get_employee", "access_grant"])
    result = registry.call("access_grant", args, CallContext(session_id, step, "model", "nonstandard_request"))

Callers never write a tool_call line of their own: call() traces each call in full.
"""

from __future__ import annotations

from dataclasses import dataclass

CALLERS = ("model", "workflow", "executor")


@dataclass
class CallContext:
    session_id: str               # what runtime gives the turn
    step: int                     # the loop's step, or the workflow's step number
    caller: str                   # one of CALLERS
    use_case: str                 # "standard_grant", "nonstandard_request", ...
    plan_step: int | None = None  # set by the executor only
    request_id: str | None = None


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


class Registry:
    def definitions(self, names: list[str] | None = None) -> list[dict]:
        """The tool definitions the model reads, always in the same order (sorted by
        canonical name), with strict mode and examples applied from the switches."""
        raise NotImplementedError("registry.definitions: not built yet")

    def call(self, name: str, args: dict, ctx: CallContext) -> ToolResult:
        """Validate, run the hook, retry transient failures, key a write, run the tool,
        check its result and trace the call."""
        raise NotImplementedError("registry.call: not built yet")


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
