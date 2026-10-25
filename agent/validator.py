"""The plan validator, run before any step (Lecture 2, slide 18): every tool exists,
no step depends on a later one, every step has an observable outcome, the step count
is within the plan's limit, and every employee the plan names is one the planner was
given. The orphan check flags rather than rejects: it is agent.detectors.orphan_check.

Owner: Shruti Tewaney. Ported from Shruti's Assignment 2 (agent/validator.py): its four
checks, with the schema check before them, as ways/planned.py did, and rules our
plans need that A2's did not (unique ids, known employee IDs).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.schema import errors as schema_errors

SCHEMA_PATH = Path(__file__).resolve().parent / "plan_schema.json"
KINDS = ("count_equals", "non_empty", "field_equals", "record_written")
EMPLOYEE_ID = re.compile(r"SYN-EMP-\d{4}", re.IGNORECASE)

# The plan's own step limit for a bulk change over n people: one listing step, then up
# to four per person (list their groups, remove them, a proposal for a protected group,
# their manager's note). Capped at five people's worth: a larger instruction should be
# split by the operator, so each plan stays small enough to read and to check.
PER_PERSON = 4
MAX_PEOPLE = 5


def plan_step_limit(people: int) -> int:
    return 1 + PER_PERSON * min(max(people, 1), MAX_PEOPLE)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def step_args(step: dict) -> dict:
    """A step's arguments as a dict; raises ValueError if args_json is not a JSON object."""
    value = json.loads(step.get("args_json") or "{}")
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def validate(plan: dict, tool_names: list[str], max_steps: int,
             known_ids: set[str] | None = None) -> list[tuple[int | None, str, str]]:
    """Every problem as (step_id, rule, message); an empty list means valid. `known_ids`
    are the employee IDs the planner was given; None skips that rule."""
    body = {k: v for k, v in (plan or {}).items() if k != "meta"}
    if (plan or {}).get("meta", {}).get("error"):
        return [(None, "schema", plan["meta"]["error"])]
    found = [(None, "schema", e) for e in schema_errors(body, load_schema())]
    if found:
        return found
    steps = body["steps"]
    problems: list[tuple[int | None, str, str]] = []
    tools = set(tool_names)
    seen: set[int] = set()
    known = {i.upper() for i in known_ids} if known_ids is not None else None
    for step in steps:
        sid = step["id"]
        if sid in seen:
            problems.append((sid, "unique_ids", f"step id {sid} is used twice; give every step its own id"))
        # 1. every tool named exists
        if step["tool"] not in tools:
            problems.append((sid, "tool_exists", f"step {sid} calls {step['tool']!r}, which is not a tool of this "
                                                 f"plan; use one of {', '.join(sorted(tools))}"))
        # 2. no step depends on a later step (or itself, or a missing one)
        late = [d for d in step["depends_on"] if d not in seen]
        if late:
            problems.append((sid, "depends_earlier", f"step {sid} depends on {late}, which do not come before it"))
        # 3. an observable outcome
        expect = step["expect"]
        if expect["kind"] not in KINDS:
            problems.append((sid, "observable_outcome", f"step {sid}'s expect.kind is {expect['kind']!r}; use one "
                                                        f"of {', '.join(KINDS)}"))
        elif expect["kind"] != "record_written" and not expect["field"].strip():
            problems.append((sid, "observable_outcome", f"step {sid}'s expect names no field of the result to read"))
        elif expect["kind"] == "count_equals" and not expect["value"].strip().isdigit():
            problems.append((sid, "observable_outcome", f"step {sid}'s count_equals value {expect['value']!r} is "
                                                        "not a whole number"))
        # the arguments: a JSON object, naming only employees the planner was given
        try:
            args = step_args(step)
        except (ValueError, TypeError) as exc:
            problems.append((sid, "args", f"step {sid}'s args_json is not a JSON object ({exc})"))
            args = {}
        if known is not None:
            strangers = sorted({m.upper() for m in EMPLOYEE_ID.findall(json.dumps(args))} - known)
            if strangers:
                problems.append((sid, "known_ids", f"step {sid} names {', '.join(strangers)}, who are not among the "
                                                   "employees you were given"))
        seen.add(sid)
    # 4. the step count is within the plan's limit
    if not steps or len(steps) > max_steps:
        problems.append((None, "step_limit", f"the plan has {len(steps)} steps; it needs at least 1 and at most "
                                             f"{max_steps}"))
    return problems


def messages(problems: list[tuple[int | None, str, str]]) -> list[str]:
    """The problems as lines our code wrote, for the planner's next try."""
    return [message for _, _, message in problems]
