"""The recovery test (Part 1, task 2): on every test task that writes, the process dies after
the sandbox commits the task's last write and before our code records it, and a new process
carries the run on, under the same run ID, with the operator's next turn.
Owner: Aman Panigrahi. No tokens: the models are fakes (free = True).

The death is os._exit(9), the same as kill -9: no handler and no atexit runs. It comes in
checkpoints.record_completed, the call by which the registry adds a write to the run's
journal, so the write is in the sandbox and not in the journal. Each case:

    1. plays the task once with no crash, as run.py plays it (runner.play: the operator's
       turns, the strangers' replies and the operator's approvals), and counts its writes
    2. plays it again under a new run ID, and the process dies at the last of those writes
       (or at the first write of a named tool, for the cases that die earlier)
    3. in a new process, plays the operator's turns that did not run, or one more turn if
       the death came in the last one; no stranger writes again and no approval is answered

and asserts three things:

    1. the task's check passes on the run
    2. the sandbox holds the same effects as the run with no crash (one effect for each write)
    3. no write ran twice: each write has one successful tool_call line in the run's trace

Each case also keeps what the sandbox held before the run, after the crash and after the
resume. With RECOVERY_RECORD set to a file name, the module writes them there as JSON
(scripts/recovery_states.py; docs/milestones/m2_recovery_<date>.json, cited in M2.md).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter

from agent import exits
from harness import ROOT
from harness.client import Usage
from harness.trace import read_trace
from tests import fakes
from tests.support import ScratchTest, _load_task, check_run

CHILD = r"""
import json, os, sys
from agent import checkpoints
from harness import runner, turns
from tests.support import _load_task, fixed_run

task_file, rid, backend, step, die_at, log, rest = sys.argv[1:8]
real_record, real_turn = checkpoints.record_completed, turns.operator_turn
written = 0


def note(**line):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def record_completed(run_key, key, *, tool, args, result):
    global written
    written += 1
    note(write=written, tool=tool)
    if written == int(die_at):
        os._exit(9)          # the sandbox committed the write; our code has not recorded it
    return real_record(run_key, key, tool=tool, args=args, result=result)


def operator_turn(session_id, text):
    note(turn=text)
    return real_turn(session_id, text)


checkpoints.record_completed, turns.operator_turn = record_completed, operator_turn
task = _load_task(task_file)
with fixed_run(task_file, rid, backend=backend, fresh=step != "resume"):
    if step == "play":       # the task as run.py plays it
        runner.play(task, rid)
    elif step == "resume":   # the operator's next turns; the strangers and the approvals are done
        runner.play({**task, "operator": json.loads(rest), "outside": [], "approvals": []}, rid)
"""
STATES: list[dict] = []          # what the sandbox held at each point of each case, for RECOVERY_RECORD

CARRY_ON = "Please handle the new access requests."
LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0001"
REQ_ID = re.compile(r"SYN-REQ-\d{4}")
CASE = re.compile(r"Request (SYN-REQ-\d{4}), received .*?The requester, from the directory: [^(]*\((SYN-EMP-\d{4})\)")


def _calls(*calls, name="recovery-fake"):
    return ([{"type": "tool_use", "id": f"toolu_{n}", "name": tool, "input": args}
             for n, (tool, args) in enumerate(calls)], "tool_use", name, Usage(40, 20))


def _said(text, name="recovery-fake"):
    return [{"type": "text", "text": text}], "end_turn", name, Usage(40, 10)


def _steps_taken(messages) -> int:
    return sum(1 for m in messages if m["role"] == "assistant")


def _offered(tools) -> set[str]:
    return {t["name"] for t in tools or []}


class DoesLeosRequest(fakes.RouterFake):
    """t04 as U4 does it: the three groups Leo's role allows are granted and the two that need
    a person are proposed; then the reply is drafted and the request set pending, its last
    write; then "Done.". The operator's question about it is routed as a question."""

    def create(self, *, system, messages, tools):
        if not tools:
            if "groups_mentioned" not in (system or "") and "What did you do" in json.dumps(messages):
                return _said(json.dumps({"intent": "question", "request_ids": []}))
            return super().create(system=system, messages=messages, tools=tools)
        if "reply_draft" not in _offered(tools):         # U5, the operator's question
            return _said("Leo has three groups; two wait for a person, and his reply is drafted.")
        steps = _steps_taken(messages)
        if steps == 0:
            return _calls(*[("access_grant", {"employee_id": LEO, "group_id": g, "request_id": REQ})
                            for g in ("SYN-GRP-FINREP-READ", "SYN-GRP-CONFLUENCE-FINANCE", "SYN-GRP-JIRA-WRITE")],
                          *[("access_propose_change", {"employee_id": LEO, "group_id": g, "action": "grant",
                                                       "request_id": REQ, "reason": "it needs a person's approval"})
                            for g in ("SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN")])
        if steps == 1:
            return _calls(("reply_draft", {"recipient_employee_id": LEO, "request_id": REQ,
                                           "body": "SYNTHETIC. Hi Leo, you now have SYN-GRP-FINREP-READ, "
                                                   "SYN-GRP-CONFLUENCE-FINANCE and SYN-GRP-JIRA-WRITE. "
                                                   "SYN-GRP-FINREP-WRITE and SYN-GRP-PAYROLL-ADMIN wait for "
                                                   "approval."}),
                          ("requests_set_status", {"request_id": REQ, "status": "pending_operator",
                                                   "note": "two groups wait for a person"}))
        return _said("Done.")


