"""The queue page (Part 2, task 4): what has arrived and its status, a gated call while it waits
(with the sandbox unchanged), a run held by an escalation, a stopped run. Through
sandbox/queue.py and the app's /queue page. No tokens. Owner: Shruti Tewaney."""

from fastapi.testclient import TestClient

import channel
from agent import checkpoints, control, disclosure, escalation
from agent.registry import CallContext, Registry
from app.main import app
from harness import runtime
from sandbox import access, queue, seed
from tests.support import ScratchTest

SID = "s-queue"
LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"


class QueueTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()

    def item(self, item_id):
        return next((i for i in queue.waiting() if i["id"] == item_id), None)

    def test_what_has_arrived_is_listed_with_its_status(self):
        self.assertEqual(self.item(REQ)["status"], "new")

    def test_a_gated_call_shows_while_it_waits_and_the_sandbox_is_unchanged(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "reply to Leo")
        args = disclosure.stamp_call("reply_send", {"recipient_employee_id": LEO, "request_id": REQ,
                                                    "body": "SYNTHETIC. Hi Leo, it is with the IT team."})
        result = Registry().call("reply_send", args, CallContext(SID, 1, "model", "nonstandard_request",
                                                                 request_id=REQ, run_key=cp.run_key))
        self.assertEqual(result.end_reason, "awaiting_approval")
        waiting = self.item(cp.run_key)
        self.assertEqual(waiting["status"], "waits for the operator's approval APR-0001")
        self.assertEqual([m for m in channel.messages("SYN-MSG-D05") if m.sender == channel.AGENT], [])

    def test_a_held_run_comes_first_with_what_it_needs(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "grant Leo")
        packet_id = escalation.escalate(cp.run_key, "ambiguous_write", attempted="a grant",
                                        why="the outcome is unknown", needs="say whether it landed")
        first = queue.waiting()[0]
        self.assertEqual((first["id"], first["run"], first["needs"]), (packet_id, cp.run_key, "say whether it landed"))
        self.assertIn(f"/answer {packet_id}", first["status"])
        self.assertIsNone(self.item(cp.run_key))                     # once, as held, not twice

    def test_a_stopped_run_is_listed(self):
        control.stop(SID, 4)
        self.assertIn("stopped", self.item(f"{SID}#4")["status"])

    def test_the_page_shows_them(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "grant Leo")
        packet_id = escalation.escalate(cp.run_key, "ambiguous_write", attempted="a grant",
                                        why="the outcome is unknown", needs="say whether it landed")
        with TestClient(app) as client:
            page = client.get("/queue").text
        self.assertIn(packet_id, page)
        self.assertIn("say whether it landed", page)
        self.assertIn(REQ, page)
        self.assertTrue(access.memberships(LEO))
