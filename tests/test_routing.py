"""Routing: a case of each use case reaches its path, the case that could be misrouted
does not take the workflow, and each run's end reasons are recorded (Part 1, task 5).
On fake models. Owner: Aman Panigrahi."""

from __future__ import annotations

from agent import router
from sandbox import requests
from tests.aman_support import TaskRunTest

REQ = "SYN-REQ-0001"


class EachUseCaseReachesItsPath(TaskRunTest):
    def test_u1_a_standard_grant_takes_the_workflow(self):
        record = self.run_on("t01_standard_jira", "RoutesStandardGrant")
        self.assertEqual(requests.get(REQ)["route"], "workflow:standard_grant")
        self.assertEqual([r["route"] for r in self.routes("request")], ["workflow:standard_grant"])
        self.assertEqual(record["end_reasons"], ["workflow_done"])
        self.assertTrue(record["passed"], record["check"]["reason"])

    def test_u2_a_new_starter_takes_the_workflow(self):
        record = self.run_on("t02_new_starter", "RoutesToAgent")
        self.assertEqual(requests.get(REQ)["route"], "workflow:onboarding")
        self.assertEqual([m["purpose"] for m in self.of("model_call")], ["route_operator"],
                         "the HR feed is parsed in code: no request-routing call")
        self.assertEqual(record["end_reasons"], ["workflow_done"])
        self.assertTrue(record["passed"], record["check"]["reason"])

    def test_u3_an_ended_contract_takes_the_workflow(self):
        record = self.run_on("t03_contractor_left", "RoutesToAgent")
        self.assertEqual(requests.get(REQ)["route"], "workflow:offboarding")
        self.assertEqual(record["end_reasons"], ["workflow_done"])
        self.assertTrue(record["passed"], record["check"]["reason"])

    def test_u4_a_same_access_request_takes_the_agent_path(self):
        record = self.run_on("t04_same_access_as", "RoutesToAgent")
        self.assertEqual(requests.get(REQ)["route"], "agent:nonstandard_request")
        self.assertEqual([b["use_case"] for b in self.of("bound")][0], "nonstandard_request")
        self.assertEqual(record["end_reasons"][0], "done")
        self.assertFalse(record["passed"], "an agent that says Done and does nothing fails the check")

    def test_u5_a_question_takes_the_read_only_loop(self):
        record = self.run_on("t07_unusual_review", "RoutesQuestion")
        self.assertEqual([r["intent"] for r in self.routes("operator")], ["question"])
        self.assertEqual([b["use_case"] for b in self.of("bound")], ["operator_review"])
        self.assertEqual(record["end_reasons"], ["answered"])
        self.assertEqual(record["measures"]["writes"], 0)
        self.assertTrue(all(requests.get(r["id"])["route"] is None for r in requests.all_requests()),
                        "a question routes no request")

    def test_u6_a_bulk_change_takes_the_planned_executor_when_its_plan_is_on(self):
        record = self.run_on("t06_bulk_ended_contractors", "RoutesBulk", overrides=["plan.bulk_change=on"])
        self.assertEqual([r["intent"] for r in self.routes("operator")], ["bulk_change"])
        self.assertEqual(record["end_reasons"], ["plan_rejected"], "the fake writes no plan, so it is rejected")

    def test_u6_a_bulk_change_takes_the_loop_when_its_plan_is_off(self):
        self.run_on("t06_bulk_ended_contractors", "RoutesBulk", overrides=["plan.bulk_change=off"])
        self.assertEqual([b["use_case"] for b in self.of("bound")], ["bulk_change"])


