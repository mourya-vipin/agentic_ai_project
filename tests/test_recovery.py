"""The recovery test (Part 1, task 2): for each test task that writes, the process dies after
the sandbox commits a write and before our code records it, and a new process carries the
run on, under the same run ID, on the operator's next turn.
Owner: Aman Panigrahi. No tokens: the models are fakes (free = True).

The death is os._exit(9), the same as kill -9: no handler and no atexit runs. It comes in
checkpoints.record_completed, the call by which the registry adds a write to the run's
journal, so the write is in the sandbox and not in the journal. Each case asserts three things:

    1. the task's check passes on the run
    2. the sandbox holds the same effects as a run with no crash (one effect for each write)
    3. no write ran twice: each write has one successful tool_call line in the run's trace

Each case also keeps what the sandbox held before the run, after the crash and after the
resume. With RECOVERY_RECORD set to a file name, the module writes them there as JSON
(scripts/recovery_states.py; docs/milestones/m2_recovery_<date>.json, cited in M2.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter

from agent import exits
from harness import ROOT
from harness.client import Usage
from harness.trace import read_trace
from tests import fakes
from tests.support import ScratchTest, check_run

CHILD = r"""
import os, sys
from agent import checkpoints
from harness import turns
from tests.support import fixed_run

task, rid, backend, fresh, die_tool, die_status, message = sys.argv[1:8]
if die_tool:
    real = checkpoints.record_completed

    def record_completed(run_key, key, *, tool, args, result):
        if tool == die_tool and (not die_status or str(args.get("status")) == die_status):
            os._exit(9)          # the sandbox committed the write; our code has not recorded it
        return real(run_key, key, tool=tool, args=args, result=result)

    checkpoints.record_completed = record_completed
with fixed_run(task, rid, backend=backend, fresh=fresh == "fresh"):
    if message:                  # with no message, the process only seeds the run: the state before it
        turns.operator_turn(rid, message)
