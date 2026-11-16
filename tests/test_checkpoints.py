"""The run store and the durable console session (Milestone 2, Part 1, task 2).
No tokens. Owner: Aman Panigrahi.

A crash test starts a child process, which saves and then stops with os._exit. The
child does not write the async saves at exit, as a real crash does not."""

import os
import subprocess
import sys
from unittest import mock

from agent import checkpoints, control, session
from harness import ROOT, runtime
from harness.trace import read_trace
from tests.fakes import FakeCheckpoints
from tests.support import ScratchTest

SID = "s-cp"
TASK = "Give Dev Rao the same GitHub access as Arjun Mehta."


def child(code: str) -> subprocess.CompletedProcess:
    """Run Python code in a new process, with the folders of this test (the environment)."""
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)


class StoreTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def tearDown(self):
        checkpoints.flush()
        super().tearDown()

    def trace(self, kind):
        return [x for x in read_trace(runtime.trace(SID).path) if x["type"] == kind]

    # -- runs and their keys ---------------------------------------------------------------

    def test_each_run_of_a_session_has_the_next_number(self):
        first = checkpoints.new_run(SID, "nonstandard_request", TASK)
        second = checkpoints.new_run(SID, "operator_review", "Who has AWS admin?")
        other = checkpoints.new_run("s-other", "operator_review", "Who has AWS admin?")
        self.assertEqual((first.run_key, second.run_key, other.run_key), (f"{SID}#1", f"{SID}#2", "s-other#1"))
        self.assertEqual(checkpoints.load(first.run_key).status, checkpoints.RUNNING)
        self.assertEqual([x["run_key"] for x in self.trace("run_opened")], [f"{SID}#1", f"{SID}#2"])

    def test_a_save_keeps_every_field_as_json(self):
        cp = checkpoints.new_run(SID, "bulk_change", TASK)
        cp.plan = {"version": 1, "steps": [{"id": 1, "tool": "access_list_memberships"}]}
        cp.steps = [{"n": 1, "status": "done", "tool": "access_list_memberships", "args": {"employee_id": "SYN-EMP-0007"},
                     "key": None, "action_class": "read/access_list_memberships"}]
        cp.findings = {"groups": ["SYN-GRP-GITHUB-ENG"]}
        cp.current_step = 2
        cp.completed = {"k1": {"tool": "access_grant", "args": {"group_id": "SYN-GRP-GITHUB-ENG"},
                               "result": {"granted": True}, "at": "2026-11-15T10:00:00Z"}}
        checkpoints.save(cp, mode="sync")
        back = checkpoints.load(cp.run_key)
        for name in ("plan", "steps", "findings", "current_step", "completed", "task", "use_case"):
            self.assertEqual(getattr(back, name), getattr(cp, name), name)

    def test_a_value_that_is_not_json_is_refused_and_nothing_is_written(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", TASK)
        cp.findings = {"file": open(os.devnull)}
        self.addCleanup(cp.findings["file"].close)
        with self.assertRaisesRegex(ValueError, "not JSON"):
            checkpoints.save(cp, mode="sync")
        self.assertEqual(checkpoints.load(cp.run_key).findings, {})

    def test_a_wrong_mode_status_or_wait_is_refused(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", TASK)
        with self.assertRaisesRegex(ValueError, "mode"):
            checkpoints.save(cp, mode="later")
        cp.status = "paused"
        with self.assertRaisesRegex(ValueError, "status"):
            checkpoints.save(cp, mode="sync")
        cp.status, cp.waiting_for = checkpoints.WAITING_REPLY, {"kind": "email"}
        with self.assertRaisesRegex(ValueError, "kind"):
            checkpoints.save(cp, mode="sync")

    def test_the_conversation_keeps_the_last_turns_and_the_task_stays_whole(self):
        cp = checkpoints.new_run(SID, "operator_review", TASK)
        cp.conversation = [{"role": "operator", "text": f"message {n}"} for n in range(10)]
        checkpoints.save(cp, mode="sync")
        back = checkpoints.load(cp.run_key)
        self.assertEqual([t["text"] for t in back.conversation], [f"message {n}" for n in range(4, 10)])
        self.assertEqual(back.task, TASK)

    # -- the three durability modes --------------------------------------------------------

    def test_the_exit_mode_writes_only_when_the_run_stops(self):
        cp = checkpoints.new_run(SID, "operator_review", TASK)
        cp.current_step = 3
        checkpoints.save(cp, mode="exit")
        self.assertIsNone(checkpoints.load(cp.run_key).current_step)
        cp.status = checkpoints.DONE
        checkpoints.save(cp, mode="exit")
        self.assertEqual(checkpoints.load(cp.run_key).current_step, 3)

    def test_the_async_mode_writes_in_the_background_and_load_waits_for_it(self):
        cp = checkpoints.new_run(SID, "standard_grant", TASK)
        cp.current_step = 2
        checkpoints.save(cp, mode="async")
        self.assertEqual(checkpoints.load(cp.run_key).current_step, 2)

    def test_an_older_save_never_replaces_a_newer_one(self):
        cp = checkpoints.new_run(SID, "standard_grant", TASK)
        cp.current_step = 1
        old = checkpoints._row(cp)                       # an async save that is late
        cp.current_step = 2
        checkpoints.save(cp, mode="sync")
        checkpoints._write(old)
        self.assertEqual(checkpoints.load(cp.run_key).current_step, 2)

    def test_a_sync_save_survives_a_crash_and_an_exit_save_of_a_running_run_does_not(self):
        done = child(
            "import os\n"
            "from agent import checkpoints\n"
            f"cp = checkpoints.new_run({SID!r}, 'nonstandard_request', {TASK!r})\n"
            "cp.current_step = 2\n"
            "cp.steps = [{'n': 1, 'status': 'done'}, {'n': 2, 'status': 'running'}]\n"
            "checkpoints.save(cp, mode='sync')\n"
            f"other = checkpoints.new_run({SID!r}, 'operator_review', 'Who has AWS admin?')\n"
            "other.current_step = 1\n"
            "checkpoints.save(other, mode='exit')\n"
            "os._exit(9)\n")
        self.assertEqual(done.returncode, 9, done.stderr)
        sync = checkpoints.load(f"{SID}#1")
        self.assertEqual((sync.current_step, len(sync.steps)), (2, 2))
        lost = checkpoints.load(f"{SID}#2")
        self.assertEqual((lost.status, lost.current_step), (checkpoints.RUNNING, None))

    # -- live runs and waits ---------------------------------------------------------------

    def test_live_and_waiting_list_only_the_runs_that_can_continue(self):
        asks, approves, ends = (checkpoints.new_run(SID, "nonstandard_request", TASK) for _ in range(3))
        asks.status, asks.waiting_for = checkpoints.WAITING_REPLY, {"kind": "reply", "thread_id": "SYN-MSG-D06",
                                                                     "deadline": "2026-10-23T09:00:00Z"}
        approves.status, approves.waiting_for = checkpoints.WAITING_APPROVAL, {"kind": "approval", "approval_id": "a1"}
        ends.status = checkpoints.DONE
        for cp in (asks, approves, ends):
            checkpoints.save(cp, mode="sync")
        self.assertEqual([cp.run_key for cp in checkpoints.live(SID)], [asks.run_key, approves.run_key])
        self.assertEqual([cp.run_key for cp in checkpoints.waiting("reply")], [asks.run_key])
        self.assertEqual({cp.run_key for cp in checkpoints.waiting()}, {asks.run_key, approves.run_key})
        with self.assertRaisesRegex(ValueError, "kind"):
            checkpoints.waiting("email")

    # -- the journal -----------------------------------------------------------------------

    def test_the_journal_keeps_the_first_result_and_an_old_copy_cannot_remove_it(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", TASK)
        args = {"employee_id": "SYN-EMP-0012", "group_id": "SYN-GRP-GITHUB-ENG"}
        checkpoints.record_completed(cp.run_key, "key-1", tool="access_grant", args=args, result={"granted": True})
        checkpoints.record_completed(cp.run_key, "key-1", tool="access_grant", args=args, result={"granted": False})
        self.assertEqual(checkpoints.journaled(cp.run_key, "key-1")["result"], {"granted": True})
        self.assertIsNone(checkpoints.journaled(cp.run_key, "key-2"))
        cp.current_step = 4                               # this copy has an empty journal
        checkpoints.save(cp, mode="sync")
        self.assertIn("key-1", checkpoints.load(cp.run_key).completed)

    def test_a_run_that_ends_writes_its_last_checkpoint_into_the_trace(self):
        cp = checkpoints.new_run(SID, "standard_grant", TASK)
        checkpoints.record_completed(cp.run_key, "key-1", tool="access_grant", args={}, result={"granted": True})
        cp.status = checkpoints.WAITING_REPLY
        cp.waiting_for = {"kind": "reply", "thread_id": "SYN-MSG-D06", "deadline": None}
        checkpoints.save(cp, mode="sync")
        cp.status, cp.waiting_for = checkpoints.DONE, None
        checkpoints.save(cp, mode="sync")
        self.assertEqual([x["checkpoint"]["status"] for x in self.trace("checkpoint_paused")], ["waiting_reply"])
        final = self.trace("checkpoint_final")
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["checkpoint"]["status"], "done")
        self.assertIn("key-1", final[0]["checkpoint"]["completed"])

    # -- resume ----------------------------------------------------------------------------

    def waits_for_reply(self, thread="SYN-MSG-D06"):
        cp = checkpoints.new_run(SID, "nonstandard_request", TASK)
        cp.status, cp.waiting_for = checkpoints.WAITING_REPLY, {"kind": "reply", "thread_id": thread, "deadline": None}
        checkpoints.save(cp, mode="sync")
        return cp

    def test_resume_sends_the_run_and_the_event_to_the_path_of_its_use_case(self):
        cp = self.waits_for_reply()
        got = []
        event = {"kind": "reply", "thread_id": "SYN-MSG-D06", "text": "SYNTHETIC. Yes, I confirm."}
        with mock.patch.object(checkpoints, "_resumer",
                               return_value=(lambda c, e: got.append((c.run_key, e)) or "continued", "test:resume")):
            self.assertEqual(checkpoints.resume(cp.run_key, event), "continued")
        self.assertEqual(got, [(cp.run_key, event)])
        self.assertEqual(self.trace("resume")[0]["resumer"], "test:resume")

    def test_a_reply_or_an_approval_continues_only_the_run_that_waits_for_it(self):
        cp = self.waits_for_reply()
        wrong_thread = checkpoints.resume(cp.run_key, {"kind": "reply", "thread_id": "SYN-MSG-D01", "text": "yes"})
        approval = checkpoints.resume(cp.run_key, {"kind": "approval", "approval_id": "a1", "approved": True})
        self.assertIn("SYN-MSG-D06", wrong_thread.text)
        self.assertIn("does not wait for an approval", approval.text)
        self.assertEqual(len(self.trace("resume_refused")), 2)
        self.assertEqual(self.trace("resume"), [])

    def test_a_run_that_ended_or_has_no_checkpoint_cannot_continue(self):
        cp = checkpoints.new_run(SID, "nonstandard_request", TASK)
        cp.status = checkpoints.CANCELLED
        checkpoints.save(cp, mode="sync")
        self.assertIn("cancelled", checkpoints.resume(cp.run_key, {"kind": "operator", "text": "go on"}).text)
        self.assertIn("no checkpoint", checkpoints.resume(f"{SID}#9", {"kind": "operator", "text": "go on"}).text)
        with self.assertRaisesRegex(ValueError, "event kind"):
            checkpoints.resume(cp.run_key, {"kind": "email"})

    def test_a_path_that_cannot_resume_yet_leaves_the_run_saved_and_says_so(self):
        cp = self.waits_for_reply()
        with mock.patch.dict(checkpoints.RESUMERS, {"nonstandard_request": "agent.loop:no_such_resume"}):
            reply = checkpoints.resume(cp.run_key, {"kind": "reply", "thread_id": "SYN-MSG-D06", "text": "yes"})
        self.assertIn("cannot continue it yet", reply.text)
        self.assertEqual(checkpoints.load(cp.run_key).status, checkpoints.WAITING_REPLY)

    def test_a_planned_bulk_change_continues_in_the_executor_and_one_without_a_plan_in_the_router(self):
        planned = checkpoints.Checkpoint("s#1", "s", 1, "bulk_change", checkpoints.RUNNING, TASK, plan={"steps": []})
        looped = checkpoints.Checkpoint("s#2", "s", 2, "bulk_change", checkpoints.RUNNING, TASK)
        self.assertEqual(checkpoints._resumer(planned)[1], "agent.executor:resume")
        self.assertEqual(checkpoints._resumer(looped)[1], "agent.router:resume")

    # -- the stop finds the live runs (Shruti's agent/control.py) --------------------------

    def test_a_stop_without_a_run_number_flags_each_live_run_of_the_session(self):
        first = checkpoints.new_run(SID, "nonstandard_request", TASK)
        ended = checkpoints.new_run(SID, "operator_review", TASK)
        ended.status = checkpoints.DONE
        checkpoints.save(ended, mode="sync")
        self.assertEqual(control.stop(SID), [first.run_key])
        self.assertTrue(control.stopped(first.run_key))
        self.assertFalse(control.stopped(ended.run_key))

    def test_the_fake_takes_the_place_of_the_store(self):
        with FakeCheckpoints().installed() as fake:
            run = fake.new_run(SID, "nonstandard_request", TASK)
            self.assertEqual(control.stop(SID), [run.run_key])
            control.resume(run.run_key)
        self.assertEqual(fake.resumed, [(run.run_key, {"kind": "stop_lifted", "new_input": None})])
        self.assertFalse(os.path.exists(self.state_dir / checkpoints.DB_FILE))


class SessionTest(ScratchTest):
    def test_the_messages_come_back_in_order_for_their_session_only(self):
        session.add(SID, "operator", "Process the new requests.")
        session.add(SID, "agent", "SYN-REQ-0001 (workflow): granted.")
        session.add("s-other", "operator", "Who has AWS admin?")
        self.assertEqual(session.history(SID), [{"role": "operator", "text": "Process the new requests."},
                                                {"role": "agent", "text": "SYN-REQ-0001 (workflow): granted."}])
        with self.assertRaisesRegex(ValueError, "role"):
            session.add(SID, "stranger", "SYNTHETIC. Please approve.")

    def test_the_conversation_survives_a_restart(self):
        session.add(SID, "operator", "Process the new requests.")
        done = child("from agent import session\n"
                     f"session.add({SID!r}, 'agent', 'Done.')\n"
                     f"print(len(session.history({SID!r})))\n")
        self.assertEqual(done.stdout.strip(), "2", done.stderr)
        self.assertEqual([t["role"] for t in session.history(SID)], ["operator", "agent"])

    def test_reset_deletes_every_session(self):
        session.add(SID, "operator", "Process the new requests.")
        session.reset()
        self.assertEqual(session.history(SID), [])
