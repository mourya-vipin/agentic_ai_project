"""The outside exchange and a wait that survives a restart (Part 1, task 5).
Owner: Aman Panigrahi. No tokens: the models are fakes (free = True).

    t08 in one run       run.py's own code plays Hannah's scripted reply and the operator's
                         approval: the check passes
    the wait             the question goes out in one process, which then ends; the reply comes
                         in a new process, through harness.turns.outside_reply; the run carries
                         on from its checkpoint, and the check passes
    the deadline         past two working days on the sandbox's clock, the operator's next turn
                         ends the run reply_deadline and leaves the request for the operator
    t05                  the same ask, with no reply in that task: the check still passes
    the reply            after the answer, U4 sends the reply to Arjun with reply_send; the
                         operator approves it, and it is on his thread
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import channel
from agent import bounds, checkpoints
from harness import ROOT, turns
from harness.client import Usage
from harness.trace import read_trace
from sandbox import clock, requests
from tests import fakes
from tests.support import ScratchTest, check_run, fixed_run

T05, T08 = "tasks/t05_on_behalf.yaml", "tasks/t08_ask_manager.yaml"
ARJUN, DEV, HANNAH, REQ = "SYN-EMP-0007", "SYN-EMP-0052", "SYN-EMP-0060", "SYN-REQ-0001"
GITHUB_ENG, THREAD = "SYN-GRP-GITHUB-ENG", "SYN-MAIL-SYN-EMP-0060"
YES = "SYNTHETIC. YES. Dev is on my team now, so he can be in the GitHub engineers group."
BACKEND = "tests.test_wait:AsksTheManagerThenGrants"


def _tool_uses(messages, name):
    return [b for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_use" and b.get("name") == name]


class AsksTheManagerThenGrants(fakes.RouterFake):
    """Routes the request to U4. There, as the prompt says:
        1. ask Dev's manager (channel_ask_template), draft the reply to Arjun, and set the
           request to pending_operator, in one step; then end the turn
        2. when the manager's reply is in the conversation, grant the group (the operator
           approves it), then send the reply to Arjun with reply_send (the operator approves
           it too), then say Done."""

    def create(self, *, system, messages, tools):
        if not tools:
            return super().create(system=system, messages=messages, tools=tools)
        heard = "not the operator]" in json.dumps(messages)
        if not _tool_uses(messages, "channel_ask_template"):
            calls = [("toolu_ask", "channel_ask_template", {"template": "manager_confirm", "employee_id": DEV,
                                                            "group_id": GITHUB_ENG, "request_id": REQ}),
                     ("toolu_draft", "reply_draft", {"recipient_employee_id": ARJUN, "request_id": REQ,
                                                     "body": "SYNTHETIC. Hi Arjun, we have asked Dev's manager to "
                                                             "confirm this request."}),
                     ("toolu_status", "requests_set_status", {"request_id": REQ, "status": "pending_operator",
                                                              "note": "waiting for Dev's manager"})]
            return ([{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in calls], "tool_use",
                    self.name, Usage(40, 30))
        if not heard:
            return [{"type": "text", "text": "I asked Hannah Lee, Dev's manager, and wait for her reply."}], \
                "end_turn", self.name, Usage(40, 12)
        if not _tool_uses(messages, "access_grant"):
            return ([{"type": "tool_use", "id": "toolu_grant", "name": "access_grant",
                      "input": {"employee_id": DEV, "group_id": GITHUB_ENG, "request_id": REQ}}],
                    "tool_use", self.name, Usage(40, 10))
        if not _tool_uses(messages, self.answer_tool):
            return ([{"type": "tool_use", "id": "toolu_answer", "name": self.answer_tool,
                      "input": {"recipient_employee_id": ARJUN, "request_id": REQ,
                                "body": f"SYNTHETIC. Hi Arjun, Hannah Lee agreed, so Dev Rao now has {GITHUB_ENG}."}}],
                    "tool_use", self.name, Usage(40, 20))
        return [{"type": "text", "text": "Done."}], "end_turn", self.name, Usage(40, 2)

    answer_tool = "reply_send"


class DraftsAfterTheAnswer(AsksTheManagerThenGrants):
    """As above, but after the manager's answer it drafts the reply, as U4 did before t10."""

    answer_tool = "reply_draft"


