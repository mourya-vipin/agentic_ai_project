"""Messages mid-task (Part 2, task 7): each one is sent from a second thread while a turn runs.
Owner: Aman Panigrahi. No tokens: the models are fakes (free = True).

    carry on     "Also ...": the sandbox holds the earlier work once, plus the addition
    start over   "Forget that. Instead ...": the run ends "replaced" before its next step; after
                 the message the old run writes nothing more, and the operator sees what it wrote;
                 a fresh run takes the new message, and the operator's /rollback undoes the old
                 run's grant, while its draft waits on /pending for the operator to dismiss
    queue        a question waits, and it is taken as a new message when the turn ends
    reject       past the queue's size, the operator knows at once
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from types import SimpleNamespace

from agent import bounds, inbox
from harness import runtime, turns
from harness.client import Usage
from harness.trace import read_trace
from sandbox import access, pending
from tests import fakes
from tests.support import ScratchTest, fixed_run

T04 = "tasks/t04_same_access_as.yaml"
LEO, REQ, GROUP = "SYN-EMP-0031", "SYN-REQ-0001", "SYN-GRP-JIRA-WRITE"
FINREP_READ = "SYN-GRP-FINREP-READ"               # a group Leo's role allows (t04)
PROCESS = "Please handle the new access requests."
ADDS = "Also propose Jira write access for Leo, for me to review."
REPLACES = "Forget that. Instead, just tell me which requests are open."
ASKS = "How far along are you?"


class PausesInItsFirstStep(fakes.RouterFake):
    """Routes the request to U4. Its first step waits until the test has sent the message
    mid-task (the gate), then drafts the reply to Leo. If the conversation then holds the
    operator's addition, it proposes the group. Then "Done."."""
    entered, gate = threading.Event(), threading.Event()

    def create(self, *, system, messages, tools):
        if not tools:
            return super().create(system=system, messages=messages, tools=tools)
        said = [m for m in messages if m["role"] == "assistant"]
        if not said:
            PausesInItsFirstStep.entered.set()
            PausesInItsFirstStep.gate.wait(30)
            return ([{"type": "tool_use", "id": "toolu_draft", "name": "reply_draft",
                      "input": {"recipient_employee_id": LEO, "request_id": REQ,
                                "body": "SYNTHETIC. Hi Leo, your request is with the IT team."}}],
                    "tool_use", self.name, Usage(40, 10))
        added = "adding to the task] " + ADDS in json.dumps(messages)
        proposed = "access_propose_change" in json.dumps([m for m in said])
        if added and not proposed:
            return ([{"type": "tool_use", "id": "toolu_propose", "name": "access_propose_change",
                      "input": {"action": "grant", "employee_id": LEO, "group_id": GROUP, "request_id": REQ,
                                "reason": "the operator asked for it mid-task"}}], "tool_use", self.name, Usage(40, 10))
        return [{"type": "text", "text": "Done."}], "end_turn", self.name, Usage(40, 2)


class ReplacedByAQuestion(PausesInItsFirstStep):
    """As above, but its first step also grants Leo a group his role allows, so the old run has
    a write that can be undone. The operator router routes the replacement as a question, and
    U5 answers it: the new task."""
    ANSWER = "SYN-REQ-0001 is open: its reply to Leo waits on /pending."

    def create(self, *, system, messages, tools):
        if not tools and "groups_mentioned" not in (system or ""):
            asked = json.dumps(messages).rsplit("The operator's message:", 1)[-1]
            intent = "question" if "Forget that" in asked else "process_requests"
            return [{"type": "text", "text": json.dumps({"intent": intent, "request_ids": []})}], "end_turn", \
                self.name, Usage(40, 20)
        if tools and "reply_draft" not in {t["name"] for t in tools}:       # U5, the new task
            return [{"type": "text", "text": self.ANSWER}], "end_turn", self.name, Usage(40, 12)
        content, stop, name, usage = super().create(system=system, messages=messages, tools=tools)
        if tools and not any(m["role"] == "assistant" for m in messages):
            content = content + [{"type": "tool_use", "id": "toolu_grant", "name": "access_grant",
                                  "input": {"employee_id": LEO, "group_id": FINREP_READ, "request_id": REQ}}]
        return content, stop, name, usage


