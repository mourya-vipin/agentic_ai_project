"""The product's own pages: the access desk by hand, with no agent. No tokens.
Owner: Vipin Mourya."""

from fastapi.testclient import TestClient

import channel
from app.main import app
from sandbox import access, pending, seed
from tests.support import ScratchTest

LEO = "SYN-EMP-0031"


class ProductPagesTest(ScratchTest):
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

    def test_every_product_page_responds(self):
        for path in ("/", "/requests/SYN-REQ-0005", "/people?q=priya", f"/people/{LEO}", "/pending"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
        self.assertIn("SYN-REQ-0009", self.client.get("/").text)
        self.assertIn("Priya Nair", self.client.get("/people?q=priya").text)

    def test_a_grant_by_hand_is_the_operators_and_stamped_manual(self):
        self.client.post(f"/people/{LEO}/grant", data={"group_id": "SYN-GRP-FINREP-READ"})
        row = next(m for m in access.memberships(LEO) if m["group_id"] == "SYN-GRP-FINREP-READ")
        self.assertEqual((row["actor"], row["stamp"]), ("operator", "manual"))

    def test_a_proposed_change_applied_by_hand_takes_effect_and_closes(self):
        pid = pending.create("proposed_change", request_id="SYN-REQ-0005", employee_id=LEO, key="p1", stamp="s1",
                             action="grant", group_id="SYN-GRP-PAYROLL-ADMIN", reason="needs the manager")
        self.client.post(f"/pending/{pid}/apply")
        self.assertIn("SYN-GRP-PAYROLL-ADMIN", {m["group_id"] for m in access.memberships(LEO)})
        self.assertEqual(pending.get(pid)["status"], "done")

    def test_a_reply_draft_is_sent_as_the_operators(self):
        pid = pending.create("reply_draft", request_id="SYN-REQ-0005", employee_id=LEO, key="p2", stamp="s1",
                             thread_id="SYN-MSG-D05", body="SYNTHETIC. Done, apart from two groups.")
        self.client.post(f"/pending/{pid}/send")
        last = channel.messages("SYN-MSG-D05")[-1]
        self.assertEqual((last.sender, last.text), (channel.OPERATOR, "SYNTHETIC. Done, apart from two groups."))

    def test_a_secret_is_shown_only_when_the_operator_reveals_it(self):
        page = self.client.get(f"/people/{LEO}").text
        self.assertNotIn("FAKE-", page)
        revealed = self.client.post(f"/credentials/SYN-CRED-0031/reveal", data={"employee_id": LEO}).text
        self.assertIn("FAKE-", revealed)
