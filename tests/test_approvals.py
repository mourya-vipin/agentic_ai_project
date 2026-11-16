"""The approval gate (Part 2, task 4): a gated call leaves the sandbox unchanged until the
operator answers; the screen shows the dry run and computed facts first; the answer is logged
and resumes the run; one yes covers one call; a stop withdraws what waits. Real registry,
hook and MCP server, on the demo sandbox. No tokens. Owner: Shruti Tewaney."""

import importlib
from unittest import mock

import channel
from agent import approvals, checkpoints, control, disclosure, service
from agent.registry import CallContext, Registry
from harness import runtime
from harness.trace import read_trace
from sandbox import seed
from tests.support import ScratchTest

SID = "s-gate"
LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"
LEO_THREAD = "SYN-MSG-D05"
TEXT = "SYNTHETIC. Hi Leo, your request is with the IT team."
REPLY = "reply_send/model_text"


def resumed_path(cp, event):
    """A path's resume for the gate's tests: the run gets to it, and no model is called."""
    return service.Reply("resumed")


class GateTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def ctx(self, run_key=f"{SID}#1"):
        return CallContext(SID, 1, "model", "nonstandard_request", request_id=REQ, run_key=run_key)

    def reply(self, run_key=f"{SID}#1", body=TEXT):
        args = disclosure.stamp_call("reply_send", {"recipient_employee_id": LEO, "request_id": REQ, "body": body})
        return self.registry.call("reply_send", args, self.ctx(run_key))

    def sent(self):
        return [m for m in channel.messages(LEO_THREAD) if m.sender == channel.AGENT]

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace(SID).path) if x["type"] == kind]

    # -- the call waits, and nothing changes ------------------------------------------------

    def test_a_gated_call_waits_and_leaves_the_sandbox_unchanged(self):
        result = self.reply()
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.end_reason, approvals.AWAITING_APPROVAL)
        self.assertIn("APR-0001", result.content)
        self.assertEqual(self.sent(), [])
        waiting = service.pending_approvals()
        self.assertEqual([w["id"] for w in waiting], ["APR-0001"])
        self.assertEqual(waiting[0]["session_id"], SID)
        call = self.lines("tool_call")[-1]
        self.assertEqual(call["awaiting_approval"], "APR-0001")
        self.assertEqual(call["action_class"], REPLY)

    def test_the_same_call_asked_twice_is_one_approval(self):
        self.reply()
        self.reply()
        self.assertEqual(len(approvals.pending()), 1)
        self.assertEqual([x["new"] for x in self.lines("approval_requested")], [True, False])

    def test_the_run_is_checkpointed_as_waiting_for_its_approval(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "reply to Leo")
        self.reply(run_key=cp.run_key)
        saved = checkpoints.load(cp.run_key)
        self.assertEqual(saved.status, checkpoints.WAITING_APPROVAL)
        self.assertEqual(saved.waiting_for, {"kind": "approval", "approval_id": "APR-0001"})
        self.assertEqual([c.run_key for c in checkpoints.waiting("approval")], [cp.run_key])

    # -- the screen -------------------------------------------------------------------------

    def test_the_screen_puts_the_dry_run_and_computed_facts_first_and_no_rationale(self):
        self.reply()
        item = approvals.pending()[0]
        keys = [k for k in item if k not in ("id", "session_id")]
        self.assertEqual(keys[:2], ["action", "dry run"])
        self.assertIn(disclosure.LINE, item["dry run"])                       # exactly as it would be sent
        self.assertIn(LEO, item["dry run"])
        self.assertIn("their words", " ".join(keys))                          # the requester's text, labelled
        self.assertIn("what still checks it", item)
        self.assertFalse([k for k in keys if "rationale" in k or "recommend" in k])

    def test_a_grant_screen_shows_the_membership_and_the_groups_reach(self):
        shown = approvals.screen("access_grant", {"employee_id": LEO, "group_id": "SYN-GRP-PAYROLL-ADMIN",
                                                  "request_id": REQ},
                                 "access_grant/high_sensitivity",
                                 {"policy": {"decision": "needs_manager", "reason": "high sensitivity"},
                                  "already_holds": False})
        self.assertIn(f"Add {LEO}", shown["dry run"])
        self.assertIn("SYN-GRP-PAYROLL-ADMIN", shown["dry run"])
        self.assertIn("member(s) now", shown["reach (computed)"])
        self.assertIn("sensitivity high", shown["reach (computed)"])
        self.assertEqual(shown["already holds it (computed)"], "no")
        self.assertIn("none on record", shown["manager's answer (computed)"])   # read, not assumed from the class

    def test_a_grant_screen_reads_the_managers_answer_from_their_reply(self):
        group = "SYN-GRP-FINREP-WRITE"
        asked = self.registry.call("channel_ask_template", {"template": "manager_confirm", "employee_id": LEO,
                                                            "group_id": group, "request_id": REQ}, self.ctx())
        self.assertFalse(asked.is_error, asked.content)
        channel.receive(asked.data["thread_id"], "SYNTHETIC. YES, that is fine.", sender="Grace Okafor")
        shown = approvals.screen("access_grant", {"employee_id": LEO, "group_id": group, "request_id": REQ},
                                 "access_grant/manager_confirmed", {"policy": {}, "already_holds": False})
        self.assertTrue(shown["manager's answer (computed)"].startswith("YES, the first reply"))

    # -- the answer -------------------------------------------------------------------------

    def test_an_approval_covers_that_one_call_once(self):
        self.reply()
        reply = service.decide_approval("APR-0001", True, "fine")
        self.assertIn("no saved run", reply.text)                              # no path resumes it yet
        first = self.reply()
        self.assertFalse(first.is_error, first.content)
        self.assertIsNone(first.end_reason or None)
        self.assertEqual(len(self.sent()), 1)
        self.assertTrue(self.sent()[0].text.endswith(disclosure.LINE))
        again = self.reply()                                                    # the yes is used up
        self.assertEqual(again.end_reason, approvals.AWAITING_APPROVAL)
        self.assertEqual(len(self.sent()), 1)

    def test_an_approval_covers_no_other_text_and_no_other_run(self):
        self.reply()
        approvals.decide("APR-0001", True)
        other_text = self.reply(body="SYNTHETIC. Hi Leo, it is done.")
        other_run = self.reply(run_key=f"{SID}#2")
        self.assertEqual(other_text.end_reason, approvals.AWAITING_APPROVAL)
        self.assertEqual(other_run.end_reason, approvals.AWAITING_APPROVAL)
        self.assertEqual(self.sent(), [])

    def test_a_denial_sends_nothing_and_ends_approval_denied(self):
        self.reply()
        reply = approvals.decide("APR-0001", False, "not yet")
        self.assertIn("Denied", reply.text)
        self.assertEqual(self.lines("end")[-1]["terminal_reason"], approvals.APPROVAL_DENIED)
        self.assertEqual(self.reply().end_reason, approvals.AWAITING_APPROVAL)   # asks again; never a yes
        self.assertEqual(self.sent(), [])

    def test_a_decided_approval_cannot_be_decided_again(self):
        self.reply()
        approvals.decide("APR-0001", False)
        self.assertIn("already", approvals.decide("APR-0001", True).text)
        self.assertEqual(approvals.get("APR-0001")["status"], approvals.DENIED)
        self.assertIn("no approval", approvals.decide("APR-9999", True).text)

    def test_the_answer_resumes_the_waiting_run(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "reply to Leo")
        self.reply(run_key=cp.run_key)
        with mock.patch.dict(checkpoints.RESUMERS, {"nonstandard_request": "tests.test_approvals:resumed_path"}):
            reply = approvals.decide("APR-0001", True)
        resumed = self.lines("resume")
        self.assertEqual([(r["run_key"], r["event_kind"]) for r in resumed], [(cp.run_key, "approval")])
        self.assertEqual(reply.text, "resumed")             # the path's resume (Aman's router.resume) gets the run

    def test_every_decision_is_logged(self):
        self.reply()
        approvals.decide("APR-0001", True, "fine")
        entry = approvals.log()[0]
        self.assertEqual((entry["approval_id"], entry["action_class"], entry["decision"], entry["who"]),
                         ("APR-0001", REPLY, "approved", "operator"))
        self.assertEqual(entry["run_key"], f"{SID}#1")
        self.assertGreaterEqual(entry["seconds"], 0)
        self.assertIn("dry run", entry["shown"])
        self.assertIn("setting", entry)

    def test_an_answer_after_a_restart_still_lands(self):
        self.reply()
        importlib.reload(approvals)                                             # a new process, the same file
        self.assertEqual([w["id"] for w in approvals.pending()], ["APR-0001"])
        approvals.decide("APR-0001", True)
        self.assertFalse(self.reply().is_error)
        self.assertEqual(len(self.sent()), 1)

    # -- the stop ---------------------------------------------------------------------------

    def test_a_stop_withdraws_what_the_run_waits_on(self):
        self.reply()
        self.reply(run_key=f"{SID}#2")
        control.stop(SID, 1)
        self.assertEqual([w["id"] for w in approvals.pending()], ["APR-0002"])
        self.assertEqual(approvals.get("APR-0001")["status"], approvals.WITHDRAWN)
        self.assertEqual([(e["approval_id"], e["decision"], e["who"]) for e in approvals.log()],
                         [("APR-0001", "withdrawn", "stop")])
        self.assertIn("withdrawn", approvals.decide("APR-0001", True).text)

    def test_reset_clears_every_approval(self):
        self.reply()
        approvals.reset()
        self.assertEqual(approvals.pending(), [])
        self.assertEqual(approvals.log(), [])
