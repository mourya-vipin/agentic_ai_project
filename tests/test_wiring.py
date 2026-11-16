"""The wiring of Milestone 2 in the router, the loop, the workflows and the service.
Owner: Aman Panigrahi. No tokens: every model here is a fake (free = True).

    the order of operator_turn   a command first, then the stated limits, then the router
    the runs                     each request, question and bulk change is one run, and
                                 every call carries its key and its origin
    the stop                     a stopped run makes no more model calls and is saved as stopped
    the context                  every model call is built by context.assemble, and the
                                 compaction holds
    the outside reply            the run that waits on the thread continues
"""

from __future__ import annotations

from unittest import mock

from agent import checkpoints, context, control, disclosure, loop, notes, router, service, session
from harness import runtime
from harness.client import Usage
from harness.trace import read_trace
from policy import limits
from tests import fakes
from tests.aman_support import TaskRunTest
from tests.support import ScratchTest, fixed_run

SID = "s-wiring"
THREAD = "SYN-MAIL-SYN-EMP-0002"


class StopsDuringTheFirstStep(fakes.RouterFake):
    """Routes the case to U4. In the first step of the loop, the operator stops the run
    (as /stop from another tab does), and the model asks for a read."""

    def create(self, *, system, messages, tools):
        if tools and not any(m["role"] == "assistant" for m in messages):
            control.stop(runtime.current_run().run_id)
            return ([{"type": "tool_use", "id": "toolu_read", "name": tools[0]["name"], "input": {}}],
                    "tool_use", self.name, Usage(40, 10))
        return super().create(system=system, messages=messages, tools=tools)


class SendsAReply(fakes.RouterFake):
    """Routes the case to U4, where the model writes a reply without the disclosure line."""

    def create(self, *, system, messages, tools):
        if tools and not any(m["role"] == "assistant" for m in messages):
            return ([{"type": "tool_use", "id": "toolu_send", "name": "reply_send",
                      "input": {"recipient_employee_id": "SYN-EMP-0002", "request_id": None, "body": "Hello."}}],
                    "tool_use", self.name, Usage(40, 10))
        return super().create(system=system, messages=messages, tools=tools)


class TheOrderOfAnOperatorTurn(ScratchTest):
    """A command first, then the stated limits, then the router."""

    def turn(self, run_id: str, *texts: str):
        from harness import turns
        with fixed_run("tasks/t07_unusual_review.yaml", run_id, backend="tests.fakes:RoutesQuestion") as run:
            replies = [turns.operator_turn(run_id, t) for t in texts]
            return replies, read_trace(run.trace_path)

    def test_a_command_gets_its_answer_from_code_and_no_model_sees_it(self):
        replies, lines = self.turn("wiring-command", "/stop")
        self.assertIn("stop", replies[0].text.lower())
        self.assertEqual([x for x in lines if x["type"] == "model_call"], [], "no model call for a command")
        self.assertEqual(session.history("wiring-command"), [], "a command does not go into the conversation")

    def test_a_stated_limit_becomes_a_rule_and_the_reply_says_so(self):
        replies, _ = self.turn("wiring-limit", "No grants of Jira editors today, the migration runs until tonight.")
        self.assertTrue(replies[0].text.startswith("Rule L1 is recorded. It holds until "), replies[0].text)
        self.assertEqual([r.id for r in limits.active("wiring-limit")], ["L1"])
        self.assertEqual(session.history("wiring-limit")[0]["role"], "operator", "the message still goes on")

    def test_a_message_can_lift_the_rules(self):
        replies, _ = self.turn("wiring-lift", "Send nothing until I have read it.", "Lift all limits.")
        self.assertTrue(replies[1].text.startswith("Rules lifted: L1."), replies[1].text)
        self.assertEqual(limits.active("wiring-lift"), [])

    def test_a_turn_writes_its_async_checkpoints_before_it_replies(self):
        with mock.patch.object(checkpoints, "flush", wraps=checkpoints.flush) as flush:
            self.turn("wiring-flush", "What did you do today?")
        self.assertGreaterEqual(flush.call_count, 1)


