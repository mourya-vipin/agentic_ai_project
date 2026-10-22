"""The app's pages respond, on a freshly seeded sandbox, as `make app` serves them. No tokens.
The agent's functions are replaced where a test needs them not built, so these tests
keep passing once yours is."""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import channel
from agent import service
from app.main import app
from harness import runtime
from harness.paths import traces_dir
from harness.turns import Reply
from tests.support import ScratchTest

NOT_BUILT = NotImplementedError("operator_turn: not built")


class AppTests(ScratchTest):
    def setUp(self):
        super().setUp()
        from sandbox import seed
        channel.reset()
        seed.reset()
        seed.seed(None)
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def test_every_page_responds(self):
        self.assertEqual(self.client.get("/").status_code, 200)       # your product's pages, as you write them
        for path in ("/console", "/queue", "/threads", "/approvals", "/traces"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("<nav>", response.text, path)

    def test_the_console_says_when_the_agent_is_not_built(self):
        response = self.client.post("/console/new", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        session = response.headers["location"].rsplit("/", 1)[1]
        with mock.patch.object(service, "operator_turn", side_effect=NOT_BUILT):
            reply = self.client.post(f"/console/{session}/turn", data={"text": "Hello"},
                                     headers={"accept": "application/json"}).json()
        self.assertFalse(reply["ok"])
        self.assertIn("not built yet", reply["error"])
        page = self.client.get(f"/console/{session}").text
        self.assertIn("Hello", page)
        self.assertIn("not built yet", page)
        self.assertIn(session, self.client.get("/console").text)

    def test_a_console_reply_and_its_actions(self):
        from harness.turns import Reply
        with mock.patch.object(service, "operator_turn", return_value=Reply("Done it.", ["wrote SYN-1"])):
            self.client.post("/console/s-x/turn", data={"text": "Do it"})
        page = self.client.get("/console/s-x/history").text
        self.assertIn("Done it.", page)
        self.assertIn("wrote SYN-1", page)

    def test_threads_and_the_queue_show_the_channel(self):
        channel.receive("SYN-thread-9", "A question from outside", sender="SYN-person-9")
        channel.send("SYN-thread-9", "We will look into it.")
        self.assertIn("SYN-thread-9", self.client.get("/queue").text)
        page = self.client.get("/threads", params={"thread": "SYN-thread-9"}).text
        self.assertIn("A question from outside", page)
        with mock.patch.object(service, "on_outside_reply", side_effect=NotImplementedError("on_outside_reply: x")):
            reply = self.client.post("/threads/reply", data={"thread_id": "SYN-thread-9", "text": "Thanks",
                                                             "sender": "SYN-person-9"})
        self.assertEqual(reply.status_code, 200)
        self.assertIn("not built yet", reply.text)
        self.assertEqual([m.text for m in channel.replies("SYN-thread-9")][-1], "Thanks")

    def test_traces_show_runs_and_sessions(self):
        with mock.patch.object(service, "operator_turn", side_effect=NOT_BUILT):
            record = self.run_fixture("smoke")                       # free: results/_free/smoke/
            counted = self.run_fixture("mine", backend="tests.support:Scripted")
        listing = self.client.get("/traces").text
        self.assertIn(f'href="/traces/run/_free/smoke/{record["run_id"]}"', listing)
        self.assertIn(f'href="/traces/run/mine/{counted["run_id"]}"', listing)
        page = self.client.get(f"/traces/run/_free/smoke/{record['run_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("not_built", page.text)
        self.assertEqual(self.client.get(f"/traces/run/mine/{counted['run_id']}").status_code, 200)
        self.assertEqual(self.client.get("/traces/run/smoke/nothing-here").status_code, 404)

    def test_current_session_in_the_app(self):
        from harness import runtime
        from harness.turns import Reply
        seen = []
        with mock.patch.object(service, "operator_turn",
                               lambda s, t: seen.append((runtime.current_session(), runtime.current_run())) or Reply("ok")):
            self.client.post("/console/s-stamp/turn", data={"text": "Do it"})
        self.assertEqual(seen, [("s-stamp", None)])

    def test_an_approval_is_answered_in_its_session(self):
        seen = {}

        def decide(approval_id, approved, note=""):
            seen["session"] = runtime.current_session()
            return Reply(text="Sent.")

        waiting = [{"id": "A-2", "session_id": "S-approval-test", "summary": "SYNTHETIC. A reply to send"}]
        with mock.patch.object(service, "pending_approvals", return_value=waiting), \
                mock.patch.object(service, "decide_approval", side_effect=decide):
            response = self.client.post("/approvals", data={"approval_id": "A-2", "decision": "approve"})
        self.assertIn("Sent.", response.text)
        self.assertEqual(seen["session"], "S-approval-test")
        lines = (traces_dir() / "S-approval-test.jsonl").read_text()
        self.assertIn('"type": "approval"', lines)

    def test_approvals(self):
        with mock.patch.object(service, "pending_approvals", return_value=[]):
            self.assertIn("Nothing waits for approval", self.client.get("/approvals").text)
        waiting = [{"id": "A-1", "action": "send_message", "to": "SYN-person-1"}]
        with mock.patch.object(service, "pending_approvals", return_value=waiting):
            self.assertIn("SYN-person-1", self.client.get("/approvals").text)
        with mock.patch.object(service, "decide_approval", side_effect=NotImplementedError("decide_approval: x")):
            response = self.client.post("/approvals", data={"approval_id": "A-1", "decision": "approve"})
        self.assertIn("not built yet", response.text)


if __name__ == "__main__":
    unittest.main()