"""
STATES: list[dict] = []          # what the sandbox held at each point of each case, for RECOVERY_RECORD

PROCESS = "Please handle the new access requests."
LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0001"


class DraftsAReplyToLeo(fakes.RouterFake):
    """Routes t04's request to U4, where the model's first step drafts a reply to Leo: a
    keyed write that the loop (not a workflow) makes. Then it says "Done."."""

    def create(self, *, system, messages, tools):
        if tools and not any(m["role"] == "assistant" for m in messages):
            return ([{"type": "tool_use", "id": "toolu_draft", "name": "reply_draft",
                      "input": {"recipient_employee_id": LEO, "request_id": REQ,
                                "body": "SYNTHETIC. Hi Leo, your request is with the IT team, who will check it."}}],
                    "tool_use", self.name, Usage(40, 10))
        return super().create(system=system, messages=messages, tools=tools)


class RecoveryTest(ScratchTest):
    def child(self, task: str, rid: str, backend: str, *, fresh: bool, die: tuple[str, str] = ("", ""),
              message: str = PROCESS) -> int:
        done = subprocess.run([sys.executable, "-c", CHILD, task, rid, backend, "fresh" if fresh else "keep",
                               die[0], die[1], message], cwd=ROOT, env=os.environ.copy(),
                              capture_output=True, text=True, timeout=180)
        if done.returncode not in (0, 9):
            self.fail(f"the child process failed ({done.returncode}):\n{done.stderr[-2000:]}")
        return done.returncode

    def effects(self) -> dict:
        """What the sandbox holds that a run writes, counted."""
        from sandbox import db
        with db.connect() as conn:
            def count(sql):              # each row by its values (the sandbox's rows are dicts)
                return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in conn.execute(sql).fetchall()]
            return {
                "memberships": Counter(count("SELECT employee_id, group_id, removed_at IS NULL FROM memberships "
                                             "WHERE request_id IS NOT NULL OR removed_request_id IS NOT NULL")),
                "pending": Counter(count("SELECT kind, employee_id, group_id FROM pending_actions")),
                "notices": Counter(count("SELECT thread_id, template_id FROM notices_sent")),
                "credentials": Counter(count("SELECT employee_id, system_id FROM credentials")),
                "statuses": Counter(count("SELECT id, status FROM requests")),
            }

    def writes_made(self, rid: str) -> Counter:
        """Each write's successful tool_call lines in the run's trace, by tool and effect."""
        path = next((self.tmp / "traces").rglob(f"{rid}.trace.jsonl"))
        made = Counter()
        for line in read_trace(path):
            if line["type"] == "tool_call" and exits.is_write(line.get("name", "")) and not line.get("is_error"):
                made[(line["name"], tuple(sorted(exits._effect(line["name"], line.get("input") or {}).items())))] += 1
        return made

    def recover(self, task: str, backend: str, die: tuple[str, str], *, message: str = PROCESS):
        """A clean run, then the same run with a death, then the next turn in a new process.
        It keeps what the sandbox held before the run, after the crash and after the resume."""
        name = os.path.basename(task).split(".")[0]
        self.child(task, f"clean-{name}", backend, fresh=True)
        clean = self.effects()
        rid = f"crash-{name}-{die[0]}-{die[1] or 'any'}"
        self.child(task, rid, backend, fresh=True, message="")
        before = self.effects()
        self.assertEqual(self.child(task, rid, backend, fresh=True, die=die), 9, "the process died at the write")
        crashed = self.effects()
        self.assertEqual(self.child(task, rid, backend, fresh=False, message=message), 0)
        STATES.append(_states(task, die, rid, before, crashed, self.effects(), clean))
        return rid, clean

    def assert_recovered(self, task: str, rid: str, clean: dict, *, check: bool = True):
        if check:
            result = check_run(task, rid)
            self.assertTrue(result["passed"], result["reason"])
        self.assertEqual(self.effects(), clean, "one effect for each write, as in the run with no crash")
        twice = {k: n for k, n in self.writes_made(rid).items() if n > 1}
        self.assertEqual(twice, {}, "no write ran twice")


def _flat(effects: dict) -> Counter:
    return Counter({f"{table}: {' '.join(str(v) for v in row)}": n
                    for table, rows in effects.items() for row, n in rows.items()})


def _states(task, die, rid, before, crashed, resumed, clean) -> dict:
    """One case: each effect that the run changes, with its count before the run, after the
    crash, after the resume and in the run with no crash."""
    points = [_flat(x) for x in (before, crashed, resumed, clean)]
    rows = sorted(k for k in set().union(*points) if len({p[k] for p in points}) > 1)
    return {"task": task, "died_at": {"tool": die[0], "status": die[1] or None}, "run_id": rid,
            "effects": [{"effect": k, "before": points[0][k], "after_crash": points[1][k],
                         "after_resume": points[2][k], "no_crash": points[3][k]} for k in rows]}


def tearDownModule():
    path = os.environ.get("RECOVERY_RECORD")
    if path and STATES:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"what": "the sandbox's effects at each point of each recovery case (tests/test_recovery.py)",
                       "cases": STATES}, f, indent=1)


class TheWorkflowsRecover(RecoveryTest):
    """U1, U2 and U3: a death at the first write, and at the last one."""

    def test_t01_after_its_last_write(self):
        rid, clean = self.recover("tasks/t01_standard_jira.yaml", "tests.fakes:RoutesStandardGrant",
                                  ("requests_set_status", "done"))
        self.assert_recovered("tasks/t01_standard_jira.yaml", rid, clean)

    def test_t01_after_its_grant(self):
        rid, clean = self.recover("tasks/t01_standard_jira.yaml", "tests.fakes:RoutesStandardGrant",
                                  ("access_grant", ""))
        self.assert_recovered("tasks/t01_standard_jira.yaml", rid, clean)

    def test_t02_after_its_last_write(self):
        rid, clean = self.recover("tasks/t02_new_starter.yaml", "tests.fakes:RouterFake",
                                  ("requests_set_status", "done"))
        self.assert_recovered("tasks/t02_new_starter.yaml", rid, clean)

    def test_t02_after_its_credential(self):
        rid, clean = self.recover("tasks/t02_new_starter.yaml", "tests.fakes:RouterFake", ("credentials_issue", ""))
        self.assert_recovered("tasks/t02_new_starter.yaml", rid, clean)

    def test_t03_after_its_last_write(self):
        rid, clean = self.recover("tasks/t03_contractor_left.yaml", "tests.fakes:RouterFake",
                                  ("requests_set_status", "done"))
        self.assert_recovered("tasks/t03_contractor_left.yaml", rid, clean)

    def test_t03_after_it_removes_the_access(self):
        rid, clean = self.recover("tasks/t03_contractor_left.yaml", "tests.fakes:RouterFake",
                                  ("access_revoke_all", ""))
        self.assert_recovered("tasks/t03_contractor_left.yaml", rid, clean)


class TheLoopRecovers(RecoveryTest):
    """U4: the death comes during the model's step, after its write landed. The new process
    finishes that step with the saved arguments, never new ones from the model."""

    def test_t04_after_the_draft_that_the_model_wrote(self):
        task = "tasks/t04_same_access_as.yaml"
        rid, clean = self.recover(task, "tests.test_recovery:DraftsAReplyToLeo", ("reply_draft", ""),
                                  message="What did you do for Leo, and what is still waiting on someone?")
        # t04's check wants the real model's work; the fake makes one draft, so the check is not run here
        self.assert_recovered(task, rid, clean, check=False)
        lines = read_trace(next((self.tmp / "traces").rglob(f"{rid}.trace.jsonl")))
        kinds = [x["type"] for x in lines]
        self.assertIn("run_crashed", kinds)
        self.assertIn("step_finished_after_restart", kinds)
        settled = [x for x in lines if x["type"] == "write_settled"]
        self.assertEqual([(x["tool"], x["outcome"]) for x in settled], [("reply_draft", exits.MADE)],
                         "the same call again, with the same key: the target kept its first result")


if __name__ == "__main__":
    import unittest
    unittest.main()