class MidTaskTest(ScratchTest):
    def setUp(self):
        super().setUp()
        PausesInItsFirstStep.entered, PausesInItsFirstStep.gate = threading.Event(), threading.Event()

    def during_a_turn(self, rid: str, message: str, backend: str = "tests.test_midtask:PausesInItsFirstStep"):
        """The first turn in a thread; the message from this thread once the run's first step has begun."""
        with fixed_run(T04, rid, backend=backend) as run:
            done = {}
            first = threading.Thread(target=lambda: done.update(reply=turns.operator_turn(rid, PROCESS)))
            first.start()
            self.assertTrue(PausesInItsFirstStep.entered.wait(60), "the run's first step began")
            mid = turns.operator_turn(rid, message)
            PausesInItsFirstStep.gate.set()
            first.join(120)
            self.assertFalse(first.is_alive())
            kinds = dict(Counter(p["kind"] for p in pending.by_request(REQ)))
            return mid, done["reply"], kinds, read_trace(run.trace_path)

    def test_a_message_that_adds_to_the_task_keeps_the_work_and_adds_to_it(self):
        mid, reply, kinds, lines = self.during_a_turn("midtask-add", ADDS)
        self.assertIn("It adds your message to its task", mid.text)
        self.assertEqual(kinds, {"reply_draft": 1, "proposed_change": 1}, "the earlier work once, plus the addition")
        self.assertIn("midtask_taken", [x["type"] for x in lines])

    def test_a_message_that_replaces_the_task_stops_the_run_and_lists_what_it_wrote(self):
        mid, reply, kinds, lines = self.during_a_turn("midtask-replace", REPLACES)
        self.assertIn("Your message replaces its task", mid.text)
        self.assertEqual(kinds, {"reply_draft": 1}, "the old run writes nothing after the message")
        self.assertIn(bounds.REPLACED, [x.get("terminal_reason") for x in lines if x["type"] == "end"])
        self.assertIn("stopped, because your message replaced its task. It had written:", reply.text)
        self.assertIn("/rollback 1 undoes what can be undone.", reply.text)
        self.assertIn(REPLACES, [x["text"] for x in lines if x["type"] == "operator"])

    def test_after_a_replacement_the_new_task_runs_and_the_operator_picks_what_to_undo(self):
        rid = "midtask-undo"
        mid, reply, kinds, lines = self.during_a_turn(rid, REPLACES, backend="tests.test_midtask:ReplacedByAQuestion")
        self.assertIn(f"access_grant (employee_id={LEO}, group_id={FINREP_READ}, request_id={REQ})", reply.text)
        self.assertIn("Or pick what to undo, by its number", reply.text)            # the operator picks
        self.assertIn(f"access_grant of {FINREP_READ} for {LEO} → access_revoke", reply.text)
        routes = [x["intent"] for x in lines if x["type"] == "route" and x.get("scope") == "operator"]
        self.assertEqual(routes, ["process_requests", "question"], "a fresh run takes the new message")
        self.assertIn(ReplacedByAQuestion.ANSWER, reply.text, "the new task's answer")
        self.assertIn(FINREP_READ, {m["group_id"] for m in access.memberships(LEO)})
        with fixed_run(T04, rid, backend="tests.test_midtask:ReplacedByAQuestion", fresh=False):
            undone = turns.operator_turn(rid, "/rollback 1")          # the operator undoes what can be undone
        self.assertIn(f"access_grant → access_revoke {FINREP_READ}: done", undone.text)
        self.assertIn("reply_draft: none (the operator dismisses a pending record on /pending)", undone.text)
        self.assertNotIn(FINREP_READ, {m["group_id"] for m in access.memberships(LEO)})
        self.assertEqual(kinds, {"reply_draft": 1}, "the draft stays until the operator dismisses it")

    def test_a_question_waits_and_is_taken_when_the_turn_ends(self):
        mid, reply, kinds, lines = self.during_a_turn("midtask-queue", ASKS)
        self.assertIn("Your message waits", mid.text)
        self.assertEqual(kinds, {"reply_draft": 1})
        routes = [x for x in lines if x["type"] == "route" and x.get("scope") == "operator"]
        self.assertEqual(len(routes), 2, "the question went to the router once the turn ended")


class TheWays(ScratchTest):
    def test_our_code_reads_the_way_from_the_words(self):
        self.assertEqual(inbox.way_of(ADDS), inbox.ADD)
        self.assertEqual(inbox.way_of("Do it for Priya as well."), inbox.ADD)
        self.assertEqual(inbox.way_of(REPLACES), inbox.REPLACE)
        self.assertEqual(inbox.way_of("Never mind, do SYN-REQ-0002 instead."), inbox.REPLACE)
        self.assertEqual(inbox.way_of(ASKS), inbox.QUEUE)

    def test_past_the_queues_size_a_message_is_refused_at_once(self):
        runtime.configure(backend="never-stops")
        cp = SimpleNamespace(run_key="s-mid#1", run_no=1)
        replies = [inbox.post("s-mid", f"Question {n}?", cp) for n in range(inbox.MAX_QUEUED + 1)]
        self.assertIn("this one is not kept", replies[-1].text)
        self.assertTrue(all("waits" in r.text for r in replies[:-1]))
        self.assertEqual(len(inbox.after_turn("s-mid")), inbox.MAX_QUEUED)


# -- a message to a run that waits for a stranger (Part 2, task 7) ------------------------------

