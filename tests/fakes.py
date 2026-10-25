"""Fake models and stand-ins for our tests. Every fake model is marked free = True,
so its runs are tests, not evidence (they go to results/_free/<arm>/).

Each owner keeps their fakes in their own block below, so merges never touch the
same lines.
"""

from __future__ import annotations

import json
import re

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

# One bad argument for each tool a model may call, by canonical name (Part 3, task 5).
BAD_ARGS = {
    "directory_get_employee": {"employee_id": "Priya Nair"},
    "directory_search_employees": {"query": "x" * 200},
    "directory_list_contractors_ended": {"before": "last Friday"},
    "access_list_memberships": {"employee_id": "priya"},
    "catalog_search_groups": {"query": "finance", "system_id": "Finance Reports"},
    "policy_check_access": {"employee_id": "SYN-EMP-0031", "group_id": "payroll admin"},
    "requests_get": {"request_id": "REQ-5"},
    "requests_list_today": {"day": "today"},
    "access_grant": {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-PAYROLL-ADMIN", "request_id": "SYN-REQ-0001"},
    "access_revoke": {"employee_id": "SYN-EMP-0031", "group_id": "jira"},
    "access_revoke_all": {"employee_id": "all contractors"},
    "access_propose_change": {"action": "approve", "employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-PAYROLL-ADMIN"},
    "reply_draft": {"recipient_employee_id": "SYN-EMP-0031", "body": ""},
    "manager_confirmation_draft": {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-NO-SUCH-GROUP"},
    "requests_set_status": {"request_id": "SYN-REQ-0001", "status": "finished"},
}


class BadArgs:
    """Calls each tool it is offered once, in order, with one bad argument, then ends
    its turn: every error goes through the running system and lands in the trace
    with is_error set (Part 3, task 5).

        python run.py --tasks tasks/<task>.yaml --sweeps 1 --arm bad-args --backend tests.fakes:BadArgs
    """
    free = True

    def __init__(self, settings=None):
        pass

    def create(self, *, system, messages, tools):
        from agent.tools.definitions_v1 import ALIASES
        from harness.client import Usage
        done = sum(1 for m in messages if m.get("role") == "assistant")
        offered = [t["name"] for t in tools or []]
        usage = Usage(input_tokens=10, output_tokens=5)
        if done >= len(offered):
            return [{"type": "text", "text": "Done."}], "end_turn", "bad-args", usage
        name = offered[done]
        args = BAD_ARGS.get(ALIASES.get(name, name), {})
        return ([{"type": "tool_use", "id": f"bad_{done:02d}", "name": name, "input": args}], "tool_use",
                "bad-args", usage)


# -- Aman Panigrahi: routing, workflows, loop ----------------------------------------

class RouterFake:
    """Answers the two routing calls (no tools offered) with fixed JSON: the operator
    router's with OPERATOR, the request router's (its prompt asks for groups_mentioned)
    with REQUEST. Every call that offers tools then gets AFTER:

        "done"          says "Done." and ends its turn
        "never_stops"   calls the first tool it is offered, as never-stops does
        "unknown_tool"  calls a tool that does not exist
    """
    free = True
    name = "router-fake"
    OPERATOR = {"intent": "process_requests", "request_ids": []}
    REQUEST = {"intent": "other", "subject": "self", "groups_mentioned": []}
    AFTER = "done"
    DELAY_S = 0.0

    def __init__(self, settings=None):
        self.calls = 0

    def create(self, *, system, messages, tools):
        import time
        from harness.client import Usage
        from harness.scripted import NeverStops
        self.calls += 1
        if self.DELAY_S:
            time.sleep(self.DELAY_S)
        if not tools:
            answer = self.REQUEST if "groups_mentioned" in (system or "") else self.OPERATOR
            return [{"type": "text", "text": json.dumps(answer)}], "end_turn", self.name, Usage(40, 20)
        if self.AFTER == "never_stops":
            return NeverStops().create(system=system, messages=messages, tools=tools)
        if self.AFTER == "unknown_tool":
            return ([{"type": "tool_use", "id": f"toolu_fake_{self.calls:04d}", "name": "teleport_user",
                      "input": {}}], "tool_use", self.name, Usage(40, 10))
        return [{"type": "text", "text": "Done."}], "end_turn", self.name, Usage(40, 2)


class RoutesStandardGrant(RouterFake):
    """t01: the request router reads one group, for the sender."""
    REQUEST = {"intent": "grant", "subject": "self", "groups_mentioned": ["write access to Jira"]}


class RoutesSelfForTeammate(RouterFake):
    """t05, described wrongly: the request router says the access is for the sender."""
    REQUEST = {"intent": "grant", "subject": "self", "groups_mentioned": ["GitHub engineers group"]}


class RoutesToAgent(RouterFake):
    """Processes the requests; the request router says "other", so U4."""


class RoutesToAgentThenNeverStops(RouterFake):
    """Routes the case to the agent path, then never stops."""
    AFTER = "never_stops"


class RoutesToAgentThenSlow(RoutesToAgentThenNeverStops):
    """The same, 0.05 s a call, so a short wall clock comes before the step limit."""
    DELAY_S = 0.05


class RoutesToAgentThenUnknownTool(RouterFake):
    """Routes the case to the agent path, which then calls a tool that does not exist."""
    AFTER = "unknown_tool"


class RoutesQuestion(RouterFake):
    """The operator router says the message is a question."""
    OPERATOR = {"intent": "question", "request_ids": []}


class RoutesBulk(RouterFake):
    """The operator router says the message is a bulk change."""
    OPERATOR = {"intent": "bulk_change", "request_ids": []}


class RoutesBulkThenNeverStops(RoutesBulk):
    AFTER = "never_stops"

class RoutesToAgentThenSetsDone(RouterFake):
    """Routes the case to the agent path; the agent sets the request refused, then stops."""

    def create(self, *, system, messages, tools):
        from harness.client import Usage
        if tools and not any(m["role"] == "assistant" for m in messages):
            self.calls += 1
            return ([{"type": "tool_use", "id": "toolu_fake_status", "name": "update_request",
                      "input": {"request_id": "SYN-REQ-0001", "status": "refused", "note": "test"}}],
                    "tool_use", self.name, Usage(40, 10))
        return super().create(system=system, messages=messages, tools=tools)


# -- Shruti Tewaney: plan, critique, detectors ---------------------------------------

# The fakes below serve every model call of a planned U6 run: the planner and the
# reviser (offered submit_plan, told apart by their prompts) and the executor's fill
# calls (offered the step's one tool). They know t06's people from the base data.

T06_TEXT = ("Remove all access for contractors whose contract ended before today, "
            "and draft a note to each one's manager.")
ANA, VICTOR, MIRA = "SYN-EMP-0161", "SYN-EMP-0162", "SYN-EMP-0163"
HANNAH, GRACE = "SYN-EMP-0060", "SYN-EMP-0040"
LEGAL_HOLD = "SYN-GRP-LEGAL-HOLD"


def plan_step(sid, action, tool, args, depends_on, kind, field="", value=""):
    return {"id": sid, "action": action, "tool": tool, "args_json": json.dumps(args), "depends_on": depends_on,
            "expect": {"kind": kind, "field": field, "value": value}}


def t06_plan(with_hold: bool = False) -> dict:
    """t06's plan as a good planner writes it; with_hold adds the legal-hold proposal
    that only the checkpoint can know is needed."""
    reason = "contract ended"
    steps = [plan_step(1, "list ended contractors", "directory_list_contractors_ended", {"before": "2026-10-21"},
                       [], "count_equals", "contractors", "3")]
    for list_id, person in ((2, ANA), (4, VICTOR), (6, MIRA)):
        steps.append(plan_step(list_id, f"list {person}'s groups", "access_list_memberships",
                               {"employee_id": person}, [1], "non_empty", "memberships"))
        steps.append(plan_step(list_id + 1, f"remove {person}'s access", "access_revoke_all",
                               {"employee_id": person, "reason": reason}, [list_id], "non_empty", "removed"))
    hold = plan_step(10, f"propose removing {VICTOR}'s legal hold", "access_propose_change",
                     {"action": "revoke", "employee_id": VICTOR, "group_id": LEGAL_HOLD, "reason": reason},
                     [5], "record_written")      # after the removal that kept it, as the real reviser wrote
    steps.append(plan_step(8, "note to Hannah Lee", "reply_draft", {"recipient_employee_id": HANNAH, "body": "$fill"},
                           [3, 7], "record_written"))
    steps.append(plan_step(9, "note to Grace Okafor", "reply_draft", {"recipient_employee_id": GRACE, "body": "$fill"},
                           [5, 10] if with_hold else [5], "record_written"))
    if with_hold:
        steps.insert(5, hold)          # after step 5, Victor's removal; before the notes
    return {"steps": steps}


def true_note(request: str) -> str:
    """A note that says exactly what the dependency results show."""
    lines = []
    proposed = {}
    found = re.findall(r"^step \d+ \((\w+)\): (.*)$", request, re.M)
    for tool, data in found:
        if tool == "access_propose_change":
            d = json.loads(data)
            proposed[(d["employee_id"], d["group_id"])] = d["pending_id"]
    for tool, data in found:
        if tool != "access_revoke_all":
            continue
        d = json.loads(data)
        lines.append(f"{d['employee_id']}'s contract has ended: I removed {', '.join(d['removed'])}.")
        for g in d["kept_protected"]:
            pid = proposed.get((d["employee_id"], g))
            lines.append(f"{g} was kept for {d['employee_id']}" + (f"; a proposal to take it away ({pid}) is "
                                                                    "waiting for the operator's approval." if pid else "."))
    return " ".join(lines) + " IT access desk."


class GoodPlanner:
    """Routes the operator's message as a bulk change; plans t06 without the legal-hold
    proposal; revises it in when the checkpoint fails; fills every note truthfully."""
    free = True
    name = "good-planner"
    with_hold = False

    def __init__(self, settings=None):
        self.calls = 0

    def create(self, *, system, messages, tools):
        self.calls += 1
        names = [t["name"] for t in tools or []]
        request = messages[-1]["content"]
        if not names:                  # the operator router's call: this is a bulk change
            from harness.client import Usage
            return ([{"type": "text", "text": json.dumps({"intent": "bulk_change", "request_ids": []})}], "end_turn",
                    self.name, Usage(input_tokens=40, output_tokens=20))
        if "submit_plan" in names:
            body = self.revise(request) if system.startswith("You are the reviser") else self.plan(request)
            return self.use("submit_plan", body)
        return self.fill(names[0], request)

    def use(self, name, args):
        from harness.client import Usage
        return ([{"type": "tool_use", "id": f"fake_{self.calls:03d}", "name": name, "input": args}], "tool_use",
                self.name, Usage(input_tokens=200, output_tokens=50))

    def plan(self, request):
        return t06_plan(with_hold=self.with_hold)

    def revise(self, request):
        return t06_plan(with_hold=True)

    def fill(self, tool, request):
        return self.use(tool, {"body": self.note(request)})

    def note(self, request):
        return true_note(request)


class GoodPlannerWithHold(GoodPlanner):
    """Plans t06 with the legal-hold proposal from the start: no revision is needed."""
    with_hold = True


class BadPlanner(GoodPlanner):
    """Always writes a plan whose first step calls a tool that does not exist."""
    def plan(self, request):
        plan = t06_plan()
        plan["steps"][0]["tool"] = "teleport_user"
        return plan


class BadReviser(GoodPlanner):
    """Revises t06's plan into one where a note depends on a step that never comes."""
    def revise(self, request):
        plan = t06_plan(with_hold=True)
        plan["steps"][-1]["depends_on"] = [5, 99]
        return plan


class ReplacingReviser(GoodPlanner):
    """Revises as gpt-4.1-mini did in our first dev run: Grace's note now depends on the
    new proposal alone, dropping Victor's removal from its depends_on."""
    def revise(self, request):
        plan = t06_plan(with_hold=True)
        plan["steps"][-1]["depends_on"] = [10]
        return plan


class OffPlanFiller(GoodPlanner):
    """Asked to fill a note, calls grant_access instead."""
    def fill(self, tool, request):
        return self.use("grant_access", {"employee_id": HANNAH, "group_id": "SYN-GRP-PAYROLL-ADMIN"})


class EmptyNoteFiller(GoodPlanner):
    """Fills Hannah's note (step 8) with an empty body, every time: a permanent failure."""
    def note(self, request):
        return "" if "This step: 8," in request else true_note(request)


class WrongThenRightFiller(GoodPlanner):
    """Each note's first draft claims a removal that never happened; told why, it
    writes the true note."""
    def note(self, request):
        if "Your last call failed" in request:
            return true_note(request)
        return true_note(request) + " I also removed SYN-GRP-PAYROLL-ADMIN."


class AlwaysWrongFiller(GoodPlanner):
    """Every draft claims a removal that never happened, however often it is told."""
    def note(self, request):
        return true_note(request) + " I also removed SYN-GRP-PAYROLL-ADMIN."

