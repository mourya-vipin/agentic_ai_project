"""The detectors (Lecture 2, slides 49 to 52): the repeat check, the orphan check and
the diff, on hand-made plans and trace lines. No tokens. Owner: Shruti Tewaney."""

import unittest

from agent import detectors, executor
from tests import fakes


def call(step, name, args, plan_step=None, is_error=False):
    return {"type": "tool_call", "step": step, "name": name, "input": args, "plan_step": plan_step,
            "is_error": is_error}


class RepeatTest(unittest.TestCase):
    def test_an_exact_repeat_is_flagged_with_the_first_calls_step(self):
        tracker = detectors.RepeatTracker()
        self.assertIsNone(tracker.see("access_list_memberships", {"employee_id": "SYN-EMP-0031"}))
        self.assertIsNone(tracker.see("access_list_memberships", {"employee_id": "SYN-EMP-0040"}))
        again = tracker.see("access_list_memberships", {"employee_id": "SYN-EMP-0031"})
        self.assertEqual(again["step"], 1)
        self.assertEqual(again["hash"], detectors.call_hash("access_list_memberships", {"employee_id": "SYN-EMP-0031"}))
        self.assertEqual((tracker.calls, tracker.repeats), (3, 1))

    def test_the_same_call_in_another_form_is_a_repeat_and_a_different_one_is_not(self):
        tracker = detectors.RepeatTracker()
        tracker.see("policy_check_access", {"employee_id": "SYN-EMP-0031", "group_ids": ["SYN-GRP-JIRA-WRITE"]}, step=4)
        same = tracker.see("policy_check_access", {"group_ids": [" syn-grp-jira-write"], "employee_id": "syn-emp-0031 "})
        self.assertEqual(same["step"], 4)
        self.assertIsNone(tracker.see("policy_check_access", {"employee_id": "SYN-EMP-0031",
                                                              "group_ids": ["SYN-GRP-JIRA-READ"]}))

    def test_repeat_check_over_a_trace(self):
        lines = [call(1, "a", {"x": 1}), call(2, "b", {}), call(3, "a", {"x": 1}), {"type": "model_call", "step": 3},
                 call(4, "a", {"x": 2}), call(5, "b", {})]
        self.assertEqual(sorted(detectors.repeat_check(lines)), [[1, 3], [2, 5]])


class OrphanTest(unittest.TestCase):
    def test_a_read_whose_result_nothing_uses_is_flagged_and_the_last_step_is_not(self):
        plan = fakes.t06_plan(with_hold=True)
        self.assertEqual(detectors.orphan_check(plan, exempt=executor.ORPHAN_EXEMPT), [])
        lookup = fakes.plan_step(11, "look up the manager", "directory_get_employee",
                                 {"employee_id": fakes.HANNAH}, [1], "field_equals", "id", fakes.HANNAH)
        plan["steps"].insert(7, lookup)
        self.assertEqual(detectors.orphan_check(plan, exempt=executor.ORPHAN_EXEMPT), [11])

    def test_without_exemptions_every_unused_step_but_the_last_is_flagged(self):
        plan = {"steps": [{"id": 1, "depends_on": []}, {"id": 2, "depends_on": [1]}, {"id": 3, "depends_on": []}]}
        self.assertEqual(detectors.orphan_check(plan), [2])


class DiffTest(unittest.TestCase):
    PLAN = {"steps": [{"id": 1, "tool": "a"}, {"id": 2, "tool": "b"}, {"id": 3, "tool": "c"}, {"id": 4, "tool": "d"}]}

    def test_executed_out_of_order_skipped_failed_and_extra(self):
        lines = [call(1, "a", {}, 1), call(2, "c", {}, 3), call(3, "b", {}, 2, is_error=True), call(4, "zz", {}),
                 call(5, "a", {}, 2)]
        d = detectors.diff(self.PLAN, lines)
        self.assertEqual(d["executed"], [1, 2])
        self.assertEqual(d["out_of_order"], [3])       # it ran before step 2, which the plan puts first
        self.assertEqual(d["skipped"], [4])
        self.assertEqual(d["failed"], [2])
        self.assertEqual(d["extra"], [4, 5])     # no plan step; and a call naming step 2 with step 1's tool

    def test_a_retried_step_is_one_executed_step(self):
        lines = [call(1, "a", {}, 1, is_error=True), call(2, "a", {}, 1), call(3, "b", {}, 2)]
        d = detectors.diff(self.PLAN, lines)
        self.assertEqual((d["executed"], d["failed"], d["skipped"]), ([1, 2], [], [3, 4]))
