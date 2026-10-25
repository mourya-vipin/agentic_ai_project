"""The agent path's bound, from outside the model, and the loop's other ends (Part 1,
task 5). On fake models. Owner: Aman Panigrahi."""

from __future__ import annotations

from unittest import mock

from agent import router
from agent.bounds import Bound
from tests.aman_support import TaskRunTest

AT_THE_BOUND = ("step_limit", "loop_token_budget")


class TheBound(TaskRunTest):
    def test_an_agent_path_task_on_never_stops_ends_on_our_bound(self):
        record = self.run_on("t04_same_access_as", "never-stops")
        self.assertIn(record["end_reasons"][-1], AT_THE_BOUND)

    def test_the_router_asks_a_model_then_the_agent_never_stops(self):
        record = self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        self.assertEqual([b["use_case"] for b in self.of("bound")][0], "nonstandard_request")
        self.assertEqual(record["end_reasons"][0], "step_limit")
        loop_calls = [m for m in self.first_turn("model_call") if m["purpose"] == "nonstandard_request"]
        self.assertEqual(len(loop_calls), router.BOUND.max_steps)

    def test_u6_with_its_plan_off_ends_on_our_bound(self):
        record = self.run_on("t06_bulk_ended_contractors", "RoutesBulkThenNeverStops", overrides=["plan.bulk_change=off"])
        self.assertEqual([b["use_case"] for b in self.of("bound")], ["bulk_change"])
        self.assertIn(record["end_reasons"][-1], AT_THE_BOUND)

    def test_the_bounds_values_are_in_the_trace(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        bound = self.of("bound")[0]
        self.assertEqual((bound["max_steps"], bound["token_budget"], bound["wall_clock_s"]),
                         (router.BOUND.max_steps, router.BOUND.token_budget, router.BOUND.wall_clock_s))

    def test_the_token_budget_ends_the_loop(self):
        with mock.patch.object(router, "BOUND", Bound(max_steps=50, token_budget=3_000, wall_clock_s=120)):
            record = self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        self.assertEqual(record["end_reasons"][0], "loop_token_budget")

    def test_the_wall_clock_ends_the_loop(self):
        with mock.patch.object(router, "BOUND", Bound(max_steps=50, token_budget=900_000, wall_clock_s=0.3)):
            record = self.run_on("t04_same_access_as", "RoutesToAgentThenSlow")
        self.assertEqual(record["end_reasons"][0], "wall_clock")


class TheLoopsOtherEnds(TaskRunTest):
    def test_the_second_protocol_error_ends_the_loop(self):
        record = self.run_on("t04_same_access_as", "RoutesToAgentThenUnknownTool")
        self.assertEqual(record["end_reasons"][0], "protocol_error")
        self.assertEqual(len(self.first_turn("protocol_error")), 2)

    def test_exact_repeats_are_flagged(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        self.assertEqual(len(self.first_turn("repeat_call")), router.BOUND.max_steps - 1,
                         "never-stops makes the same call every step")

    def test_the_console_is_shown_progress_every_three_steps(self):
        self.run_on("t04_same_access_as", "RoutesToAgentThenNeverStops")
        steps = [p["text"] for p in self.first_turn("progress") if "step" in p["text"]]
        self.assertEqual(len(steps), (router.BOUND.max_steps - 1) // 3)

    def test_a_workflow_records_its_end_reason(self):
        record = self.run_on("t01_standard_jira", "RoutesStandardGrant")
        self.assertEqual(record["end_reasons"], ["workflow_done"])

    def test_every_tool_call_is_traced_in_full(self):
        self.run_on("t01_standard_jira", "RoutesStandardGrant")
        calls = self.of("tool_call")
        self.assertEqual([c["name"] for c in calls], ["requests_get", "directory_get_employee", "requests_set_status",
                                                      "access_grant", "notices_send_template", "requests_set_status"])
        self.assertTrue(all("input" in c and "output" in c for c in calls))