CHILD = r"""
import sys
from harness import turns
from tests.support import fixed_run

task, rid, backend, fresh, step, thread, text, sender = sys.argv[1:9]
with fixed_run(task, rid, backend=backend, fresh=fresh == "fresh"):
    if step == "ask":
        reply = turns.operator_turn(rid, "Please handle the new access requests.")
        print("WAITING_FOR", reply.waiting_for)
    else:
        turns.outside_reply(thread, text, sender=sender)
        from agent import approvals
        for _ in range(5):                   # the grant, then the reply to Arjun
            waiting = approvals.pending()
            if not waiting:
                break
            turns.decide_approval(waiting[0]["id"], True, "the operator approves it")
"""


class TheWait(ScratchTest):
    def test_t08_passes_with_run_py_playing_the_manager_and_the_operator(self):
        record = self.run_task(T08, "aman", backend=BACKEND)
        self.assertTrue(record["passed"], record["check"])
        self.assertEqual(record["outside"][0]["asked"], 1)

    def test_after_the_answer_u4_sends_the_reply_once_the_operator_approves_it(self):
        from agent.router import BULK_CHANGE, NONSTANDARD, PROMPTS, TOOLS
        self.assertIn("reply_send", TOOLS[NONSTANDARD])
        self.assertNotIn("reply_send", TOOLS[BULK_CHANGE], "U6's loop sends nothing")
        self.assertIn("reply_send", (PROMPTS / "nonstandard_request.md").read_text(encoding="utf-8"))
        record = self.run_task(T08, "aman-send", backend=BACKEND)
        self.assertTrue(record["passed"], record["check"])
        self.assertEqual(record["approvals"]["answered"], 2, "the grant, then the reply")
        sent = [c for c in record["tool_calls"] if c["name"] == "reply_send"]
        self.assertTrue(sent, "U4 called reply_send")
        outputs = [str(c["output"]) for c in sent]        # the gated call first, then the approved one
        self.assertTrue(any('"status": "sent"' in o for o in outputs), outputs)
        to_arjun = [m for m in channel.messages("SYN-MSG-T08") if m.sender in channel.OURS]
        self.assertTrue(to_arjun and "Hannah Lee agreed" in to_arjun[-1].text, "the reply is on Arjun's thread")

    def test_t08_fails_a_run_that_drafts_the_reply_after_the_answer_instead_of_sending_it(self):
        record = self.run_task(T08, "aman-draft", backend="tests.test_wait:DraftsAfterTheAnswer")
        self.assertFalse(record["passed"])
        self.assertIn("0 replies sent", record["check"]["reason"])

    def test_the_wait_survives_a_restart_the_reply_comes_in_a_new_process(self):
        rid = "wait-restart"

        def child(step, fresh, text=""):
            done = subprocess.run([sys.executable, "-c", CHILD, T08, rid, BACKEND, fresh, step, THREAD, text,
                                   "hannah.lee@syn-corp.example"], cwd=ROOT, env=os.environ.copy(),
                                  capture_output=True, text=True, timeout=180)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            return done.stdout

        out = child("ask", "fresh")
        self.assertIn(f"WAITING_FOR {THREAD}", out, "the turn names the thread it waits on")
        child("reply", "keep", YES)                         # a new process: the app was restarted
        result = check_run(T08, rid)
        self.assertTrue(result["passed"], result["reason"])
        lines = read_trace(next((self.tmp / "traces").rglob(f"{rid}.trace.jsonl")))
        ends = [x["terminal_reason"] for x in lines if x["type"] == "end"]
        self.assertEqual(ends[0], bounds.WAITING_REPLY)
        self.assertIn("resumed", [x["type"] for x in lines])


