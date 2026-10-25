"""One function per canonical tool, run(args, ctx, key) -> dict, wrapping the
sandbox/ modules. Owner: Vipin Mourya.

What each tool may reach is decided here, not in a prompt:
- no function returns a credential's secret;
- access_grant refuses every group the role policy does not allow for that person
  (the rest go to access_propose_change, a record the operator acts on);
- on a non-standard request, access_grant grants only to the requester, or to someone
  who reports to the requester; anyone else needs their manager's confirmation;
- access_revoke on a non-standard request removes only the requester's own groups;
- notices_send_template, credentials_issue and credentials_record_handover are for our
  workflows only (CALLERS).

A failure raises one of agent.errors' classes with a message that says what was
wrong, what would be right, and whether a retry can work.
"""

from __future__ import annotations

import hashlib

from agent import critique
from agent.errors import EndRun, PermanentError
from harness import runtime
from sandbox import access, catalog, clock, credentials, db, directory, notices, pending, requests

MODEL = {"model", "workflow", "executor"}
CODE = {"workflow"}


def _employee(employee_id: str) -> dict:
    person = directory.get_employee(employee_id) if employee_id else None
    if person is None:
        raise PermanentError(f"No employee {employee_id!r}. Employee IDs look like SYN-EMP-0031; find one with "
                             "directory_search_employees. A retry with the same ID will fail the same way.")
    return person


def _group(group_id: str) -> dict:
    group = catalog.get_group(group_id) if group_id else None
    if group is None:
        raise PermanentError(f"No group {group_id!r}. Group IDs look like SYN-GRP-JIRA-WRITE; find one with "
                             "catalog_search_groups. A retry with the same ID will fail the same way.")
    return group


def _request(request_id: str | None) -> dict | None:
    if not request_id:
        return None
    item = requests.get(request_id)
    if item is None:
        raise PermanentError(f"No request {request_id!r}. Request IDs look like SYN-REQ-0005; "
                             "requests_list_today lists today's. A retry with the same ID will fail the same way.")
    return item


def _case(args, ctx) -> dict | None:
    """The request a write is made under. On a non-standard request it is the case the
    router gave this loop (ctx.request_id), whatever request the model names, so the
    rules below cannot be stepped around by naming another request."""
    if ctx.use_case == "nonstandard_request" and ctx.request_id:
        return requests.get(ctx.request_id)
    return _request(args.get("request_id"))


def _actor(ctx) -> str:
    return "workflow" if ctx.caller == "workflow" else "agent"


def _person_view(p: dict) -> dict:
    manager = directory.get_employee(p["manager_id"]) if p["manager_id"] else None
    return {**p, "manager_name": manager["name"] if manager else None}


# -- reads -------------------------------------------------------------------------------

def directory_get_employee(args, ctx, key):
    if args.get("email"):
        person = directory.by_email(args["email"])
        if person is None:
            raise PermanentError(f"No employee with the email {args['email']!r}. A retry will fail the same way.")
        return _person_view(person)
    return _person_view(_employee(args.get("employee_id")))


def directory_search_employees(args, ctx, key):
    query, dept = str(args.get("query", "")), args.get("dept")
    found = directory.search_employees(query, dept, limit=10)
    return {"matches": found, "shown": len(found), "total": directory.count_matching(query, dept)}


def directory_list_contractors_ended(args, ctx, key):
    before = args.get("before") or clock.today()
    return {"before": before, "contractors": directory.contractors_ended(before)}


def access_list_memberships(args, ctx, key):
    person = _employee(args.get("employee_id"))
    active_only = not args.get("include_removed", False)
    return {"employee_id": person["id"], "active_only": active_only,
            "memberships": access.memberships(person["id"], active_only=active_only)}


def catalog_search_groups(args, ctx, key):
    found = catalog.search_groups(str(args.get("query", "")), args.get("system_id"), limit=10)
    return {"groups": found, "shown": len(found)}


def policy_check_access(args, ctx, key):
    person = _employee(args.get("employee_id"))
    ids = args.get("group_ids") or args.get("group_id") or []
    ids = [ids] if isinstance(ids, str) else list(ids)
    return {"employee_id": person["id"], "role": person["role"],
            "decisions": [{"group_id": str(g).upper(), **catalog.policy_for(person["id"], g)} for g in ids]}


def requests_get(args, ctx, key):
    item = _request(args.get("request_id"))
    return {**item, "changes": access.by_request(item["id"]), "pending": pending.by_request(item["id"])}


def requests_list_today(args, ctx, key):
    return {"day": clock.today(), "requests": requests.all_requests(clock.today())}


# -- writes ------------------------------------------------------------------------------

