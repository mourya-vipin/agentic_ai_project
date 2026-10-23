"""The first-draft tools and the plain registry, on the seeded sandbox. No tokens.
Owner: Vipin Mourya."""

import channel
from agent.registry import GENERIC_PROTOCOL, CallContext, Registry
from harness import runtime
from harness.trace import read_trace
from sandbox import access, pending, seed
from tests.support import ScratchTest

LEO, PRIYA = "SYN-EMP-0031", "SYN-EMP-0012"


def ctx(caller="model", use_case="nonstandard_request", step=1, plan_step=None):
    return CallContext("s-tools", step, caller, use_case, plan_step=plan_step)


class ToolsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def trace_lines(self, kind):
        return [x for x in read_trace(runtime.trace("s-tools").path) if x["type"] == kind]

    def test_definitions_come_in_one_fixed_order_and_hide_the_code_only_tools(self):
        names = [d["name"] for d in self.registry.definitions()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, [d["name"] for d in self.registry.definitions()])
        self.assertEqual(len(names), 15)
        self.assertNotIn("notices_send_template", names)
        self.assertNotIn("credentials_issue", names)

    def test_a_first_draft_name_reaches_its_tool_and_the_call_is_traced_in_full(self):
        result = self.registry.call("get_access", {"employee_id": PRIYA}, ctx())
        self.assertFalse(result.is_error)
        self.assertIn("SYN-GRP-PAYROLL-ADMIN", result.content)
        line = self.trace_lines("tool_call")[-1]
        self.assertEqual((line["name"], line["asked_as"], line["input"]), ("access_list_memberships", "get_access",
                                                                          {"employee_id": PRIYA}))
        self.assertIn("SYN-GRP-PAYROLL-ADMIN", line["output"])

    def test_a_grant_the_policy_does_not_allow_is_refused_with_a_repair_fact(self):
        result = self.registry.call("grant_access", {"employee_id": LEO, "group_id": "SYN-GRP-PAYROLL-ADMIN",
                                                     "request_id": "SYN-REQ-0005"}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("access_propose_change", result.content)
        self.assertNotIn("SYN-GRP-PAYROLL-ADMIN", {m["group_id"] for m in access.memberships(LEO)})

    def test_a_write_retried_under_its_key_leaves_one_effect(self):
        args = {"employee_id": LEO, "group_id": "SYN-GRP-FINREP-READ", "request_id": "SYN-REQ-0005"}
        first = self.registry.call("grant_access", args, ctx())
        again = self.registry.call("grant_access", dict(args, group_id="syn-grp-finrep-read "), ctx())
        self.assertEqual(first.key, again.key)
        self.assertEqual(first.data["membership_id"], again.data["membership_id"])
        self.assertEqual(len([m for m in access.memberships(LEO) if m["group_id"] == "SYN-GRP-FINREP-READ"]), 1)

    def test_a_reworded_draft_has_the_same_key_and_makes_one_record(self):
        base = {"recipient_employee_id": LEO, "request_id": "SYN-REQ-0005"}
        a = self.registry.call("draft_reply", dict(base, body="SYNTHETIC. Three groups are done."), ctx())
        b = self.registry.call("draft_reply", dict(base, body="SYNTHETIC. We granted three groups."), ctx())
        self.assertEqual(a.key, b.key)
        drafts = [p for p in pending.by_request("SYN-REQ-0005") if p["kind"] == "reply_draft"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["thread_id"], "SYN-MSG-D05")

    def test_an_unknown_tool_gets_a_short_generic_answer_and_is_logged(self):
        result = self.registry.call("export_everything", {}, ctx())
        self.assertEqual((result.is_error, result.error_class, result.content), (True, "protocol", GENERIC_PROTOCOL))
        self.assertEqual(self.trace_lines("protocol_error")[-1]["tool"], "export_everything")

    def test_the_model_may_not_send_a_notice_but_a_workflow_may_once(self):
        args = {"thread_id": "SYN-MSG-D01", "request_id": "SYN-REQ-0001", "template_id": "received",
                "facts": {"name": "Arjun Mehta", "request_id": "SYN-REQ-0001"}}
        self.assertEqual(self.registry.call("notices_send_template", args, ctx()).error_class, "protocol")
        first = self.registry.call("notices_send_template", args, ctx(caller="workflow", use_case="standard_grant"))
        again = self.registry.call("notices_send_template", args, ctx(caller="workflow", use_case="standard_grant"))
        self.assertEqual((first.data["status"], again.data["status"]), ("sent", "already_sent"))
        self.assertEqual(len([m for m in channel.messages("SYN-MSG-D01") if m.sender == channel.AGENT]), 1)

    def test_a_transient_failure_says_a_retry_can_work(self):
        access.set_fault("access_grant", 1)
        result = self.registry.call("grant_access", {"employee_id": LEO, "group_id": "SYN-GRP-FINREP-READ"}, ctx())
        self.assertEqual((result.error_class, result.retryable), ("transient", True))

    def test_removing_all_access_keeps_a_protected_group(self):
        result = self.registry.call("remove_all_access", {"employee_id": "SYN-EMP-0162", "reason": "contract ended"},
                                    ctx(use_case="bulk_change"))
        self.assertEqual(result.data["kept_protected"], ["SYN-GRP-LEGAL-HOLD"])
        self.assertEqual([m["group_id"] for m in access.memberships("SYN-EMP-0162")], ["SYN-GRP-LEGAL-HOLD"])

    def test_on_an_employees_request_only_the_requesters_own_groups_can_be_removed(self):
        result = self.registry.call("remove_access", {"employee_id": PRIYA, "group_id": "SYN-GRP-JIRA-WRITE",
                                                      "request_id": "SYN-REQ-0005"}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("SYN-GRP-JIRA-WRITE", {m["group_id"] for m in access.memberships(PRIYA)})

    def test_no_tool_result_carries_a_secret(self):
        issued = self.registry.call("credentials_issue", {"employee_id": "SYN-EMP-0215", "system_id": "SYN-SYS-SSO"},
                                    ctx(caller="workflow", use_case="onboarding"))
        self.assertTrue(issued.data["credential_id"].startswith("SYN-CRED-"))
        for line in self.trace_lines("tool_call"):
            self.assertNotIn("FAKE-", str(line["output"]))
