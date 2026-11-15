"""The planned agent for U6 (Lecture 2, Sections A and D), on fake models, each on
t06's case with plan.bulk_change on: an invalid plan ends the run with nothing
stamped; an off-plan call is refused and traced; a valid plan runs in order; a
failing checkpoint gives a validated, logged revision, and an invalid revision never
runs; the diff labels skipped steps. Plus the validator's rules. No tokens.
Owner: Shruti Tewaney."""

import json
import unittest
import uuid

import channel

from agent import approvals, disclosure, executor, validator
from agent.bounds import Bound
from agent.planner import U6_TOOLS
from harness.trace import read_trace
from sandbox import access, db, pending
from tests import fakes
from tests.support import ScratchTest, fixed_run

T06 = {"id": "t06_plan_test", "operator": [fakes.T06_TEXT], "outside": [], "check": "checks/example.py",
       "input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0164", "set": {"end_date": "2026-10-28"}}}


def stamped_rows(run_id: str) -> dict:
    """Every row any table holds stamped with this run."""
    with db.connect() as conn:
        return {
            "memberships": conn.execute("SELECT COUNT(*) AS n FROM memberships WHERE stamp = ? OR removed_stamp = ?",
                                        (run_id, run_id)).fetchone()["n"],
            **{table: conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE stamp = ?", (run_id,)).fetchone()["n"]
               for table in ("pending_actions", "requests", "notices_sent", "audit_log", "identity_keys")}}


class PlannedTest(ScratchTest):
    def run_planned(self, backend: str, overrides=None, bound: Bound | None = None, answer: bool | None = True):
        """Run t06's plan; then, as run.py's approvals: list does, answer each approval it waits
        on (`answer`; None leaves them waiting). Returns the last turn's result: the run's, or
        the reply of the answer that resumed it, with the trace's last end reason."""
        run_id = f"plan-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend=f"tests.fakes:{backend}", overrides=overrides) as run:
            result = executor.run_planned(run_id, fakes.T06_TEXT, bound=bound or Bound())
            self.asked = 0
            while answer is not None and (waiting := approvals.pending()) and self.asked < 5:
                self.asked += 1
                reply = approvals.decide(waiting[0]["id"], answer, "scripted", who="scripted")
                ends = [x["terminal_reason"] for x in read_trace(run.trace_path) if x["type"] == "end"]
                result = executor.LoopResult(reply.text, reply.actions, ends[-1] if ends else "", 0)
        self.run_id = run_id
        self.lines = read_trace(run.trace_path)
        return result

    def of(self, kind):
        return [x for x in self.lines if x["type"] == kind]

    def end_reasons(self):
        return [x["terminal_reason"] for x in self.of("end")]


class InvalidPlanTest(PlannedTest):
    def test_a_planner_that_always_writes_an_invalid_plan_ends_the_run_with_nothing_stamped(self):
        result = self.run_planned("BadPlanner")
        self.assertEqual(self.end_reasons(), [executor.PLAN_REJECTED])
        self.assertEqual(result.end_reason, executor.PLAN_REJECTED)
        self.assertEqual(len(self.of("plan_check")), executor.MAX_REPLANS + 1)
        self.assertFalse(any(x["valid"] for x in self.of("plan_check")))
        self.assertEqual(self.of("tool_call"), [])
        self.assertEqual(self.of("plan"), [])              # no version was ever accepted
        self.assertEqual(set(stamped_rows(self.run_id).values()), {0})
        self.assertIn("nothing was run", result.text)

    def test_the_planner_is_told_why_its_last_plan_was_rejected(self):
        self.run_planned("BadPlanner")
        plans = [x for x in self.of("model_call") if x["purpose"] == "plan"]
        self.assertEqual(len(plans), 3)


