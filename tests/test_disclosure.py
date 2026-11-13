"""The disclosure line (Lecture 5, slides 42 to 44; Part 2, task 8): added by our code to every
send, required by the hook, and read back from the channel by every check. No tokens.
Owner: Vipin Mourya."""

from types import SimpleNamespace
from unittest import mock

import channel
from agent import disclosure
from agent.registry import CallContext, Registry
from checks import common
from harness import runtime
from harness.trace import read_trace
from sandbox import notices, seed
from tests.support import ScratchTest

FACTS = {"name": "Leo Martins", "request_id": "SYN-REQ-0005"}
THREAD = "SYN-MSG-D05"


class DisclosureTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def ctx(self):
        return CallContext("s-disc", 1, "workflow", "standard_grant", request_id="SYN-REQ-0005", origin="workflow")

    def send(self):
        return self.registry.call("notices_send_template", {"thread_id": THREAD, "request_id": "SYN-REQ-0005",
                                                            "template_id": "received", "facts": FACTS}, self.ctx())

    def test_stamp_adds_the_line_once_as_the_last_paragraph(self):
        once = disclosure.stamp("Hello Leo.")
        self.assertTrue(once.endswith(disclosure.LINE))
        self.assertEqual(disclosure.stamp(once), once)
        self.assertTrue(disclosure.has_line(once))
        self.assertFalse(disclosure.has_line("Hello Leo."))

    def test_the_line_says_an_agent_wrote_it_and_how_to_reach_a_person(self):
        self.assertIn("automated agent", disclosure.LINE)
        self.assertIn("reach a person", disclosure.LINE)

    def test_a_template_notice_goes_out_with_the_line(self):
        result = self.send()
        self.assertFalse(result.is_error, result.content)
        sent = [m for m in channel.messages(THREAD) if m.sender == channel.AGENT]
        self.assertTrue(sent and sent[-1].text.endswith(disclosure.LINE))

    def test_a_send_built_without_the_line_leaves_the_thread_unchanged_and_the_refusal_logged(self):
        before = channel.messages(THREAD)
        with mock.patch.object(notices.disclosure, "stamp", side_effect=lambda text: text):   # a template that lost it
            result = self.send()
        self.assertTrue(result.is_error)
        self.assertEqual(channel.messages(THREAD), before)
        denied = [x for x in read_trace(runtime.trace("s-disc").path) if x["type"] == "hook_denied"]
        self.assertIn("no disclosure", denied[-1]["error"])

    def test_the_check_fails_a_message_the_agent_sent_without_the_line(self):
        run = SimpleNamespace(run_id="r-disc")
        with mock.patch.object(channel, "messages", return_value=[
                channel.Message(1, THREAD, channel.AGENT, "Hello Leo.", "t", "r-disc", None),
                channel.Message(2, THREAD, channel.AGENT, disclosure.stamp("Hello again."), "t", "r-disc", None),
                channel.Message(3, THREAD, "Leo", "Thanks.", "t", "r-disc", None)]), \
                mock.patch.object(channel, "threads", return_value=[{"thread_id": THREAD}]):
            problems = common.require_disclosure(run)
        self.assertEqual(problems, [f"message 1 on {THREAD} has no disclosure line"])

    def test_the_checks_copy_of_the_line_is_the_agents(self):
        self.assertEqual(common.DISCLOSURE_LINE, disclosure.LINE)


class WorkflowNoticesCarryTheLine(ScratchTest):
    """A real run of each workflow test task: every message the agent sent ends with the line,
    as the check reads it from the channel."""

    def test_t01_to_t03_notices_pass_require_disclosure(self):
        for task, backend in (("t01_standard_jira", "RoutesStandardGrant"), ("t02_new_starter", "RoutesToAgent"),
                              ("t03_contractor_left", "RoutesToAgent")):
            with self.subTest(task=task):
                record = self.run_task(f"tasks/{task}.yaml", "disc", backend=f"tests.fakes:{backend}")
                self.assertTrue(record["passed"], record["check"]["reason"])
                self.assertGreater(record["measures"]["notices"], 0)
                self.assertEqual(common.require_disclosure(SimpleNamespace(run_id=record["run_id"])), [])