def access_grant(args, ctx, key):
    person, group = _employee(args.get("employee_id")), _group(args.get("group_id"))
    request = _case(args, ctx)
    if ctx.use_case == "nonstandard_request" and request and request["requester_id"] \
            and person["id"] != request["requester_id"] and person["manager_id"] != request["requester_id"]:
        manager = directory.manager_of(person["id"])
        raise PermanentError(
            f"Cannot grant to {person['id']} on {request['id']}: the requester {request['requester_id']} is neither "
            f"{person['id']} nor their manager. Write manager_confirmation_draft for {person['id']} and "
            f"{group['id']}" + (f", for {manager['name']} ({manager['id']}) to confirm" if manager else "")
            + ". A retry of this call will fail the same way.")
    decision = catalog.policy_for(person["id"], group["id"])
    if decision["decision"] not in ("birthright", "allowed"):
        raise PermanentError(f"Cannot grant {group['id']} to {person['id']}: {decision['reason']}. "
                             + ("Use access_propose_change with action \"grant\" instead, for the operator to approve. "
                                if decision["decision"] == "needs_manager" else "It cannot be granted on request. ")
                             + "A retry of this call will fail the same way.")
    result = access.grant(person["id"], group["id"], request_id=request["id"] if request else None, key=key,
                          stamp=db.current_stamp(), actor=_actor(ctx), expires_at=args.get("expires_at"))
    return {**result, "employee_id": person["id"], "group_id": group["id"], "group_name": group["name"]}


def access_revoke(args, ctx, key):
    person, group = _employee(args.get("employee_id")), _group(args.get("group_id"))
    request = _case(args, ctx)
    if ctx.use_case == "nonstandard_request" and request and request["requester_id"] != person["id"]:
        raise PermanentError(f"On an employee's request you may remove only the requester's own groups, and "
                             f"{person['id']} is not the requester of {request['id']}. Use access_propose_change with "
                             "action \"revoke\" instead. A retry will fail the same way.")
    result = access.revoke(person["id"], group["id"], request_id=request["id"] if request else None, key=key,
                           stamp=db.current_stamp(), actor=_actor(ctx), reason=str(args.get("reason", "")))
    return {**result, "employee_id": person["id"], "group_id": group["id"]}


def access_revoke_all(args, ctx, key):
    person = _employee(args.get("employee_id"))
    request = _request(args.get("request_id"))
    removed, kept = [], []
    for m in access.memberships(person["id"]):
        sub_key = hashlib.sha256(f"{key}|{m['group_id']}".encode()).hexdigest()[:32] if key else None
        result = access.revoke(person["id"], m["group_id"], request_id=request["id"] if request else None,
                               key=sub_key, stamp=db.current_stamp(), actor=_actor(ctx),
                               reason=str(args.get("reason", "")))
        (kept if result["status"] == "protected" else removed).append(m["group_id"])
    return {"employee_id": person["id"], "removed": removed, "kept_protected": kept}


def access_propose_change(args, ctx, key):
    action = str(args.get("action", "")).lower()
    if action not in ("grant", "revoke"):
        raise PermanentError(f"action is \"grant\" or \"revoke\", not {args.get('action')!r}.")
    person, group = _employee(args.get("employee_id")), _group(args.get("group_id"))
    request = _request(args.get("request_id"))
    pid = pending.create("proposed_change", request_id=request["id"] if request else None, employee_id=person["id"],
                         key=key, stamp=db.current_stamp(), action=action, group_id=group["id"],
                         reason=str(args.get("reason", "")))
    return {"pending_id": pid, "status": "waiting_for_operator", "action": action, "employee_id": person["id"],
            "group_id": group["id"]}


_rounds: dict[tuple, int] = {}      # (stamp, recipient, request) -> critique rounds used


def reply_draft(args, ctx, key):
    recipient = _employee(args.get("recipient_employee_id"))
    request = _request(args.get("request_id"))
    body = str(args.get("body", "")).strip()
    if not body:
        raise PermanentError("The draft's body is empty: write the reply to the recipient.")
    stamp = db.current_stamp()
    if runtime.switch("critique.requester_reply") == "external":
        slot = (stamp, recipient["id"], request["id"] if request else None)
        _rounds[slot] = _rounds.get(slot, 0) + 1
        verdict = critique.review_draft(recipient_employee_id=recipient["id"], request_id=slot[2], body=body,
                                        stamp=stamp, round=_rounds[slot])
        runtime.trace(ctx.session_id).event("critique", round=verdict.round, verdict="pass" if verdict.passed
                                            else "fail", reason=verdict.reason, changed=verdict.changed,
                                            recipient=recipient["id"], request_id=slot[2])
        if not verdict.passed:
            if verdict.round >= critique.MAX_ROUNDS:
                raise EndRun(f"The draft was held and not written: {verdict.reason}", critique.CRITIQUE_HELD)
            raise PermanentError(f"The draft was not written (round {verdict.round} of {critique.MAX_ROUNDS}): "
                                 f"{verdict.reason} Revise the draft and call reply_draft again.")
    thread = request["thread_id"] if request and request["requester_id"] == recipient["id"] else \
        f"SYN-MAIL-{recipient['id']}"
    pid = pending.create("reply_draft", request_id=request["id"] if request else None, employee_id=recipient["id"],
                         key=key, stamp=stamp, thread_id=thread, body=body)
    return {"pending_id": pid, "status": "waiting_for_operator", "thread_id": thread}


