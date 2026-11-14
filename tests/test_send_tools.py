"""The two sends M2 adds (C3.9): channel_ask_template, a question our code writes from a
template, and reply_send, a reply the model writes, which always waits for the operator.
Through the registry and the MCP server; no tokens. Owner: Vipin Mourya."""

import sys
import types
from contextlib import contextmanager
from unittest import mock

import channel
from agent import disclosure
from agent.registry import CallContext, Registry
from harness import runtime
from harness.trace import read_trace
from sandbox import seed
from tests.support import ScratchTest

LEO, PRIYA, MANAGER, REQ = "SYN-EMP-0031", "SYN-EMP-0012", "SYN-EMP-0040", "SYN-REQ-0005"
LEO_THREAD, MANAGER_THREAD = "SYN-MSG-D05", "SYN-MAIL-SYN-EMP-0040"


def ctx(use_case="nonstandard_request", caller="model", run_key="s-send#1"):
    return CallContext("s-send", 1, caller, use_case, request_id=REQ, run_key=run_key)


@contextmanager
def approvals(covers: bool):
    module = types.ModuleType("agent.approvals")
    module.covers = lambda run_key, action_class, args: covers
    module.request = lambda ctx, tool, args, action_class, facts: "APR-1"
    with mock.patch.dict(sys.modules, {"agent.approvals": module}):
        yield


class SendToolsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def agent_messages(self, thread):
        return [m for m in channel.messages(thread) if m.sender == channel.AGENT]

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace("s-send").path) if x["type"] == kind]

    # -- channel_ask_template ------------------------------------------------------------------

    def ask(self, employee, group):
        return self.registry.call("channel_ask_template", {"template": "manager_confirm", "employee_id": employee,
                                                           "group_id": group, "request_id": REQ}, ctx())

    def test_a_question_from_the_template_goes_to_the_manager_unasked_with_the_line(self):
        result = self.ask(LEO, "SYN-GRP-FINREP-WRITE")                  # needs the manager's agreement
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.data["waiting_for"], MANAGER_THREAD)
        self.assertEqual(result.data["recipient_employee_id"], MANAGER)
        sent = self.agent_messages(MANAGER_THREAD)
        self.assertEqual(len(sent), 1)
        self.assertIn("SYN-GRP-FINREP-WRITE", sent[0].text)
        self.assertIn("reply YES or NO", sent[0].text)
        self.assertTrue(sent[0].text.endswith(disclosure.LINE))
        self.assertEqual(self.lines("tool_call")[-1]["action_class"], "ask_stranger/template")

    def test_asking_again_under_the_same_key_sends_once(self):
        self.ask(LEO, "SYN-GRP-FINREP-WRITE")
        again = self.ask(LEO, "SYN-GRP-FINREP-WRITE")
        self.assertEqual(again.data["status"], "already_sent")
        self.assertEqual(len(self.agent_messages(MANAGER_THREAD)), 1)

    def test_on_someone_elses_behalf_there_is_a_question_even_for_an_allowed_group(self):
        result = self.ask(PRIYA, "SYN-GRP-JIRA-WRITE")                 # Leo asks for Priya
        self.assertFalse(result.is_error, result.content)

    def test_when_nothing_is_missing_nothing_is_asked(self):
        result = self.ask(LEO, "SYN-GRP-FINREP-READ")                  # allowed, and Leo asks for himself
        self.assertTrue(result.is_error)
        self.assertIn("nothing to ask", result.content)
        self.assertEqual(self.agent_messages(MANAGER_THREAD), [])

    # -- reply_send -----------------------------------------------------------------------------

    def reply(self, body, use_case="nonstandard_request", caller="model"):
        args = disclosure.stamp_call("reply_send", {"recipient_employee_id": LEO, "request_id": REQ, "body": body})
        return self.registry.call("reply_send", args, ctx(use_case=use_case, caller=caller))

    def test_a_reply_waits_for_the_operator_and_nothing_is_sent(self):
        with approvals(covers=False):
            result = self.reply("SYNTHETIC. Hi Leo, your request is with the IT team.")
        self.assertEqual(result.end_reason, "awaiting_approval")
        self.assertEqual(self.agent_messages(LEO_THREAD), [])
        self.assertEqual(self.lines("tool_call")[-1]["action_class"], "reply_send/model_text")

    def test_until_the_gate_is_built_the_tool_itself_refuses_to_send(self):
        result = self.reply("SYNTHETIC. Hi Leo, your request is with the IT team.")
        self.assertTrue(result.is_error)
        self.assertIn("only once the operator has approved", result.content)
        self.assertEqual(self.agent_messages(LEO_THREAD), [])

    def test_an_approved_reply_goes_out_once_with_the_line(self):
        with approvals(covers=True):
            first = self.reply("SYNTHETIC. Hi Leo, your request is with the IT team.")
            again = self.reply("SYNTHETIC. Hi Leo, your request is with the IT team.")
        self.assertFalse(first.is_error, first.content)
        self.assertEqual(again.data["status"], "already_sent")
        sent = self.agent_messages(LEO_THREAD)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].text.endswith(disclosure.LINE))

    def test_an_approved_reply_still_passes_the_critique(self):
        with approvals(covers=True):
            result = self.reply("SYNTHETIC. Hi Leo, I granted you SYN-GRP-FINREP-WRITE.")
        self.assertTrue(result.is_error)
        self.assertIn("reply was not sent", result.content)
        self.assertEqual(self.agent_messages(LEO_THREAD), [])

    def test_a_reply_built_without_the_line_is_refused_and_logged(self):
        with approvals(covers=True):
            result = self.registry.call("reply_send", {"recipient_employee_id": LEO, "request_id": REQ,
                                                       "body": "SYNTHETIC. Hi Leo."}, ctx())
        self.assertTrue(result.is_error)
        self.assertEqual(self.agent_messages(LEO_THREAD), [])
        self.assertIn("no disclosure", self.lines("hook_denied")[-1]["error"])

    def test_in_an_approved_plan_a_managers_note_is_its_own_class(self):
        with approvals(covers=False):
            self.reply("SYNTHETIC. A note.", use_case="bulk_change", caller="executor")
        self.assertEqual(self.lines("tool_call")[-1]["action_class"], "manager_note/plan_listed")

    def test_stamp_call_stamps_only_a_send_the_model_wrote(self):
        args = {"recipient_employee_id": LEO, "body": "Hi."}
        self.assertTrue(disclosure.stamp_call("reply_send", args)["body"].endswith(disclosure.LINE))
        self.assertEqual(disclosure.stamp_call("reply_draft", args), args)
        self.assertEqual(disclosure.without_line(disclosure.stamp("Hi.")), "Hi.")
