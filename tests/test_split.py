"""The split's contract and cheap checks, on the m2-split branch (Part 3): a return that breaks the
contract is rejected and traced; the end-state check passes the right combined result and fails a
planted divergent one; the one budget is shared by every agent. No tokens. Owner: Shruti Tewaney."""

import unittest
import uuid

import channel
from agent import disclosure, executor, planner
from agent.bounds import Bound
from agent.split import checks, contract
from harness import runtime
from harness.trace import read_trace
from sandbox import access
from tests import fakes
from tests.support import ScratchTest, fixed_run

SID = "s-split"
COORD, ACCESS = f"{SID}#1", f"{SID}#2"
ANA, VICTOR, MIRA, HANNAH, GRACE = fakes.ANA, fakes.VICTOR, fakes.MIRA, fakes.HANNAH, fakes.GRACE

T06 = {"id": "t06_split_test", "operator": [fakes.T06_TEXT], "outside": [], "check": "checks/example.py",
       "input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0164", "set": {"end_date": "2026-10-28"}}}


def lines_of_a_role(run_key=ACCESS):
    """The trace lines one access role leaves: two tool calls and one model call, with its run key,
    and a call of another agent that must not count."""
    return [
        {"type": "model_call", "run_key": run_key, "usage": {"input_tokens": 300, "output_tokens": 40}},
        {"type": "tool_call", "run_key": run_key, "name": "access_list_memberships", "input": {"employee_id": ANA}},
        {"type": "tool_call", "run_key": run_key, "name": "access_revoke_all", "input": {"employee_id": ANA}},
        {"type": "tool_call", "run_key": COORD, "name": "directory_list_contractors_ended", "input": {}},
    ]


def a_return(lines, **change):
    ret = contract.build("access", run_key=ACCESS, parent=COORD, task=f"remove all access of {ANA}",
                         status="found", result={"employee_id": ANA, "removed": ["SYN-GRP-JIRA-READ"]}, lines=lines)
    ret.update(change)
    return ret


class ContractTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def returns(self):
        return [x for x in read_trace(runtime.trace(SID).path) if x["type"] == "role_return"]

    def test_a_return_built_from_the_trace_is_accepted_and_traced(self):
        lines = lines_of_a_role()
        ret = a_return(lines)
        self.assertEqual(ret["spent"], {"tool_calls": 2, "tokens": 340})        # the coordinator's call is not its
        self.assertEqual([s["tool"] for s in ret["searched"]], ["access_list_memberships", "access_revoke_all"])
        self.assertTrue(contract.accept(ret, lines))
        self.assertEqual([(r["valid"], r["role"], r["parent"]) for r in self.returns()], [(True, "access", COORD)])

    def test_a_return_that_breaks_the_contract_is_rejected_and_traced(self):
        lines = lines_of_a_role()
        broken = {
            "a missing field": {k: v for k, v in a_return(lines).items() if k != "not_determined"},
            "an unknown status": a_return(lines, status="done"),
            "spent as the model says it": a_return(lines, spent={"tool_calls": 2, "tokens": 100}),
            "a search it never made": a_return(lines, searched=[{"tool": "access_revoke_all", "args": {}}]),
            "found, with something undetermined": a_return(lines, not_determined=["whether Ana's portal group is gone"]),
            "a candidate that names nothing undetermined": a_return(lines, status="candidate"),
            "an empty result": a_return(lines, result={}),
            "its parent's run key": a_return(lines, run_key=COORD, parent=COORD),
            "an extra field": {**a_return(lines), "confidence": 0.9},
        }
        for why, ret in broken.items():
            with self.subTest(why=why):
                self.assertTrue(contract.problems(ret, lines), why)
                self.assertFalse(contract.accept(ret, lines))
        rejected = [r for r in self.returns() if not r["valid"]]
        self.assertEqual(len(rejected), len(broken))
        self.assertTrue(all(r["problems"] for r in rejected))

    def test_out_of_budget_holds_only_when_the_budget_is_spent(self):
        lines = lines_of_a_role()
        ret = a_return(lines, status="out_of_budget", result={})
        self.assertFalse(contract.accept(ret, lines, budget_left=500))
        self.assertTrue(contract.accept(ret, lines, budget_left=0))


class SharedBudgetTest(unittest.TestCase):
    def test_one_budget_counts_every_agent(self):
        budget = checks.SharedBudget(total=1000)
        budget.charge(COORD, 400)
        budget.charge(ACCESS, 350)
        self.assertEqual((budget.used, budget.left, budget.exhausted()), (750, 250, False))
        budget.charge(ACCESS, 300)
        self.assertTrue(budget.exhausted())
        self.assertEqual(budget.by_agent, {COORD: 400, ACCESS: 650})

    def test_the_default_is_the_agent_paths_budget(self):
        self.assertEqual(checks.SharedBudget().total, Bound().token_budget)


class EndStateTest(ScratchTest):
    """The combined result read from the sandbox: the right one passes; a divergent one fails,
    though every part of it could pass its own check."""

    def start(self, run_id):
        context = planner.build_context(fakes.T06_TEXT)
        return checks.expected_end(context)

    def note(self, manager, people):
        channel.send(f"SYN-MAIL-{manager}", disclosure.stamp(f"SYNTHETIC. Removed the access of {', '.join(people)}."))

    def test_the_right_combined_result_passes(self):
        run_id = f"split-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold"):
            expected = self.start(run_id)
            executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
            fakes.approve_all()
            self.assertEqual(checks.end_state(expected, run_id), [])

    def test_a_divergent_end_state_fails_though_each_part_looks_done(self):
        run_id = f"split-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="never-stops"):
            expected = self.start(run_id)
            # Ana's access went; Mira's did not; no proposal for Victor's hold; both notes were sent,
            # and Hannah's names Victor, who is Grace's
            for m in access.memberships(ANA):
                access.revoke(ANA, m["group_id"], request_id=None, key=None, stamp=run_id, actor="agent", reason="x")
            self.note(HANNAH, [ANA, MIRA, VICTOR])
            self.note(GRACE, [VICTOR])
            wrong = checks.end_state(expected, run_id)
        self.assertTrue(any(w.startswith(f"{MIRA} still holds") for w in wrong), wrong)
        self.assertTrue(any(w.startswith(f"{VICTOR} still holds") for w in wrong), wrong)
        self.assertIn(f"no proposal to remove {VICTOR}'s protected {fakes.LEGAL_HOLD}", wrong)
        self.assertTrue(any(f"the note to {HANNAH} names" in w for w in wrong), wrong)

    def test_a_stranger_touched_fails(self):
        run_id = f"split-{uuid.uuid4().hex[:8]}"
        leo = "SYN-EMP-0031"
        with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold"):
            expected = self.start(run_id)
            executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
            fakes.approve_all()
            group = access.memberships(leo)[0]["group_id"]
            access.revoke(leo, group, request_id=None, key=None, stamp=run_id, actor="agent", reason="x")
            wrong = checks.end_state(expected, run_id)
        self.assertEqual(wrong, [f"access was removed from ['{leo}'], whom the task does not cover"])

    def test_a_missing_note_and_a_note_without_the_line_fail(self):
        run_id = f"split-{uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="never-stops"):
            expected = self.start(run_id)
            channel.send(f"SYN-MAIL-{GRACE}", f"SYNTHETIC. Removed the access of {VICTOR}.")   # no line; none to Hannah
            wrong = checks.end_state(expected, run_id)
        self.assertIn(f"0 notes to {HANNAH}, not one", wrong)
        self.assertIn(f"the note to {GRACE} has no disclosure line", wrong)
