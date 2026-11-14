"""The app's guard against other web pages (app/security.py): a POST from another site is refused
before it changes anything, on the product's pages and the operator's alike; the product's forms
carry a token another site cannot read; a request for another host is refused. No tokens.
Owner: Vipin Mourya."""

import channel
from app import security
from app.main import app
from sandbox import access, seed
from tests.support import ScratchTest
from tests.support_app import FormClient

LEO, GROUP = "SYN-EMP-0031", "SYN-GRP-FINREP-READ"
EVIL = "http://evil.example"


class AppSecurityTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        self.client = FormClient(app)

    def holds(self):
        return GROUP in {m["group_id"] for m in access.memberships(LEO)}

    def grant(self, **headers):
        return self.client.post(f"/people/{LEO}/grant", data={"group_id": GROUP}, headers=headers,
                                follow_redirects=False)

    def test_a_post_from_another_site_is_refused_and_changes_nothing(self):
        for headers in ({"origin": EVIL}, {"referer": f"{EVIL}/page"}, {"origin": "null"},
                        {"sec-fetch-site": "cross-site"}):
            with self.subTest(headers=headers):
                response = self.grant(**headers)
                self.assertEqual(response.status_code, 403)
                self.assertIn("did not come from the app", response.text)
        self.assertFalse(self.holds())

    def test_the_operators_pages_are_guarded_too(self):
        for path, data in (("/console/new", {}), ("/approvals", {"approval_id": "A-1", "decision": "approve"}),
                           ("/threads/reply", {"thread_id": "SYN-MSG-D05", "text": "hi"})):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=data, headers={"origin": EVIL}).status_code, 403)

    def test_the_apps_own_page_and_a_client_with_no_origin_pass(self):
        self.assertEqual(self.grant(origin="http://testserver").status_code, 303)
        self.assertTrue(self.holds())
        self.assertEqual(self.client.post("/console/new", follow_redirects=False).status_code, 303)   # curl, run.py

    def test_a_request_for_another_host_is_refused(self):
        response = self.client.get("/", headers={"host": "evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(security.local_host("localhost:8000"))
        self.assertTrue(security.local_host("127.0.0.1:8000"))
        self.assertTrue(security.local_host("[::1]:8000"))
        self.assertFalse(security.local_host("evil.example:8000"))

    def test_a_product_form_without_this_processs_token_is_refused(self):
        from fastapi.testclient import TestClient
        plain = TestClient(app)
        for data in ({"group_id": GROUP}, {"group_id": GROUP, "csrf": "an-old-token"}):
            with self.subTest(data=data):
                response = plain.post(f"/people/{LEO}/grant", data=data)
                self.assertEqual(response.status_code, 403)
                self.assertIn("Reload the page", response.text)
        self.assertFalse(self.holds())

    def test_the_product_page_carries_the_token_in_its_forms(self):
        page = self.client.get(f"/people/{LEO}").text
        self.assertIn(f'name="csrf" value="{security.token()}"', page)
