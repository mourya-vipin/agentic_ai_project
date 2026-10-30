"""experiments/analysis/analyze.py: the t07 runs read again by substance, gate 4's
arithmetic, each loop's use of our bound and the plan diff, on made-up traces; then the
figures M1.md gives, from the committed arms. No tokens. Owner: Aman Panigrahi."""

import importlib.util
import json
import unittest

from harness import ROOT

SPEC = importlib.util.spec_from_file_location("analyze", ROOT / "experiments" / "analysis" / "analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)

REQUESTS = [("SYN-REQ-0001", "SYN-EMP-0070", "Omar Haddad"), ("SYN-REQ-0002", "SYN-EMP-0098", "Rosa Lindqvist"),
            ("SYN-REQ-0003", "SYN-EMP-0040", "Grace Okafor"), ("SYN-REQ-0004", "SYN-EMP-0021", "Maya Chen"),
            ("SYN-REQ-0005", "SYN-EMP-0007", "Arjun Mehta"), ("SYN-REQ-0006", "SYN-EMP-0120", "Felix Wagner")]
BY_PEOPLE = ("Unusual requests today:\n"
             "1. Rosa Lindqvist (SYN-EMP-0098) is a leaver and asks for the VPN.\n"
             "2. Maya Chen wants a password sent to a personal address.\n"
             "3. Felix Wagner (SYN-EMP-0120), an intern, asks for AWS production admin.\n\n"
             "Routine requests:\n"
             "- Omar Haddad (SYN-EMP-0070): allowed for his role.\n"
             "- SYN-EMP-0040 asks for Data Warehouse read, which is allowed.\n"
             "- Arjun Mehta: Jira write, allowed.")


def t07_run(answer, passed=False, writes=0, task="t07_unusual_review"):
    listed = {"requests": [{"id": r, "requester_id": e, "requester_name": n} for r, e, n in REQUESTS]}
    record = {"task": task, "passed": passed, "measures": {"writes": writes}}
    events = [{"type": "tool_call", "name": "requests_list_today", "output": json.dumps(listed)},
              {"type": "reply", "function": "operator_turn", "text": answer}]
    return record, events


def call(prompt, output, latency, at):
    return {"type": "model_call", "at": at, "latency_s": latency, "cost_usd": 0.001,
            "usage": {"input_tokens": prompt - 100, "cache_read_input_tokens": 100, "output_tokens": output,
                      "cache_creation_input_tokens": 0}}


class BySubstanceTest(unittest.TestCase):
    def test_a_failed_t07_run_that_names_the_people_passes_by_substance(self):
        self.assertTrue(analyze.by_substance(*t07_run(BY_PEOPLE)))

    def test_it_still_fails_a_wrong_answer_a_run_that_wrote_or_another_task(self):
        missed = BY_PEOPLE.replace("3. Felix Wagner (SYN-EMP-0120), an intern, asks for AWS production admin.\n", "")
        flags = BY_PEOPLE.replace("- Arjun Mehta: Jira write, allowed.", "- Arjun Mehta: Jira write, unusual.")
        self.assertFalse(analyze.by_substance(*t07_run(missed)))
        self.assertFalse(analyze.by_substance(*t07_run(flags)))
        self.assertFalse(analyze.by_substance(*t07_run(BY_PEOPLE, writes=1)))
        self.assertFalse(analyze.by_substance(*t07_run(BY_PEOPLE, task="t05_on_behalf")))

    def test_a_run_its_check_passed_counts_as_passed(self):
        self.assertTrue(analyze.by_substance(*t07_run("Done.", passed=True)))


class Gate4Test(unittest.TestCase):
    def test_the_arithmetic(self):
        runs = [{"passed": p, "case_paths": {"nonstandard_request": ["agent"]}} for p in (True, False)]
        runs.append({"passed": True, "case_paths": {"standard_grant": ["workflow"]}})
        g = analyze.gate4(runs, c=0.0002, own_m=None, a1_m=11.0, wrong_case=10.0, r=0.2)
        self.assertEqual((g["u4_passed"], g["u4_runs"], g["f"]), (1, 2, 0.5))
        a = g["a1"]
        for key, value in (("extra_per_case", 0.002), ("per_extra_correct", 0.004), ("break_even_f", 0.0002),
                           ("one_in", 5000), ("blended", 0.0006), ("agent_every_case", 0.0022)):
            self.assertAlmostEqual(a[key], value, msg=key)
        self.assertIsNone(g["own"])


class LoopsTest(unittest.TestCase):
    def test_each_loop_runs_from_its_bound_line_to_its_end_line(self):
        events = [call(500, 10, 0.4, "2026-10-26T10:00:00.000+00:00"),           # routing, before the bound
                  {"type": "bound", "use_case": "nonstandard_request", "at": "2026-10-26T10:00:01.000+00:00"},
                  call(2000, 50, 1.0, "2026-10-26T10:00:02.000+00:00"),
                  call(2600, 50, 1.5, "2026-10-26T10:00:03.500+00:00"),
                  call(3400, 80, 2.0, "2026-10-26T10:00:04.000+00:00"),
                  {"type": "end", "terminal_reason": "done", "at": "2026-10-26T10:00:04.500+00:00"}]
        [loop] = analyze.loops(events)
        self.assertEqual((loop["calls"], loop["tokens"], loop["seconds"]), (3, 8180, 3.5))
        self.assertEqual((loop["prompts"], loop["path"], loop["end_reason"]), ([2000, 2600, 3400], "loop", "done"))
        h = analyze.headroom([{"run_id": "r1", "loops": [loop]}])
        self.assertEqual((h["first_prompt"]["median"], h["added_per_step"]["median"]), (2000, 700))
        self.assertEqual(h["at_step_limit"]["medians"], 14 * 2000 + 700 * 91)


class PlanDiffTest(unittest.TestCase):
    def test_every_plan_diff_line_is_summed_and_a_run_without_one_has_none(self):
        events = [{"type": "plan_diff", "executed": [1, 2], "skipped": [3], "out_of_order": [], "failed": [2],
                   "extra": [7]},
                  {"type": "tool_call", "name": "access_revoke_all"},
                  {"type": "plan_diff", "executed": [1], "skipped": [], "out_of_order": [2], "failed": [], "extra": []}]
        self.assertEqual(analyze.plan_diff(events), {"diffs": 2, "executed": 3, "skipped": 1, "out_of_order": 1,
                                                     "failed": 1, "extra": 1})
        self.assertIsNone(analyze.plan_diff([{"type": "tool_call", "name": "requests_get"}]))

    def test_an_arm_counts_the_planned_runs_that_had_each_label(self):
        runs = [{"run_id": "planned", "diff": analyze.plan_diff([{"type": "plan_diff", "executed": [1], "skipped": [2],
                                                                  "out_of_order": [], "failed": [], "extra": []}])},
                {"run_id": "loop", "diff": None}]
        d = analyze.diff_totals(runs)
        self.assertEqual((d["runs"], d["executed"], d["skipped"]), (1, 1, 1))
        self.assertEqual(d["runs_with"], {"skipped": ["planned"], "out_of_order": [], "failed": [], "extra": []})


class CommittedArmsTest(unittest.TestCase):
    """The figures M1.md and the ledger take from the script."""

    def test_baseline(self):
        a = analyze.arm(ROOT / "results" / "baseline")
        self.assertEqual(a["passes_by_substance"], 20)
        planning = a["per_task_detail"]["t06_bulk_ended_contractors"]["planning"]
        self.assertEqual([p["tokens"] for p in planning], [5587, 5611, 5799])
        self.assertEqual([p["revise_seconds"] for p in planning], [5.708, 14.794, 8.061])
        self.assertEqual(round(a["gate4"]["a1"]["break_even_f"], 5), 0.00013)
        h = a["bound_headroom"]
        self.assertEqual((h["most_calls"]["value"], h["most_tokens"]["value"]), (7, 29088))
        self.assertEqual(round(h["at_step_limit"]["medians"]), 89216)
        d = a["plan"]["diff"]
        self.assertEqual((d["runs"], d["executed"], d["skipped"], d["out_of_order"], d["failed"], d["extra"]),
                         (3, 27, 0, 0, 0, 0))
        calls = a["per_task_detail"]["t07_unusual_review"]["tool_calls"]
        self.assertEqual((calls["min"], calls["max"]), (18, 21))

    def test_pass1(self):
        before, after = (analyze.arm(ROOT / "results" / name) for name in ("pass1-before", "pass1-after"))
        self.assertEqual((before["passes_by_substance"], after["passes_by_substance"]), (21, 19))
        t06 = after["per_task_detail"]["t06_bulk_ended_contractors"]
        self.assertEqual((round(t06["uncached"]["mean"]), round(t06["turns"]["median"], 2)), (1768, 10.54))
