"""The reviser: repairs the plan only when the named checkpoint's check fails, from
step results alone, never changing a step already done (Lecture 2, slide 17).
Owner: Shruti Tewaney. Ported from Shruti's Assignment 2 (agent/plan.py: revise_plan),
which repaired before it replaced: whatever the model writes, a done step comes back
exactly as it was, in its place."""

from __future__ import annotations

import copy
import json

from agent import planner

SAFE_FIELDS = ("id", "tool", "status", "counts", "ids", "protected", "error_class")   # never a result's text


def revise_plan(session_id: str, plan: dict, done_steps: list[int], results: dict, failure: str,
                *, call=None) -> dict:
    """The next version of `plan`. `results` maps a step id to its summary (status,
    counts, record IDs); `failure` is the checkpoint's reason, written by our code."""
    call = call or planner.default_call(session_id)
    body = {"steps": [s for s in plan.get("steps") or []]}
    safe = [{k: r.get(k) for k in SAFE_FIELDS if k in r} for r in results.values()]
    user = (f"The plan:\n{json.dumps(body, indent=1)}\n\n"
            f"Steps done: {done_steps}\n\n"
            f"The results of the steps that ran:\n{json.dumps(safe, indent=1)}\n\n"
            f"Why the plan is being revised: {failure}\n\n"
            f"The tools the plan may use:\n{planner.describe_tools()}")
    response = call("revise", (planner.PROMPTS / "reviser.md").read_text(), [{"role": "user", "content": user}],
                    [planner.plan_tool()])
    meta = {**(plan.get("meta") or {}), "model": response.model, "prompt": "agent/prompts/reviser.md"}
    revised = planner.read_plan(response, meta)
    if not isinstance(revised.get("steps"), list) or not revised["steps"]:
        return revised
    # Repair before replace: the done steps, exactly as they were, first and in order.
    done = [copy.deepcopy(s) for s in plan.get("steps") or [] if s.get("id") in set(done_steps)]
    rest = [s for s in revised["steps"] if isinstance(s, dict) and s.get("id") not in set(done_steps)]
    revised["steps"] = done + rest
    return revised
