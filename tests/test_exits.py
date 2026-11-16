"""The three exits for a write in doubt (agent/exits.py), and the runs that a stopped process
left as running (checkpoints.crashed). Owner: Aman Panigrahi. No tokens."""

from __future__ import annotations

import json
from unittest import mock

from agent import checkpoints, escalation, exits, router, workflows
from agent.registry import CallContext, ToolResult
from harness import runtime
from tests.support import ScratchTest

SID = "s-exits"
GRANT = {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-JIRA-WRITE", "request_id": "SYN-REQ-0001"}


class Scripted:
    """A registry that gives the scripted results in order, and keeps each call."""

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def call(self, tool, args, ctx):
        self.calls.append((tool, dict(args)))
        return self.results.pop(0)


def transient(key="k1"):
    return ToolResult("The server did not answer. Tried 3 times.", is_error=True, error_class="transient",
                      retryable=True, key=key)


class ExitsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure(backend="never-stops")
        self.cp = checkpoints.new_run(SID, "nonstandard_request", "the task")
        self.ctx = CallContext(SID, 1, "model", "nonstandard_request", request_id="SYN-REQ-0001",
                               run_key=self.cp.run_key)

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def test_the_journal_finds_the_same_write_by_the_arguments_that_name_its_effect(self):
        checkpoints.record_completed(self.cp.run_key, "k1", tool="access_grant", args=GRANT, result={"status": "granted"})
        found = exits.replayed(self.cp.run_key, "access_grant", {**GRANT, "employee_id": "syn-emp-0031", "note": "x"})
        self.assertEqual(found[0], "k1")
        self.assertIsNone(exits.replayed(self.cp.run_key, "access_grant", {**GRANT, "group_id": "SYN-GRP-OTHER"}))
        self.assertIsNone(exits.replayed(self.cp.run_key, "requests_get", {"request_id": "SYN-REQ-0001"}), "a read")

    def test_exit_one_a_write_that_the_journal_holds_happened_and_is_not_made_again(self):
        checkpoints.record_completed(self.cp.run_key, "k1", tool="access_grant", args=GRANT, result={"status": "granted"})
        registry = Scripted()
        settled = exits.settle("access_grant", GRANT, self.ctx, first=transient(), registry=registry)
        self.assertEqual((settled.outcome, settled.result.data, registry.calls), (exits.HAPPENED, {"status": "granted"}, []))

    def test_exit_two_the_same_call_again_makes_the_write_under_the_same_key(self):
        registry = Scripted(ToolResult('{"status": "granted"}', data={"status": "granted"}, key="k1"))
        settled = exits.settle("access_grant", GRANT, self.ctx, first=transient(), registry=registry)
        self.assertEqual((settled.outcome, registry.calls), (exits.MADE, [("access_grant", GRANT)]))

    def test_a_second_try_that_fails_for_good_is_a_failure(self):
        registry = Scripted(ToolResult("SYN-GRP-JIRA-WRITE is not allowed.", is_error=True, error_class="permanent"))
        self.assertEqual(exits.settle("access_grant", GRANT, self.ctx, registry=registry).outcome, exits.FAILED)

    def test_a_read_of_the_target_decides_when_the_second_try_also_fails(self):
        with mock.patch.object(exits, "confirm", return_value=True):
            settled = exits.settle("access_grant", GRANT, self.ctx, first=transient(), registry=Scripted(transient()))
        self.assertEqual((settled.outcome, settled.result.is_error), (exits.HAPPENED, False))
        with mock.patch.object(exits, "confirm", return_value=False):
            settled = exits.settle("access_grant", GRANT, self.ctx, first=transient(), registry=Scripted(transient()))
        self.assertEqual((settled.outcome, settled.result.is_error), (exits.FAILED, True))

    def test_exit_three_when_no_one_can_tell_the_run_holds_for_the_operator(self):
        with mock.patch.object(exits, "confirm", return_value=None):
            settled = exits.settle("access_grant", GRANT, self.ctx, first=transient(), registry=Scripted(transient()))
        self.assertEqual((settled.outcome, settled.result.end_reason), (exits.UNKNOWN, escalation.ESCALATED))
        packet = escalation.get(settled.packet_id)
        self.assertEqual((packet["signal"], packet["run_key"]), ("ambiguous_write", self.cp.run_key))
        self.assertEqual(checkpoints.load(self.cp.run_key).status, checkpoints.HELD)

    def test_a_write_in_doubt_in_a_workflow_holds_the_workflow(self):
        data = {"requests_get": {"requester_id": "SYN-EMP-0031", "thread_id": "SYN-MSG-X"},
                "directory_get_employee": {"id": "SYN-EMP-0031", "name": "Leo Martins"},
                "requests_set_status": {"status": "in_progress"}}

        class FailsAtTheGrant:
            def call(self, tool, args, ctx):
                return transient() if tool == "access_grant" else ToolResult("{}", data=data[tool])

        with mock.patch.object(workflows, "get_registry", return_value=FailsAtTheGrant()), \
                mock.patch.object(exits, "get_registry", return_value=FailsAtTheGrant()), \
                mock.patch.object(exits, "confirm", return_value=None):
            result = workflows.standard_grant(SID, "SYN-REQ-0001", "SYN-GRP-JIRA-WRITE", run_key=self.cp.run_key)
        self.assertEqual(result.end_reason, escalation.ESCALATED)
        self.assertIn(self.cp.run_key, result.text)


class CrashedRunsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure(backend="never-stops")

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def saved_by_another_process(self, cp):
        with checkpoints._connect(write=True) as conn:
            state = json.loads(conn.execute("SELECT state FROM runs WHERE run_key = ?", (cp.run_key,)).fetchone()["state"])
            state["process"] = "99999-0dead0ff"
            conn.execute("UPDATE runs SET state = ? WHERE run_key = ?", (json.dumps(state), cp.run_key))

    def test_a_running_run_that_another_process_saved_is_crashed(self):
        dead = checkpoints.new_run(SID, "nonstandard_request", "t")
        mine = checkpoints.new_run(SID, "nonstandard_request", "t")
        waits = checkpoints.new_run(SID, "nonstandard_request", "t")
        checkpoints.end_turn(waits, "waiting_reply", waiting_for={"thread_id": "SYN-MAIL-X"})
        for cp in (dead, waits):
            self.saved_by_another_process(cp)
        self.assertEqual([c.run_key for c in checkpoints.crashed(SID)], [dead.run_key])
        self.assertEqual(checkpoints.load(mine.run_key).process, checkpoints.PROCESS)

    def test_the_next_turn_continues_a_crashed_run_and_ends_a_crashed_question(self):
        work = checkpoints.new_run(SID, "nonstandard_request", "t")
        question = checkpoints.new_run(SID, "operator_review", "t")
        for cp in (work, question):
            self.saved_by_another_process(cp)
        with mock.patch.object(checkpoints, "resume", return_value=router.Reply("carried on")) as resume:
            replies = router._carry_on_crashed(SID)
        resume.assert_called_once_with(work.run_key, {"kind": "restart"})
        self.assertEqual(replies[0].text, "carried on")
        self.assertIn("stopped when the process stopped", replies[1].text)
        self.assertEqual(checkpoints.load(question.run_key).findings["end_reason"], router.PROCESS_STOPPED)


if __name__ == "__main__":
    import unittest
    unittest.main()