class WorksTheOperatorsDay(fakes.RouterFake):
    """s01, the operator's day, as a fair model does it. The operator router routes a stated rule
    as "none", a message that names request IDs as process_requests with those IDs, and a
    question as a question. The request router names the one group each request asks for, by
    ID. U4 drafts a reply to the requester and sets the request pending. U5 answers each
    question from the sandbox, as its read tools would show it."""

    GROUPS = (("Finance Reports", "SYN-GRP-FINREP-READ"), ("data warehouse", "SYN-GRP-DWH-READ"),
              ("Jira write", "SYN-GRP-JIRA-WRITE"))
    QUESTIONS = {"finish": "Which requests did you finish today?", "remove": "Whose access did you remove today?",
                 "Jira editors": "Did anyone get Jira editors", "still waiting": "Is SYN-REQ-0005 still waiting"}

    def create(self, *, system, messages, tools):
        text = json.dumps(messages)
        if not tools and "groups_mentioned" in (system or ""):
            groups = [g for words, g in self.GROUPS if words.lower() in text.lower()]
            answer = ({"intent": "grant", "subject": "self", "groups_mentioned": groups[:1]}
                      if groups and "same access" not in text else
                      {"intent": "other", "subject": "self", "groups_mentioned": []})
            return _said(json.dumps(answer))
        if not tools:
            message = text.rsplit("The operator's message:", 1)[-1]
            if "This message states a rule" in message:
                answer = {"intent": "none", "request_ids": []}
            elif "?" in message:
                answer = {"intent": "question", "request_ids": []}
            else:
                answer = {"intent": "process_requests", "request_ids": sorted(set(REQ_ID.findall(message)))}
            return _said(json.dumps(answer))
        if "reply_draft" not in _offered(tools):         # U5
            return _said(self.answer(messages))
        if _steps_taken(messages):
            return _said("Done.")
        request, requester = CASE.search(text).groups()
        return _calls(("reply_draft", {"recipient_employee_id": requester, "request_id": request,
                                       "body": "SYNTHETIC. Hi, your request is with the IT team, who will check it."}),
                      ("requests_set_status", {"request_id": request, "status": "pending_operator",
                                               "note": "it waits for the operator"}))

    @staticmethod
    def answer(messages) -> str:
        from harness import runtime
        from sandbox import db, requests
        said = json.dumps(messages)          # the earlier questions are in it too: the last one is asked now
        question = max(("finish", "remove", "Jira editors", "still waiting"), key=lambda q: said.rfind(WorksTheOperatorsDay.QUESTIONS[q]))
        run_id = runtime.current_run().run_id
        with db.connect() as conn:
            removed = [r["employee_id"] for r in conn.execute(
                "SELECT DISTINCT employee_id FROM memberships WHERE removed_stamp = ? ORDER BY employee_id",
                (run_id,)).fetchall()]
            jira = conn.execute("SELECT COUNT(*) AS n FROM memberships WHERE stamp = ? AND group_id = ?",
                                (run_id, "SYN-GRP-JIRA-WRITE")).fetchone()["n"]
        if question == "finish":
            return ", ".join(r["id"] for r in requests.all_requests() if r["status"] == "done") or "None."
        if question == "remove":
            return ", ".join(removed) or "No one."
        if question == "Jira editors":
            return "Yes." if jira else "No."
        return "No." if (requests.get("SYN-REQ-0005") or {}).get("status") in ("done", "refused") else "Yes."


