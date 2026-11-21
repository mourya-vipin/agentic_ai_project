"""The stop and its commands (Part 2, task 6): the flag outside the agent that the hook reads
first, cancel, resume and the rollback plan, and the commands read before any model.
No tokens. Owner: Shruti Tewaney."""

import importlib
import threading

import channel
from agent import checkpoints, commands, control
from agent.registry import CallContext, Registry
from harness import runtime
from harness.paths import state_dir
from harness.trace import read_trace
from sandbox import access, seed
from tests.support import ScratchTest

SID = "s-stop"
LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"
ALLOWED = "SYN-GRP-FINREP-READ"
READ = {"employee_id": LEO, "email": None, "name": None}


def ctx(run_no=1, session_id=SID):
    return CallContext(session_id, 1, "model", "nonstandard_request", request_id=REQ,
                       run_key=control.run_key(session_id, run_no))


class StopTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def lines(self, kind=None):
        return [x for x in read_trace(runtime.trace(SID).path) if kind is None or x["type"] == kind]

    def holds(self, person, group):
        return group in {m["group_id"] for m in access.memberships(person)}

    # -- the flag --------------------------------------------------------------------------

    def test_nothing_is_stopped_until_a_stop(self):
        self.assertFalse(control.stopped(None))
        self.assertFalse(control.stopped(control.run_key(SID, 1)))
        self.assertIsNone(control.state(control.run_key(SID, 1)))

    def test_a_stop_of_one_run_refuses_its_calls_and_no_other_runs(self):
        self.assertEqual(control.stop(SID, 1), [f"{SID}#1"])
        grant = self.registry.call("access_grant", {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ}, ctx(1))
        read = self.registry.call("directory_get_employee", READ, ctx(1))
        other = self.registry.call("directory_get_employee", READ, ctx(2))
        self.assertTrue(grant.is_error)
        self.assertTrue(read.is_error)
        self.assertIn("stopped", read.content)
        self.assertFalse(self.holds(LEO, ALLOWED))          # nothing reached the sandbox
        self.assertFalse(other.is_error, other.content)
        denied = self.lines("hook_denied")
        self.assertEqual(len(denied), 2)
        self.assertTrue(all("stopped" in d["error"] for d in denied))

    def test_a_stop_of_the_session_covers_its_runs_and_no_other_session(self):
        # no checkpoint store yet, so the whole session is flagged
        self.assertEqual(control.stop(SID), [f"{SID}#*"])
        self.assertTrue(control.stopped(f"{SID}#1"))
        self.assertTrue(control.stopped(f"{SID}#7"))
        self.assertFalse(control.stopped("s-other#1"))

    def test_the_stop_is_traced_before_any_later_call(self):
        control.stop(SID, 1)
        self.registry.call("directory_get_employee", READ, ctx(1))
        kinds = [x["type"] for x in self.lines() if x["type"] in ("stop_flag", "tool_call")]
        self.assertEqual(kinds, ["stop_flag", "tool_call"])
        self.assertTrue(self.lines("stop_flag")[0]["at"])

    def test_the_flag_survives_a_restart(self):
        control.stop(SID, 3)
        self.assertTrue((state_dir() / control.DB_FILE).exists())
        importlib.reload(control)                           # a new process reads the same file
        self.assertTrue(control.stopped(f"{SID}#3"))

    def test_a_stop_sent_from_another_thread_lands(self):
        thread = threading.Thread(target=control.stop, args=(SID, 4))
        thread.start()
        thread.join(5)
        self.assertTrue(control.stopped(f"{SID}#4"))

    def test_reset_clears_every_flag(self):
        control.stop(SID, 1)
        control.reset()
        self.assertFalse(control.stopped(f"{SID}#1"))
        self.assertEqual(control.history(), [])

    def test_a_session_id_with_a_hash_keeps_its_run_number(self):
        self.assertEqual(control.split_key("s#weird#12"), ("s#weird", "12"))
        with self.assertRaises(ValueError):
            control.split_key("no-run-number")

    # -- cancel and resume -----------------------------------------------------------------

    def test_cancel_is_for_good(self):
        key = control.run_key(SID, 1)
        control.stop(SID, 1)
        control.cancel(key)
        self.assertTrue(control.stopped(key))
        self.assertEqual(control.state(key), control.CANCELLED)
        self.assertIn("cannot be resumed", control.resume(key).text)
        self.assertEqual(control.stop(SID, 1), [])          # a cancelled run is not stopped again
        self.assertEqual(self.lines("end")[-1]["terminal_reason"], "cancelled")

    def test_resume_lifts_the_stop_and_records_its_time(self):
        key = control.run_key(SID, 1)
        control.stop(SID, 1)
        reply = control.resume(key)
        self.assertFalse(control.stopped(key))
        self.assertIn("lifted", reply.text)
        self.assertEqual([h["command"] for h in control.history(key)], ["stop", "resume"])
        self.assertTrue(all(h["at"] for h in control.history(key)))
        self.assertIn("not stopped", control.resume(key).text)
        read = self.registry.call("directory_get_employee", READ, ctx(1))
        self.assertFalse(read.is_error, read.content)

    def test_resume_of_one_run_lifts_a_session_stop(self):
        control.stop(SID)
        control.resume(control.run_key(SID, 2))
        self.assertFalse(control.stopped(f"{SID}#2"))

    def test_a_run_stopped_again_after_a_resume_is_stopped(self):
        key = control.run_key(SID, 1)
        control.stop(SID, 1)
        control.resume(key)
        control.stop(SID, 1)
        self.assertTrue(control.stopped(key))

    # -- the rollback plan -----------------------------------------------------------------

    def test_compensations_are_newest_first_and_say_none_where_there_is_none(self):
        completed = {
            "k1": {"tool": "access_grant", "args": {"employee_id": LEO, "group_id": ALLOWED},
                   "result": {}, "at": "2026-11-13T10:00:00.000+00:00"},
            "k2": {"tool": "access_revoke_all", "args": {"employee_id": "SYN-EMP-0161"},
                   "result": {"employee_id": "SYN-EMP-0161", "removed": ["SYN-GRP-A", "SYN-GRP-B"],
                              "kept_protected": ["SYN-GRP-HOLD"]}, "at": "2026-11-13T10:01:00.000+00:00"},
            "k3": {"tool": "reply_send", "args": {"recipient_employee_id": LEO, "body": "x"},
                   "result": {}, "at": "2026-11-13T10:02:00.000+00:00"},
        }
        plan = control.compensations(completed)
        self.assertEqual([p["of"] for p in plan], ["reply_send", "access_revoke_all", "access_revoke_all",
                                                   "access_grant"])
        self.assertIsNone(plan[0]["tool"])
        self.assertIn("cannot be recalled", plan[0]["why"])
        self.assertEqual({p["args"]["group_id"] for p in plan[1:3]}, {"SYN-GRP-A", "SYN-GRP-B"})
        self.assertTrue(all(p["tool"] == "access_grant" for p in plan[1:3]))   # the kept group is not regranted
        self.assertEqual(plan[3]["tool"], "access_revoke")

    def test_rollback_undoes_the_runs_writes_and_a_second_changes_nothing(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", "grant Leo")
        ctx_ = CallContext(SID, 1, "model", "nonstandard_request", request_id=REQ, run_key=cp.run_key)
        grant = self.registry.call("access_grant", {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ}, ctx_)
        self.assertFalse(grant.is_error, grant.content)
        self.assertTrue(self.holds(LEO, ALLOWED))
        commands.handle(SID, f"/stop {cp.run_no}")
        first = commands.handle(SID, f"/rollback {cp.run_no}")
        self.assertIn("access_grant → access_revoke", first.text)
        self.assertFalse(self.holds(LEO, ALLOWED))                       # the compensation ran past the stop
        self.assertTrue(control.stopped(cp.run_key))                      # and the run stays stopped
        second = commands.handle(SID, f"/rollback {cp.run_no}")
        self.assertIn("nothing changed", second.text)
        self.assertEqual(self.lines("end")[-1]["terminal_reason"], control.END_ROLLED_BACK)

    def test_rollback_without_a_journal_changes_nothing(self):
        reply = control.rollback(control.run_key(SID, 1))
        self.assertIn("nothing to roll back", reply.text)


class CommandsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def test_parse(self):
        self.assertEqual(commands.parse("/stop"), ("stop", None, True))
        self.assertEqual(commands.parse("  /STOP 3 "), ("stop", 3, True))
        self.assertEqual(commands.parse("/resume #2"), ("resume", 2, True))
        self.assertEqual(commands.parse("/rollback 12"), ("rollback", 12, True))
        self.assertEqual(commands.parse("/cancel"), ("cancel", None, False))
        self.assertEqual(commands.parse("/cancel run two"), ("cancel", None, False))
        for text in ("please stop", "stop", "/stopper", "/pending", "", "grant Leo /stop", None):
            self.assertIsNone(commands.parse(text), text)

    def test_a_message_that_is_no_command_goes_on(self):
        self.assertIsNone(commands.handle(SID, "Please handle the new access requests."))
        self.assertFalse(control.stopped(f"{SID}#*"))

    def test_stop_writes_the_flag_and_ends_the_turn_stopped(self):
        reply = commands.handle(SID, "/stop 2")
        self.assertIn("Stopped run 2", reply.text)
        self.assertTrue(control.stopped(f"{SID}#2"))
        trace = read_trace(runtime.trace(SID).path)
        self.assertEqual([x["command"] for x in trace if x["type"] == "operator_command"], ["stop"])
        self.assertEqual(trace[-1]["terminal_reason"], "stopped")
        self.assertFalse([x for x in trace if x["type"] == "model_call"])   # no model saw it

    def test_a_malformed_command_does_nothing_and_says_how(self):
        reply = commands.handle(SID, "/cancel")
        self.assertIn("Nothing was done", reply.text)
        self.assertIn("/cancel <run number>", reply.text)
        self.assertIsNone(control.state(f"{SID}#1"))

    def test_resume_and_cancel_go_to_their_run(self):
        commands.handle(SID, "/stop 1")
        self.assertIn("lifted", commands.handle(SID, "/resume 1").text)
        commands.handle(SID, "/stop 1")
        self.assertIn("cancelled", commands.handle(SID, "/cancel 1").text)
        self.assertEqual(control.state(f"{SID}#1"), control.CANCELLED)


# -- the five conditions (slide 40), from the environment ----------------------------------

import threading as _threading
import time as _time
import uuid as _uuid
from datetime import datetime as _dt

from agent import approvals, executor
from agent.bounds import Bound
from sandbox import db
from tests import fakes
from tests.support import fixed_run

T06 = {"id": "t06_stop_test", "operator": [fakes.T06_TEXT], "outside": [], "check": "checks/example.py",
       "input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0164", "set": {"end_date": "2026-10-28"}}}