class WhatNoRunTakes(ScratchTest):
    """Vipin Mourya's review: a message that no run waits for stayed in the channel, and no one was told
    (traces/s-20261117-215442-ce95.jsonl:312, the manager's "no" after her "yes"); and nothing read the
    HUMAN that our disclosure line offered until #65. Both now wait for the operator on /pending and the queue."""

    def outside_for_operator(self):
        from sandbox import pending
        return pending.open_items("outside_message")

    def test_a_message_that_no_run_waits_for_waits_for_the_operator(self):
        from sandbox import queue
        rid = "outside-unwaited"
        with fixed_run(T08, rid, backend=BACKEND) as run:
            reply = turns.outside_reply(THREAD, "SYNTHETIC. Hi IT, did someone want to ask me about Dev Rao?",
                                        sender="hannah.lee@syn-corp.example")
            items = self.outside_for_operator()
            lines = read_trace(run.trace_path)
            shown = [i["summary"] for i in queue.arrived()]
        self.assertIn("has no run that waits for it, so it waits for the operator on /pending as SYN-PEND-", reply.text)
        self.assertEqual([(i["employee_id"], i["thread_id"]) for i in items], [(HANNAH, THREAD)])
        self.assertTrue(any(s.startswith(f"{HANNAH} wrote on {THREAD} (has no run that waits for it)") for s in shown),
                        shown)
        self.assertEqual([x["why"] for x in lines if x["type"] == "outside_for_operator"],
                         ["has no run that waits for it"])

    def test_human_goes_to_the_operator_and_the_run_waits_on_for_the_managers_answer(self):
        from agent import approvals
        rid = "outside-human"
        with fixed_run(T08, rid, backend=BACKEND):
            asked = turns.operator_turn(rid, "Please handle the new access requests.")
            self.assertEqual(asked.waiting_for, THREAD)
            human = turns.outside_reply(THREAD, "SYNTHETIC. HUMAN. I would like to talk to a person first.",
                                        sender="hannah.lee@syn-corp.example")
            self.assertIn("asks for a person", human.text)
            self.assertIn("still waits for a reply", human.text)
            self.assertEqual([cp.waiting_for["thread_id"] for cp in checkpoints.waiting("reply")], [THREAD])
            self.assertEqual(approvals.pending(), [], "HUMAN is not her answer, so nothing is put up")
            self.assertEqual(len(self.outside_for_operator()), 1)
            turns.outside_reply(THREAD, YES, sender="hannah.lee@syn-corp.example")      # then her answer
            waiting = approvals.pending()
        self.assertEqual(len(waiting), 1, "her YES lets the grant be put up for the operator")
        self.assertIn("access_grant/manager_confirmed", json.dumps(waiting[0]))

    def test_one_open_record_a_thread_and_reason_and_none_on_a_thread_of_no_one(self):
        """Vipin Mourya's second review: a stranger could fill /pending, with one record a message on
        any thread ID, and the queue named the sender that the address claimed as if it were checked."""
        from sandbox import queue
        rid = "outside-flood"
        hannah = dict(sender="hannah.lee@syn-corp.example")
        with fixed_run(T08, rid, backend=BACKEND):
            first = turns.outside_reply(THREAD, "SYNTHETIC. Hi IT, a question about Dev.", **hannah)
            again = turns.outside_reply(THREAD, "SYNTHETIC. Hi IT, and a second question.", **hannah)
            human = turns.outside_reply(THREAD, "SYNTHETIC. HUMAN, please.", **hannah)
            no_one = turns.outside_reply("SYN-MAIL-SYN-EMP-9999", "SYNTHETIC. HUMAN.", sender="x@example.com")
            made_up = turns.outside_reply("SYN-NOT-A-THREAD", "SYNTHETIC. Hello?", sender="x@example.com")
            items = self.outside_for_operator()
            shown = [i["summary"] for i in queue.arrived() if THREAD in i["summary"]]
        self.assertIn("so it waits for the operator on /pending as", first.text)
        self.assertIn(f"It joins {items[0]['id']}, which already waits for the operator", again.text)
        self.assertIn("so it waits for the operator on /pending as", human.text, "a HUMAN gets its own record")
        self.assertEqual([(i["thread_id"], i["reason"]) for i in items],
                         [(THREAD, "has no run that waits for it"), (THREAD, "asks for a person")])
        for reply in (no_one, made_up):
            self.assertIn("is not kept for the operator", reply.text)
        self.assertEqual(len(shown), 2)
        self.assertTrue(all(s.endswith(queue.UNCHECKED_SENDER) for s in shown), shown)

    def test_a_message_that_the_thread_does_not_have_is_keyed_by_its_text(self):
        """Vipin Mourya's review of #66: the key ended in "-unknown"."""
        from unittest import mock

        from agent import service
        with fixed_run(T08, "outside-not-on-thread", backend=BACKEND), \
                mock.patch.object(channel, "messages", return_value=[]):
            service._for_the_operator(THREAD, "SYNTHETIC. HUMAN, please.", None, "asks for a person")
            items = self.outside_for_operator()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["key"].startswith(f"outside-{THREAD}-text-"), items[0]["key"])

    def test_a_reply_to_a_stopped_run_is_left_for_resume(self):
        from agent import control
        rid = "outside-stopped"
        with fixed_run(T08, rid, backend=BACKEND):
            turns.operator_turn(rid, "Please handle the new access requests.")
            cp = checkpoints.waiting("reply")[0]
            control.stop(rid, cp.run_no)
            turns.outside_reply(THREAD, YES, sender="hannah.lee@syn-corp.example")
            self.assertEqual(self.outside_for_operator(), [], "/resume reads the thread")


