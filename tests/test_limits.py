"""Stated limits (Lecture 5, slide 17; Part 2, task 3): a limit the operator types at the console
becomes a rule by code, never a model; the hook reads it before every call; it lasts as long as
the operator said, survives a restart, and is lifted by the operator. No tokens. Owner: Vipin Mourya."""

import os
import subprocess
import sys
from datetime import date, timedelta

import channel
from agent.registry import CallContext, Registry
from harness import ROOT, runtime
from harness.trace import read_trace
from policy import limits
from sandbox import access, clock, seed
from tests.support import ScratchTest

LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"


def ctx(caller="model", use_case="nonstandard_request"):
    return CallContext("s-lim", 1, caller, use_case, request_id=REQ, run_key="s-lim#1")


class LimitsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def grant(self, group):
        return self.registry.call("access_grant", {"employee_id": LEO, "group_id": group, "request_id": REQ}, ctx())

    def holds(self, group):
        return group in {m["group_id"] for m in access.memberships(LEO)}

    # -- the parser ----------------------------------------------------------------------------

    def test_each_kind_of_limit_is_parsed_by_code(self):
        cases = {
            "Send nothing until I have read it.": {"classes": list(limits.SEND_CLASSES)},
            "No grants to SYN-GRP-FINREP-READ today, please.": {"classes": ["access_grant/*"],
                                                                "group_id": "SYN-GRP-FINREP-READ"},
            "Nobody gets AWS-Prod admin this week without my sign-off.": {"classes": ["access_grant/*"],
                                                                          "group_id": "SYN-GRP-AWSPROD-ADMIN"},
            "Don't grant anything today.": {"classes": ["access_grant/*"]},
            "No removals from Payroll administrators until legal says so.": {"classes": ["access_revoke/*"],
                                                                             "group_id": "SYN-GRP-PAYROLL-ADMIN"},
        }
        for text, blocks in cases.items():
            with self.subTest(text=text):
                self.assertEqual(limits.parse(text), blocks)

    def test_a_message_that_states_no_limit_is_not_a_rule(self):
        for text in ("How far along are you?", "Give Leo the finance reports.", "Send Leo a note when it is done."):
            with self.subTest(text=text):
                self.assertIsNone(limits.from_text("s-lim", text))
        self.assertEqual(limits.active("s-lim"), [])

    def test_how_long_a_limit_holds_is_what_the_operator_said(self):
        today = clock.today()
        self.assertEqual(limits.from_text("s-lim", "No grants today.").until, f"{today}T23:59:59Z")
        self.assertTrue(limits.from_text("s-lim", "Nobody gets AWS-Prod admin this week.").until.endswith("T23:59:59Z"))
        self.assertEqual(limits.from_text("s-lim", "Send nothing until I have read it.").until, limits.OPERATOR_LIFTS)

    def test_a_days_limit_ends_with_the_day(self):
        limits.from_text("s-lim", "No grants today.")
        self.assertEqual(len(limits.active("s-lim")), 1)
        tomorrow = date.fromisoformat(clock.today()) + timedelta(days=1)
        clock.set_now(f"{tomorrow.isoformat()}T09:00:00Z")
        self.assertEqual(limits.active("s-lim"), [])

    # -- the hook reads it ------------------------------------------------------------------------

    def test_a_stated_limit_blocks_the_call_it_names_and_no_other(self):
        limits.from_text("s-lim", "No grants to SYN-GRP-FINREP-READ today.")
        blocked = self.grant("SYN-GRP-FINREP-READ")
        other = self.grant("SYN-GRP-JIRA-WRITE")
        self.assertTrue(blocked.is_error)
        self.assertIn("No grants to SYN-GRP-FINREP-READ today.", blocked.content)
        self.assertFalse(self.holds("SYN-GRP-FINREP-READ"))
        self.assertFalse(other.is_error, other.content)
        denied = [x for x in read_trace(runtime.trace("s-lim").path) if x["type"] == "hook_denied"]
        self.assertIn("limit: L1", denied[-1]["error"])

    def test_send_nothing_holds_a_workflows_notice_and_lifting_it_lets_it_go(self):
        limits.from_text("s-lim", "Send nothing until I have read it.")
        notice = {"thread_id": "SYN-MSG-D05", "request_id": REQ, "template_id": "received",
                  "facts": {"name": "Leo Martins", "request_id": REQ}}
        held = self.registry.call("notices_send_template", notice, ctx("workflow", "standard_grant"))
        self.assertTrue(held.is_error)
        self.assertEqual([m for m in channel.messages("SYN-MSG-D05") if m.sender == channel.AGENT], [])
        self.assertEqual(limits.lift_from_text("s-lim", "OK, you can send now."), ["L1"])
        sent = self.registry.call("notices_send_template", notice, ctx("workflow", "standard_grant"))
        self.assertFalse(sent.is_error, sent.content)

    def test_a_limit_stated_in_one_session_holds_in_the_next(self):
        limits.from_text("s-earlier", "No grants to SYN-GRP-FINREP-READ today.")
        self.assertTrue(self.grant("SYN-GRP-FINREP-READ").is_error)       # this test's session is s-lim

    def test_a_lifted_rule_stays_in_the_store_marked_lifted(self):
        limits.from_text("s-lim", "Don't grant anything today.")
        limits.lift("s-lim", "L1")
        self.assertEqual(limits.active("s-lim"), [])
        self.assertEqual(limits.lift_from_text("s-lim", "lift L1"), [])
        lifted = [x for x in read_trace(runtime.trace("s-lim").path) if x["type"] == "limit_lifted"]
        self.assertEqual(lifted[-1]["rule_id"], "L1")

    # -- durable -----------------------------------------------------------------------------------

    def test_a_limit_survives_a_restart_and_the_reset_clears_it(self):
        limits.from_text("s-lim", "Send nothing until I have read it.")
        read = subprocess.run([sys.executable, "-c", "from policy import limits; print([r.id for r in limits.active()])"],
                              cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
        self.assertEqual(read.stdout.strip(), "['L1']", read.stderr)       # a new process, as after make serve
        seed.reset()
        self.assertEqual(limits.active(), [])                              # make app starts the day clean
