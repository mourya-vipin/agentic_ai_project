"""Check your model before you spend anything real: `make ping`.

Two tiny calls through the same client and backend your agent uses. The first
offers one tool and asks the model to call it; the second sends the tool's
result back and asks for a one-word answer. Together they cost a fraction of a
cent, and they prove four things: your key works, the model is the one you
chose, it calls tools, and pricing.yaml has its line.
"""

from __future__ import annotations

import sys

from harness import ROOT
from harness.backends import REAL_BACKENDS, make_backend
from harness.client import ModelClient, tool_result
from harness.pricing import PriceSheet
from harness.settings import load_settings

ECHO = {"name": "echo", "description": "Return the text you give it.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}


def main() -> int:
    settings = load_settings(ROOT / "settings.yaml")
    if settings.provider not in REAL_BACKENDS or not settings.model:
        print("settings.yaml: set provider (anthropic or openai-compatible) and model first.")
        return 1
    print(f"provider {settings.provider}, model {settings.model}"
          + (f", base_url {settings.base_url}" if settings.base_url else ""))
    try:
        return _ping(settings)
    except Exception as exc:  # the vendor's own words say what is wrong: key, model name, or endpoint
        print(f"PROBLEM: the call failed: {type(exc).__name__}: {str(exc)[:600]}")
        print("not ready")
        return 1


def _ping(settings) -> int:
    prices = PriceSheet(ROOT / "pricing.yaml")
    client = ModelClient(make_backend(settings.provider, settings), prices)
    messages = [{"role": "user", "content": "Call the echo tool once with the text 'ping'. Do not answer in words."}]
    first = client.create(system="You are a connectivity test.", messages=messages, tools=[ECHO])
    calls = first.tool_calls
    print(f"1. served by {first.model}; stop_reason {first.stop_reason}; "
          f"tool calls {[(c.name, c.input) for c in calls] or 'none'}")
    problems = []
    if not calls:
        problems.append("the model did not call the tool: your agent needs a model with tool calling")
    else:
        messages += [first.assistant_message(), {"role": "user", "content": [tool_result(calls[0], "ping")]}]
        second = client.create(system="You are a connectivity test. Answer in one word.",
                               messages=messages, tools=[ECHO])
        print(f"2. stop_reason {second.stop_reason}; answer {second.text[:60]!r}")
    t = client.totals()
    print(f"tokens: {t['prompt_tokens']} prompt, {t['output_tokens']} output; "
          + (f"cost ${t['cost_usd']:.6f}" if not t["unpriced_calls"] else "cost: UNPRICED"))
    if t["unpriced_calls"]:
        problems.append(f"pricing.yaml has no line for {sorted(t['models'])}: add one, with its source and the date you read it")
    for p in problems:
        print(f"PROBLEM: {p}")
    print("ready" if not problems else "not ready")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
