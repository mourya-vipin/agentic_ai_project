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
    "policy_check_access": {"employee_id": "SYN-EMP-0031", "group_ids": ["payroll admin"]},
    "requests_get": {"request_id": "REQ-5"},
    "requests_list_today": {"day": "today"},
    "access_grant": {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-PAYROLL-ADMIN", "request_id": "SYN-REQ-0001"},
    "access_revoke": {"employee_id": "SYN-EMP-0031", "group_id": "jira"},
    "access_revoke_all": {"employee_id": "all contractors"},
    "access_propose_change": {"action": "approve", "employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-PAYROLL-ADMIN"},
    "reply_draft": {"recipient_employee_id": "SYN-EMP-0031", "body": ""},
    "manager_confirmation_draft": {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-NO-SUCH-GROUP"},
    "requests_set_status": {"request_id": "SYN-REQ-0001", "status": "finished"},
    "channel_ask_template": {"template": "please_confirm", "employee_id": "SYN-EMP-0031",
                             "group_id": "SYN-GRP-FINREP-WRITE"},
    "reply_send": {"recipient_employee_id": "Leo Martins", "body": "Hi Leo."},
    "tools_search": {"query": ""},
    "code_run": {"program": ""},
}


class BadArgs:
    """Calls each tool it is offered once, in order, with one bad argument, then ends
    its turn: every error goes through the running system and lands in the trace
    with is_error set (Part 3, task 5).

        python run.py --tasks tasks/t04_same_access_as.yaml tasks/t06_bulk_ended_contractors.yaml --sweeps 1 \
            --arm bad-args --backend tests.fakes:BadArgs --set plan.bulk_change=off

    It answers a routing call (no tools offered) by sending the case to the agent path,
    so t04 reaches every tool of a non-standard request, and t06 on the loop the two
    bulk-change tools besides.
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
        if not offered:             # a routing call: send the case down the agent path, to reach the write tools
            asked = json.dumps(messages).lower()
            intent = "bulk_change" if "contract" in asked and "remove all" in asked else "process_requests"
            return [{"type": "text", "text": json.dumps({"intent": intent, "request_ids": []})}], "end_turn", \
                "bad-args", usage
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


class FakeCheckpoints:
    """An in-memory stand-in for agent/checkpoints.py (C3.1). It has the same functions
    and writes no file. It also keeps each save with its mode (`saves`) and each resume
    with its event (`resumed`), so that a test can read them.

        with FakeCheckpoints().installed() as fake:
            ...            # code that imports agent.checkpoints gets the fake

    resume() calls on_resume(cp, event) if the test gives one. If not, it returns a
    Reply that names the run. The constants (RUNNING, DONE, ...) come from the real module."""

    def __init__(self, on_resume=None):
        from agent import checkpoints
        self._real = checkpoints
        self.runs: dict = {}
        self.saves: list[tuple[str, str, str]] = []     # (run_key, mode, status)
        self.resumed: list[tuple[str, dict]] = []       # (run_key, event)
        self.on_resume = on_resume

    def __getattr__(self, name):
        return getattr(self._real, name)

    def installed(self):
        """Put the fake in place of agent.checkpoints until the block ends."""
        import contextlib
        import sys
        from unittest import mock

        import agent

        @contextlib.contextmanager
        def block():
            with mock.patch.dict(sys.modules, {"agent.checkpoints": self}), \
                    mock.patch.object(agent, "checkpoints", self, create=True):
                yield self
        return block()

    def _copy(self, cp):
        import copy
        return copy.deepcopy(cp)

    def new_run(self, session_id, use_case, task):
        run_no = 1 + max((cp.run_no for cp in self.runs.values() if cp.session_id == session_id), default=0)
        cp = self._real.Checkpoint(self._real.key_of(session_id, run_no), session_id, run_no, use_case,
                                   self._real.RUNNING, task)
        self.runs[cp.run_key] = self._copy(cp)
        return cp

    def save(self, cp, *, mode):
        self._real._check(cp, mode)
        self.saves.append((cp.run_key, mode, cp.status))
        if mode == "exit" and cp.status == self._real.RUNNING:
            return
        old = self.runs.get(cp.run_key)
        kept = self._copy(cp)
        kept.completed = {**(old.completed if old else {}), **(cp.completed or {})}
        self.runs[cp.run_key] = kept

    def load(self, run_key):
        cp = self.runs.get(run_key)
        return self._copy(cp) if cp else None

    def live(self, session_id):
        return [self._copy(cp) for cp in sorted(self.runs.values(), key=lambda c: c.run_no)
                if cp.session_id == session_id and cp.status not in self._real.ENDED]

    def waiting(self, kind=None):
        return [self._copy(cp) for cp in self.runs.values() if cp.waiting_for and cp.status not in self._real.ENDED
                and (kind is None or cp.waiting_for.get("kind") == kind)]

    def journaled(self, run_key, key):
        cp = self.runs.get(run_key)
        return (cp.completed or {}).get(key) if cp else None

    def record_completed(self, run_key, key, *, tool, args, result):
        self.runs[run_key].completed.setdefault(key, {"tool": tool, "args": args, "result": result, "at": "fake"})

    def resume(self, run_key, event):
        from harness.turns import Reply
        self.resumed.append((run_key, event))
        if self.on_resume is not None:
            return self.on_resume(self.load(run_key), event)
        return Reply(f"resumed {run_key}")

    def flush(self):
        pass

    def reset(self):
        self.runs.clear()


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
    steps.append(plan_step(8, "note to Hannah Lee", "reply_send", {"recipient_employee_id": HANNAH, "body": "$fill"},
                           [3, 7], "field_equals", "status", "sent"))
    steps.append(plan_step(9, "note to Grace Okafor", "reply_send", {"recipient_employee_id": GRACE, "body": "$fill"},
                           [5, 10] if with_hold else [5], "field_equals", "status", "sent"))
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


def approve_all(approved: bool = True, limit: int = 10) -> list[str]:
    """The scripted operator of a test that runs the executor directly: answer every approval
    waiting, oldest first, as run.py's approvals: list does, until none is left. Returns the IDs."""
    from agent import approvals
    answered = []
    while (waiting := approvals.pending()) and len(answered) < limit:
        answered.append(waiting[0]["id"])
        approvals.decide(waiting[0]["id"], approved, "scripted answer in a test", who="scripted")
    return answered


class GoodPlannerWithHold(GoodPlanner):
    """Plans t06 with the legal-hold proposal from the start: no revision is needed."""
    with_hold = True


class BadPlanner(GoodPlanner):
    """Always writes a plan whose first step calls a tool that does not exist."""
    def plan(self, request):
        plan = t06_plan()
        plan["steps"][0]["tool"] = "teleport_user"
        return plan


class DecliningPlanner(GoodPlanner):
    """Submits a plan with no steps, as the planner's prompt asks for an instruction that
    is not a removal of ended contractors' access."""
    def plan(self, request):
        return {"steps": []}


class NoPlanCall(GoodPlanner):
    """Answers the planner in text, with no submit_plan call: a bad plan, not a decline."""
    def create(self, *, system, messages, tools):
        if "submit_plan" in [t["name"] for t in tools or []]:
            from harness.client import Usage
            return ([{"type": "text", "text": "Here is my plan: remove everyone's access."}], "end_turn", self.name,
                    Usage(input_tokens=200, output_tokens=20))
        return super().create(system=system, messages=messages, tools=tools)


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

