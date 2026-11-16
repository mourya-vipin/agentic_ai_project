"""The return contract: what a role hands back to the coordinator (Part 3; Lecture 6, slide 13).

Owner: Shruti Tewaney. On the m2-split branch.

Every return is built by our code, validated against agent/split/return_schema.json and against
the trace, and recorded there, accepted or rejected:

    build(role, run_key=, parent=, task=, status=, result=, not_searched=, not_determined=, lines=)
        a return whose `searched` and `spent` come from the role's own trace lines (its tool calls
        and model calls carry its run key), never from what its model says
    problems(ret, lines, budget_left=None)
        every way the return breaks the contract: the schema, then what the trace shows
    accept(ret, lines, budget_left=None) -> bool
        validate and record: `role_return` with valid=True, or valid=False with its problems.
        A rejected return never reaches the coordinator's result.

What a return cannot say is lost for good: the role's reasoning, the order it considered things in,
and anything it read but did not record in `result` (slide 14). The coordinator sees only this.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import runtime
from harness.schema import errors as schema_errors

SCHEMA_PATH = Path(__file__).with_name("return_schema.json")
STATUSES = ("found", "candidate", "out_of_budget")


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _mine(lines: list[dict], run_key: str, kind: str) -> list[dict]:
    return [x for x in lines if x.get("type") == kind and x.get("run_key") == run_key]


def _tokens(line: dict) -> int:
    usage = line.get("usage") or {}
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                 "cache_creation_input_tokens"))


def searched(lines: list[dict], run_key: str) -> list[dict]:
    """The role's tool calls, in order, from the trace: what it searched."""
    return [{"tool": x.get("name"), "args": dict(x.get("input") or {})} for x in _mine(lines, run_key, "tool_call")]


def spent(lines: list[dict], run_key: str) -> dict:
    """What the role spent, from the trace: its tool calls, and its model calls' tokens."""
    return {"tool_calls": len(_mine(lines, run_key, "tool_call")),
            "tokens": sum(_tokens(x) for x in _mine(lines, run_key, "model_call"))}


def build(role: str, *, run_key: str, parent: str, task: str, status: str, result: dict,
          not_searched: list[str] | None = None, not_determined: list[str] | None = None,
          lines: list[dict]) -> dict:
    """A return as our code writes it: what it searched and spent read from the trace."""
    return {"role": role, "run_key": run_key, "parent": parent, "task": task, "status": status,
            "result": dict(result), "searched": searched(lines, run_key), "not_searched": list(not_searched or []),
            "spent": spent(lines, run_key), "not_determined": list(not_determined or [])}


def problems(ret, lines: list[dict], budget_left: int | None = None) -> list[str]:
    """Every way `ret` breaks the contract, one line each; [] when it holds."""
    found = schema_errors(ret, schema())
    if found or not isinstance(ret, dict):
        return found or ["the return is not an object"]
    key = ret["run_key"]
    if key == ret["parent"]:
        found.append("a role's run key is its parent's: the trace could not tell them apart")
    if ret["spent"] != spent(lines, key):
        found.append(f"spent is {ret['spent']}, but the role's trace lines show {spent(lines, key)}: "
                     "it is read from the calls, never from what the model says")
    if ret["searched"] != searched(lines, key):
        found.append("searched does not match the role's tool calls in the trace")
    if any(v < 0 for v in ret["spent"].values()):
        found.append("spent holds a negative count")
    status, nd = ret["status"], ret["not_determined"]
    if status == "found" and nd:
        found.append("status is found, but the return lists what it could not determine: that is a candidate")
    if status == "candidate" and not nd:
        found.append("status is candidate, but it names nothing it could not determine")
    if status == "out_of_budget" and budget_left is not None and budget_left > 0:
        found.append(f"status is out_of_budget, but {budget_left} tokens of the shared budget were left")
    if status != "out_of_budget" and not ret["result"]:
        found.append(f"status is {status}, with an empty result")
    return found


def accept(ret, lines: list[dict], budget_left: int | None = None) -> bool:
    """Validate a return and record it in the trace. False: the coordinator must not use it."""
    wrong = problems(ret, lines, budget_left)
    session = (ret.get("parent") if isinstance(ret, dict) else None) or ""
    session = session.rpartition("#")[0] or None
    fields = {k: ret.get(k) for k in ("role", "run_key", "parent", "status", "spent")} if isinstance(ret, dict) else {}
    runtime.trace(session).event("role_return", valid=not wrong, problems=wrong[:10], **fields)
    return not wrong
