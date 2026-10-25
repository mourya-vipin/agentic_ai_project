"""The critique loop on reply_draft (Lecture 2, Section C), on fakes and planted
sandbox states, critique.requester_reply = external unless a test says off: each of
the check's failure conditions; a failing draft revised with its reason while a round
remains; a draft failing at the bound never written; a passing draft written once.
No tokens. Owner: Shruti Tewaney."""

import uuid

from agent import critique, executor
from agent.bounds import Bound
from agent.registry import CallContext, get_registry
from harness.trace import read_trace
from sandbox import access, pending
from tests import fakes
from tests.support import ScratchTest, fixed_run

LEO, PRIYA, HANNAH = "SYN-EMP-0031", "SYN-EMP-0012", "SYN-EMP-0060"
REQ = "SYN-REQ-0001"
JIRA_W, FINREP_W, JIRA_R = "SYN-GRP-JIRA-WRITE", "SYN-GRP-FINREP-WRITE", "SYN-GRP-JIRA-READ"
U4 = {"id": "critique_test", "operator": ["Handle the new request."], "outside": [], "check": "checks/example.py",
      "input": {"kind": "request", "thread": "SYN-MSG-T90", "from": "leo.martins@syn-corp.example",
                "text": "SYNTHETIC. Priya Nair is on leave; could I have the same access she has? Leo"}}
TRUE = (f"Hi Leo, I granted you {JIRA_W} for {REQ}. {FINREP_W} needs your manager's approval, so it is "
        "waiting for the operator. IT access desk.")


class CritiqueTest(ScratchTest):
    """A run of U4's case in which this run granted Jira editors under the request and
    wrote a proposal for Finance Reports editors."""

    def open_run(self, overrides=None):
        self.run_id = f"crit-{uuid.uuid4().hex[:8]}"
        self._run = fixed_run(U4, self.run_id, overrides=overrides)
        self.run = self._run.__enter__()
        self.addCleanup(self._run.__exit__, None, None, None)
        access.grant(LEO, JIRA_W, request_id=REQ, key=None, stamp=self.run_id, actor="agent")
        self.pid = pending.create("proposed_change", request_id=REQ, employee_id=LEO, key=None, stamp=self.run_id,
                                  action="grant", group_id=FINREP_W)

    def check(self, body):
        return critique.check_draft(recipient_employee_id=LEO, request_id=REQ, body=body, stamp=self.run_id)

    def draft(self, body):
        return get_registry().call("reply_draft", {"recipient_employee_id": LEO, "request_id": REQ, "body": body},
                                   CallContext(self.run_id, 1, "model", "nonstandard_request", request_id=REQ))

    def events(self):
        return [x for x in read_trace(self.run.trace_path) if x["type"] == "critique"]

    def drafts(self):
        return [p for p in pending.by_request(REQ) if p["kind"] == "reply_draft" and p["stamp"] == self.run_id]


class CheckTest(CritiqueTest):
    def setUp(self):
        super().setUp()
        self.open_run()

    def test_a_true_draft_passes(self):
        self.assertEqual(self.check(TRUE), [])

    def test_groups_are_recognised_by_their_catalog_name_too(self):
        body = "I granted you Jira editors. Finance Reports editors is waiting for your manager's approval."
        self.assertEqual(self.check(body), [])

    def test_1_a_group_said_granted_that_was_not(self):
        body = TRUE.replace(f"{FINREP_W} needs", f"I also granted {FINREP_W}; it needs")
        found = self.check(body)
        self.assertEqual(len(found), 1, found)
        self.assertIn(f"says {FINREP_W} was granted, but it is waiting for approval ({self.pid})", found[0])

    def test_2_a_group_said_removed_that_was_not(self):
        found = self.check(TRUE + f" I removed {JIRA_R}.")
        self.assertEqual(found, [f"The draft says {JIRA_R} was removed, but it is still active."])

    def test_3_a_group_said_waiting_with_no_open_record(self):
        body = f"Hi Leo, {JIRA_W} is waiting for approval, and so is {FINREP_W}."
        found = self.check(body)
        self.assertEqual(found, [f"The draft says {JIRA_W} is waiting for approval, but it was granted."])

    def test_4_a_draft_that_leaves_out_a_group_of_the_case(self):
        found = self.check(f"Hi Leo, I granted you {JIRA_W}. IT access desk.")
        self.assertEqual(len(found), 1, found)
        self.assertIn(f"does not mention {FINREP_W}", found[0])

    def test_5_another_request_or_a_person_outside_the_case(self):
        self.assertIn("names SYN-REQ-0002", self.check(TRUE + " See also SYN-REQ-0002.")[0])
        self.assertIn(f"names {HANNAH}", self.check(TRUE + f" Ask {HANNAH} if unsure.")[0])

    def test_a_person_the_request_itself_names_may_be_named(self):
        self.assertEqual(self.check(TRUE + f" These match what {PRIYA} has."), [])

    def test_a_negated_claim_is_not_a_claim(self):
        body = (f"Hi Leo, I granted you {JIRA_W}. {FINREP_W} has not been granted yet: it is waiting for your "
                "manager's approval.")
        self.assertEqual(self.check(body), [])

    def test_a_group_that_does_not_exist(self):
        self.assertIn("not a group in the catalog", self.check(TRUE + " Also SYN-GRP-TELEPORT.")[0])

    def test_the_reason_is_capped_at_a_few_facts(self):
        verdict = critique.review_draft(recipient_employee_id=LEO, request_id=REQ, body="Hello.", stamp=self.run_id,
                                        round=1)
        self.assertFalse(verdict.passed)
        self.assertLessEqual(verdict.reason.count("The draft"), critique.MAX_REASONS)


