"""The escalation (Part 2, task 5): a named signal holds the run with a packet, a checkpoint and
no write; with the operator silent the run ends held, never approved; answered in a new console
session it resumes, in its own session, with no duplicate in the sandbox; past the day's cap a
signal still holds but sends no packet. No tokens. Owner: Shruti Tewaney."""

from unittest import mock

import channel
from agent import approvals, checkpoints, commands, disclosure, escalation
from agent.registry import CallContext, Registry
from harness import runtime
from harness.trace import read_trace
from harness.turns import Reply
from sandbox import access, db, seed
from tests.support import ScratchTest

SID, LATER = "s-esc", "s-esc-later"
LEO, REQ, ALLOWED = "SYN-EMP-0031", "SYN-REQ-0005", "SYN-GRP-FINREP-READ"
GRANT = {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ}
PACKET = {"attempted": f"grant {ALLOWED} to {LEO} on {REQ}",
          "why": "the identity API timed out twice and the membership list cannot say whether the grant landed",
          "needs": f"check {LEO}'s groups on /people and answer whether {ALLOWED} is there"}
RESUMED: list[tuple] = []


def ctx(run_key):
    return CallContext(SID, 1, "model", "nonstandard_request", request_id=REQ, run_key=run_key)


def resume_path(cp, event):
    """A path's resume, as Aman's loop will have it: it reads the journal before the write it
    was making, so a replay never writes twice."""
    RESUMED.append((cp.run_key, event, runtime.current_session()))
    if checkpoints.journaled(cp.run_key, "grant") is None:
        result = Registry().call("access_grant", GRANT, ctx(cp.run_key))
        checkpoints.record_completed(cp.run_key, "grant", tool="access_grant", args=GRANT, result=result.data)
    return Reply(f"carried on after {event['packet_id']}")


class EscalationTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        RESUMED.clear()
        self._paths = mock.patch.dict(checkpoints.RESUMERS, {"nonstandard_request": "tests.test_escalation:resume_path"})
        self._paths.start()
        self.cp = checkpoints.new_run(SID, "nonstandard_request", f"Please give Leo {ALLOWED}.")

    def tearDown(self):
        self._paths.stop()
        super().tearDown()

    def writes(self) -> int:
        with db.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

    def lines(self, session=SID, kind=None):
        return [x for x in read_trace(runtime.trace(session).path) if kind is None or x["type"] == kind]

    def grants(self):
        return [m for m in access.memberships(LEO) if m["group_id"] == ALLOWED]

    # -- the trigger and the packet ---------------------------------------------------------

    def test_the_signal_leaves_a_packet_a_checkpoint_and_no_write(self):
        before = self.writes()
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        self.assertEqual(packet_id, "ESC-0001")
        packet = escalation.get(packet_id)
        self.assertTrue(all(packet[k].strip() for k in ("attempted", "why", "needs")))
        saved = checkpoints.load(self.cp.run_key)
        self.assertEqual(saved.status, checkpoints.HELD)
        self.assertEqual(saved.waiting_for, {"kind": "escalation", "packet_id": packet_id})
        self.assertEqual(self.writes(), before)

    def test_the_packet_is_on_the_console(self):
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        shown = self.lines(kind="progress")[-1]["text"]
        for field in PACKET.values():
            self.assertIn(field, shown)
        self.assertIn(f"/answer {packet_id}", shown)
        self.assertIn("What it needs from you", escalation.hold_reply(packet_id).text)

    def test_only_a_named_signal_with_three_fields_escalates(self):
        with self.assertRaises(ValueError):
            escalation.escalate(self.cp.run_key, "low_confidence", **PACKET)
        with self.assertRaises(ValueError):
            escalation.escalate(self.cp.run_key, "ambiguous_write", **{**PACKET, "needs": "  "})
        self.assertEqual(escalation.held(), [])

    # -- the hold ---------------------------------------------------------------------------

    def test_with_the_operator_silent_the_run_ends_held_with_no_write(self):
        before = self.writes()
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        self.assertEqual(escalation.expire(at="2000-01-01T00:00:00.000+00:00"), [])     # not yet
        late = escalation.get(packet_id)["deadline"].replace("+00:00", "1+00:00")      # just after it
        self.assertEqual(escalation.expire(at=late), [packet_id])
        self.assertEqual(escalation.get(packet_id)["status"], escalation.TIMED_OUT)
        self.assertEqual(checkpoints.load(self.cp.run_key).status, checkpoints.FAILED)
        self.assertEqual(self.lines(kind="end")[-1]["terminal_reason"], escalation.HELD_TIMEOUT)
        self.assertIn("timed out", escalation.answer(packet_id, "it is there").text)   # never approved after
        self.assertEqual(RESUMED, [])
        self.assertEqual(self.writes(), before)

    def test_answered_in_a_new_session_it_resumes_in_its_own_with_no_duplicate(self):
        Registry().call("access_grant", GRANT, ctx(self.cp.run_key))          # the write, then the doubt
        checkpoints.record_completed(self.cp.run_key, "grant", tool="access_grant", args=GRANT, result={})
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        reply = commands.handle(LATER, f"/answer {packet_id} Yes, {ALLOWED} is on his groups.")
        self.assertIn("carried on", reply.text)
        self.assertEqual([(k, e["kind"], e["packet_id"], s) for k, e, s in RESUMED],
                         [(self.cp.run_key, "operator", packet_id, SID)])     # in the run's own session
        self.assertEqual(len(self.grants()), 1)                                 # no duplicate
        answered = self.lines(kind="escalation_answered")[-1]
        self.assertEqual(answered["answered_in"], LATER)
        self.assertIn("already", escalation.answer(packet_id, "again").text)

    def test_the_held_run_is_not_resumed_by_a_reply_or_an_approval(self):
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        refused = checkpoints.resume(self.cp.run_key, {"kind": "approval", "approval_id": "APR-0001",
                                                       "approved": True})
        self.assertIn("did not continue", refused.text)
        self.assertEqual(RESUMED, [])
        self.assertEqual(escalation.get(packet_id)["status"], escalation.PENDING)

    # -- the budget -------------------------------------------------------------------------

    def test_past_the_cap_a_signal_holds_but_sends_no_packet(self):
        with mock.patch.object(escalation, "DAILY_CAP", 1):
            first = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
            other = checkpoints.new_run(SID, "nonstandard_request", "another")
            second = escalation.escalate(other.run_key, "blocked_three_times", **PACKET)
        self.assertTrue(escalation.get(first)["sent"])
        self.assertFalse(escalation.get(second)["sent"])
        self.assertEqual(len(self.lines(kind="progress")), 1)                    # one packet on the console
        self.assertEqual(checkpoints.load(other.run_key).status, checkpoints.HELD)   # still held
        self.assertIn("past the cap", escalation.hold_reply(second).text)
        self.assertIn(PACKET["needs"], commands.handle(SID, f"/packet esc-{second[-1]}").text)

    def test_approvals_count_against_the_same_cap(self):
        args = disclosure.stamp_call("reply_send", {"recipient_employee_id": LEO, "request_id": REQ,
                                                    "body": "SYNTHETIC. Hi Leo."})
        Registry().call("reply_send", args, ctx(f"{SID}#9"))                    # one approval asked today
        self.assertEqual(len(approvals.pending()), 1)
        self.assertEqual(escalation.asks_today(), 1)
        with mock.patch.object(escalation, "DAILY_CAP", 1):
            packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        self.assertFalse(escalation.get(packet_id)["sent"])

    # -- the commands -----------------------------------------------------------------------

    def test_a_malformed_answer_does_nothing(self):
        packet_id = escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        self.assertIn("Nothing was done", commands.handle(LATER, f"/answer {packet_id}").text)
        self.assertIn("Nothing was done", commands.handle(LATER, "/answer yes").text)
        self.assertEqual(escalation.get(packet_id)["status"], escalation.PENDING)
        self.assertEqual(escalation.normal_id("esc-7"), "ESC-0007")

    def test_reset_clears_every_packet(self):
        escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        escalation.reset()
        self.assertEqual(escalation.held(), [])

    def test_the_sandbox_reset_clears_the_escalations_too(self):
        escalation.escalate(self.cp.run_key, "ambiguous_write", **PACKET)
        seed.reset()                                           # make app, run.py
        self.assertEqual(escalation.held(), [])