class RecoveryTest(ScratchTest):
    def child(self, task: str, rid: str, backend: str, step: str, *, die_at: int = 0, rest=()) -> int:
        log = self.tmp / f"{rid}.{step}.log"
        done = subprocess.run([sys.executable, "-c", CHILD, task, rid, backend, step, str(die_at), str(log),
                               json.dumps(list(rest))], cwd=ROOT, env=os.environ.copy(),
                              capture_output=True, text=True, timeout=300)
        if done.returncode not in (0, 9):
            self.fail(f"the child process failed ({done.returncode}):\n{done.stderr[-3000:]}")
        self.log = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return done.returncode

    def effects(self) -> dict:
        """What the sandbox and the channel hold that a run writes, counted."""
        import channel
        from sandbox import db
        with db.connect() as conn:
            def count(sql):              # each row by its values (the sandbox's rows are dicts)
                return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in conn.execute(sql).fetchall()]
            found = {
                "memberships": Counter(count("SELECT employee_id, group_id, removed_at IS NULL FROM memberships "
                                             "WHERE stamp <> 'seed' OR COALESCE(removed_stamp, 'seed') <> 'seed'")),
                "pending": Counter(count("SELECT kind, employee_id, group_id FROM pending_actions")),
                "notices": Counter(count("SELECT thread_id, template_id FROM notices_sent")),
                "credentials": Counter(count("SELECT employee_id, system_id FROM credentials")),
                "statuses": Counter(count("SELECT id, status FROM requests")),
            }
        found["messages"] = Counter((t["thread_id"], m.text) for t in channel.threads()
                                    for m in channel.messages(t["thread_id"])
                                    if m.sender in channel.OURS and not m.seeded)
        return found

    def trace(self, rid: str) -> list[dict]:
        return read_trace(next((self.tmp / "traces").rglob(f"{rid}.trace.jsonl")))

    def writes_made(self, rid: str) -> Counter:
        """Each write's successful tool_call lines in the run's trace, by tool and effect. A gated
        call has a line for its wait too, so the counts are compared with the run with no crash."""
        made = Counter()
        for line in self.trace(rid):
            if line["type"] == "tool_call" and exits.is_write(line.get("name", "")) and not line.get("is_error"):
                made[(line["name"], tuple(sorted(exits._effect(line["name"], line.get("input") or {}).items())))] += 1
        return made

    def recover(self, task: str, backend: str, *, at: str | None = None):
        """The run with no crash, then the same task killed at a write, then the operator's next
        turns in a new process. `at` names a tool: the death comes at its first write, not at the
        last write. Keeps what the sandbox held before the run, after the crash and after the resume."""
        name = os.path.basename(task).split(".")[0]
        self.assertEqual(self.child(task, f"clean-{name}", backend, "play"), 0)
        clean, writes = self.effects(), [x for x in self.log if "write" in x]
        self.assertTrue(writes, f"{name} makes no write")
        die = next(x for x in writes if x["tool"] == at) if at else writes[-1]
        rid = f"crash-{name}-{die['tool']}-{die['write']}"
        self.child(task, rid, backend, "seed")
        before = self.effects()
        self.assertEqual(self.child(task, rid, backend, "play", die_at=die["write"]), 9, "the process died at the write")
        crashed, played = self.effects(), [x["turn"] for x in self.log if "turn" in x]
        operator = _load_task(task)["operator"]
        rest = operator[len(played):] or [CARRY_ON]
        self.assertEqual(self.child(task, rid, backend, "resume", rest=rest), 0)
        STATES.append(_states(task, die, rid, before, crashed, self.effects(), clean))
        return rid, clean, f"clean-{name}"

    def assert_recovered(self, task: str, rid: str, clean: dict, clean_rid: str):
        replies = [{k: x.get(k) for k in ("function", "text", "actions", "waiting_for")}
                   for x in self.trace(rid) if x["type"] == "reply"]
        result = check_run(task, rid, replies=replies)
        self.assertTrue(result["passed"], result["reason"])
        self.assertEqual(self.effects(), clean, "one effect for each write, as in the run with no crash")
        once = self.writes_made(clean_rid)
        again = {k: n for k, n in self.writes_made(rid).items() if n > once[k]}
        self.assertEqual(again, {}, "no write ran more often than in the run with no crash")

    def case(self, task: str, backend: str, *, at: str | None = None) -> list[dict]:
        task = f"tasks/{task}.yaml"
        rid, clean, clean_rid = self.recover(task, backend, at=at)
        self.assert_recovered(task, rid, clean, clean_rid)
        return self.trace(rid)


