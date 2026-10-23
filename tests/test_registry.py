"""The registry (Lecture 3, slides 22 to 42): every call validated at the boundary,
transient failures retried in code, writes keyed, a hook that fails closed, results
checked and capped, and the tools in one fixed order. No tokens. Owner: Vipin Mourya."""

from unittest import mock

import channel
from agent import registry as reg
from agent.registry import CallContext, Registry
from agent.tools import impl
from harness import runtime
from harness.trace import read_trace
from sandbox import access, seed
from tests.support import ScratchTest

LEO = "SYN-EMP-0031"
GRANT = {"employee_id": LEO, "group_id": "SYN-GRP-FINREP-READ", "request_id": "SYN-REQ-0005"}


def ctx(caller="model", use_case="nonstandard_request"):
    return CallContext("s-reg", 1, caller, use_case)


class RegistryTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()
        self._sleep = mock.patch.object(reg.time, "sleep")     # the backoff, without waiting
        self.slept = self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        super().tearDown()

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace("s-reg").path) if x["type"] == kind]

    def held(self, group_id):
        return [m for m in access.memberships(LEO) if m["group_id"] == group_id]

    def test_a_call_rejected_at_the_boundary_gets_its_repair_fact_and_nothing_runs(self):
        result = self.registry.call("grant_access", {"employee_id": LEO, "group_id": "Finance Reports"}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("group_id is 'Finance Reports'; give a group ID such as SYN-GRP-JIRA-WRITE", result.content)
        self.assertIn("will fail the same way", result.content)
        self.assertLessEqual(len(result.content), reg.REPAIR_CHARS)
        self.assertTrue(self.lines("tool_call")[-1]["rejected"])
        self.assertEqual(access.by_request("SYN-REQ-0005"), [])

    def test_a_missing_argument_and_an_unknown_one_are_both_named(self):
        result = self.registry.call("grant_access", {"group_id": "SYN-GRP-FINREP-READ", "colour": "blue"}, ctx())
        self.assertIn("employee_id is required", result.content)
        self.assertIn("colour is not an argument", result.content)

    def test_enum_values_are_compared_case_insensitively(self):
        result = self.registry.call("propose_access", {"action": "GRANT", "employee_id": LEO,
                                                       "group_id": "syn-grp-payroll-admin"}, ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertEqual((result.data["action"], result.data["group_id"]), ("grant", "SYN-GRP-PAYROLL-ADMIN"))

    def test_an_unknown_tool_is_logged_and_the_model_gets_a_short_generic_answer(self):
        result = self.registry.call("delete_everyone", {}, ctx())
        self.assertEqual((result.error_class, result.content), ("protocol", reg.GENERIC_PROTOCOL))
        self.assertEqual(self.lines("protocol_error")[-1]["tool"], "delete_everyone")

    def test_a_transient_failure_is_retried_in_code_up_to_its_cap(self):
        access.set_fault("access_grant", 2)
        result = self.registry.call("grant_access", GRANT, ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.lines("tool_call")[-1]["attempts"], 3)
        self.assertEqual([c.args[0] for c in self.slept.call_args_list], [0.05, 0.1])

    def test_a_transient_failure_past_the_cap_goes_back_saying_a_retry_can_work(self):
        access.set_fault("access_grant", 5)
        result = self.registry.call("grant_access", GRANT, ctx())
        self.assertEqual((result.error_class, result.retryable), ("transient", True))
        self.assertEqual(self.lines("tool_call")[-1]["attempts"], reg.MAX_ATTEMPTS)
        self.assertEqual(self.held("SYN-GRP-FINREP-READ"), [])

    def test_a_write_retried_under_its_key_leaves_one_effect(self):
        access.set_fault("access_grant", 1)
        first = self.registry.call("grant_access", GRANT, ctx())
        again = self.registry.call("grant_access", GRANT, ctx())     # the model calls it again
        self.assertEqual(first.key, again.key)
        self.assertEqual(len(self.held("SYN-GRP-FINREP-READ")), 1)
        self.assertEqual(self.lines("tool_call")[-1]["key"], first.key)

    def test_a_raising_hook_denies_the_call(self):
        def broken(tool, args, ctx):
            raise RuntimeError("policy store unreachable")
        self.registry.hooks = [broken]
        result = self.registry.call("grant_access", GRANT, ctx())
        self.assertEqual(result.content, reg.DENIED)
        self.assertEqual(self.held("SYN-GRP-FINREP-READ"), [])
        self.assertEqual(self.lines("hook_denied")[-1]["tool"], "access_grant")

    def test_the_tools_come_in_the_same_order_on_every_request(self):
        first = self.registry.definitions()
        for _ in range(10):
            self.assertEqual(self.registry.definitions(), first)
        self.assertEqual(self.registry.definitions(["access_grant", "directory_get_employee"]),
                         sorted(self.registry.definitions(["directory_get_employee", "access_grant"]),
                                key=lambda d: d["name"]))

    def test_a_result_of_the_wrong_shape_never_reaches_the_model_as_success(self):
        with mock.patch.dict(impl.TOOLS, {"requests_list_today": (lambda a, c, k: {"oops": 1}, impl.MODEL, False)}):
            result = self.registry.call("list_requests", {}, ctx())
        self.assertEqual(result.content, reg.BAD_RESULT)
        self.assertEqual(self.lines("result_invalid")[-1]["problems"], ["the result has no day", "the result has no requests"])

    def test_a_long_result_is_capped_and_says_so(self):
        big = {"day": "2026-10-21", "requests": [{"text": "x" * 500}] * 20}
        with mock.patch.dict(impl.TOOLS, {"requests_list_today": (lambda a, c, k: big, impl.MODEL, False)}):
            result = self.registry.call("list_requests", {}, ctx())
        self.assertLessEqual(len(result.content), reg.MAX_CHARS)
        self.assertIn("[cut: showed 4000 of", result.content)
