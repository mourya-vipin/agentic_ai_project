"""The sandbox: its data is synthetic, its clock fixed, and its writes keyed. No tokens.
Owner: Vipin Mourya."""

import channel
from sandbox import access, catalog, clock, credentials, db, directory, notices, pending, requests, seed
from tests.support import ScratchTest

LEO, PRIYA = "SYN-EMP-0031", "SYN-EMP-0012"


class SandboxTest(ScratchTest):
    def setUp(self):
        super().setUp()
        self.reseed(None)

    def reseed(self, task):
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(task)

    def test_every_record_is_synthetic_and_every_secret_fake(self):
        with db.connect() as conn:
            for table in db.TABLES:
                self.assertEqual(conn.execute(f"SELECT count(*) AS n FROM {table} WHERE synthetic <> 1").fetchone()["n"],
                                 0, table)
            secrets = [r["secret"] for r in conn.execute("SELECT secret FROM credentials").fetchall()]
            ids = [r["id"] for r in conn.execute("SELECT id FROM employees").fetchall()]
        self.assertTrue(secrets and all(s.startswith("FAKE-") for s in secrets))
        self.assertEqual(len(ids), 250)
        self.assertTrue(all(i.startswith("SYN-EMP-") for i in ids))

    def test_the_clock_is_fixed_by_the_seed(self):
        self.assertEqual(clock.now(), "2026-10-21T09:00:00Z")
        self.assertEqual(clock.today(), "2026-10-21")

    def test_the_demo_day_has_nine_requests_on_the_channel(self):
        found = requests.all_requests()
        self.assertEqual(len(found), 9)
        self.assertEqual(found[0]["id"], "SYN-REQ-0001")
        self.assertEqual({r["source"] for r in found}, {"employee", "hr_feed"})
        self.assertTrue(all(channel.messages(r["thread_id"]) for r in found))

    def test_the_same_access_case_splits_three_allowed_and_two_for_the_manager(self):
        mine = {m["group_id"] for m in access.memberships(LEO)}
        decisions = {g["group_id"]: catalog.policy_for(LEO, g["group_id"])["decision"]
                     for g in access.memberships(PRIYA) if g["group_id"] not in mine}
        self.assertEqual(sorted(decisions.values()), ["allowed"] * 3 + ["needs_manager"] * 2)

    def test_a_grant_repeated_under_one_key_leaves_one_membership(self):
        first = access.grant(LEO, "SYN-GRP-FINREP-READ", request_id="SYN-REQ-0005", key="k1", stamp="r1", actor="agent")
        again = access.grant(LEO, "SYN-GRP-FINREP-READ", request_id="SYN-REQ-0005", key="k1", stamp="r1", actor="agent")
        self.assertEqual(first, again)
        self.assertEqual(first["status"], "granted")
        rows = [m for m in access.memberships(LEO) if m["group_id"] == "SYN-GRP-FINREP-READ"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(access.by_request("SYN-REQ-0005")[0]["change"], "granted")

    def test_a_grant_of_a_group_already_held_changes_nothing(self):
        result = access.grant(LEO, "SYN-GRP-SLACK-MEMBER", request_id=None, key=None, stamp="manual", actor="operator")
        self.assertEqual(result["status"], "already_member")

    def test_a_protected_group_is_never_removed(self):
        result = access.revoke("SYN-EMP-0162", "SYN-GRP-LEGAL-HOLD", request_id=None, key="k2", stamp="r1",
                               actor="agent", reason="contract ended")
        self.assertEqual(result["status"], "protected")
        self.assertIn("SYN-GRP-LEGAL-HOLD", {m["group_id"] for m in access.memberships("SYN-EMP-0162")})

    def test_a_removal_is_recorded_under_its_request(self):
        access.revoke("SYN-EMP-0161", "SYN-GRP-JIRA-WRITE", request_id="SYN-REQ-0099", key="k3", stamp="r1",
                      actor="workflow", reason="contract ended")
        self.assertNotIn("SYN-GRP-JIRA-WRITE", {m["group_id"] for m in access.memberships("SYN-EMP-0161")})
        self.assertEqual([m["change"] for m in access.by_request("SYN-REQ-0099")], ["removed"])

    def test_a_fault_fails_the_call_exactly_its_times_then_clears(self):
        access.set_fault("access_grant", 2)
        for _ in range(2):
            with self.assertRaises(access.TransientError):
                access.grant(LEO, "SYN-GRP-FINREP-READ", request_id=None, key="k4", stamp="r1", actor="agent")
        self.assertEqual(access.grant(LEO, "SYN-GRP-FINREP-READ", request_id=None, key="k4", stamp="r1",
                                      actor="agent")["status"], "granted")

    def test_a_notice_repeated_under_one_key_is_one_message(self):
        facts = {"name": "Leo Martins", "request_id": "SYN-REQ-0005"}
        first = notices.send_template("SYN-MSG-D05", "received", facts, key="n1", stamp="r1")
        again = notices.send_template("SYN-MSG-D05", "received", facts, key="n1", stamp="r1")
        self.assertEqual((first["status"], again["status"]), ("sent", "already_sent"))
        sent = [m for m in channel.messages("SYN-MSG-D05") if m.sender == channel.AGENT]
        self.assertEqual(len(sent), 1)
        self.assertNotIn("FAKE-", sent[0].text)

    def test_a_notice_missing_a_fact_says_which(self):
        with self.assertRaisesRegex(ValueError, "group_name"):
            notices.send_template("SYN-MSG-D01", "granted", {"name": "Arjun", "request_id": "SYN-REQ-0001",
                                                             "group_id": "SYN-GRP-JIRA-WRITE"}, key="n2", stamp="r1")

    def test_a_pending_record_repeated_under_one_key_is_one_record(self):
        args = dict(request_id="SYN-REQ-0005", employee_id=LEO, key="p1", stamp="r1", action="grant",
                    group_id="SYN-GRP-PAYROLL-ADMIN", reason="high sensitivity")
        self.assertEqual(pending.create("proposed_change", **args), pending.create("proposed_change", **args))
        self.assertEqual(len(pending.by_request("SYN-REQ-0005")), 1)

    def test_a_reply_draft_sent_by_hand_is_the_operators(self):
        pid = pending.create("reply_draft", request_id="SYN-REQ-0005", employee_id=LEO, key="p2", stamp="r1",
                             thread_id="SYN-MSG-D05", body="SYNTHETIC. Your request is partly done.")
        notices.send_as_operator(pid)
        self.assertEqual(channel.messages("SYN-MSG-D05")[-1].sender, channel.OPERATOR)
        self.assertEqual(pending.get(pid)["status"], "done")

    def test_an_issued_credential_returns_its_id_never_its_secret(self):
        cid = credentials.issue("SYN-EMP-0215", "SYN-SYS-SSO", key="c1", stamp="r1")
        self.assertTrue(cid.startswith("SYN-CRED-"))
        self.assertEqual(credentials.issue("SYN-EMP-0215", "SYN-SYS-SSO", key="c1", stamp="r1"), cid)
        self.assertTrue(all("secret" not in c for c in credentials.for_employee("SYN-EMP-0215")))
        self.assertTrue(credentials.reveal(cid).startswith("FAKE-"))

    def test_a_task_seeds_its_inputs_facts_and_faults(self):
        self.reseed({"inputs": [
            {"kind": "fact", "table": "employees", "id": "SYN-EMP-0150", "set": {"end_date": "2026-10-20"}},
            {"kind": "request", "thread": "SYN-HR-T03", "from": "hr-system@syn-corp.example",
             "text": "SYNTHETIC. EVENT: contract_ended; EMPLOYEE: SYN-EMP-0150; END: 2026-10-20"},
            {"thread": "SYN-MSG-T01", "from": "arjun.mehta@syn-corp.example", "text": "SYNTHETIC. Jira write, please."},
            {"kind": "fault", "tool": "access_revoke", "times": 1}]})
        found = requests.all_requests()
        self.assertEqual([(r["id"], r["source"], r["requester_id"]) for r in found],
                         [("SYN-REQ-0001", "hr_feed", None), ("SYN-REQ-0002", "employee", "SYN-EMP-0007")])
        self.assertEqual(directory.get_employee("SYN-EMP-0150")["end_date"], "2026-10-20")
        with self.assertRaises(access.TransientError):
            access.revoke("SYN-EMP-0150", "SYN-GRP-JIRA-WRITE", request_id=None, key=None, stamp="r1",
                          actor="workflow", reason="x")

    def test_without_the_task_fact_only_three_contracts_have_ended(self):
        self.reseed({"input": {"thread": "SYN-MSG-X", "from": "x@syn-corp.example", "text": "SYNTHETIC. Hello."}})
        self.assertEqual([e["id"] for e in directory.contractors_ended(clock.today())],
                         ["SYN-EMP-0161", "SYN-EMP-0162", "SYN-EMP-0163"])

    def test_a_fact_that_names_an_unknown_column_is_refused(self):
        with self.assertRaisesRegex(ValueError, "may set"):
            self.reseed({"input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0150",
                                   "set": {"salary": 1}}})