class OffPlanTest(PlannedTest):
    def test_an_off_plan_call_is_refused_and_traced(self):
        result = self.run_planned("OffPlanFiller")
        refused = self.of("off_plan")
        self.assertEqual(len(refused), executor.MAX_FILL_ATTEMPTS)
        self.assertEqual({(x["asked_for"], x["expected"], x["plan_step"]) for x in refused},
                         {("grant_access", "reply_send", 8)})
        self.assertFalse([x for x in self.of("tool_call") if x["name"] == "access_grant"])
        self.assertEqual(result.end_reason, executor.STEP_HALTED)
        self.assertFalse([m for m in access.memberships(fakes.HANNAH) if m["group_id"] == "SYN-GRP-PAYROLL-ADMIN"])


class ValidPlanTest(PlannedTest):
    def test_a_valid_plans_steps_run_in_order_and_the_run_ends_plan_done(self):
        result = self.run_planned("GoodPlannerWithHold")
        self.assertEqual(self.end_reasons(), [executor.AWAITING_APPROVAL, executor.PLAN_DONE])
        plan = self.of("plan")[-1]["plan"]
        order = [s["id"] for s in plan["steps"]]
        ran = [x["plan_step"] for x in self.of("tool_call")]
        self.assertEqual(ran, order)
        self.assertTrue(all(x["caller"] == "executor" for x in self.of("tool_call")))
        self.assertEqual(self.of("revision"), [])
        diff = self.of("plan_diff")[-1]
        self.assertEqual((diff["executed"], diff["skipped"], diff["out_of_order"], diff["extra"]),
                         (order, [], [], []))
        self.assertIn("Done.", result.text)

    def test_the_plan_is_logged_before_the_first_tool_call(self):
        self.run_planned("GoodPlannerWithHold")
        types = [x["type"] for x in self.lines]
        self.assertLess(types.index("plan"), types.index("tool_call"))

    def test_t06s_effects_are_in_the_sandbox(self):
        self.run_planned("GoodPlannerWithHold")
        for person in (fakes.ANA, fakes.MIRA):
            self.assertEqual(access.memberships(person), [])
        self.assertEqual([m["group_id"] for m in access.memberships(fakes.VICTOR)], [fakes.LEGAL_HOLD])
        self.assertTrue(access.memberships("SYN-EMP-0164"))             # ends next week: untouched
        mine = [p for p in pending.open_items() if p["stamp"] == self.run_id]
        self.assertEqual(sorted((p["kind"], p["employee_id"]) for p in mine), [("proposed_change", fakes.VICTOR)])
        for manager in (fakes.HANNAH, fakes.GRACE):                     # each note sent once, with the line
            sent = [m for m in channel.messages(f"SYN-MAIL-{manager}") if m.sender == channel.AGENT]
            self.assertEqual(len(sent), 1, manager)
            self.assertTrue(sent[0].text.endswith(disclosure.LINE))

    def test_the_bound_is_recorded_and_ends_a_run_that_reaches_it(self):
        result = self.run_planned("GoodPlannerWithHold", bound=Bound(max_steps=2))
        self.assertEqual(self.of("bound")[0]["max_steps"], 2)
        self.assertEqual(result.end_reason, "step_limit")
        # the second note's fill was the third call; at the plan, both notes are prepared before
        # either is sent, so neither went
        self.assertEqual(self.of("plan_diff")[-1]["skipped"], [8, 9])
        self.assertEqual(approvals.pending(), [])


class ThroughTheRouterTest(ScratchTest):
    """t06 as run.py runs it: the operator's turn, the router, the planned executor, and
    t06's own check reading the sandbox."""

    def test_t06_passes_its_check_on_the_planned_path(self):
        record = self.run_task("tasks/t06_bulk_ended_contractors.yaml", "plan-e2e", backend="tests.fakes:GoodPlanner",
                               overrides=["plan.bulk_change=on"])
        self.assertEqual(record["end_reasons"], [executor.AWAITING_APPROVAL, executor.PLAN_DONE])
        self.assertEqual(record["approvals"], {"asked": 1, "answered": 1, "unanswered": 0})
        self.assertTrue(record["passed"], record.get("reason"))

    def test_a_note_still_sees_results_its_dependencies_depend_on(self):
        """dev-t06_bulk_ended_contractors-s01-338ad1: the revision made Grace's note depend on
        the proposal alone, and the note never saw Victor's removals."""
        record = self.run_task("tasks/t06_bulk_ended_contractors.yaml", "plan-e2e",
                               backend="tests.fakes:ReplacingReviser", overrides=["plan.bulk_change=on"])
        self.assertEqual(record["end_reasons"], [executor.AWAITING_APPROVAL, executor.PLAN_DONE])
        self.assertTrue(record["passed"], record.get("reason"))

    def test_a_held_note_fails_t06s_check(self):
        record = self.run_task("tasks/t06_bulk_ended_contractors.yaml", "plan-e2e",
                               backend="tests.fakes:AlwaysWrongFiller", overrides=["plan.bulk_change=on"])
        self.assertEqual(record["end_reasons"], ["critique_held"])
        self.assertFalse(record["passed"])