def manager_confirmation_draft(args, ctx, key):
    person, group = _employee(args.get("employee_id")), _group(args.get("group_id"))
    request = _request(args.get("request_id"))
    manager = directory.manager_of(person["id"])
    if manager is None:
        raise PermanentError(f"{person['id']} has no manager on record; write access_propose_change instead.")
    pid = pending.create("manager_confirmation", request_id=request["id"] if request else None,
                         employee_id=person["id"], key=key, stamp=db.current_stamp(), group_id=group["id"],
                         thread_id=f"SYN-MAIL-{manager['id']}", body=str(args.get("body", "")).strip() or None)
    return {"pending_id": pid, "status": "waiting_for_operator", "manager_id": manager["id"],
            "manager_name": manager["name"]}


def requests_set_status(args, ctx, key):
    _request(args.get("request_id"))
    status = str(args.get("status", "")).lower()
    if status not in requests.STATUSES:
        raise PermanentError(f"status is one of {', '.join(requests.STATUSES)}, not {args.get('status')!r}.")
    row = requests.set_status(args["request_id"], status, stamp=db.current_stamp(), note=str(args.get("note", "")))
    return {"request_id": row["id"], "status": row["status"]}


def notices_send_template(args, ctx, key):
    return notices.send_template(args["thread_id"], args["template_id"], dict(args.get("facts") or {}), key=key,
                                 stamp=db.current_stamp())


def credentials_issue(args, ctx, key):
    person = _employee(args.get("employee_id"))
    cid = credentials.issue(person["id"], str(args.get("system_id", "SYN-SYS-SSO")), key=key, stamp=db.current_stamp())
    return {"credential_id": cid, "employee_id": person["id"]}


def credentials_record_handover(args, ctx, key):
    """A credential is handed over in person, never in a message: a pending record for the operator."""
    person = _employee(args.get("employee_id"))
    credential_id = str(args.get("credential_id", "")).strip().upper()
    if credential_id not in {c["id"] for c in credentials.for_employee(person["id"])}:
        raise PermanentError(f"{person['id']} has no credential {credential_id!r}; issue one with credentials_issue "
                             "first.")
    request = _request(args.get("request_id"))
    pid = pending.create("credential_handover", request_id=request["id"] if request else None, employee_id=person["id"],
                         key=key, stamp=db.current_stamp(), reason=f"Hand over {credential_id} in person.")
    return {"pending_id": pid, "status": "waiting_for_operator", "employee_id": person["id"],
            "credential_id": credential_id}


# name -> (function, who may call it, whether it writes)
TOOLS = {
    "directory_get_employee": (directory_get_employee, MODEL, False),
    "directory_search_employees": (directory_search_employees, MODEL, False),
    "directory_list_contractors_ended": (directory_list_contractors_ended, MODEL, False),
    "access_list_memberships": (access_list_memberships, MODEL, False),
    "catalog_search_groups": (catalog_search_groups, MODEL, False),
    "policy_check_access": (policy_check_access, MODEL, False),
    "requests_get": (requests_get, MODEL, False),
    "requests_list_today": (requests_list_today, MODEL, False),
    "access_grant": (access_grant, MODEL, True),
    "access_revoke": (access_revoke, MODEL, True),
    "access_revoke_all": (access_revoke_all, MODEL, True),
    "access_propose_change": (access_propose_change, MODEL, True),
    "reply_draft": (reply_draft, MODEL, True),
    "manager_confirmation_draft": (manager_confirmation_draft, MODEL, True),
    "requests_set_status": (requests_set_status, MODEL, True),
    "notices_send_template": (notices_send_template, CODE, True),
    "credentials_issue": (credentials_issue, CODE, True),
    "credentials_record_handover": (credentials_record_handover, CODE, True),
}

# The arguments that name a write's effect: the key is derived from these only, never
# from text the model writes, so a reworded retry gets the same key (Lecture 3, slide 38).
EFFECT_ARGS = {
    "access_grant": ("employee_id", "group_id", "request_id"),
    "access_revoke": ("employee_id", "group_id", "request_id"),
    "access_revoke_all": ("employee_id", "request_id"),
    "access_propose_change": ("action", "employee_id", "group_id", "request_id"),
    "reply_draft": ("recipient_employee_id", "request_id"),
    "manager_confirmation_draft": ("employee_id", "group_id", "request_id"),
    "requests_set_status": ("request_id", "status"),
    "notices_send_template": ("thread_id", "request_id", "template_id"),
    "credentials_issue": ("employee_id", "system_id"),
    "credentials_record_handover": ("employee_id", "credential_id"),
}
