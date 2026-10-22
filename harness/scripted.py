"""Scripted models: test your code without spending a token.

    python run.py --tasks tasks/ --arm smoke --backend never-stops
    python run.py --tasks tasks/ --arm smoke --backend harness.scripted:SaysDone

NeverStops  asks for a tool on every call and never ends its turn, so the only
            thing that can stop your agent is its loop's bounds. It calls the
            first tool it is offered, with arguments made from the tool's schema.
SaysDone    never calls a tool: it answers "Done." and ends its turn. Your check
            must fail a run like this, which reports success and does nothing.

Write your own the same way, in tests/fakes.py say: a class with
create(system=, messages=, tools=) returning (content, stop_reason, model, usage),
named on the command line as --backend module:Class. Give it free = True, as these
two have, and its runs are tests too, not evidence, in results/_free/<arm>/;
without it, they count like a real model's, in results/<arm>/. Token counts from scripted models are
estimates, one token per four characters of the request, fine for testing and not
for anything you report.
"""

from __future__ import annotations

import json
import math
import time

from harness.client import Usage

NEVER_STOPS_OUTPUT_TOKENS = 30
_EMPTY = {"string": "x", "integer": 1, "number": 1, "boolean": True, "array": [], "object": {}, "null": None}


def estimate_tokens(obj) -> int:
    return math.ceil(len(json.dumps(obj, sort_keys=True, default=str)) / 4)


def input_for(tool: dict) -> dict:
    """Arguments for a tool, made from its schema: every required property gets a
    plain value of its type (its first enum value, if it has one)."""
    schema = tool.get("input_schema") or {}
    props = schema.get("properties") or {}
    out = {}
    for name in schema.get("required") or list(props):
        prop = props.get(name) or {}
        if prop.get("enum"):
            out[name] = prop["enum"][0]
            continue
        kind = prop.get("type", "string")
        kind = next((k for k in kind if k != "null"), "string") if isinstance(kind, list) else kind
        out[name] = _EMPTY.get(kind, "x")
    return out


class NeverStops:
    """Always calls the first tool it is offered. Optional delay per call, in seconds."""

    name = "never-stops"
    free = True          # a test, not evidence: its runs go to results/_free/<arm>/

    def __init__(self, settings=None, delay_s: float = 0.0):
        self.delay_s = delay_s
        self.calls = 0

    def create(self, *, system, messages, tools):
        if self.delay_s:
            time.sleep(self.delay_s)
        self.calls += 1
        prompt = estimate_tokens({"system": system, "tools": tools, "messages": messages})
        if tools:
            block = {"type": "tool_use", "id": f"toolu_scripted_{self.calls:04d}",
                     "name": tools[0]["name"], "input": input_for(tools[0])}
            return [block], "tool_use", self.name, Usage(prompt, NEVER_STOPS_OUTPUT_TOKENS)
        return ([{"type": "text", "text": "I would call a tool, but none was offered."}],
                "end_turn", self.name, Usage(prompt, NEVER_STOPS_OUTPUT_TOKENS))


class SaysDone:
    """Never calls a tool. Answers "Done." and ends its turn."""

    name = "says-done"
    free = True

    def __init__(self, settings=None):
        self.calls = 0

    def create(self, *, system, messages, tools):
        self.calls += 1
        prompt = estimate_tokens({"system": system, "tools": tools, "messages": messages})
        return [{"type": "text", "text": "Done."}], "end_turn", self.name, Usage(prompt, 2)
