"""The product's own pages: the IT access desk with no agent at all.
Owner: Vipin Mourya.

    /                      today's access requests and their status
    /requests/{id}         one request: its message, what changed under it, its pending records
    /people?q=             the staff directory
    /people/{id}           one person: role, manager, groups, credentials; grant or remove by hand
    /pending               what waits for the operator: proposed changes, reply drafts,
                           manager confirmations and credential hand-overs

Every read and write goes through the sandbox/ modules the agent's tools wrap too.
A change made here is the operator's: actor "operator", stamp "manual", no key. A
reply draft sent from here goes out as the operator's (channel.send(..., sender="operator")).
"""

from __future__ import annotations

import functools

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

import channel
from app import security, templates
from sandbox import access, catalog, clock, credentials, directory, notices, pending, requests

router = APIRouter(dependencies=[Depends(security.require_token)])   # every product POST carries the form token
templates.env.globals["csrf_token"] = security.token
MANUAL = "manual"


def _page(request: Request, name: str, **context):
    return templates.TemplateResponse(request, name, {"page": "home", "today": clock.today(), **context})


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _refused(handler):
    """A form the sandbox refuses answers with its reason, not a server error: a value it
    does not take is a 400, a record that does not exist (or is no longer open) a 404.
    The sandbox checks before it writes, so nothing has changed."""
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except (KeyError, access.NotFound) as exc:      # NotFound is a ValueError, so first
            raise HTTPException(404, str(exc.args[0]) if exc.args else "not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return wrapper


@router.get("/")
def home(request: Request):
    rows = requests.all_requests()
    people = {r["requester_id"]: directory.get_employee(r["requester_id"]) for r in rows if r["requester_id"]}
    return _page(request, "home.html", requests=rows, people=people, open_count=len(requests.open_requests()),
                 pending_count=len(pending.open_items()))


@router.get("/requests/{request_id}")
def request_page(request: Request, request_id: str):
    item = requests.get(request_id)
    if item is None:
        raise HTTPException(404, f"no request {request_id}")
    return _page(request, "product_request.html", item=item,
                 requester=directory.get_employee(item["requester_id"]) if item["requester_id"] else None,
                 messages=channel.messages(item["thread_id"]), changes=access.by_request(item["id"]),
                 pending_items=pending.by_request(item["id"]), statuses=requests.STATUSES)


@router.post("/requests/{request_id}/status")
@_refused
def request_status(request_id: str, status: str = Form(...), note: str = Form("")):
    requests.set_status(request_id, status, stamp=MANUAL, note=note)
    return _back(f"/requests/{request_id}")


@router.get("/people")
def people_page(request: Request, q: str = ""):
    found = directory.search_employees(q, limit=50) if q.strip() else []
    return _page(request, "product_people.html", q=q, found=found)


@router.get("/people/{employee_id}")
def person_page(request: Request, employee_id: str):
    return _person(request, employee_id)


def _person(request: Request, employee_id: str, revealed: str = "", secret: str = ""):
    """A secret is shown only from credential_reveal, never from the address bar."""
    person = directory.get_employee(employee_id)
    if person is None:
        raise HTTPException(404, f"no employee {employee_id}")
    held = access.memberships(person["id"], active_only=False)
    active = {m["group_id"] for m in held if m["removed_at"] is None}
    grantable = [g for g in catalog.search_groups(limit=100) if g["id"] not in active]
    return _page(request, "product_person.html", person=person, manager=directory.manager_of(person["id"]),
                 held=held, grantable=grantable, creds=credentials.for_employee(person["id"]),
                 policy={g["id"]: catalog.policy_for(person["id"], g["id"])["decision"] for g in grantable},
                 revealed=revealed, secret=secret)


@router.post("/people/{employee_id}/grant")
@_refused
def person_grant(employee_id: str, group_id: str = Form(...), request_id: str = Form("")):
    access.grant(employee_id, group_id, request_id=request_id or None, key=None, stamp=MANUAL, actor="operator")
    return _back(f"/people/{employee_id}")


@router.post("/people/{employee_id}/revoke")
@_refused
def person_revoke(employee_id: str, group_id: str = Form(...), request_id: str = Form("")):
    access.revoke(employee_id, group_id, request_id=request_id or None, key=None, stamp=MANUAL, actor="operator",
                  reason="removed by hand")
    return _back(f"/people/{employee_id}")


@router.post("/credentials/{credential_id}/reveal")
@_refused
def credential_reveal(request: Request, credential_id: str, employee_id: str = Form(...)):
    """Shows one secret once, on the person's page, for a hand-over outside our channel;
    only the credential's own holder's page shows it."""
    cred_id = str(credential_id).strip().upper()
    if cred_id not in {c["id"] for c in credentials.for_employee(str(employee_id).strip().upper())}:
        raise KeyError(f"no credential {credential_id} for {employee_id}")
    return _person(request, employee_id, revealed=cred_id, secret=credentials.reveal(cred_id))


@router.get("/pending")
def pending_page(request: Request):
    return _page(request, "product_pending.html", items=pending.open_items())


@router.post("/pending/{pending_id}/apply")
@_refused
def pending_apply(pending_id: str):
    """A proposed change, done by hand by the operator. It is closed first, so a second
    click finds it closed, and opened again if the change then fails."""
    item = pending.get(pending_id)
    if item is None or item["kind"] != "proposed_change" or item["status"] != "open":
        raise HTTPException(404, f"no open proposed change {pending_id}")
    pending.close(pending_id, "done", stamp=MANUAL)
    try:
        if item["action"] == "revoke":
            access.revoke(item["employee_id"], item["group_id"], request_id=item["request_id"], key=None,
                          stamp=MANUAL, actor="operator", reason="approved by the operator")
        else:
            access.grant(item["employee_id"], item["group_id"], request_id=item["request_id"], key=None,
                         stamp=MANUAL, actor="operator")
    except Exception:
        pending.reopen(pending_id, stamp=MANUAL)
        raise
    return _back("/pending")


@router.post("/pending/{pending_id}/send")
@_refused
def pending_send(pending_id: str):
    notices.send_as_operator(pending_id)
    return _back("/pending")


@router.post("/pending/{pending_id}/close")
@_refused
def pending_close(pending_id: str, status: str = Form("done")):
    pending.close(pending_id, status, stamp=MANUAL)
    return _back("/pending")