def _at(line) -> float:
    return _dt.fromisoformat(line["at"]).timestamp()


def stopped_mid_run(test) -> dict:
    """A planned t06 run on SlowReviser in a second thread. The checkpoint after Victor's
    listing fails and the reviser's model call starts; the stop is sent from this thread
    while it runs. Returns what the conditions read: the trace, the run key, the writes."""
    run_id = f"stop-{_uuid.uuid4().hex[:8]}"
    out: dict = {"run_id": run_id}
    with fixed_run(T06, run_id, backend="tests.fakes:SlowReviser") as run:
        worker = _threading.Thread(target=lambda: out.setdefault(
            "result", executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())))
        worker.start()
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline and not any(
                x["type"] == "checkpoint" and not x["passed"] for x in read_trace(run.trace_path)):
            _time.sleep(0.01)
        out["stop"] = commands.handle(run_id, "/stop")
        worker.join(10)
        test.assertFalse(worker.is_alive())
        with db.connect() as conn:
            out["writes_at_stop"] = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
        out["lines"] = read_trace(run.trace_path)
        out["key"] = checkpoints.live(run_id)[0].run_key
        out["trace_path"] = run.trace_path
    return out


class StopConditionsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def test_watched_the_notice_comes_before_the_first_write_and_again_on_resume(self):
        run = stopped_mid_run(self)
        lines = run["lines"]
        notice = next(i for i, x in enumerate(lines) if x["type"] == "run_notice")
        first_write = next(i for i, x in enumerate(lines) if x["type"] == "tool_call" and x.get("key"))
        self.assertLess(notice, first_write)
        self.assertLessEqual(_at(lines[notice]), _at(lines[first_write]))
        self.assertIn("/stop 1", lines[notice - 1]["text"])                  # the console's progress line
        with fixed_run(T06, run["run_id"], backend="tests.fakes:SlowReviser", fresh=False):
            control.resume(run["key"])
        resumed = [x for x in read_trace(run["trace_path"]) if x["type"] == "run_notice" and x["resumed"]]
        self.assertEqual(len(resumed), 1)

    def test_fast_over_five_stops_the_next_call_is_refused(self):
        latencies = []
        for _ in range(5):
            run = stopped_mid_run(self)
            stop = next(x for x in run["lines"] if x["type"] == "stop_flag")
            refused = next(x for x in run["lines"] if x["type"] == "tool_call" and x.get("denied")
                           and _at(x) >= _at(stop))
            latencies.append(_at(refused) - _at(stop))
            self.assertEqual([x["terminal_reason"] for x in run["lines"] if x["type"] == "end"][-1],
                             executor.STOPPED)
        # the call in flight at the stop (the reviser's model call, SlowReviser.REVISER_S) finishes first
        self.assertTrue(all(0 <= s < fakes.SlowReviser.REVISER_S + 1.0 for s in latencies), latencies)

    def test_durable_the_stop_saves_the_run_and_the_resume_writes_nothing_twice(self):
        run = stopped_mid_run(self)
        saved = checkpoints.load(run["key"])
        last_call = [x for x in run["lines"] if x["type"] == "tool_call"][-1]
        self.assertEqual(saved.status, checkpoints.STOPPED)
        self.assertGreaterEqual(saved.updated_at, last_call["at"])           # saved after the last call
        with fixed_run(T06, run["run_id"], backend="tests.fakes:SlowReviser", fresh=False):
            control.resume(run["key"])
            fakes.approve_all()
        with db.connect() as conn:
            removals = conn.execute("SELECT employee_id, group_id, COUNT(*) AS n FROM memberships "
                                    "WHERE removed_at IS NOT NULL GROUP BY employee_id, group_id HAVING n > 1").fetchall()
        self.assertEqual(removals, [])
        for manager in (fakes.HANNAH, fakes.GRACE):
            sent = [m for m in channel.messages(f"SYN-MAIL-{manager}") if m.sender == channel.AGENT]
            self.assertEqual(len(sent), 1, manager)
        self.assertEqual(checkpoints.load(run["key"]).status, checkpoints.DONE)

    def test_safe_every_write_before_the_stop_is_in_the_runs_journal_with_its_key(self):
        run = stopped_mid_run(self)
        stop = next(x for x in run["lines"] if x["type"] == "stop_flag")
        written = [x["key"] for x in run["lines"] if x["type"] == "tool_call" and x.get("key")
                   and not x.get("denied") and _at(x) <= _at(stop)]
        self.assertTrue(written)                                             # Ana's removal ran before it
        journal = checkpoints.load(run["key"]).completed
        self.assertEqual([k for k in written if k not in journal], [])

    def test_outside_the_agent_nothing_is_written_from_the_stop_to_the_resume(self):
        run = stopped_mid_run(self)
        stop = next(i for i, x in enumerate(run["lines"]) if x["type"] == "stop_flag")
        after = [x for x in run["lines"][stop:] if x["type"] == "tool_call" and not x.get("denied")]
        self.assertEqual(after, [])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"],
                             run["writes_at_stop"])

    def test_outside_the_agent_a_stop_while_an_approval_waits_and_then_the_answer(self):
        run_id = f"stop-{_uuid.uuid4().hex[:8]}"
        with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold"):
            executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
            waiting = approvals.pending()[0]["id"]
            commands.handle(run_id, "/stop")
            answer = approvals.decide(waiting, True, "the answer lands after the stop")
        self.assertIn("withdrawn", answer.text)
        for manager in (fakes.HANNAH, fakes.GRACE):
            self.assertEqual([m for m in channel.messages(f"SYN-MAIL-{manager}") if m.sender == channel.AGENT], [])


