"""The one-path test: a console turn through the app's route and a scripted operator
turn through the runner call the same function, agent.service.operator_turn. No tokens."""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from agent import service
from app.main import app
from harness.turns import Reply
from tests.support import ScratchTest


class OnePathTest(ScratchTest):
    def test_console_and_runner_call_the_same_function(self):
        calls = []

        def operator_turn(session_id, text):
            calls.append((session_id, text))
            return Reply(f"heard: {text}")

        with mock.patch.object(service, "operator_turn", operator_turn):
            with TestClient(app) as client:
                reply = client.post("/console/s-test/turn", data={"text": "from the console"},
                                    headers={"accept": "application/json"}).json()
            record = self.run_fixture("one-path")
        self.assertEqual(reply["reply"]["text"], "heard: from the console")
        self.assertEqual(calls[0], ("s-test", "from the console"))
        self.assertEqual(calls[1:], [(record["run_id"], "Handle the new message."),
                                     (record["run_id"], "Is anything still open?")])
        self.assertEqual(record["outcome"], "completed")
        self.assertFalse(record["passed"])          # the probe sent nothing on the thread: the check reads that


if __name__ == "__main__":
    unittest.main()