T08 = "tasks/t08_ask_manager.yaml"
DEV, HANNAH_THREAD = "SYN-EMP-0052", "SYN-MAIL-SYN-EMP-0060"
GITHUB_ENG = "SYN-GRP-GITHUB-ENG"
WAIT_ADDS = "Also tell Arjun that the GitHub group can take a day to sync."
WAIT_REPLACES = "Forget that. Instead, just tell me which requests are open."
HER_YES = "SYNTHETIC. YES. Dev is on my team now, so he can be in the GitHub engineers group."


class AsksThenHearsTheOperator(__import__("tests.test_wait", fromlist=["x"]).AsksTheManagerThenGrants):
    """t08's fake (it asks Dev's manager and waits), which keeps what each U4 call carried, and
    routes the replacement as a question that U5 answers."""
    seen: list[str] = []
    ANSWER = "SYN-REQ-0001 is open: it waits for Dev's manager."

    def create(self, *, system, messages, tools):
        if not tools and "groups_mentioned" not in (system or ""):
            asked = json.dumps(messages).rsplit("The operator's message:", 1)[-1]
            intent = "question" if "Forget that" in asked else "process_requests"
            return [{"type": "text", "text": json.dumps({"intent": intent, "request_ids": []})}], "end_turn", \
                self.name, Usage(40, 20)
        if tools and "reply_send" not in {t["name"] for t in tools}:       # U5, the new task
            return [{"type": "text", "text": self.ANSWER}], "end_turn", self.name, Usage(40, 12)
        if tools:
            AsksThenHearsTheOperator.seen.append(json.dumps(messages))
        return super().create(system=system, messages=messages, tools=tools)


class WhileARunWaits(ScratchTest):
    """A run that waits for a stranger has not finished: a message that adds to its task reaches
    it when the reply resumes it, and one that replaces its task ends it at once."""

    def setUp(self):
        super().setUp()
        AsksThenHearsTheOperator.seen = []

    def approve_all(self):
        from agent import approvals
        for _ in range(5):
            waiting = approvals.pending()
            if not waiting:
                return
            turns.decide_approval(waiting[0]["id"], True, "the operator approves it")

    def test_a_message_that_adds_reaches_the_run_when_the_reply_resumes_it(self):
        import channel
        from tests.support import check_run
        rid = "midtask-wait-add"
        with fixed_run(T08, rid, backend="tests.test_midtask:AsksThenHearsTheOperator"):
            first = turns.operator_turn(rid, PROCESS)
            self.assertEqual(first.waiting_for, HANNAH_THREAD)
            mid = turns.operator_turn(rid, WAIT_ADDS)
            self.assertIn("waits for a reply. Your message is added to its task", mid.text)
            before = len(AsksThenHearsTheOperator.seen)
            self.assertFalse(any(WAIT_ADDS in c for c in AsksThenHearsTheOperator.seen))   # not before the reply
            turns.outside_reply(HANNAH_THREAD, HER_YES, sender="hannah.lee@syn-corp.example")
            self.approve_all()
        after = AsksThenHearsTheOperator.seen[before:]
        self.assertTrue(after and all("adding to the task] " + WAIT_ADDS in c for c in after))
        result = check_run(T08, rid)
        self.assertTrue(result["passed"], result["reason"])                  # the earlier work once, then on
        asked = [m for m in channel.messages(HANNAH_THREAD) if m.sender in channel.OURS]
        self.assertEqual(len(asked), 1, "the question went once")

    def test_a_message_that_replaces_ends_the_waiting_run_and_her_late_reply_runs_nothing(self):
        from agent import checkpoints
        rid = "midtask-wait-replace"
        with fixed_run(T08, rid, backend="tests.test_midtask:AsksThenHearsTheOperator"):
            turns.operator_turn(rid, PROCESS)
            reply = turns.operator_turn(rid, WAIT_REPLACES)
            self.assertIn("stopped, because your message replaced its task. It had written:", reply.text)
            self.assertIn(AsksThenHearsTheOperator.ANSWER, reply.text)          # the new task's answer
            self.assertEqual(checkpoints.load(f"{rid}#1").status, checkpoints.CANCELLED)
            calls = len(AsksThenHearsTheOperator.seen)
            late = turns.outside_reply(HANNAH_THREAD, HER_YES, sender="hannah.lee@syn-corp.example")
            self.assertEqual(len(AsksThenHearsTheOperator.seen), calls, "no run took her reply")
            self.assertNotIn(GITHUB_ENG, {m["group_id"] for m in access.memberships(DEV)})
            told = pending.open_items("outside_message")
        self.assertIsNotNone(late)
        self.assertEqual(len(told), 1, "her reply goes to the operator on /pending, as no run waits for it")


if __name__ == "__main__":
    import unittest
    unittest.main()
