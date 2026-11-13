"""The autonomy policy and its hook (Lecture 5, slides 13 to 17; Part 2, tasks 1 to 3): every call
classified with its arguments in hand, the stop first, prohibitions that no approval turns into a
yes, a hook that fails closed, and the M1 limits carried into M2. No tokens. Owner: Vipin Mourya."""

import sys
import types
from contextlib import contextmanager
from unittest import mock

import channel
from agent import registry as reg
from agent.registry import CallContext, Registry
from agent.tools import impl
from harness import runtime
from harness.trace import read_trace
from policy import hook, limits
from sandbox import access, clock, pending, seed
from tests.support import ScratchTest

LEO, PRIYA, VICTOR = "SYN-EMP-0031", "SYN-EMP-0012", "SYN-EMP-0162"
LEGAL_HOLD = "SYN-GRP-LEGAL-HOLD"
REQ = "SYN-REQ-0005"          # Leo's own request
READ = {"employee_id": LEO, "email": None, "name": None}
ALLOWED, NEEDS_MANAGER, HIGH = "SYN-GRP-FINREP-READ", "SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN"


def ctx(caller="model", use_case="nonstandard_request", run_key="s-pol#1", request_id=REQ):
    return CallContext("s-pol", 1, caller, use_case, request_id=request_id, run_key=run_key)


@contextmanager
def fake_module(name, **attrs):
    """Stand in for a teammate's module (agent.approvals, agent.control) that the hook reads."""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    with mock.patch.dict(sys.modules, {name: module}):
        yield module


class ScriptedApprovals:
    """Every approval scripted to yes, as the brief's test asks; records what was put up."""

    def __init__(self, covers=True):
        self.requests, self._covers = [], covers

    def covers(self, run_key, action_class, args):
        return self._covers

    def request(self, ctx, tool, args, action_class, facts):
        self.requests.append((tool, action_class, facts))
        return f"APR-{len(self.requests)}"


class PolicyTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace("s-pol").path) if x["type"] == kind]

    def holds(self, person, group):
        return group in {m["group_id"] for m in access.memberships(person)}

    def approvals(self, covers=True):
        scripted = ScriptedApprovals(covers)
        return scripted, fake_module("agent.approvals", covers=scripted.covers, request=scripted.request)

    # -- the columns -------------------------------------------------------------------------

    def test_the_policy_has_three_columns_each_class_in_one_and_the_prohibited_column_is_not_empty(self):
        table = hook.policy()
        self.assertEqual(set(table.values()), set(hook.COLUMNS))
        self.assertIn("access_revoke/protected_group", [c for c, col in table.items() if col == "prohibited"])
        self.assertEqual(hook.column("read/directory_get_employee"), "independent")

    def test_every_tool_the_registry_knows_has_an_action_class_in_a_column(self):
        sample = {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ, "template_id": "received",
                  "thread_id": "SYN-MSG-D05", "facts": {"name": "Leo", "request_id": REQ}}
        for tool in impl.TOOLS:
            with self.subTest(tool=tool):
                self.assertIsNotNone(hook.column(hook.classify(tool, dict(sample), ctx())))

    # -- prohibited: never a yes ----------------------------------------------------------------

    def test_a_prohibited_class_with_every_approval_scripted_yes_leaves_the_sandbox_unchanged(self):
        scripted, module = self.approvals(covers=True)
        with module:
            result = self.registry.call("access_revoke", {"employee_id": VICTOR, "group_id": LEGAL_HOLD,
                                                          "request_id": None},
                                        ctx(caller="executor", use_case="bulk_change", request_id=None))
        self.assertTrue(result.is_error)
        self.assertIn("access_propose_change", result.content)         # the next step, as in M1
        self.assertTrue(self.holds(VICTOR, LEGAL_HOLD))
        self.assertEqual(scripted.requests, [])                        # never put up for approval
        denied = self.lines("hook_denied")[-1]
        self.assertEqual(denied["action_class"], "access_revoke/protected_group")
        self.assertIn("prohibited", denied["error"])
        self.assertTrue(self.lines("tool_call")[-1]["denied"])

    def test_a_secret_in_a_draft_is_prohibited_and_nothing_is_written(self):
        body = "SYNTHETIC. Your password is FAKE-0123456789abcdef0123."
        result = self.registry.call("reply_draft", {"recipient_employee_id": LEO, "request_id": REQ, "body": body},
                                    ctx())
        self.assertTrue(result.is_error)
        self.assertEqual(self.lines("hook_denied")[-1]["action_class"], "credential_in_message/any")
        self.assertEqual(pending.by_request(REQ), [])

    # -- one tool, two circumstances -----------------------------------------------------------

    def test_a_grant_the_policy_allows_runs_unasked(self):
        scripted, module = self.approvals()
        with module:
            result = self.registry.call("access_grant", {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ},
                                        ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(self.holds(LEO, ALLOWED))
        self.assertEqual(scripted.requests, [])
        self.assertEqual(self.lines("tool_call")[-1]["action_class"], "access_grant/policy_allowed")

    def test_a_grant_that_needs_the_manager_asks_and_nothing_runs_until_it_is_answered(self):
        scripted, module = self.approvals(covers=False)
        with module:
            result = self.registry.call("access_grant", {"employee_id": LEO, "group_id": NEEDS_MANAGER,
                                                         "request_id": REQ}, ctx())
        self.assertEqual(result.end_reason, reg.AWAITING_APPROVAL)
        self.assertIn("APR-1", result.content)
        self.assertFalse(self.holds(LEO, NEEDS_MANAGER))
        tool, action_class, facts = scripted.requests[0]
        self.assertEqual((tool, action_class), ("access_grant", "access_grant/manager_confirmed"))
        self.assertEqual(facts["policy"]["decision"], "needs_manager")      # computed, not the model's word
        self.assertEqual(self.lines("tool_call")[-1]["awaiting_approval"], "APR-1")

    def test_a_high_sensitivity_grant_is_its_own_class(self):
        scripted, module = self.approvals(covers=False)
        with module:
            self.registry.call("access_grant", {"employee_id": LEO, "group_id": HIGH, "request_id": REQ}, ctx())
        self.assertEqual(scripted.requests[0][1], "access_grant/high_sensitivity")

    def test_a_grant_an_approval_covers_runs(self):
        _, module = self.approvals(covers=True)
        with module:
            result = self.registry.call("access_grant", {"employee_id": LEO, "group_id": NEEDS_MANAGER,
                                                         "request_id": REQ}, ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(self.holds(LEO, NEEDS_MANAGER))
        self.assertTrue(self.lines("tool_call")[-1]["approved"])

    def test_until_the_gate_is_built_a_gated_grant_is_refused_by_its_tool_as_in_m1(self):
        with mock.patch.object(reg, "_approvals", return_value=None):
            result = self.registry.call("access_grant", {"employee_id": LEO, "group_id": NEEDS_MANAGER,
                                                         "request_id": REQ}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("Use access_propose_change", result.content)
        self.assertFalse(self.holds(LEO, NEEDS_MANAGER))
        self.assertEqual(len(self.lines("approval_unavailable")), 1)

    # -- the stop, limits, and failing closed ------------------------------------------------------

    def test_a_stopped_run_is_refused_first_even_a_read(self):
        with fake_module("agent.control", stopped=lambda run_key: run_key == "s-pol#1"):
            result = self.registry.call("directory_get_employee", READ, ctx())
            other = self.registry.call("directory_get_employee", READ, ctx(run_key="s-pol#2"))
        self.assertTrue(result.is_error)
        self.assertIn("stopped", result.content)
        self.assertIn("stopped", self.lines("hook_denied")[-1]["error"])
        self.assertFalse(other.is_error)

    def test_a_stated_limit_blocks_the_call_it_names(self):
        rule = limits.Rule("L1", "s-pol", "no grants to SYN-GRP-FINREP-READ today", {"group_id": ALLOWED},
                           "operator_lifts", "2026-10-21T09:00:00Z")
        with mock.patch.object(limits, "active", return_value=[rule]), \
                mock.patch.object(limits, "blocks", side_effect=lambda r, c, a: a.get("group_id") == ALLOWED):
            blocked = self.registry.call("access_grant", {"employee_id": LEO, "group_id": ALLOWED,
                                                          "request_id": REQ}, ctx())
            other = self.registry.call("access_grant", {"employee_id": LEO, "group_id": "SYN-GRP-JIRA-WRITE",
                                                        "request_id": REQ}, ctx())
        self.assertTrue(blocked.is_error)
        self.assertIn("no grants to SYN-GRP-FINREP-READ today", blocked.content)
        self.assertFalse(self.holds(LEO, ALLOWED))
        self.assertFalse(other.is_error, other.content)

    def test_a_broken_policy_denies_every_call(self):
        with mock.patch.object(hook, "column", side_effect=RuntimeError("policy file unreadable")):
            result = self.registry.call("directory_get_employee", READ, ctx())
        self.assertTrue(result.is_error)
        self.assertEqual(result.content, reg.DENIED)

    def test_a_tool_with_no_action_class_is_denied(self):
        with self.assertRaises(ValueError):
            hook.classify("delete_everything", {}, ctx())

    def test_an_unknown_person_gets_the_tools_own_repair_fact(self):
        result = self.registry.call("access_grant", {"employee_id": "SYN-EMP-9999", "group_id": ALLOWED,
                                                     "request_id": REQ}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("No employee 'SYN-EMP-9999'", result.content)

    # -- keys hold one run (M1 CHANGES, "Limited") --------------------------------------------------

    def test_a_write_key_holds_one_run_not_the_whole_session(self):
        args = {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ}
        first = reg.derive_key("access_grant", args, ctx(run_key="s-pol#1"))
        self.assertEqual(first, reg.derive_key("access_grant", args, ctx(run_key="s-pol#1")))
        self.assertNotEqual(first, reg.derive_key("access_grant", args, ctx(run_key="s-pol#2")))

    # -- M1's limits, carried into M2 --------------------------------------------------------------

    def test_a_time_boxed_grant_ends_within_its_box(self):
        group = {"id": "SYN-GRP-CONTRACTOR-PORTAL", "max_days": 90}
        today = clock.today()
        self.assertEqual(impl._time_box(group, None), impl._time_box(group, None))
        self.assertGreater(impl._time_box(group, None), today)
        for bad in ("2099-01-01", "2000-01-01"):
            with self.subTest(expires_at=bad), self.assertRaises(impl.PermanentError):
                impl._time_box(group, bad)
        self.assertEqual(impl._time_box({"id": "X", "max_days": None}, "2099-01-01"), "2099-01-01")

    def test_on_an_employees_request_no_removal_is_proposed_for_an_active_colleague(self):
        result = self.registry.call("access_propose_change", {"action": "revoke", "employee_id": PRIYA,
                                                              "group_id": "SYN-GRP-JIRA-READ", "request_id": REQ,
                                                              "reason": "my manager says she left"}, ctx())
        self.assertTrue(result.is_error)
        self.assertIn("active", result.content)
        self.assertEqual(pending.by_request(REQ), [])