class NothingToDoTest(PlannedTest):
    def test_with_no_ended_contractor_holding_access_no_plan_is_written(self):
        later = [{"kind": "fact", "table": "employees", "id": person, "set": {"end_date": "2026-12-31"}}
                 for person in (fakes.ANA, fakes.VICTOR, fakes.MIRA)]
        run_id = f"plan-{uuid.uuid4().hex[:8]}"
        task = {**{k: v for k, v in T06.items() if k != "input"}, "inputs": later}
        with fixed_run(task, run_id, backend="tests.fakes:GoodPlanner") as run:
            result = executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
        lines = read_trace(run.trace_path)
        self.assertEqual(result.end_reason, executor.NOTHING_TO_DO)
        self.assertEqual([x for x in lines if x["type"] in ("model_call", "plan", "tool_call")], [])
        self.assertEqual(set(stamped_rows(run_id).values()), {0})
        self.assertIn("nothing to do", result.text)


class CheckpointTest(PlannedTest):
    def test_a_failing_checkpoint_gives_a_revised_plan_validated_and_logged(self):
        self.run_planned("GoodPlanner")
        checks = self.of("checkpoint")
        failed = [x for x in checks if not x["passed"]]
        self.assertEqual([x["plan_step"] for x in failed], [4])
        self.assertIn(fakes.LEGAL_HOLD, failed[0]["reason"])
        self.assertEqual([x["version"] for x in self.of("plan")], [1, 2])
        self.assertEqual([(x["valid"], x["version"]) for x in self.of("revision")], [(True, 2)])
        v1, v2 = (x["plan"] for x in self.of("plan"))
        self.assertEqual(v2["steps"][:4], v1["steps"][:4])                # steps done stay as they were
        proposals = [x for x in self.of("tool_call") if x["name"] == "access_propose_change"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(self.end_reasons(), [executor.AWAITING_APPROVAL, executor.PLAN_DONE])

    def test_a_revision_that_fails_validation_never_runs(self):
        result = self.run_planned("BadReviser")
        self.assertEqual([x["valid"] for x in self.of("revision")], [False] * executor.MAX_REVISIONS)
        self.assertEqual([x["version"] for x in self.of("plan")], [1])
        self.assertEqual([x["plan_step"] for x in self.of("tool_call")], [1, 2, 3, 4])
        self.assertEqual(self.end_reasons(), [executor.REVISION_BOUND])
        self.assertEqual(result.end_reason, executor.REVISION_BOUND)


class UnsupportedInstructionTest(PlannedTest):
    """A bulk instruction the plan's tools do not support: the planner was always handed
    the ended contractors, and once planned their removal for "Give everyone in Finance
    read access to payroll" (a live console session, 2026-10-30)."""

    def test_a_plan_with_no_steps_ends_the_run_with_nothing_run_or_stamped(self):
        run_id = f"plan-{uuid.uuid4().hex[:8]}"
        text = "Give everyone in Finance read access to payroll."
        with fixed_run(T06, run_id, backend="tests.fakes:DecliningPlanner") as run:
            result = executor.run_planned(run_id, text, bound=Bound())
        lines = read_trace(run.trace_path)
        self.assertEqual(result.end_reason, executor.UNSUPPORTED)
        self.assertEqual([x["terminal_reason"] for x in lines if x["type"] == "end"], [executor.UNSUPPORTED])
        self.assertEqual(len([x for x in lines if x["type"] == "model_call" and x["purpose"] == "plan"]), 1)
        self.assertEqual([x for x in lines if x["type"] in ("plan", "tool_call", "plan_diff")], [])
        self.assertEqual([x["supported"] for x in lines if x["type"] == "plan_check"], [False])
        self.assertEqual(set(stamped_rows(run_id).values()), {0})
        self.assertTrue(access.memberships(fakes.ANA))         # the ended contractors keep their access
        self.assertIn("changed nothing", result.text)

    def test_a_reply_with_no_plan_call_is_still_rejected_and_re_planned(self):
        result = self.run_planned("NoPlanCall")
        self.assertEqual(result.end_reason, executor.PLAN_REJECTED)
        self.assertEqual(len(self.of("plan_check")), executor.MAX_REPLANS + 1)


class DiffTest(PlannedTest):
    def test_the_diff_labels_the_steps_a_halted_run_never_reached_as_skipped(self):
        # Hannah's note is empty every time: the critique holds it before the operator sees it,
        # so neither note is put up or sent
        result = self.run_planned("EmptyNoteFiller")
        diff = self.of("plan_diff")[-1]
        self.assertEqual(diff["skipped"], [8, 9])
        self.assertEqual(diff["failed"], [])
        self.assertEqual(self.end_reasons(), ["critique_held"])
        self.assertIn("Not run: plan steps 8, 9", result.text)


# -- the validator, on its own ----------------------------------------------------------

def good_plan():
    return fakes.t06_plan(with_hold=True)


def rules(plan, limit=13, known=None):
    known = known if known is not None else {fakes.ANA, fakes.VICTOR, fakes.MIRA, fakes.HANNAH, fakes.GRACE}
    return {rule for _, rule, _ in validator.validate(plan, list(U6_TOOLS), limit, known)}




class PlanApprovalTest(PlannedTest):
    """Approval at the plan and per action (Part 2, task 4), on U6's notes to the managers."""

    def sent(self):
        return [m for who in (fakes.HANNAH, fakes.GRACE) for m in channel.messages(f"SYN-MAIL-{who}")
                if m.sender == channel.AGENT]

    def test_at_the_plan_one_approval_lists_every_note_and_nothing_is_sent_before(self):
        result = self.run_planned("GoodPlannerWithHold", answer=None)
        self.assertEqual(result.end_reason, executor.AWAITING_APPROVAL)
        waiting = approvals.pending()
        self.assertEqual(len(waiting), 1)
        steps = approvals.steps_of(waiting[0]["id"])
        self.assertEqual([(s["plan_step"], s["action_class"]) for s in steps],
                         [(8, "manager_note/plan_listed"), (9, "manager_note/plan_listed")])
        self.assertTrue(all(s["args"]["body"].endswith(disclosure.LINE) for s in steps))
        self.assertEqual(self.sent(), [])                                  # nothing sent before the answer
        self.assertEqual(access.memberships(fakes.ANA), [])                # the reversible removals ran
        self.assertIn("Answer it on /approvals", result.text)

    def test_the_plan_screen_shows_each_note_as_sent_and_what_checked_it(self):
        self.run_planned("GoodPlannerWithHold", answer=None)
        item = approvals.pending()[0]
        self.assertEqual([k for k in item][2:4], ["action", "dry run"])
        self.assertIn("Step 8: send to SYN-EMP-0060", item["dry run"])
        self.assertIn("Step 9: send to SYN-EMP-0040", item["dry run"])
        self.assertEqual(item["dry run"].count(disclosure.LINE), 2)
        self.assertIn("passed", item["the critique on each note (computed)"])
        self.assertIn("removed", item["done before this, reversible (computed)"])
        self.assertIn("asks again", item["not listed here"])

    def test_once_approved_the_listed_notes_run_unasked_and_each_is_sent_once(self):
        self.run_planned("GoodPlannerWithHold")
        self.assertEqual(self.asked, 1)
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.end_reasons(), [executor.AWAITING_APPROVAL, executor.PLAN_DONE])
        calls = [x for x in self.of("tool_call") if x["name"] == "reply_send"]
        self.assertEqual([(x["plan_step"], x["approved"]) for x in calls], [(8, True), (9, True)])

    def test_per_action_asks_for_each_note_as_it_comes(self):
        self.run_planned("GoodPlannerWithHold", overrides=["approval.bulk_change=per_action"])
        self.assertEqual(self.asked, 2)
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.end_reasons(), [executor.AWAITING_APPROVAL, executor.AWAITING_APPROVAL,
                                              executor.PLAN_DONE])
        self.assertEqual([e["setting"] for e in approvals.log()], ["per_action", "per_action"])

    def test_a_denied_plan_sends_nothing_and_the_removals_stay(self):
        result = self.run_planned("GoodPlannerWithHold", answer=False)
        self.assertEqual(self.end_reasons(), [executor.AWAITING_APPROVAL, executor.APPROVAL_DENIED])
        self.assertEqual(self.sent(), [])
        self.assertEqual(access.memberships(fakes.ANA), [])
        self.assertIn("denied", result.text)

    def test_the_approval_covers_only_the_listed_text(self):
        self.run_planned("GoodPlannerWithHold", answer=None)
        apr = approvals.pending()[0]["id"]
        listed = approvals.steps_of(apr)[0]
        approvals.decide(apr, True, who="scripted")
        other = {**listed["args"], "body": "SYNTHETIC. Something else.\n\n" + disclosure.LINE}
        key = approvals.get(apr)["run_key"]
        self.assertFalse(approvals.covers(key, listed["action_class"], other))

    def test_a_stop_withdraws_the_plan_and_resume_asks_again_with_the_same_notes(self):
        from agent import commands, control
        run_id = f"plan-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold") as run:
            executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
            first = approvals.pending()[0]
            key = approvals.get(first["id"])["run_key"]
            commands.handle(run_id, "/stop")
            self.assertEqual(approvals.pending(), [])
            fills = len([x for x in read_trace(run.trace_path) if x["type"] == "fill"])
            control.resume(key)
            again = approvals.pending()
            self.assertEqual(len(again), 1)
            self.assertNotEqual(again[0]["id"], first["id"])
            self.assertEqual(again[0]["dry run"], first["dry run"])          # the same notes, word for word
            lines = read_trace(run.trace_path)
            self.assertEqual(len([x for x in lines if x["type"] == "fill"]), fills)   # no new model calls
            fakes.approve_all()
        self.assertEqual(len(self.sent()), 2)

    def test_the_waiting_run_resumes_from_its_checkpoint_in_a_new_process(self):
        import importlib
        run_id = f"plan-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold"):
            executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
            importlib.reload(executor)                                         # nothing held in memory
            fakes.approve_all()
        self.assertEqual(len(self.sent()), 2)