class TheDeadline(ScratchTest):
    def test_past_the_deadline_the_next_turn_ends_the_run_and_leaves_the_request_for_the_operator(self):
        rid = "wait-deadline"
        with fixed_run(T05, rid, backend=BACKEND):
            first = turns.operator_turn(rid, "Please handle the new access requests.")
            self.assertEqual(first.waiting_for, THREAD)
            saved = checkpoints.load(f"{rid}#1")
            self.assertEqual((saved.status, saved.waiting_for["thread_id"], saved.waiting_for["deadline"]),
                             ("waiting_reply", THREAD, "2026-10-23T09:00:00Z"), "Wednesday + two working days")
            on_time = turns.operator_turn(rid, "Is anything still open?")
            self.assertNotIn("none came", on_time.text)
            clock.set_now("2026-10-23T09:00:01Z")
            late = turns.operator_turn(rid, "Is anything still open?")
            status = requests.get(REQ)["status"]
            after = turns.outside_reply(THREAD, YES, sender="hannah.lee@syn-corp.example")
        self.assertIn("none came, so it ended", late.text)
        self.assertEqual(checkpoints.load(f"{rid}#1").findings["end_reason"], bounds.REPLY_DEADLINE)
        self.assertEqual(status, "pending_operator")
        self.assertIsNone(after.waiting_for, "a late reply continues nothing")
        self.assertIn("has no run that waits for it, so it waits for the operator on /pending", after.text)

    def test_the_deadline_skips_the_weekend(self):
        self.assertEqual(bounds.reply_deadline("2026-10-23T09:00:00Z"), "2026-10-27T09:00:00Z")   # Friday -> Tuesday
        self.assertEqual(bounds.reply_deadline("2026-10-21T09:00:00Z"), "2026-10-23T09:00:00Z")   # Wednesday -> Friday


class T05StillPasses(ScratchTest):
    def test_t05_passes_when_the_agent_asks_the_manager_and_no_reply_comes(self):
        record = self.run_task(T05, "aman", backend=BACKEND)
        self.assertTrue(record["passed"], record["check"])
        with fixed_run(T05, "t05-asks", backend=BACKEND):
            turns.operator_turn("t05-asks", "Please handle the new access requests.")
            sent = [m for m in channel.messages(THREAD, run_id="t05-asks") if m.sender == channel.AGENT]
        self.assertEqual(len(sent), 1, "the question went out once")


if __name__ == "__main__":
    import unittest
    unittest.main()
