"""For tests of the product's pages: a test client whose form posts carry the page's form token,
as the product's own forms do (app/security.py). Owner: Vipin Mourya."""

from fastapi.testclient import TestClient

from app import security


class FormClient(TestClient):
    def post(self, url, *args, data=None, **kwargs):
        return super().post(url, *args, data={**(data or {}), "csrf": security.token()}, **kwargs)