class EachRunCarriesItsKey(TaskRunTest):
    """Each request, question and bulk change is one run, and each of its
    calls carries the run key and its origin."""

    def calls(self) -> list[dict]:
        return self.of("tool_call")

    def key(self, n: int = 1) -> str:
        return f"{self.record['run_id']}#{n}"

    def test_a_u4_request_is_one_run_and_every_call_carries_its_key(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenSetsDone")
        self.assertEqual([x["run_key"] for x in self.of("run_opened")], [self.key()])
        self.assertTrue(self.calls())
        self.assertEqual({c["run_key"] for c in self.calls()}, {self.key()})
        self.assertIn("agent", {c["origin"] for c in self.calls()}, "the model's calls")
        final = self.of("checkpoint_final")
        self.assertEqual([(f["run_key"], f["checkpoint"]["use_case"]) for f in final],
                         [(self.key(), "nonstandard_request")])

    def test_a_workflow_run_carries_its_key_and_ends_done(self):
        self.run_on("t01_standard_jira", "RoutesStandardGrant")
        self.assertEqual({(c["run_key"], c["origin"]) for c in self.calls()}, {(self.key(), "workflow")})
        final = self.of("checkpoint_final")[-1]["checkpoint"]
        self.assertEqual((final["use_case"], final["status"], final["findings"]["end_reason"]),
                         ("standard_grant", "done", "workflow_done"))

    def test_every_request_of_a_turn_is_its_own_run(self):
        self.run_on("sessions/s01_operator_day", "RoutesToAgent")
        opened = [x["run_key"] for x in self.of("run_opened")]
        self.assertEqual(opened[:5], [self.key(n) for n in range(1, 6)], "the five requests of the first turn")
        replies = self.of("reply")
        self.assertTrue(replies[0]["text"].startswith("Rule L1 is recorded."), replies[0]["text"])

    def test_the_planned_bulk_change_gets_the_key_of_its_run(self):
        self.run_on("t06_bulk_ended_contractors", "RoutesBulk", overrides=["plan.bulk_change=on"])
        planner = [x for x in self.of("context") if x["call_kind"] == "planner"]
        self.assertTrue(planner)
        self.assertEqual({x["run_key"] for x in planner}, {self.key()})
        self.assertEqual(self.of("checkpoint_final")[-1]["checkpoint"]["findings"]["path"], "planned")

    def test_every_model_call_is_built_by_assemble(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops", overrides=["context_budget=400000"])
        kinds = [x["call_kind"] for x in self.of("context")]
        self.assertEqual(kinds[:2], ["router", "router"], "the operator router, then the request router")
        loop_calls = [x for x in self.of("model_call") if x.get("purpose") == "nonstandard_request"]
        built = [x for x in self.of("context") if x["call_kind"] == "loop"]
        self.assertEqual(len(built), len(loop_calls), "one context line for each model call of the loop")
        self.assertEqual({x["run_key"] for x in built}, {self.key()})

    def test_a_call_that_cannot_fit_ends_the_turn_before_any_model_call(self):
        record = self.run_on("t04_same_access_as", "RoutesToAgent", overrides=["context_budget=50"])
        self.assertEqual(self.of("model_call"), [])
        self.assertIn(context.BUDGET_UNFIT, record["end_reasons"])

    def test_the_text_of_a_send_gets_the_disclosure_line_before_the_registry(self):
        self.run_on("t04_same_access_as", "tests.test_wiring:SendsAReply")
        sent = [c for c in self.calls() if c["name"] == "reply_send"]
        self.assertTrue(sent)
        self.assertTrue(disclosure.has_line(sent[0]["input"]["body"]))


class TheStopEndsTheLoop(TaskRunTest):
    def test_a_run_stopped_during_its_turn_makes_no_more_model_calls_and_is_saved_as_stopped(self):
        record = self.run_on("t04_same_access_as", "tests.test_wiring:StopsDuringTheFirstStep")
        key = f"{record['run_id']}#1"
        loop_calls = [x for x in self.of("model_call") if x.get("purpose") == "nonstandard_request"]
        self.assertEqual(len(loop_calls), 1, "the loop checks the flag before its next model call")
        self.assertIn(control.END_STOPPED, record["end_reasons"])
        paused = self.of("checkpoint_paused")[-1]
        self.assertEqual((paused["run_key"], paused["checkpoint"]["status"], paused["checkpoint"]["waiting_for"]),
                         (key, "stopped", {"kind": "stop"}))
        self.assertEqual([x["run_key"] for x in self.of("run_opened")], [key], "a later turn starts no new run")
        later = [x["text"] for x in self.of("reply")][1:]
        self.assertTrue(any("run 1 is stopped (use /resume or /cancel)" in t for t in later), later)

    def test_in_a_run_py_run_a_stopped_run_of_an_earlier_run_does_not_hold_the_request(self):
        runtime.configure()
        earlier = checkpoints.new_run("an-earlier-sweep", "nonstandard_request", "task")
        earlier.findings = {"request_id": "SYN-REQ-0001"}
        checkpoints.end_turn(earlier, "stopped")
        record = self.run_on("t04_same_access_as", "RoutesToAgentThenSetsDone")
        self.assertEqual([x["run_key"] for x in self.of("run_opened")], [f"{record['run_id']}#1"])


class TheRunStore(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def test_end_turn_gives_each_end_reason_its_status(self):
        cases = {"answered": ("done", None), "workflow_done": ("done", None), "plan_done": ("done", None),
                 "awaiting_approval": ("waiting_approval", "approval"), "stopped": ("stopped", "stop"),
                 "cancelled": ("cancelled", None), "step_limit": ("failed", None), "budget_unfit": ("failed", None)}
        for reason, (status, kind) in cases.items():
            cp = checkpoints.new_run(SID, "nonstandard_request", "task")
            checkpoints.end_turn(cp, reason)
            saved = checkpoints.load(cp.run_key)
            self.assertEqual((saved.status, (saved.waiting_for or {}).get("kind")), (status, kind), reason)
            self.assertEqual(saved.findings["end_reason"], reason)

    def test_a_reply_wait_keeps_its_thread(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "task")
        checkpoints.end_turn(cp, "waiting_reply", waiting_for={"thread_id": THREAD})
        self.assertEqual([c.waiting_for for c in checkpoints.waiting("reply")], [{"thread_id": THREAD, "kind": "reply"}])

    def test_each_use_case_has_its_durability_mode(self):
        self.assertEqual({u: checkpoints.mode_for(u) for u in checkpoints.DURABILITY},
                         {"standard_grant": "async", "onboarding": "sync", "offboarding": "async",
                          "nonstandard_request": "sync", "operator_review": "exit", "bulk_change": "sync"})
        self.assertEqual(checkpoints.mode_for("something_new"), "sync")

    def test_the_stop_flag_decides_the_end_of_a_run_that_is_not_done(self):
        stopped = checkpoints.new_run(SID, "nonstandard_request", "task")
        control.stop(SID, stopped.run_no)
        self.assertEqual(router._end_of(stopped.run_key, "step_limit"), "stopped")
        self.assertEqual(router._end_of(stopped.run_key, "done"), "done", "a run that is done stays done")
        control.cancel(stopped.run_key)
        self.assertEqual(router._end_of(stopped.run_key, "step_limit"), "cancelled")
        other = checkpoints.new_run(SID, "nonstandard_request", "task")
        self.assertEqual(router._end_of(other.run_key, "step_limit"), "step_limit")

    def test_a_planned_bulk_change_continues_in_the_executor(self):
        cp = checkpoints.new_run(SID, "bulk_change", "task")
        cp.findings = {"path": "planned"}
        self.assertEqual(checkpoints._resumer(cp)[1], checkpoints.PLANNED_RESUMER)
        cp.findings = {"path": "loop"}
        self.assertEqual(checkpoints._resumer(cp)[1], "agent.loop:resume")


class TheOutsideReply(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def waiting_run(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "task")
        return checkpoints.end_turn(cp, "waiting_reply", waiting_for={"thread_id": THREAD})

    def test_the_run_that_waits_on_the_thread_continues_and_the_notes_hear_of_it(self):
        cp = self.waiting_run()
        with mock.patch.object(notes, "thread_answered") as answered, \
                mock.patch.object(checkpoints, "resume", return_value=service.Reply("carried on")) as resume:
            reply = service.on_outside_reply(THREAD, "SYNTHETIC. YES, that is fine.")
        self.assertEqual(reply.text, "carried on")
        resume.assert_called_once_with(cp.run_key, {"kind": "reply", "thread_id": THREAD,
                                                    "text": "SYNTHETIC. YES, that is fine."})
        answered.assert_called_once_with(SID, THREAD)

    def test_until_the_loop_can_resume_the_run_stays_saved_and_the_reply_says_so(self):
        cp = self.waiting_run()
        reply = service.on_outside_reply(THREAD, "SYNTHETIC. YES.")
        self.assertIn("cannot continue it yet", reply.text)
        self.assertEqual(checkpoints.load(cp.run_key).status, "waiting_reply")

    def test_in_a_run_the_notes_of_the_run_hear_of_a_reply_that_no_run_waits_on(self):
        from harness import turns
        with fixed_run("tasks/t05_on_behalf.yaml", "wiring-outside"), \
                mock.patch.object(notes, "thread_answered") as answered:
            turns.outside_reply(THREAD, "SYNTHETIC. YES.")
        answered.assert_called_once_with("wiring-outside", THREAD)

    def test_a_reply_on_a_thread_that_no_run_waits_on_continues_nothing(self):
        self.waiting_run()
        with mock.patch.object(checkpoints, "resume") as resume:
            reply = service.on_outside_reply("SYN-MAIL-SYN-EMP-0099", "SYNTHETIC. Hello?")
        self.assertEqual((reply.text, reply.actions, reply.waiting_for), ("", [], None))
        resume.assert_not_called()


class TheCompactionHolds(ScratchTest):
    def test_after_a_compaction_the_loop_keeps_the_compacted_messages_without_the_notes(self):
        task = {"role": "user", "content": "the task"}
        step = {"role": "assistant", "content": [{"type": "text", "text": "a step"}]}
        last = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}
        with_notes = {"role": "user", "content": last["content"] + [{"type": "text", "text": "[Notes ...]"}]}
        summary = {"role": "user", "content": "the task\n\n[Summary of the earlier steps ...]"}
        built = context.Assembled("system", [summary, step, with_notes], [], 10, compacted=True)
        self.assertEqual(loop._kept(built, [task, step, last]), [summary, step, last])

    def test_without_a_compaction_the_loop_keeps_its_own_messages(self):
        mine = [{"role": "user", "content": "the task"}]
        built = context.Assembled("system", [{"role": "user", "content": "the task\n\n[Notes ...]"}], [], 10)
        self.assertIs(loop._kept(built, mine), mine)


if __name__ == "__main__":
    import unittest
    unittest.main()
