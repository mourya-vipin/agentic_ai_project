"""The save after each step and the resume of the workflows and the loop.
Owner: Aman Panigrahi. No tokens: every model here is a fake (free = True), and a test that
can call a model names its fake.

    the save after each step   the loop saves its messages and a record of each call; a
                               workflow saves a record of each call; a status that the gate
                               saves during a step stays
    the resume                 after an approval (and a denial), /resume after a stop, a
                               stranger's reply (also one that comes during a stop), and the
                               operator's answer to an escalation
"""

from __future__ import annotations

from unittest import mock

import channel
from agent import approvals, checkpoints, control, disclosure, escalation, loop, workflows
from agent.registry import AWAITING_APPROVAL, ToolResult
from harness import runtime, turns
from harness.client import Usage
from harness.trace import read_trace
from tests import fakes
from tests.aman_support import TaskRunTest
from tests.support import ScratchTest, fixed_run

T04, T05 = "tasks/t04_same_access_as.yaml", "tasks/t05_on_behalf.yaml"
THREAD = "SYN-MAIL-SYN-EMP-0002"
LEO, LEO_THREAD = "SYN-EMP-0031", "SYN-MSG-T04"          # t04's requester and the thread of SYN-REQ-0001
ASK = {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_ask", "name": "channel_ask_template",
                                         "input": {"template": "manager_confirm"}}]}
ASKED = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_ask",
                                      "content": '{"thread_id": "SYN-MAIL-SYN-EMP-0002", "status": "sent"}'}]}


class RecordsTheCall(fakes.RouterFake):
    """Routes the case to U4 and says "Done." to each call with tools, and keeps the messages
    of each such call, so that a test can read what the resumed loop sent."""
    seen: list = []

    def create(self, *, system, messages, tools):
        if tools:
            RecordsTheCall.seen.append(messages)
        return super().create(system=system, messages=messages, tools=tools)


class RepliesToLeo(fakes.RouterFake):
    """Routes the case to U4, where the model's first step is a reply to the requester. The
    gate holds it for the operator (reply_send/model_text)."""

    def create(self, *, system, messages, tools):
        if tools and not any(m["role"] == "assistant" for m in messages):
            body = "SYNTHETIC. Hi Leo, your request is with the IT team."
            return ([{"type": "tool_use", "id": "toolu_reply", "name": "reply_send",
                      "input": {"recipient_employee_id": LEO, "request_id": "SYN-REQ-0001", "body": body}}],
                    "tool_use", self.name, Usage(40, 10))
        return super().create(system=system, messages=messages, tools=tools)


def last_text(messages: list[dict]) -> str:
    """The text of the last user turn of a call, all of its blocks."""
    content = messages[-1]["content"]
    return content if isinstance(content, str) else " ".join(str(b.get("text") or b.get("content") or "")
                                                             for b in content)