class OperatorTurnsReadTheirControlSwitch(TaskRunTest):
    """U5 and U6 have only the agent path. Their switches are read on every turn, and a
    setting other than agent stops the turn before the loop or the planner runs.
    switches.yaml allows only agent, so the setting is patched where the router reads it."""

    def run_with(self, task: str, backend: str, name: str, value: str) -> dict:
        from unittest import mock
        from harness import runtime
        real = runtime.switch
        read = []

        def switch(key, *default):
            read.append(key)
            return value if key == name else real(key, *default)
        with mock.patch.object(router.runtime, "switch", side_effect=switch):
            record = self.run_on(task, backend)
        self.assertIn(name, read)
        return record

    def test_each_operator_use_case_reads_its_switch(self):
        for task, backend, name in (("t07_unusual_review", "RoutesQuestion", "control.operator_review"),
                                    ("t06_bulk_ended_contractors", "RoutesBulk", "control.bulk_change")):
            with self.subTest(name=name):
                record = self.run_with(task, backend, name, "agent")
                self.assertIsNone(record["error"])

    def test_a_setting_other_than_agent_stops_the_turn(self):
        for task, backend, name in (("t07_unusual_review", "RoutesQuestion", "control.operator_review"),
                                    ("t06_bulk_ended_contractors", "RoutesBulk", "control.bulk_change")):
            with self.subTest(name=name):
                record = self.run_with(task, backend, name, "workflow")
                self.assertIn(f"{name} is 'workflow'", record["error"])
                self.assertEqual(self.of("bound"), [], "no loop ran")
                self.assertEqual([m["purpose"] for m in self.of("model_call")], ["route_operator"])


class TheCaseThatCouldBeMisrouted(TaskRunTest):
    def test_t05_goes_to_the_agent_even_when_the_router_says_self(self):
        """t05 names one group Dev Rao's role allows; the routing call wrongly says the
        access is for the sender. Our code finds Dev Rao in the text and sends it to U4."""
        self.run_on("t05_on_behalf", "RoutesSelfForTeammate")
        self.assertEqual(requests.get(REQ)["route"], "agent:nonstandard_request")
        why = self.routes("request")[0]["why"]
        self.assertTrue(any("SYN-EMP-0052" in w for w in why), why)

    def test_a_routing_answer_that_does_not_parse_is_a_question(self):
        self.run_on("t01_standard_jira", "never-stops")
        self.assertEqual([r["intent"] for r in self.routes("operator")], ["question"])
        self.assertIsNone(requests.get(REQ)["route"], "the read-only path routes nothing")


class Decide(TaskRunTest):
    """The conditions for U1, one at a time, on t01's case (Arjun Mehta, Jira editors)."""

    def setUp(self):
        super().setUp()
        self.run_on("t01_standard_jira", "RoutesToAgent")      # a seeded sandbox to read
        self.request = requests.get(REQ)
        from sandbox import directory
        self.arjun = directory.get_employee("SYN-EMP-0007")

    def route(self, **answer):
        base = {"intent": "grant", "subject": "self", "groups_mentioned": ["write access to Jira"]}
        return router.decide(self.request, self.arjun, {**base, **answer})

    def test_every_condition_holds(self):
        found = self.route()
        self.assertEqual((found.use_case, found.group_id), ("standard_grant", "SYN-GRP-JIRA-WRITE"))

    def test_each_failing_condition_sends_it_to_the_agent(self):
        for answer in ({"intent": "remove"}, {"subject": "Dev Rao"}, {"groups_mentioned": []},
                       {"groups_mentioned": ["Jira", "GitHub"]}, {"groups_mentioned": ["Jira"]},
                       {"groups_mentioned": ["admin on AWS production"]}, {"groups_mentioned": ["Payroll admin"]},
                       {"groups_mentioned": ["Confluence write"]}):
            with self.subTest(answer=answer):
                self.assertEqual(self.route(**answer).use_case, "nonstandard_request")

    def test_the_subject_may_be_the_requesters_own_name_or_id(self):
        for subject in ("Arjun Mehta", "SYN-EMP-0007", "me"):
            with self.subTest(subject=subject):
                self.assertEqual(self.route(subject=subject).use_case, "standard_grant")

    def test_groups_resolve_by_id_name_or_system_and_level(self):
        self.assertEqual(router.resolve_group("SYN-GRP-DWH-READ"), ["SYN-GRP-DWH-READ"])
        self.assertEqual(router.resolve_group("the GitHub engineers group"), ["SYN-GRP-GITHUB-ENG"])
        self.assertEqual(router.resolve_group("Salesforce read-only"), ["SYN-GRP-SALESFORCE-READ"])
        self.assertEqual(len(router.resolve_group("Confluence write")), 2, "two Confluence write groups: "
                         "ambiguous, so decide() sends it to the agent")


