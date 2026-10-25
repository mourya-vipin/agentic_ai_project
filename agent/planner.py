"""The planner for U6 (bulk_change): it writes the whole plan and stops (Lecture 2,
slide 16). It sees the operator's instruction, the tool list, and record IDs and
counts from the sandbox, never a stranger's text.

Owner: Shruti Tewaney. Ported from Shruti's Assignment 2 (agent/plan.py: make_plan,
read_plan, the submit_plan tool compiled strict), with our context in place of A2's
issue number.

    context = build_context(operator_text)
    plan = make_plan(session_id, operator_text, context, call=executor_model_call)

The plan comes back as data, {"meta": {...}, "steps": [...]}; a reply with no
submit_plan call comes back with no steps and meta.error set, so the validator
rejects it like any other bad plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent import validator
from agent.registry import get_registry
from harness import runtime
from sandbox import access, clock, directory

PROMPTS = Path(__file__).resolve().parent / "prompts"
PLAN_TOOL = "submit_plan"
FILL = "$fill"
EMPLOYEE_ID = re.compile(r"SYN-EMP-\d{4}", re.IGNORECASE)

# The tools a U6 plan may use, by canonical name. No grant: a bulk change here removes
# access, writes proposals and drafts notes.
U6_TOOLS = ("directory_list_contractors_ended", "directory_get_employee", "access_list_memberships",
            "access_revoke", "access_revoke_all", "access_propose_change", "reply_draft")


def plan_tool() -> dict:
    """The one tool the planner and the reviser are offered: its input is a plan."""
    return {"name": PLAN_TOOL, "description": "Submit the whole plan: the steps to run, in order.",
            "input_schema": validator.load_schema(), "strict": True}


def describe_tools(names=U6_TOOLS) -> str:
    """The tool list as text: canonical names, one-line descriptions and argument names."""
    registry = get_registry()
    lines = []
    for definition in registry.definitions(list(names)):
        canon = registry.resolve(definition["name"]) or definition["name"]
        schema = definition.get("input_schema") or {}
        args = ", ".join(f"{a}{'' if a in (schema.get('required') or []) else ' (optional)'}"
                         for a in schema.get("properties") or {})
        lines.append(f"- {canon}: {definition.get('description', '').splitlines()[0]} Arguments: {args or 'none'}.")
    return "\n".join(sorted(lines))


def build_context(operator_text: str) -> dict:
    """What the planner may know, read by our code: today's date, the contractors whose
    contract ended before today, any employee the operator named by ID, each one's
    manager and count of active groups. IDs and counts only; no group names, no text."""
    today = clock.today()
    ended = [p["id"] for p in directory.contractors_ended(today)]
    named = [i.upper() for i in EMPLOYEE_ID.findall(operator_text or "")]
    people = list(dict.fromkeys(ended + [i for i in named if directory.get_employee(i)]))
    managers = {}
    for person_id in people:
        manager = directory.manager_of(person_id)
        managers[person_id] = manager["id"] if manager else None
    counts = {person_id: len(access.memberships(person_id)) for person_id in people}
    known = set(people) | {m for m in managers.values() if m}
    return {"today": today, "contractors_ended_before_today": ended, "named_by_operator": named,
            "managers": managers, "active_membership_counts": counts,
            "step_limit": validator.plan_step_limit(len(people)), "known_ids": sorted(known)}


def read_plan(response, meta: dict) -> dict:
    for call in response.tool_calls:
        if call.name == PLAN_TOOL:
            return {"meta": meta, **(call.input or {})}
    return {"meta": {**meta, "error": f"no {PLAN_TOOL} call: {response.text[:200]}"}, "steps": []}


def default_call(session_id: str):
    """A model call traced with its purpose, for a planner used outside the executor."""
    def call(purpose, system, messages, tools):
        response = runtime.client(session_id).create(system=system, messages=messages, tools=tools)
        runtime.trace(session_id).model_call(0, response, purpose=purpose)
        return response
    return call


def make_plan(session_id: str, operator_text: str, context: dict, feedback: list[str] | None = None,
              *, call=None) -> dict:
    """`feedback`, on a re-plan: why the last plan was refused, as lines our validator
    wrote. `call(purpose, system, messages, tools)` makes the model call; the executor
    passes its own, which checks the bound first."""
    call = call or default_call(session_id)
    facts = {k: v for k, v in context.items() if k != "known_ids"}
    user = (f"The operator's instruction:\n{operator_text}\n\n"
            f"Facts from the identity store:\n{json.dumps(facts, indent=1)}\n\n"
            f"The plan's step limit: {context['step_limit']} steps.\n\n"
            f"The tools the plan may use:\n{describe_tools()}")
    if feedback:
        user += ("\n\nYour previous plan was rejected, for these reasons. Write a new plan that fixes them:\n"
                 + "\n".join(f"- {line}" for line in feedback))
    response = call("plan", (PROMPTS / "planner.md").read_text(), [{"role": "user", "content": user}], [plan_tool()])
    return read_plan(response, {"model": response.model, "prompt": "agent/prompts/planner.md",
                                "use_case": "bulk_change", "tools": list(U6_TOOLS)})