class TheSaveAfterEachStep(TaskRunTest):
    def saved(self, n: int = 1):
        return checkpoints.load(f"{self.record['run_id']}#{n}")

    def test_the_loop_saves_its_messages_and_a_record_of_each_call(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenSetsDone")
        cp = self.saved()
        self.assertEqual(cp.current_step, 1)
        self.assertEqual(len(cp.messages), 3, "the task, the model's call, and its result")
        call = cp.steps[0]["calls"][0]
        self.assertEqual((call["id"], call["tool"], call["args"]["status"]), ("toolu_fake_status", "requests_set_status",
                                                                              "refused"))

    def test_a_workflow_saves_a_record_of_each_call(self):
        self.run_on("t01_standard_jira", "RoutesStandardGrant")
        self.assertEqual([s["tool"] for s in self.saved().steps],
                         ["requests_get", "directory_get_employee", "requests_set_status", "access_grant",
                          "notices_send_template", "requests_set_status"])

    def test_the_trace_holds_the_count_of_the_messages_not_the_messages(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenSetsDone")
        self.assertEqual(self.of("checkpoint_final")[-1]["checkpoint"]["messages"], 3)


class TheStatusThatTheGateSaves(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure(backend="never-stops")

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def test_a_step_does_not_write_running_over_a_wait(self):
        mine = checkpoints.new_run("s-step", "nonstandard_request", "the task")
        gate = checkpoints.load(mine.run_key)
        gate.status, gate.waiting_for = checkpoints.WAITING_APPROVAL, {"kind": "approval", "approval_id": "APR-0001"}
        checkpoints.save(gate, mode="sync")
        checkpoints.save_step(mine, {"n": 1, "calls": []}, messages=[{"role": "user", "content": "the task"}])
        saved = checkpoints.load(mine.run_key)
        self.assertEqual((saved.status, saved.waiting_for["approval_id"], saved.current_step, len(saved.messages)),
                         ("waiting_approval", "APR-0001", 1, 1))

    def test_a_workflow_step_that_the_gate_holds_ends_the_workflow_awaiting_approval(self):
        data = {"requests_get": {"requester_id": "SYN-EMP-0002", "thread_id": "SYN-MSG-X"},
                "directory_get_employee": {"id": "SYN-EMP-0002", "name": "Ann Lee"},
                "requests_set_status": {"status": "in_progress"}}

        class GateAtTheGrant:
            def call(self, tool, args, ctx):
                if tool == "access_grant":
                    return ToolResult("Waiting for the operator's approval APR-0001.", end_reason=AWAITING_APPROVAL)
                return ToolResult("{}", data=data[tool])

        with mock.patch.object(workflows, "get_registry", return_value=GateAtTheGrant()):
            result = workflows.standard_grant("s-step", "SYN-REQ-0001", "SYN-GRP-JIRA-WRITE")
        self.assertEqual((result.end_reason, result.steps), (AWAITING_APPROVAL, 4))
        self.assertIn("waits for your approval", result.text)


class TheResume(ScratchTest):
    """End to end, in a run as run.py makes it: the real registry, hook, gate and MCP server."""

    def setUp(self):
        super().setUp()
        RecordsTheCall.seen = []

    def trace(self, run) -> list[dict]:
        return read_trace(run.trace_path)

    def test_an_approval_makes_the_waiting_call_with_its_saved_arguments_and_the_run_ends_done(self):
        rid = "resume-approval"
        with fixed_run(T04, rid, backend="tests.test_resume:RepliesToLeo") as run:
            first = turns.operator_turn(rid, "Please handle the new requests.")
            waiting = approvals.pending()
            self.assertEqual(checkpoints.load(f"{rid}#1").status, "waiting_approval", first.text)
            answer = turns.decide_approval(waiting[0]["id"], True)
            lines = self.trace(run)
            sent = [m for m in channel.messages(LEO_THREAD, rid) if m.sender == channel.AGENT]
        sends = [x for x in lines if x["type"] == "tool_call" and x["name"] == "reply_send"]
        self.assertEqual(len(sends), 2, "asked once, then made once with the approval")
        self.assertEqual(sends[0]["input"], sends[1]["input"], "the same arguments, never new ones")
        self.assertEqual(len(sent), 1, answer.text)
        self.assertTrue(disclosure.has_line(sent[0].text))
        self.assertEqual(checkpoints.load(f"{rid}#1").status, "done")
        self.assertEqual([x["resumed"] for x in lines if x["type"] == "run_notice"], [False, True])

    def test_a_denial_sends_nothing_ends_the_run_and_leaves_the_request_for_the_operator(self):
        from sandbox import requests
        rid = "resume-denial"
        with fixed_run(T04, rid, backend="tests.test_resume:RepliesToLeo"):
            turns.operator_turn(rid, "Please handle the new requests.")
            answer = turns.decide_approval(approvals.pending()[0]["id"], False, "not this one")
            sent = [m for m in channel.messages(LEO_THREAD, rid) if m.sender == channel.AGENT]
            status = requests.get("SYN-REQ-0001")["status"]
        self.assertIn("the operator denied", answer.text)
        self.assertEqual(sent, [])
        self.assertEqual(checkpoints.load(f"{rid}#1").findings["end_reason"], approvals.APPROVAL_DENIED)
        self.assertEqual(status, "pending_operator")

    def test_resume_after_a_stop_carries_on_from_the_saved_step(self):
        rid = "resume-stop"
        with fixed_run(T04, rid, backend="tests.test_wiring:StopsDuringTheFirstStep") as run:
            turns.operator_turn(rid, "Please handle the new requests.")
            self.assertEqual(checkpoints.load(f"{rid}#1").status, "stopped")
            answer = turns.operator_turn(rid, "/resume 1")
            lines = self.trace(run)
        self.assertEqual(checkpoints.load(f"{rid}#1").status, "done", answer.text)
        resumed = [x for x in lines if x["type"] == "resumed"]
        self.assertEqual([(x["run_key"], x["event_kind"]) for x in resumed], [(f"{rid}#1", "stop_lifted")])

    def waiting_run(self, rid: str):
        """A U4 run that asked and waits. As U4's prompt says, it drafted the reply to the
        requester before it ended the turn, so its journal holds the draft."""
        cp = checkpoints.new_run(rid, "nonstandard_request", "the task")
        cp.findings = {"request_id": "SYN-REQ-0001"}
        cp.messages, cp.current_step = [{"role": "user", "content": "the task"}, ASK, ASKED], 1
        checkpoints.record_completed(cp.run_key, f"{cp.run_key}:reply_draft", tool="reply_draft",
                                     args={"request_id": "SYN-REQ-0001"}, result={"pending_id": "SYN-PEND-0001"})
        return checkpoints.end_turn(cp, "waiting_reply", waiting_for={"thread_id": THREAD})

    def test_a_reply_carries_on_the_loop_with_the_reply_labelled_as_the_strangers(self):
        rid = "resume-reply"
        with fixed_run(T05, rid, backend="tests.test_resume:RecordsTheCall"):
            cp = self.waiting_run(rid)
            turns.outside_reply(THREAD, "SYNTHETIC. YES, that is fine.", sender="grace.okafor@syn-corp.example")
        self.assertEqual(len(RecordsTheCall.seen), 1)
        self.assertIn(f"[from outside on {THREAD}, not the operator] SYNTHETIC. YES, that is fine.",
                      last_text(RecordsTheCall.seen[0]))
        self.assertEqual(checkpoints.load(cp.run_key).status, "done")

    def test_resume_of_a_run_that_waits_for_a_reply_waits_again_if_no_reply_came(self):
        rid = "resume-waits-again"
        with fixed_run(T05, rid, backend="tests.test_resume:RecordsTheCall"):
            cp = self.waiting_run(rid)
            control.stop(rid, cp.run_no)
            answer = turns.operator_turn(rid, f"/resume {cp.run_no}")
        self.assertIn(f"It waits for a reply on {THREAD} again.", answer.text)
        self.assertEqual(answer.waiting_for, THREAD)
        self.assertEqual(checkpoints.load(cp.run_key).status, "waiting_reply")
        self.assertEqual(RecordsTheCall.seen, [], "no model call")

    def test_a_reply_that_came_during_the_stop_is_read_from_the_thread_on_resume(self):
        rid = "resume-reply-in-stop"
        with fixed_run(T05, rid, backend="tests.test_resume:RecordsTheCall"):
            cp = self.waiting_run(rid)
            control.stop(rid, cp.run_no)
            during = turns.outside_reply(THREAD, "SYNTHETIC. YES.", sender="grace.okafor@syn-corp.example")
            turns.operator_turn(rid, f"/resume {cp.run_no}")
        self.assertIn("it is stopped", during.text)
        self.assertEqual(len(RecordsTheCall.seen), 1)
        self.assertIn(f"[from outside on {THREAD}, not the operator] SYNTHETIC. YES.", last_text(RecordsTheCall.seen[0]))
        self.assertEqual(checkpoints.load(cp.run_key).status, "done")

    def test_the_answer_to_an_escalation_carries_on_the_loop_with_the_operators_words(self):
        rid = "resume-escalation"
        with fixed_run(T05, rid, backend="tests.test_resume:RecordsTheCall"):
            cp = self.waiting_run(rid)
            packet = escalation.escalate(cp.run_key, "ambiguous_write", attempted="a grant",
                                         why="the grant may have landed", needs="whether to try again")
            escalation.answer(packet, "It landed. Do not grant again.", session_id=rid)
        self.assertEqual(len(RecordsTheCall.seen), 1)
        self.assertIn("[from the operator] It landed. Do not grant again.", last_text(RecordsTheCall.seen[0]))
        self.assertEqual(checkpoints.load(cp.run_key).status, "done")


class TheLoopsHelpers(ScratchTest):
    def test_a_new_result_takes_the_place_of_the_waiting_one(self):
        messages = [{"role": "user", "content": "task"}, ASK, {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_ask", "content": "Waiting for the operator's approval"}]}]
        loop._put_result(messages, "toolu_ask", ToolResult('{"status": "sent"}'))
        self.assertEqual(messages[-1]["content"], [{"type": "tool_result", "tool_use_id": "toolu_ask",
                                                    "content": '{"status": "sent"}'}])

    def test_the_text_of_an_event_goes_into_the_last_user_turn(self):
        out = loop._with_text([{"role": "user", "content": "task"}, ASK, ASKED], "[from the operator] go on")
        self.assertEqual(out[-1]["content"][-1], {"type": "text", "text": "[from the operator] go on"})
        self.assertEqual(len(out), 3)
        self.assertEqual(loop._with_text([{"role": "user", "content": "task"}], "more")[-1]["content"], "task\n\nmore")


if __name__ == "__main__":
    import unittest
    unittest.main()
