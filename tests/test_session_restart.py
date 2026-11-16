"""Durable console sessions: an earlier session carries on after a restart (make serve).
Owner: Aman Panigrahi. No tokens: the models are fakes (free = True).

A first process has two turns in one console session and ends. A second process, as after
`make serve`, gets a third message in the same session. Its conversation comes from the
session store (state_dir()/sessions.sqlite3), so the model's call carries the earlier turns,
and the session does not start again: no session-start read of the memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from harness import ROOT
from harness.trace import read_trace
from tests import fakes
from tests.support import ScratchTest

T07 = "tasks/t07_unusual_review.yaml"
SID = "restart-session"
FIRST, SECOND, THIRD = "Which requests came in today?", "Which of them are still open?", "And what did I ask first?"


class ShowsWhatItGot(fakes.RouterFake):
    """Routes each message as a question (U5), says "Done.", and prints each call's messages
    with tools, so that the test can read what the model got."""
    OPERATOR = {"intent": "question", "request_ids": []}

    def create(self, *, system, messages, tools):
        if tools:
            print("CALL " + json.dumps(messages), flush=True)
        return super().create(system=system, messages=messages, tools=tools)


CHILD = r"""
import sys
from harness import turns
from tests.support import fixed_run

task, sid, fresh, *texts = sys.argv[1:]
with fixed_run(task, sid, backend="tests.test_session_restart:ShowsWhatItGot", fresh=fresh == "fresh"):
    for text in texts:
        turns.operator_turn(sid, text)
"""


class TheSessionRestart(ScratchTest):
    def child(self, fresh: str, *texts: str) -> list[list[dict]]:
        done = subprocess.run([sys.executable, "-c", CHILD, T07, SID, fresh, *texts], cwd=ROOT, env=os.environ.copy(),
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return [json.loads(line[5:]) for line in done.stdout.splitlines() if line.startswith("CALL ")]

    def test_an_earlier_session_carries_on_after_a_restart(self):
        self.child("fresh", FIRST, SECOND)
        calls = self.child("keep", THIRD)            # a new process: the app was restarted
        self.assertTrue(calls)
        seen = json.dumps(calls[0])
        self.assertIn(FIRST, seen, "the earlier turns come from the session store")
        self.assertIn(SECOND, seen)
        self.assertIn(THIRD, seen)
        from agent import session
        self.assertEqual([m["role"] for m in session.history(SID)], ["operator", "agent"] * 3)
        lines = read_trace(next((self.tmp / "traces").rglob(f"{SID}.trace.jsonl")))
        starts = [x for x in lines if x["type"] == "memory_read" and x.get("purpose") == "session_start"]
        self.assertEqual(len(starts), 1, "the session started once, in the first process")


if __name__ == "__main__":
    import unittest
    unittest.main()