def _flat(effects: dict) -> Counter:
    return Counter({f"{table}: {' '.join(str(v) for v in row)}": n
                    for table, rows in effects.items() for row, n in rows.items()})


def _states(task, die, rid, before, crashed, resumed, clean) -> dict:
    """One case: each effect that the run changes, with its count before the run, after the
    crash, after the resume and in the run with no crash."""
    points = [_flat(x) for x in (before, crashed, resumed, clean)]
    rows = sorted(k for k in set().union(*points) if len({p[k] for p in points}) > 1)
    return {"task": task, "died_at": {"tool": die["tool"], "write": die["write"]}, "run_id": rid,
            "effects": [{"effect": k, "before": points[0][k], "after_crash": points[1][k],
                         "after_resume": points[2][k], "no_crash": points[3][k]} for k in rows]}


def tearDownModule():
    path = os.environ.get("RECOVERY_RECORD")
    if path and STATES:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"what": "the sandbox's effects at each point of each recovery case (tests/test_recovery.py)",
                       "cases": STATES}, f, indent=1)


class TheWorkflowsRecover(RecoveryTest):
    """U1, U2 and U3: a death at the last write, and at an earlier one."""

    def test_t01_at_its_last_write(self):
        self.case("t01_standard_jira", "tests.fakes:RoutesStandardGrant")

    def test_t01_at_its_grant(self):
        self.case("t01_standard_jira", "tests.fakes:RoutesStandardGrant", at="access_grant")

    def test_t02_at_its_last_write(self):
        self.case("t02_new_starter", "tests.fakes:RouterFake")

    def test_t02_at_its_credential(self):
        self.case("t02_new_starter", "tests.fakes:RouterFake", at="credentials_issue")

    def test_t03_at_its_last_write(self):
        self.case("t03_contractor_left", "tests.fakes:RouterFake")

    def test_t03_at_its_removal(self):
        self.case("t03_contractor_left", "tests.fakes:RouterFake", at="access_revoke_all")


class TheLoopRecovers(RecoveryTest):
    """U4: the death comes during the model's step, after its write landed. The new process
    finishes that step with the saved arguments, never new ones from the model."""

    def assert_finished_after_restart(self, lines, event="step_finished_after_restart"):
        kinds = [x["type"] for x in lines]
        self.assertIn("run_crashed", kinds)
        self.assertIn(event, kinds)
        self.assertNotIn("approval_requested", kinds[kinds.index("run_crashed"):],
                         "the new process asks the operator for nothing again")

    def test_t04_at_its_last_write(self):
        self.assert_finished_after_restart(self.case("t04_same_access_as", "tests.test_recovery:DoesLeosRequest"))

    def test_t05_at_its_last_write_before_the_wait(self):
        self.assert_finished_after_restart(self.case("t05_on_behalf", "tests.test_wait:AsksTheManagerThenGrants"))

    def test_t08_at_its_last_write_the_approved_reply(self):
        """The death comes as our code makes the reply that the operator approved. The new
        process reads the channel, finds the reply, and does not send it or ask again."""
        lines = self.case("t08_ask_manager", "tests.test_wait:AsksTheManagerThenGrants")
        self.assert_finished_after_restart(lines, "approved_calls_finished_after_restart")
        settled = [x for x in lines if x["type"] == "write_settled"]
        self.assertEqual([(x["tool"], x["outcome"], x["read"]) for x in settled], [("reply_send", "happened", "target")])

    def test_t09_at_its_last_write(self):
        self.assert_finished_after_restart(self.case("t09_later_session", "tests.test_memory:DraftsWhenTheMemorySaysSo"))

    def test_t10_at_its_last_write_the_approved_reply(self):
        self.assert_finished_after_restart(self.case("t10_gated_reply", "tests.fakes:AsksTheManagerHearsNo"),
                                           "approved_calls_finished_after_restart")


class ThePlanRecovers(RecoveryTest):
    """U6: the death comes at the last manager note, after the operator approved the plan."""

    def test_t06_at_its_last_write(self):
        lines = self.case("t06_bulk_ended_contractors", "tests.fakes:GoodPlanner")
        self.assertIn("run_crashed", [x["type"] for x in lines])


class TheSessionRecovers(RecoveryTest):
    """s01: the death comes at the day's last write, and the four questions follow in a new process."""

    def test_s01_at_its_last_write(self):
        lines = self.case("sessions/s01_operator_day", "tests.test_recovery:WorksTheOperatorsDay")
        self.assertIn("run_crashed", [x["type"] for x in lines])
