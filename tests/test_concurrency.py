"""Two actions at the same moment, and forms the sandbox refuses: a double click, two
console sessions, a bad value. Found by sending them to the running app (2026-10-30).
No tokens. Owner: Vipin Mourya."""

import threading

from fastapi.testclient import TestClient

import channel
from agent import router
from app.main import app
from sandbox import access, db, pending, requests, seed
from tests.support import ScratchTest

LEO, OMAR = "SYN-EMP-0031", "SYN-EMP-0070"


def at_once(*calls):
    """Run the calls in threads released together; their exceptions, by type name."""
    start, errors = threading.Barrier(len(calls)), []

    def run(call):
        start.wait()
        try:
            call()
        except Exception as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class ConcurrencyTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def active(self, employee_id, group_id):
        with db.connect() as conn:
            return conn.execute("SELECT count(*) AS n FROM memberships WHERE employee_id = ? AND group_id = ? "
                                "AND removed_at IS NULL", (employee_id, group_id)).fetchone()["n"]

    def audits(self, action):
        with db.connect() as conn:
            return conn.execute("SELECT count(*) AS n FROM audit_log WHERE action = ?", (action,)).fetchone()["n"]

    def test_two_grants_at_once_make_one_membership(self):
        # by hand (no key) and from two sessions (two keys): each made a second row, 10 of 10 times
        for keys in ((None, None), ("k-one", "k-two")):
            access.revoke(OMAR, "SYN-GRP-DWH-READ", request_id=None, key=None, stamp="t", actor="operator",
                          reason="reset")
            self.assertEqual(at_once(*[lambda k=k: access.grant(OMAR, "SYN-GRP-DWH-READ", request_id=None, key=k,
                                                                 stamp="t", actor="operator") for k in keys]), [])
            self.assertEqual(self.active(OMAR, "SYN-GRP-DWH-READ"), 1, keys)

    def test_one_removal_leaves_no_access_after_a_double_grant(self):
        # the duplicate row survived a removal, so the person kept the group
        at_once(*[lambda: access.grant(OMAR, "SYN-GRP-AWSPROD-ADMIN", request_id=None, key=None, stamp="t",
                                       actor="operator")] * 2)
        self.client.post(f"/people/{OMAR}/revoke", data={"group_id": "SYN-GRP-AWSPROD-ADMIN"})
        self.assertEqual(self.active(OMAR, "SYN-GRP-AWSPROD-ADMIN"), 0)

    def test_two_removals_at_once_record_one(self):
        access.grant(OMAR, "SYN-GRP-DWH-READ", request_id=None, key=None, stamp="t", actor="operator")
        before = self.audits("revoke")
        at_once(*[lambda: access.revoke(OMAR, "SYN-GRP-DWH-READ", request_id=None, key=None, stamp="t",
                                        actor="operator", reason="twice")] * 2)
        self.assertEqual(self.audits("revoke") - before, 1)

    def test_a_proposal_applied_twice_grants_once(self):
        pid = pending.create("proposed_change", request_id="SYN-REQ-0007", employee_id=OMAR, key="p1", stamp="s",
                             action="grant", group_id="SYN-GRP-AWSPROD-ADMIN", reason="race")
        first, second = (self.client.post(f"/pending/{pid}/apply", follow_redirects=False) for _ in range(2))
        self.assertEqual((first.status_code, second.status_code), (303, 404))
        self.assertEqual(self.active(OMAR, "SYN-GRP-AWSPROD-ADMIN"), 1)

    def test_a_failed_apply_waits_on_pending_again(self):
        pid = pending.create("proposed_change", request_id="SYN-REQ-0007", employee_id=OMAR, key="p2", stamp="s",
                             action="grant", group_id="SYN-GRP-AWSPROD-ADMIN", reason="flaky")
        access.set_fault("access_grant", 1)
        with self.assertRaises(access.TransientError):
            self.client.post(f"/pending/{pid}/apply")
        self.assertEqual(pending.get(pid)["status"], "open")

    def test_a_draft_sent_twice_goes_out_once(self):
        pid = pending.create("reply_draft", request_id="SYN-REQ-0005", employee_id=LEO, key="p3", stamp="s",
                             thread_id="SYN-MSG-D05", body="SYNTHETIC. Sent once.")
        self.client.post(f"/pending/{pid}/send")
        self.assertEqual(self.client.post(f"/pending/{pid}/send").status_code, 404)
        self.assertEqual([m.text for m in channel.messages("SYN-MSG-D05")].count("SYNTHETIC. Sent once."), 1)

    def test_a_refused_form_says_why_and_changes_nothing(self):
        before = self.audits("grant") + self.audits("revoke")
        cases = [(f"/requests/SYN-REQ-0001/status", {"status": "hacked"}, 400),
                 ("/requests/SYN-REQ-9999/status", {"status": "done"}, 404),
                 (f"/people/{LEO}/grant", {"group_id": "SYN-GRP-NOPE"}, 404),
                 ("/people/SYN-EMP-NOPE/grant", {"group_id": "SYN-GRP-JIRA-READ"}, 404),
                 ("/credentials/SYN-CRED-NOPE/reveal", {"employee_id": LEO}, 404),
                 ("/pending/SYN-PEND-NOPE/send", {}, 404),
                 ("/pending/SYN-PEND-NOPE/close", {}, 404)]
        for path, data, code in cases:
            response = self.client.post(path, data=data)
            self.assertEqual(response.status_code, code, path)
            self.assertTrue(response.json()["detail"], path)
        self.assertEqual(requests.get("SYN-REQ-0001")["status"], "new")
        self.assertEqual(self.audits("grant") + self.audits("revoke"), before)

    def test_a_secret_shows_only_on_its_holders_page(self):
        other = self.client.post("/credentials/SYN-CRED-0031/reveal", data={"employee_id": OMAR})
        self.assertEqual(other.status_code, 404)
        self.assertNotIn("FAKE-", other.text)
        forged = self.client.get(f"/people/{LEO}?revealed=SYN-CRED-0031&secret=FAKE-forged").text
        self.assertNotIn("FAKE-forged", forged)

    def test_a_request_is_claimed_by_one_session_at_a_time(self):
        self.assertTrue(requests.claim("SYN-REQ-0001", "s-one"))
        self.assertTrue(requests.claim("SYN-REQ-0001", "s-one"))
        self.assertFalse(requests.claim("SYN-REQ-0001", "s-two"))
        requests.release("SYN-REQ-0001", "s-one")
        self.assertTrue(requests.claim("SYN-REQ-0001", "s-two"))

    def test_a_request_another_session_holds_is_left_to_it(self):
        # two sessions handling the same requests sent every notice twice and issued two credentials
        requests.claim("SYN-REQ-0003", "s-other")
        reply = router.process_requests("s-mine", ["SYN-REQ-0003"])
        self.assertIn("another console session is working on it", reply.text)
        self.assertEqual(requests.get("SYN-REQ-0003")["status"], "new")
        self.assertNotIn(channel.AGENT, {m.sender for m in channel.messages(requests.get("SYN-REQ-0003")["thread_id"])})