class StopWhileAStrangerIsAwaitedTest(ScratchTest):
    """The brief's other wait: a stop sent from a second thread while a U4 run waits for the
    manager's reply, then the reply delivered (harness.turns.outside_reply). Nothing is written
    when it lands; a resume carries on from the saved state with the reply, once."""

    T08, THREAD, DEV, GITHUB_ENG = "tasks/t08_ask_manager.yaml", "SYN-MAIL-SYN-EMP-0060", "SYN-EMP-0052", "SYN-GRP-GITHUB-ENG"
    YES = "SYNTHETIC. YES. Dev is on my team now, so he can be in the GitHub engineers group."
    BACKEND = "tests.test_wait:AsksTheManagerThenGrants"

    def setUp(self):
        super().setUp()
        runtime.configure()

    def _writes(self):
        with db.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

    def _holds(self):
        return self.GITHUB_ENG in {m["group_id"] for m in access.memberships(self.DEV)}

    def test_a_stop_while_the_run_waits_for_a_stranger_and_then_the_reply_lands(self):
        from harness import turns
        rid = f"stop-wait-{_uuid.uuid4().hex[:8]}"
        with fixed_run(self.T08, rid, backend=self.BACKEND) as run:
            first = turns.operator_turn(rid, "Please handle the new access requests.")
            self.assertEqual(first.waiting_for, self.THREAD)
            key = f"{rid}#1"
            self.assertEqual(checkpoints.load(key).status, "waiting_reply")
            stopper = _threading.Thread(target=lambda: commands.handle(rid, "/stop 1"))
            stopper.start()
            stopper.join(5)
            before = self._writes()
            turns.outside_reply(self.THREAD, self.YES, sender="hannah.lee@syn-corp.example")
            lines = read_trace(run.trace_path)
            stop = next(i for i, x in enumerate(lines) if x["type"] == "stop_flag")
            after = [x for x in lines[stop:] if x["type"] == "tool_call" and not x.get("denied")]
            self.assertEqual(after, [], "no call ran after the stop, the reply's included")
            self.assertEqual(self._writes(), before, "no sandbox write when the reply lands")
            self.assertFalse(self._holds())
            self.assertEqual(approvals.pending(), [], "nothing is put up for approval either")
            refused = [x for x in lines[stop:] if x["type"] == "resume_refused"]
            self.assertEqual([x["event_kind"] for x in refused], ["reply"], "the reply did not resume it")
            self.assertTrue(control.stopped(key))

            control.resume(key)
            for _ in range(3):                       # the grant, then the reply to Arjun
                waiting = approvals.pending()
                if not waiting:
                    break
                turns.decide_approval(waiting[0]["id"], True, "the operator approves it")
        self.assertTrue(self._holds(), "the resume carried on with Hannah's YES")
        with db.connect() as conn:
            grants = conn.execute("SELECT COUNT(*) AS n FROM memberships WHERE employee_id = ? AND group_id = ?",
                                  (self.DEV, self.GITHUB_ENG)).fetchone()["n"]
        self.assertEqual(grants, 1, "granted once")