def task_file(folder, task_id: str, thread: str, sender: str, text: str, check: str) -> str:
    """A one-request test task of a test's own, in its scratch folder (never in tasks/)."""
    import yaml
    path = folder / f"{task_id}.yaml"
    path.write_text(yaml.safe_dump({"id": task_id, "operator": ["Please handle the new access requests."],
                                    "outside": [], "check": check,
                                    "input": {"kind": "request", "thread": thread, "from": sender, "text": text}}))
    return str(path)


class Refusals(TaskRunTest):
    """A workflow that finds a case it must not handle refuses it and leaves it for the
    operator; an HR message our code cannot parse goes to the agent, and the operator is told."""

    def run_file(self, path: str, backend: str = "RoutesToAgent") -> dict:
        self.record = self.run_task(path, "aman", backend=f"tests.fakes:{backend}")
        from harness.trace import read_trace
        self.lines = read_trace(next((self.tmp / "results").rglob(f"{self.record['run_id']}.trace.jsonl")))
        return self.record

    def test_a_standard_grant_the_policy_refuses_is_left_for_the_operator(self):
        """The policy can change between routing and the grant: here the workflow is handed
        a group that needs the manager. Nothing is granted, the requester is told it waits
        for review, and the request is pending_operator."""
        import channel
        from agent import workflows
        from checks.common import granted
        from tests.support import fixed_run
        with fixed_run("tasks/t01_standard_jira.yaml", "refuse-u1", backend="tests.fakes:RoutesToAgent") as run:
            result = workflows.standard_grant(run.run_id, REQ, "SYN-GRP-AWSPROD-ADMIN")
            self.assertEqual(result.end_reason, "workflow_refused")
            self.assertEqual(requests.get(REQ)["status"], "pending_operator")
            self.assertEqual(granted(run), [])
            sent = [m.text for m in channel.messages("SYN-MSG-T01", run_id=run.run_id) if not m.seeded]
            self.assertEqual(len(sent), 1)
            self.assertIn("needs a review", sent[0], "the pending_review notice")
            self.assertIn("left for you", result.text)

    def test_onboarding_refuses_someone_who_is_not_a_new_starter(self):
        path = task_file(self.tmp, "refuse_u2", "SYN-HR-X02", "hr-system@syn-corp.example",
                         "SYNTHETIC. EVENT: new_starter; EMPLOYEE: SYN-EMP-0031; ROLE: data_analyst; START: 2026-10-26",
                         "checks/t02_new_starter.py")
        record = self.run_file(path)
        self.assertEqual(record["end_reasons"], ["workflow_refused"])
        self.assertEqual(requests.get(REQ)["status"], "pending_operator")
        self.assertEqual(record["measures"]["grants"], 0)
        self.assertEqual(record["measures"]["notices"], 0)
        self.assertIn("not a new starter", record["replies"][0]["text"])

    def test_an_hr_message_that_does_not_parse_goes_to_the_agent_and_the_operator_is_told(self):
        path = task_file(self.tmp, "hr_garbled", "SYN-HR-X03", "hr-system@syn-corp.example",
                         "SYNTHETIC. Someone new is starting next week, details to follow.",
                         "checks/t02_new_starter.py")
        record = self.run_file(path)
        self.assertEqual(requests.get(REQ)["route"], "agent:nonstandard_request")
        self.assertEqual([m["purpose"] for m in self.of("model_call")][:1], ["route_operator"],
                         "no request-routing call reads the HR feed's text")
        self.assertIn("HR feed message did not parse", record["replies"][0]["text"])


class UnfinishedAgentCases(TaskRunTest):
    def test_a_request_the_agent_stopped_on_is_left_for_the_operator_and_not_run_again(self):
        """t04 has two operator turns, and this fake routes both as "process the requests"."""
        record = self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        self.assertEqual(record["end_reasons"][0], "step_limit")
        self.assertEqual(requests.get(REQ)["status"], "pending_operator")
        self.assertIn("left for you", record["replies"][0]["text"])
        self.assertEqual(len(self.of("bound")), 1, "the second turn did not run the loop again")
        self.assertEqual(record["replies"][1]["text"], "There are no new requests.")

    def test_a_request_the_agent_finished_keeps_its_status(self):
        """A fake that sets the request's status itself, then stops."""
        record = self.run_on("t05_on_behalf", "RoutesToAgentThenSetsDone")
        self.assertEqual(requests.get(REQ)["status"], "refused")
        self.assertNotIn("left for you", record["replies"][0]["text"])
