"""The console's panel: beside a session, its runs (status, wait, commands) and what the memory
holds (source and age). Owner: Aman Panigrahi. No tokens: no model is called.

    the panel      a run that waits for a reply shows its thread and the commands that fit; a
                   stopped run offers /resume and /cancel; a run with writes offers /rollback
    the memory     each record shows its source, its age and whether it is confirmed
    the buttons    each one sends its command through operator_turn, as a typed message does
    the list       a session with a waiting run says so
    no side effect reading the panel writes no trace line and changes no run
"""

from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

import channel
from agent import checkpoints, memory, service
from app.main import app
from harness import runtime
from harness.paths import traces_dir
from harness.trace import SessionTrace, read_trace
from harness.turns import Reply
from tests.support import ScratchTest

SESSION = "s-panel"
PREFERENCE = "From now on, draft a manager confirmation for me instead of asking on the channel."


class ConsolePanelTest(ScratchTest):
    def setUp(self):
        super().setUp()
        from sandbox import seed
        channel.reset()
        seed.reset()
        seed.seed(None)
        runtime.configure(backend="never-stops")
        SessionTrace(traces_dir() / f"{SESSION}.jsonl", SESSION).write(
            {"type": "session_start", "source": "console", "switches": runtime.switches()})
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def runs(self):
        waits = checkpoints.new_run(SESSION, "nonstandard_request", "Handle SYN-REQ-0001 for Leo.")
        checkpoints.end_turn(waits, "waiting_reply", waiting_for={"thread_id": "TH-0001",
                                                                  "deadline": "2026-11-18T17:00:00Z"})
        stopped = checkpoints.new_run(SESSION, "standard_grant", "Grant Jira read to SYN-EMP-0031.")
        checkpoints.record_completed(stopped.run_key, "k1", tool="access_grant",
                                     args={"employee_id": "SYN-EMP-0031"}, result={"ok": True})
        checkpoints.end_turn(stopped, "stopped")
        checkpoints.flush()
        return waits, stopped

    def test_the_panel_shows_each_run_its_wait_and_the_commands_that_fit(self):
        waits, stopped = self.runs()
        panel = service.session_panel(SESSION)
        by_no = {r["run_no"]: r for r in panel["runs"]}
        self.assertEqual([r["run_no"] for r in panel["runs"]], [2, 1], "newest first")
        self.assertEqual(by_no[1]["thread_id"], "TH-0001")
        self.assertEqual(by_no[1]["commands"], ["/stop 1", "/cancel 1"])
        self.assertEqual(by_no[2]["commands"], ["/resume 2", "/cancel 2", "/rollback 2"])
        self.assertEqual(by_no[2]["writes"], 1)
        self.assertEqual(panel["waiting"], 2)
        html = self.client.get(f"/console/{SESSION}/panel").text
        self.assertIn("waiting for a reply", html)
        self.assertIn("TH-0001", html)
        self.assertIn('value="/resume 2"', html)
        self.assertIn('value="/rollback 2"', html)

    def test_the_panel_shows_what_the_memory_holds_with_its_source_and_age(self):
        memory.from_text(SESSION, PREFERENCE)
        html = self.client.get(f"/console/{SESSION}/panel").text
        self.assertIn("draft a manager confirmation", html)
        self.assertIn("from the operator", html)
        self.assertIn("written today", html)
        self.assertIn("confirmed", html)

    def test_a_button_sends_its_command_through_operator_turn(self):
        self.runs()
        with mock.patch.object(service, "operator_turn", return_value=Reply("Resumed.")) as turn:
            self.client.post(f"/console/{SESSION}/turn", data={"text": "/resume 2"},
                             headers={"accept": "application/json"})
        turn.assert_called_once_with(SESSION, "/resume 2")

    def test_the_session_list_says_which_sessions_wait(self):
        self.runs()
        page = self.client.get("/console").text
        self.assertIn("2 waiting", page)
        self.assertIn(SESSION, page)

    def test_reading_the_panel_writes_no_trace_line_and_changes_no_run(self):
        self.runs()
        memory.from_text(SESSION, PREFERENCE)
        path = traces_dir() / f"{SESSION}.jsonl"
        before, runs = len(read_trace(path)), [(c.run_key, c.status) for c in checkpoints.runs_of(SESSION)]
        for _ in range(3):
            self.client.get(f"/console/{SESSION}/panel")
            self.client.get(f"/console/{SESSION}")
        self.assertEqual(len(read_trace(path)), before)
        self.assertEqual([(c.run_key, c.status) for c in checkpoints.runs_of(SESSION)], runs)

    def test_the_page_still_opens_when_the_agent_raises(self):
        with mock.patch.object(service, "session_panel", side_effect=NotImplementedError("session_panel: x")):
            response = self.client.get(f"/console/{SESSION}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("not built yet", response.text)


if __name__ == "__main__":
    import unittest
    unittest.main()
