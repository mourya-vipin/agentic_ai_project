"""The disclosure line (Lecture 5, slides 42 to 44; Part 2, task 8): added by our code to every
send, required by the hook, and read back from the channel by every check. No tokens.
Owner: Vipin Mourya."""

import re
from types import SimpleNamespace
from unittest import mock

import channel
from agent import disclosure
from agent.registry import CallContext, Registry
from checks import common
from harness import runtime
from harness.trace import read_trace
from sandbox import notices, seed
from agent import checkpoints
from harness import turns
from tests.support import ScratchTest, fixed_run
from tests.test_wait import BACKEND, DEV, GITHUB_ENG, HANNAH, REQ, T08, THREAD as MANAGER_THREAD

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
        self.assertIn("reply HUMAN or write to it-desk@synthetic.example", disclosure.LINE)

    def test_the_check_still_reads_a_committed_run_that_carries_the_earlier_line(self):
        run = SimpleNamespace(run_id="r-disc")
        earlier = f"Hello Leo.\n\n{common.EARLIER_DISCLOSURE_LINES[0]}"
        with mock.patch.object(channel, "messages", return_value=[
                channel.Message(1, THREAD, channel.AGENT, earlier, "t", "r-disc", None)]), \
                mock.patch.object(channel, "threads", return_value=[{"thread_id": THREAD}]):
            self.assertEqual(common.require_disclosure(run), [])
        self.assertFalse(disclosure.has_line(earlier))     # the hook asks for today's line on every new send

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


class HumanReachesTheOperator(ScratchTest):
    """End to end, the HUMAN that the line offers: the person reads it on our question, replies HUMAN,
    and the reply waits for the operator on /pending and the queue, whether or not a run waits on the
    thread. It is never the manager's answer, so the grant is not put up on it."""

    def human_items(self):
        from sandbox import pending
        return [p for p in pending.open_items("outside_message") if p["reason"] == "asks for a person"]

    def test_the_word_the_line_offers_is_the_word_our_code_reads(self):
        from policy import hook
        word = re.search(r"reply (\w+) or", disclosure.LINE).group(1)
        self.assertEqual(word, "HUMAN")
        for reply in (word, f"SYNTHETIC. {word}", f"SYNTHETIC. {word}. I would like to talk to someone.", "human please"):
            with self.subTest(reply=reply):
                self.assertTrue(hook.asks_for_a_person(reply))
        for reply in ("SYNTHETIC. YES.", "SYNTHETIC. NO.", "Humane of you to ask. YES.", "I am human, YES"):
            with self.subTest(reply=reply):
                self.assertFalse(hook.asks_for_a_person(reply))

    def test_a_human_reply_to_our_question_waits_for_the_operator_and_is_not_the_managers_answer(self):
        from agent import approvals
        from agent.tools import impl
        from policy import hook
        from sandbox import queue, requests
        rid = "disc-human-waits"
        with fixed_run(T08, rid, backend=BACKEND):
            asked = turns.operator_turn(rid, "Please handle the new access requests.")
            self.assertEqual(asked.waiting_for, MANAGER_THREAD)
            question = [m for m in channel.messages(MANAGER_THREAD) if m.sender == channel.AGENT][-1]
            self.assertTrue(question.text.endswith(disclosure.LINE), "the manager reads the line on our question")
            reply = turns.outside_reply(MANAGER_THREAD, "SYNTHETIC. HUMAN", sender="hannah.lee@syn-corp.example")
            items, shown = self.human_items(), [i["summary"] for i in queue.arrived()]
            answer = hook.manager_answer(impl._employee(DEV), impl._group(GITHUB_ENG), requests.get(REQ))
            still = [cp.waiting_for["thread_id"] for cp in checkpoints.waiting("reply")]
            put_up = approvals.pending()
        self.assertIn("asks for a person", reply.text)
        self.assertEqual([(i["employee_id"], i["thread_id"], i["request_id"]) for i in items], [(HANNAH, MANAGER_THREAD, REQ)])
        self.assertTrue(any(s.startswith(f"{HANNAH} wrote on {MANAGER_THREAD} (asks for a person): HUMAN") for s in shown), shown)
        self.assertIsNone(answer, "HUMAN is neither her YES nor her NO")
        self.assertEqual(still, [MANAGER_THREAD], "the run still waits for her answer")
        self.assertEqual(put_up, [], "nothing is put up on a HUMAN")

    def test_a_human_on_a_thread_that_no_run_waits_on_waits_for_the_operator(self):
        rid = "disc-human-unwaited"
        with fixed_run(T08, rid, backend=BACKEND):
            reply = turns.outside_reply(MANAGER_THREAD, "SYNTHETIC. HUMAN, please call me about Dev's access.",
                                        sender="hannah.lee@syn-corp.example")
            items = self.human_items()
            waiting = checkpoints.waiting("reply")
        self.assertIn("asks for a person, so it waits for the operator on /pending as SYN-PEND-", reply.text)
        self.assertEqual([(i["employee_id"], i["thread_id"]) for i in items], [(HANNAH, MANAGER_THREAD)])
        self.assertEqual(waiting, [])


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