class SendsLastTest(unittest.TestCase):
    def test_a_step_after_a_send_is_rejected(self):
        plan = fakes.t06_plan(with_hold=True)
        plan["steps"].append(fakes.plan_step(11, "list again", "access_list_memberships",
                                             {"employee_id": fakes.ANA}, [9], "non_empty", "memberships"))
        found = validator.validate(plan, list(U6_TOOLS), 13, {fakes.ANA, fakes.VICTOR, fakes.MIRA,
                                                              fakes.HANNAH, fakes.GRACE})
        self.assertIn("sends_last", {rule for _, rule, _ in found})
        self.assertNotIn("sends_last", {rule for _, rule, _ in validator.validate(
            fakes.t06_plan(with_hold=True), list(U6_TOOLS), 13, None)})


class AssembledCallsTest(PlannedTest):
    def test_every_call_is_assembled_and_the_planner_gets_no_notes(self):
        self.run_planned("GoodPlanner")                    # a plan, a revision and two fills
        built = self.of("context")
        purposes = [x["purpose"] for x in self.of("model_call")]
        self.assertEqual(len(built), len(purposes))
        self.assertEqual([x["call_kind"] for x in built],
                         ["executor" if p == "fill" else "planner" for p in purposes])
        self.assertEqual(sorted(set(purposes)), ["fill", "plan", "revise"])
        for line in built:
            if line["call_kind"] == "planner":
                self.assertFalse([o for o in line["left_out"] if o["what"] != "notes"])

    def test_a_call_over_the_budget_ends_the_run_budget_unfit(self):
        self.run_planned("GoodPlanner", overrides=["context_budget=50"])
        self.assertEqual(self.end_reasons(), ["budget_unfit"])
        self.assertFalse(self.of("model_call"))
        self.assertFalse(self.of("tool_call"))


