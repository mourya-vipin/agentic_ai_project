"""The registry's journal of a run's writes (agent/checkpoints.py), and the sandbox's reset of
the agent's stores: every successful write with a run key is journaled once, with the
arguments that ran and its result; nothing else is; a journal that cannot be written never
fails the write; and `make app` or run.py's reset leaves no saved run, conversation, approval
or stop behind. Through the registry and the MCP server; no tokens. Owner: Vipin Mourya."""

from unittest import mock

import channel
from agent import approvals, checkpoints, control, session
from agent.registry import CallContext, Registry
from harness import runtime
from harness.paths import state_dir
from harness.trace import read_trace
from sandbox import access, seed
from tests.support import ScratchTest

LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"
ALLOWED = "SYN-GRP-FINREP-READ"             # the policy allows it for Leo: it runs unasked
GRANT = {"employee_id": LEO, "group_id": ALLOWED, "request_id": REQ}
READ = {"employee_id": LEO, "email": None, "name": None}


def ctx(run_key="s-jr#1"):
    return CallContext("s-jr", 1, "model", "nonstandard_request", request_id=REQ, run_key=run_key)


def journal(run_key="s-jr#1") -> list[dict]:
    checkpoints.flush()
    with checkpoints._connect() as conn:
        return [dict(r) for r in conn.execute("SELECT key, tool, args, result FROM journal WHERE run_key = ?",
                                              (run_key,)).fetchall()]


class JournalTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace("s-jr").path) if x["type"] == kind]

    def test_a_successful_write_is_journaled_with_its_key_arguments_and_result(self):
        result = self.registry.call("access_grant", GRANT, ctx())
        self.assertFalse(result.is_error, result.content)
        entry = checkpoints.journaled("s-jr#1", result.key)
        self.assertEqual(entry["tool"], "access_grant")
        self.assertEqual(entry["args"]["group_id"], ALLOWED)
        self.assertEqual(entry["result"], result.data)
        self.assertEqual(len(journal()), 1)

    def test_the_same_write_again_keeps_one_entry_and_one_effect(self):
        first = self.registry.call("access_grant", GRANT, ctx())
        again = self.registry.call("access_grant", GRANT, ctx())
        self.assertEqual(first.key, again.key)
        self.assertEqual(len(journal()), 1)
        self.assertEqual([m["group_id"] for m in access.memberships(LEO)].count(ALLOWED), 1)

    def test_a_read_a_rejected_write_and_a_denied_write_are_not_journaled(self):
        self.registry.call("directory_get_employee", READ, ctx())
        rejected = self.registry.call("access_grant", {"employee_id": LEO}, ctx())                 # no group
        denied = self.registry.call("access_revoke", {"employee_id": "SYN-EMP-0162", "group_id": "SYN-GRP-LEGAL-HOLD",
                                                      "reason": "contract ended"}, ctx())          # prohibited
        self.assertTrue(rejected.is_error)
        self.assertTrue(denied.is_error)
        self.assertEqual([x["tool"] for x in self.lines("hook_denied")], ["access_revoke"])
        self.assertEqual(journal(), [])

    def test_a_write_with_no_run_key_has_no_run_to_journal_in(self):
        result = self.registry.call("access_grant", GRANT, ctx(run_key=None))
        self.assertFalse(result.is_error, result.content)
        checkpoints.flush()
        with checkpoints._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM journal").fetchone()["n"], 0)

    def test_a_journal_that_cannot_be_written_does_not_fail_the_write_and_the_trace_says_so(self):
        with mock.patch.object(checkpoints, "record_completed", side_effect=OSError("disk full")):
            result = self.registry.call("access_grant", GRANT, ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertIn(ALLOWED, [m["group_id"] for m in access.memberships(LEO)])
        missing = self.lines("journal_error")
        self.assertEqual(len(missing), 1)
        self.assertEqual((missing[0]["tool"], missing[0]["key"]), ("access_grant", result.key))

    def test_a_rollback_of_a_stopped_run_undoes_its_journaled_write_once(self):
        cp = checkpoints.new_run("s-jr", "nonstandard_request", "grant Leo FINREP-READ")
        checkpoints.save(cp, mode="sync")
        result = self.registry.call("access_grant", GRANT, ctx(run_key=cp.run_key))
        self.assertFalse(result.is_error, result.content)
        control.stop("s-jr", cp.run_no)
        refused = self.registry.call("directory_get_employee", READ, ctx(run_key=cp.run_key))
        self.assertIn("stopped by the operator", refused.content)          # the run itself stays stopped
        with mock.patch("agent.registry.get_registry", return_value=self.registry):
            first = control.rollback(cp.run_key)
            again = control.rollback(cp.run_key)
        self.assertIn("access_revoke SYN-GRP-FINREP-READ: done", first.text)
        self.assertNotIn(ALLOWED, [m["group_id"] for m in access.memberships(LEO)])
        self.assertEqual([e["tool"] for e in journal(cp.run_key)], ["access_grant"])   # the undo is not a step
        self.assertIn("nothing changed", again.text)                                   # nothing new to undo
        self.assertNotIn(ALLOWED, [m["group_id"] for m in access.memberships(LEO)])
        self.assertTrue(control.stopped(cp.run_key))


class ResetTest(ScratchTest):
    def test_the_sandbox_reset_empties_the_runs_sessions_approvals_and_stops(self):
        runtime.configure()
        cp = checkpoints.new_run("s-jr", "nonstandard_request", "grant Leo FINREP-READ")
        checkpoints.save(cp, mode="sync")
        checkpoints.record_completed(cp.run_key, "k1", tool="access_grant", args=GRANT, result={"granted": True})
        session.add("s-jr", "operator", "grant Leo FINREP-READ")
        with approvals._connect():
            pass
        with control._connect():
            pass
        files = [checkpoints.DB_FILE, session.DB_FILE, approvals.DB_FILE, control.DB_FILE]
        self.assertTrue(all((state_dir() / f).exists() for f in files))
        seed.reset()
        self.assertEqual([f for f in files if (state_dir() / f).exists()], [])
        self.assertIsNone(checkpoints.load(cp.run_key))
        self.assertEqual(session.history("s-jr"), [])
