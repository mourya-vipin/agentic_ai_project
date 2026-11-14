"""The app's guard against other web pages (M1 CHANGES, "Limited": the app had no sign-in and its
forms no CSRF token). Owner: Vipin Mourya.

The app serves one operator on 127.0.0.1, so it has no sign-in: anyone who can reach the port is
already on the operator's machine. What it guards against is a page in the operator's own browser
posting to http://localhost:8000, which could otherwise grant or remove access, send a reply or
answer an approval as the operator. Two checks:

    install(app)      every request names this machine as its Host (no DNS rebinding), and every
                      POST from a browser comes from this app's own pages: its Origin (or, without
                      one, its Referer) is this app. A request with neither, such as curl, a test
                      client or run.py's turns, is not a browser's and passes.
    require_token     on the product's forms (app/product.py): a hidden field with this process's
                      token, which another site cannot read. A restart makes a new one, so a page
                      from before a restart says to reload.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver")     # testserver: FastAPI's test client
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
_TOKEN = secrets.token_urlsafe(32)


def token() -> str:
    """This process's form token, for the product's templates (csrf_token())."""
    return _TOKEN


def _host_name(host: str) -> str:
    """The host a Host header names, without its port: "localhost:8000" is localhost, "[::1]:8000" is [::1]."""
    host = (host or "").strip().lower()
    return host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]


def local_host(host: str | None) -> bool:
    return _host_name(host or "") in LOCAL_HOSTS


def same_origin(request: Request) -> bool:
    """Whether a browser's POST came from this app's own pages."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == (request.headers.get("host") or "").lower()


def install(app) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not local_host(request.headers.get("host")):
            return PlainTextResponse("This app serves only this machine (localhost).", status_code=403)
        if request.method in UNSAFE and not same_origin(request):
            return PlainTextResponse("Refused: this form did not come from the app's own pages. Nothing was "
                                     "changed. Open http://localhost:8000 and use the form there.", status_code=403)
        return await call_next(request)


async def require_token(request: Request) -> None:
    """A router dependency: every POST to a product form carries this process's token."""
    if request.method not in UNSAFE:
        return
    form = await request.form()
    sent = str(form.get("csrf") or "")
    if not secrets.compare_digest(sent, _TOKEN):
        raise HTTPException(403, "This form is out of date or did not come from this app; nothing was changed. "
                                 "Reload the page and try again.")
