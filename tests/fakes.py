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


# -- Shruti Tewaney: plan, critique, detectors ---------------------------------------
