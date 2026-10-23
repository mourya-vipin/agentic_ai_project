"""Fake models and stand-ins for our tests. Every fake model is marked free = True,
so its runs are tests, not evidence (they go to results/_free/<arm>/).

Each owner keeps their fakes in their own block below, so merges never touch the
same lines.
"""

from __future__ import annotations

import json

from agent.registry import CallContext, ToolResult


# -- shared ------------------------------------------------------------------------

class StubRegistry:
    """The registry's interface with canned answers, for building against before the
    real registry exists. Give it the results you want per tool name; every call is
    remembered in .calls. A tool with no canned answer returns an empty result."""

    def __init__(self, results: dict[str, dict | list] | None = None, definitions: list[dict] | None = None):
        self.results = dict(results or {})
        self._definitions = list(definitions or [])
        self.calls: list[tuple[str, dict, CallContext]] = []

    def definitions(self, names: list[str] | None = None) -> list[dict]:
        tools = [d for d in self._definitions if names is None or d["name"] in names]
        return sorted(tools, key=lambda d: d["name"])

    def call(self, name: str, args: dict, ctx: CallContext) -> ToolResult:
        self.calls.append((name, dict(args), ctx))
        data = self.results.get(name, {})
        return ToolResult(content=json.dumps(data), data=data)


# -- Vipin Mourya: registry, tools, sandbox -------------------------------------------


# -- Aman Panigrahi: routing, workflows, loop ----------------------------------------


# -- Shruti Tewaney: plan, critique, detectors ---------------------------------------
