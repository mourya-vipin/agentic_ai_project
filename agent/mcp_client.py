"""The registry's client to our MCP server (mcp_server/server.py). Owner: Vipin Mourya.

    reply = mcp_client.shared().call("access_grant", args, context)   # {"ok", "data" | "error", "trace", ...}

The registry starts the server itself, as a child process over stdio, the first time a
tool runs, so `make app` and run.py need nothing more. One connection per process, on a
background thread with its own event loop, so the registry's synchronous callers (the
loop, the workflows, the executor, the app's threads) can all use it. The connection
adopts the 2026-07-28 revision directly: no initialize, no session, nothing kept on
either side that a call needs; each call carries its context in _meta.

If the child dies, the next call starts a new one and is sent again once. That is safe
for writes because every write carries its key, and the sandbox accepts each key once.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import sys
import threading
from concurrent.futures import Future

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from harness import ROOT

CALL_TIMEOUT_S = 60.0
START_TIMEOUT_S = 30.0


class ServerError(Exception):
    """The server could not run the call: an unknown tool, a broken process, or a failure the
    tool did not report. The registry treats it as a protocol error (Lecture 3, slide 34)."""


class McpServer:
    def __init__(self, command: list[str] | None = None):
        self._command = command or [sys.executable, "-m", "mcp_server.server"]
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Client | None = None
        self._closed: asyncio.Event | None = None
        self._done: Future | None = None
        self.revision: str | None = None
        self.starts = 0

    # -- the connection ---------------------------------------------------------------------

    def _ensure(self) -> None:
        with self._lock:
            if self._client is not None and self._done is not None and not self._done.done():
                return
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="mcp-client", daemon=True).start()
            ready: Future = Future()
            self._done = asyncio.run_coroutine_threadsafe(self._serve(ready), self._loop)
            try:
                ready.result(timeout=START_TIMEOUT_S)
            except Exception as exc:
                raise ServerError(f"the MCP server did not start: {type(exc).__name__}: {exc}") from exc
            self.starts += 1

    async def _serve(self, ready: Future) -> None:
        """Owns the connection for its whole life, so it opens and closes in one task."""
        from mcp_server.server import REVISION

        params = StdioServerParameters(command=self._command[0], args=self._command[1:], cwd=str(ROOT),
                                       env={**os.environ, "PYTHONPATH": str(ROOT)})
        self._closed = asyncio.Event()
        try:
            async with Client(params, mode=REVISION) as client:
                await client.list_tools()           # the 2026-07-28 client checks calls against the listing
                self._client, self.revision = client, client.protocol_version
                ready.set_result(True)
                await self._closed.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self._client = None

    def close(self) -> None:
        with self._lock:
            if self._loop is not None and self._closed is not None:
                self._loop.call_soon_threadsafe(self._closed.set)
                if self._done is not None:
                    try:
                        self._done.result(timeout=5)
                    except Exception:
                        pass
            self._client = None

    def _run(self, coro_fn):
        self._ensure()
        future = asyncio.run_coroutine_threadsafe(coro_fn(self._client), self._loop)
        return future.result(timeout=CALL_TIMEOUT_S)

    # -- what the registry calls ------------------------------------------------------------------

    def list_tools(self) -> list[dict]:
        """tools/list, as the server returns it."""
        async def go(client):
            return await client.list_tools()
        result = self._call_with_restart(go)
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in result.tools]

    def call(self, name: str, arguments: dict, context: dict) -> dict:
        """tools/call with the call's context in _meta. Returns the server's structured result."""
        from mcp_server.server import CONTEXT_KEY

        async def go(client):
            return await client.call_tool(name, arguments, meta={CONTEXT_KEY: context})
        result = self._call_with_restart(go)
        if not isinstance(result.structured_content, dict) or "ok" not in result.structured_content:
            raise ServerError(f"{name}: the server's result had no structured content")
        return result.structured_content

    def _call_with_restart(self, go):
        for attempt in (1, 2):
            try:
                return self._run(go)
            except ServerError:
                raise
            except Exception as exc:
                if _is_protocol_error(exc):
                    raise ServerError(f"{type(exc).__name__}: {exc}") from exc
                if attempt == 2:
                    raise ServerError(f"the MCP server failed twice: {type(exc).__name__}: {exc}") from exc
                self.close()                  # a broken connection: start a new server, send once more


def _is_protocol_error(exc: Exception) -> bool:
    """A JSON-RPC error the server sent (an unknown tool, a failure the tool did not report),
    not a connection that broke: that one starts a new server instead."""
    import mcp_types
    from mcp.shared.exceptions import MCPError
    return isinstance(exc, MCPError) and getattr(exc, "code", None) != mcp_types.CONNECTION_CLOSED


_shared: McpServer | None = None
_shared_lock = threading.Lock()


def shared() -> McpServer:
    """This process's one connection to the server, started on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = McpServer()
            atexit.register(_shared.close)
        return _shared
