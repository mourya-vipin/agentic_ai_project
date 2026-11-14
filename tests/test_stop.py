"""The stop and its commands (Part 2, task 6): the flag outside the agent that the hook reads
first, cancel, resume and the rollback plan, and the commands read before any model.
No tokens. Owner: Shruti Tewaney."""

import importlib
import threading

import channel
from agent import commands, control
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
