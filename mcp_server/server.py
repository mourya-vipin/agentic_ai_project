"""Our tools over MCP (Lecture 3, slides 29 to 34; Part 1, task 3). Owner: Vipin Mourya.

    python -m mcp_server.server        # stdio; the registry starts it itself (agent/mcp_client.py)

It serves agent/tools/impl.py's tools and keeps nothing between calls. It speaks the
protocol's 2026-07-28 revision, which has no session: no initialize, and none of Roots,
Sampling or Logging, which that revision deprecated. Everything a call needs comes with
it, in the request's _meta under CONTEXT_KEY:

    key        the write's idempotency key, derived by the registry; never seen by the model
    ctx        the CallContext's fields (session, step, caller, use case, request, run key, ...)
    run        the run.py run the call belongs to (run_id, arm, task_id, sweep), or None in the app
    session    the console session, in the app
    paths      the state, results and traces folders, which run.py moves per run
    switches   every switch's setting for this call
    state      what a tool counts across calls (the critique's rounds), held by the registry

and everything the registry must keep comes back in the result's structured content: the
data or the error, the trace lines the tool wrote, and the updated state. So state between
calls lives in our own code, in the call record and the checkpoint, never in this process.

The registry keeps the boundary, the hook, the keys, the retries and the call record; this
server only runs the tool. An execution error comes back as a result with isError: true;
an unknown tool, or a failure the tool did not report, as a JSON-RPC error.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import fields
from functools import lru_cache
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from agent import critique
from agent.errors import EndRun, ToolError
from agent.tools import definitions, impl
from harness import ROOT, runtime
from harness.pricing import PriceSheet
from harness.settings import load_settings
from harness.trace import SessionTrace
from sandbox import access

NAME = "it-access-desk-tools"
VERSION = "2.0"
REVISION = "2026-07-28"                 # the protocol revision this server speaks
CONTEXT_KEY = "it-desk/context"         # where a call's context travels in _meta


# -- the catalogue -------------------------------------------------------------------------

@lru_cache(maxsize=1)
def catalogue() -> tuple[dict, ...]:
    """Every tool, sorted by name, so tools/list returns them in the same order every time.
    The model's tools carry their definitions as written; our code's tools (workflows only)
    take an object, which the registry has already checked."""
    by_name = {d["name"]: d for d in definitions.DEFINITIONS}
    out = []
    for name in sorted(impl.TOOLS):
        written = by_name.get(name)
        out.append({"name": name,
                    "description": written["description"] if written else (impl.TOOLS[name][0].__doc__ or name).strip(),
                    "input_schema": written["input_schema"] if written else {"type": "object"}})
    return tuple(out)


# -- one call --------------------------------------------------------------------------------

class _Collector(SessionTrace):
    """The trace a tool writes to, while it runs here: its lines go back to the registry,
    which writes them into the run's own trace. Nothing is written in this process."""

    def __init__(self, session_id: str):
        super().__init__(Path(os.devnull), session_id)
        self.lines: list[dict] = []

    def write(self, record: dict) -> None:
        self.lines.append(json.loads(json.dumps(record, default=str)))


@lru_cache(maxsize=1)
def _settings():
    return load_settings(ROOT / "settings.yaml"), PriceSheet(ROOT / "pricing.yaml")


@contextlib.contextmanager
def _scope(context: dict, collector: _Collector):
    """The run or console session the call belongs to, for this call only."""
    for env, path in (context.get("paths") or {}).items():
        os.environ[env] = path
    switches = dict(context.get("switches") or {})
    saved = runtime.trace, runtime.switches
    runtime.trace = lambda session_id=None: collector
    runtime.switches = lambda: dict(switches)
    try:
        run = context.get("run")
        if run:
            settings, prices = _settings()
            scope = runtime.RunScope(run["run_id"], run["arm"], run["task_id"], run["sweep"], collector,
                                     run.get("backend") or "never-stops", switches, settings, prices)
            with runtime.run_scope(scope):
                yield
        else:
            with runtime.in_session(context.get("session")):
                yield
    finally:
        runtime.trace, runtime.switches = saved


def _load_state(state: dict) -> None:
    impl._rounds.clear()
    impl._rounds.update({tuple(slot): n for slot, n in state.get("rounds") or []})
    critique._last.clear()
    critique._last.update({tuple(slot): body for slot, body in state.get("last") or []})


def _take_state() -> dict:
    """What the tools counted, handed back, and cleared here so nothing outlives the call."""
    out = {"rounds": [[list(slot), n] for slot, n in impl._rounds.items()],
           "last": [[list(slot), body] for slot, body in critique._last.items()]}
    impl._rounds.clear()
    critique._last.clear()
    return out


def execute(name: str, arguments: dict, context: dict) -> dict:
    """Run one tool with the context its call carried. Returns what the registry needs:
    {"ok", "data" or "error", "trace", "state", "pid"}. Raises KeyError for an unknown tool."""
    from agent.registry import CallContext           # the registry imports nothing of this module

    if name not in impl.TOOLS:
        raise KeyError(name)
    run = impl.TOOLS[name][0]
    known = {f.name for f in fields(CallContext)}
    ctx = CallContext(**{k: v for k, v in (context.get("ctx") or {}).items() if k in known})
    collector = _Collector(ctx.session_id)
    _load_state(context.get("state") or {})
    out: dict = {"ok": False}
    try:
        with _scope(context, collector):
            try:
                out = {"ok": True, "data": json.loads(json.dumps(run(arguments, ctx, context.get("key")),
                                                                  default=str))}
            except access.TransientError as exc:
                out["error"] = {"type": "transient", "message": str(exc)}
            except EndRun as exc:
                out["error"] = {"type": "end_run", "message": exc.message, "error_class": exc.error_class,
                                "end_reason": exc.end_reason}
            except ToolError as exc:
                out["error"] = {"type": "tool", "message": exc.message, "error_class": exc.error_class,
                                "retryable": exc.retryable}
            except (access.NotFound, KeyError, ValueError) as exc:
                out["error"] = {"type": "not_found", "message": exc.args[0] if exc.args else str(exc)}
    finally:
        out["state"] = _take_state()
    out["trace"] = collector.lines
    out["pid"] = os.getpid()
    return out


# -- MCP --------------------------------------------------------------------------------------

async def _list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[types.Tool(name=t["name"], description=t["description"],
                                                   input_schema=t["input_schema"]) for t in catalogue()])


_lock = anyio.Lock()          # one call at a time: a call sets the process's run scope while it runs


async def _call_tool(ctx, params) -> types.CallToolResult:
    meta = dict(params.meta or {})
    context = meta.get(CONTEXT_KEY)
    if not isinstance(context, dict):
        raise MCPError(code=types.INVALID_PARAMS, message=f"a call carries its context in _meta[{CONTEXT_KEY!r}]")
    if params.name not in impl.TOOLS:
        raise MCPError(code=types.INVALID_PARAMS, message=f"no tool {params.name!r}")
    async with _lock:
        out = execute(params.name, dict(params.arguments or {}), context)
    text = json.dumps(out["data"] if out["ok"] else {"error": out["error"]["message"]}, default=str)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=out,
                                is_error=not out["ok"])


server = Server(NAME, version=VERSION, on_list_tools=_list_tools, on_call_tool=_call_tool)


async def _serve() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_serve)