class RoundsTest(CritiqueTest):
    WRONG = TRUE + f" I removed {JIRA_R}."

    def test_a_failing_draft_is_revised_with_its_reason_while_a_round_remains(self):
        self.open_run()
        first = self.draft(self.WRONG)
        self.assertTrue(first.is_error)
        self.assertIsNone(first.end_reason)
        self.assertIn(f"{JIRA_R} was removed, but it is still active", first.content)
        self.assertIn("round 1 of 2", first.content)
        self.assertEqual(self.drafts(), [])
        second = self.draft(TRUE)
        self.assertFalse(second.is_error, second.content)
        self.assertEqual([(e["round"], e["verdict"]) for e in self.events()], [(1, "fail"), (2, "pass")])
        self.assertGreater(self.events()[1]["changed"], 0)
        self.assertEqual(len(self.drafts()), 1)

    def test_a_draft_still_failing_at_the_bound_is_never_written(self):
        self.open_run()
        self.draft(self.WRONG)
        held = self.draft(self.WRONG)
        self.assertEqual(held.end_reason, critique.CRITIQUE_HELD)
        self.assertEqual([e["verdict"] for e in self.events()], ["fail", "fail"])
        self.assertEqual(self.drafts(), [])

    def test_a_passing_draft_is_written_once_even_when_drafted_twice(self):
        self.open_run()
        first, again = self.draft(TRUE), self.draft(TRUE + " Thanks.")
        self.assertEqual(first.data["pending_id"], again.data["pending_id"])
        self.assertEqual(len(self.drafts()), 1)
        self.assertEqual(first.key, again.key)

    def test_with_the_switch_off_nothing_is_checked(self):
        self.open_run(overrides=["critique.requester_reply=off"])
        result = self.draft(self.WRONG)
        self.assertFalse(result.is_error)
        self.assertEqual(self.events(), [])
        self.assertEqual(len(self.drafts()), 1)


class PlannedNotesTest(ScratchTest):
    """The same check on U6's path: the planned executor's notes to each manager."""

    def run_planned(self, backend):
        run_id = self.run_id = f"crit-u6-{uuid.uuid4().hex[:8]}"
        task = {**U4, "input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0164",
                                "set": {"end_date": "2026-10-28"}}}
        with fixed_run(task, run_id, backend=f"tests.fakes:{backend}") as run:
            result = executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
        lines = read_trace(run.trace_path)
        notes = [p for p in pending.open_items("reply_draft") if p["stamp"] == run_id]
        return result, lines, notes

    def test_a_wrong_note_is_revised_with_its_reason_and_written_once(self):
        result, lines, notes = self.run_planned("WrongThenRightFiller")
        rounds = [(x["recipient"], x["round"], x["verdict"]) for x in lines if x["type"] == "critique"]
        self.assertEqual(rounds, [(HANNAH, 1, "fail"), (HANNAH, 2, "pass"),
                                  (fakes.GRACE, 1, "fail"), (fakes.GRACE, 2, "pass")])
        self.assertEqual(result.end_reason, executor.PLAN_DONE)
        self.assertEqual(sorted(n["employee_id"] for n in notes), sorted([HANNAH, fakes.GRACE]))

    def test_a_note_still_wrong_at_the_bound_is_held_and_ends_the_run(self):
        result, lines, notes = self.run_planned("AlwaysWrongFiller")
        self.assertEqual([x["terminal_reason"] for x in lines if x["type"] == "end"], [critique.CRITIQUE_HELD])
        self.assertEqual(notes, [])
        self.assertIn("held a drafted note", result.text)

    def test_a_bulk_note_that_leaves_out_a_person_or_a_group_fails(self):
        self.run_planned("GoodPlannerWithHold")
        found = critique.check_draft(recipient_employee_id=HANNAH, request_id=None,
                                     body=f"{fakes.ANA}'s contract ended: I removed {JIRA_W}.", stamp=self.run_id)
        self.assertTrue(any(f"does not name Mira Kaur ({fakes.MIRA})" in f for f in found), found)
        self.assertTrue(any("does not mention SYN-GRP-GITHUB-ENG" in f for f in found), found)
        self.assertFalse(any(fakes.VICTOR in f for f in found), found)     # Grace's report, not Hannah's