class ValidatorTest(unittest.TestCase):
    def test_a_good_plan_is_valid(self):
        self.assertEqual(rules(good_plan()), set())

    def test_a_tool_that_does_not_exist(self):
        plan = good_plan()
        plan["steps"][2]["tool"] = "access_grant"          # a real tool, but not one a U6 plan may use
        self.assertEqual(rules(plan), {"tool_exists"})

    def test_a_step_that_depends_on_a_later_one_on_itself_or_on_a_missing_one(self):
        for deps in ([3], [2], [42]):
            plan = good_plan()
            plan["steps"][1]["depends_on"] = deps          # step 2
            self.assertEqual(rules(plan), {"depends_earlier"}, deps)

    def test_a_step_with_no_observable_outcome(self):
        plan = good_plan()
        plan["steps"][1]["expect"] = {"kind": "non_empty", "field": "", "value": ""}
        self.assertEqual(rules(plan), {"observable_outcome"})
        plan["steps"][1]["expect"] = {"kind": "count_equals", "field": "memberships", "value": "some"}
        self.assertEqual(rules(plan), {"observable_outcome"})
        plan["steps"][1]["expect"] = {"kind": "looks_fine", "field": "x", "value": ""}
        self.assertIn("schema", rules(plan))               # the enum catches it first

    def test_the_step_limit(self):
        self.assertEqual(rules(good_plan(), limit=9), {"step_limit"})
        self.assertEqual(rules({"steps": []}), {"step_limit"})
        self.assertEqual(validator.plan_step_limit(3), 12)
        self.assertEqual(validator.plan_step_limit(40), validator.plan_step_limit(validator.MAX_PEOPLE))

    def test_an_employee_the_planner_was_not_given(self):
        plan = good_plan()
        plan["steps"][1]["args_json"] = json.dumps({"employee_id": "SYN-EMP-0007"})
        self.assertEqual(rules(plan), {"known_ids"})

    def test_args_that_are_not_an_object_and_duplicate_ids(self):
        plan = good_plan()
        plan["steps"][1]["args_json"] = "[1, 2]"
        self.assertEqual(rules(plan), {"args"})
        plan = good_plan()
        plan["steps"][2]["id"] = 2
        self.assertIn("unique_ids", rules(plan))

    def test_a_reply_with_no_plan_and_a_plan_off_the_schema(self):
        self.assertEqual(rules({"meta": {"error": "no submit_plan call"}, "steps": []}), {"schema"})
        plan = good_plan()
        del plan["steps"][0]["expect"]
        self.assertEqual(rules(plan), {"schema"})


class OutcomeTest(unittest.TestCase):
    def test_each_kind_of_observable_outcome(self):
        miss = executor.outcome_miss
        self.assertIsNone(miss({"kind": "count_equals", "field": "c", "value": "2"}, {"c": [1, 2]}))
        self.assertIn("found 1", miss({"kind": "count_equals", "field": "c", "value": "2"}, {"c": [1]}))
        self.assertIn("empty", miss({"kind": "non_empty", "field": "c", "value": ""}, {"c": []}))
        self.assertIsNone(miss({"kind": "field_equals", "field": "s", "value": "Removed"}, {"s": "removed"}))
        self.assertIn("no pending record", miss({"kind": "record_written", "field": "", "value": ""}, {}))
        self.assertIn("no field", miss({"kind": "non_empty", "field": "x", "value": ""}, {"c": [1]}))
