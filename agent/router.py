"""Routing: tell a new task from a question at the console, and send each request
to its use case's path (Lecture 1, slide 19).

Owner: Aman Panigrahi. Trusted data first (the thread, the requester's directory
record); a model call, traced as a step of its own, only where code cannot tell.
The switches control.<use_case> say which path each use case takes; U5 and U6 have only
the agent path, so their switches must say agent, and any other setting stops the turn.

An operator message. One model call (purpose "route_operator", no tools) answers
{"intent", "request_ids"}, seeing the session's last three messages. A message is a
new task when it asks us to act on requests or on the directory; it is a question
when it asks about work done or about the records. Code decides from the answer:

    process_requests   every new request, or the IDs named, each routed below
    bulk_change        U6: the planned executor if plan.bulk_change is "on", else the loop
    question           U5: the loop with read-only tools

An answer that does not parse is a question: the read-only path is the safe default.

A request. The thread says where it came from (our seed and channel set it):
SYN-HR-… is the HR feed, whose fixed fields our code parses (new_starter is U2;
contract_ended or leaver is U3; anything else goes to U4 with the operator told).
On an employee's thread the requester is the directory record our seed matched to the
sender's address. One model call (purpose "route_request", no tools) then describes
the request, whose text goes in labelled as the requester's and untrusted. Code sends
it to U1 only if every condition holds: the intent is a grant; the subject is the
requester (code compares IDs, and finds no other person of the directory in the text,
so "self" alone is never trusted); exactly one group is mentioned and it resolves to
exactly one group of the catalog; the role policy says birthright or allowed; and the
group needs no manager. Anything else goes to U4. The route is recorded on the request
(requests.route) and in the trace.

After an agent case, a request still new or in progress (the loop stopped at its
bound, or the model never set a status) is set to pending_operator by our code, and
the reply says so, so a later "process the requests" turn does not run it again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent import executor, loop, session, workflows
from agent.bounds import Bound
from agent.loop import LoopResult
from agent.registry import CallContext, get_registry
from harness import runtime
from harness.turns import Reply
from sandbox import catalog, db, directory, requests

STANDARD_GRANT, ONBOARDING, OFFBOARDING = "standard_grant", "onboarding", "offboarding"
NONSTANDARD, OPERATOR_REVIEW, BULK_CHANGE = "nonstandard_request", "operator_review", "bulk_change"
INTENTS = ("process_requests", "bulk_change", "question")
PROMPTS = Path(__file__).parent / "prompts"
BOUND = Bound()
HISTORY_TURNS = 3                       # earlier messages the operator router sees; U5's loop sees this many turns

READS = ["directory_get_employee", "directory_search_employees", "access_list_memberships", "catalog_search_groups",
         "policy_check_access", "requests_get", "requests_list_today"]
TOOLS = {
    OPERATOR_REVIEW: READS + ["directory_list_contractors_ended"],
    NONSTANDARD: READS + ["access_grant", "access_revoke", "access_propose_change", "reply_draft",
                          "manager_confirmation_draft", "requests_set_status"],
}
TOOLS[BULK_CHANGE] = TOOLS[NONSTANDARD] + ["directory_list_contractors_ended", "access_revoke_all"]

REQUEST_ID = re.compile(r"\bSYN-REQ-\d{4}\b", re.I)
EMPLOYEE_ID = re.compile(r"\bSYN-EMP-\d{4}\b", re.I)
GROUP_ID = re.compile(r"\bSYN-GRP-[A-Z0-9-]+\b", re.I)
SELF = ("self", "me", "myself", "i", "")
LEVELS = {"read": ("read-only", "read only", "readonly", "read", "reader", "readers", "viewer", "view"),
          "write": ("write", "writer", "writers", "edit", "editor", "editors"),
          "admin": ("admin", "administrator", "administrators")}
SYSTEM_ALIASES = {"SYN-SYS-SSO": ("sso",), "SYN-SYS-DWH": ("dwh",), "SYN-SYS-AWSPROD": ("aws prod", "aws"),
                  "SYN-SYS-LEGAL": ("legal hold",)}
HR_EVENTS = {"new_starter": ONBOARDING, "contract_ended": OFFBOARDING, "leaver": OFFBOARDING}


@dataclass
class Route:
    use_case: str
    why: list[str] = field(default_factory=list)
    group_id: str | None = None                   # U1's group
    fields: dict = field(default_factory=dict)    # U2's and U3's HR fields
    note: str = ""                                # what the operator must be told about the routing


# -- the operator's message ---------------------------------------------------------------

def handle_operator(session_id: str, text: str) -> Reply:
    """One operator message: a new task (process requests, a bulk change) or a
    question about work already done."""
    earlier = session.history(session_id)[-HISTORY_TURNS:]
    session.add(session_id, "operator", text)
    intent, request_ids, reason = route_operator(session_id, text, earlier)
    runtime.trace(session_id).event("route", scope="operator", intent=intent, request_ids=request_ids, reason=reason)
    if intent == "process_requests":
        reply = process_requests(session_id, request_ids)
    elif intent == "bulk_change":
        reply = _as_reply(bulk_change(session_id, text))
    else:
        _agent_path(OPERATOR_REVIEW)
        reply = _as_reply(loop.run_loop(session_id, use_case=OPERATOR_REVIEW, system=_prompt(OPERATOR_REVIEW),
                                        user_content=_with_history(session_id, text), tools=TOOLS[OPERATOR_REVIEW],
                                        bound=BOUND))
    session.add(session_id, "agent", reply.text)
    return reply


def _agent_path(use_case: str) -> None:
    """Read control.<use_case> for a use case that has only the agent path, and refuse
    any other setting rather than run a path the switch does not name."""
    path = runtime.switch(f"control.{use_case}")
    if path != "agent":
        raise ValueError(f"control.{use_case} is {path!r}, but {use_case} has only the agent path: "
                         "set it to agent in switches.yaml")


def route_operator(session_id: str, text: str, earlier: list[dict]) -> tuple[str, list[str], str]:
    """(intent, request IDs, why), from one traced model call."""
    past = "\n".join(f"{t['role']}: {t['text']}" for t in earlier) or "(none)"
    content = f"Earlier turns in this session:\n{past}\n\nThe operator's message:\n{text}"
    response = runtime.client(session_id).create(system=_prompt("route_operator"),
                                                 messages=[{"role": "user", "content": content}])
    runtime.trace(session_id).model_call(0, response, purpose="route_operator")
    answer = _json(response.text)
    intent = answer.get("intent") if isinstance(answer.get("intent"), str) else None
    if intent not in INTENTS:
        return "question", [], "the routing answer did not parse, so the read-only path"
    ids = [i.upper() for i in answer.get("request_ids") or [] if isinstance(i, str) and REQUEST_ID.fullmatch(i)]
    if intent == "process_requests":
        ids += [i.upper() for i in REQUEST_ID.findall(text) if i.upper() not in ids]
    return intent, ids, "the routing call's answer"


def bulk_change(session_id: str, text: str) -> LoopResult:
    """U6: planned if plan.bulk_change is on, else the bounded loop."""
    _agent_path(BULK_CHANGE)
    if runtime.switch("plan.bulk_change") == "on":
        return executor.run_planned(session_id, text, bound=BOUND)
    return loop.run_loop(session_id, use_case=BULK_CHANGE, system=_prompt(BULK_CHANGE),
                         user_content=_with_history(session_id, text), tools=TOOLS[BULK_CHANGE], bound=BOUND)


# -- the core task: the day's requests ----------------------------------------------------

def process_requests(session_id: str, request_ids: list[str]) -> Reply:
    if request_ids:
        found = {r: requests.get(r) for r in request_ids}
        todo = [item for item in found.values() if item is not None]
        lines = [f"{r}: no such request." for r, item in found.items() if item is None]
    else:
        todo, lines = [r for r in requests.all_requests() if r["status"] == "new"], []
    if not todo and not lines:
        return Reply("There are no new requests.")
    actions: list[str] = []
    for item in todo:
        # claimed first, then read again: two console sessions at once must not both handle it,
        # and one that listed it earlier must not redo what the other has finished
        if not requests.claim(item["id"], session_id):
            lines.append(f"{item['id']}: another console session is working on it, left to that session.")
            continue
        try:
            item = requests.get(item["id"])
            if item["status"] not in ("new", "in_progress"):
                lines.append(f"{item['id']}: already {item['status']}, left as it is.")
                continue
            runtime.trace(session_id).progress(f"Working on {item['id']}.")
            result = handle_request(session_id, item)
            lines.append(result.text)
            actions += result.actions
        finally:
            requests.release(item["id"], session_id)
    return Reply("\n".join(lines), actions)


def handle_request(session_id: str, request: dict) -> LoopResult:
    """One request, on the path control.<use_case> names for its use case."""
    route = route_request(session_id, request)
    path = runtime.switch(f"control.{route.use_case}")
    requests.set_route(request["id"], f"{path}:{route.use_case}", stamp=db.current_stamp())
    runtime.trace(session_id).event("route", scope="request", request_id=request["id"],
                                    route=f"{path}:{route.use_case}", why=route.why)
    if path == "workflow" and route.use_case == STANDARD_GRANT:
        return workflows.standard_grant(session_id, request["id"], route.group_id)
    if path == "workflow" and route.use_case == ONBOARDING:
        return workflows.onboarding(session_id, request["id"], route.fields)
    if path == "workflow" and route.use_case == OFFBOARDING:
        return workflows.offboarding(session_id, request["id"], route.fields)
    result = loop.run_loop(session_id, use_case=NONSTANDARD, system=_prompt(NONSTANDARD),
                           user_content=_case(request, route), tools=TOOLS[NONSTANDARD], bound=BOUND,
                           request_id=request["id"])
    left = _leave_for_operator(session_id, request["id"], result)
    text = " ".join(t for t in (f"{request['id']} (agent):", route.note, result.text, left) if t)
    return LoopResult(text, result.actions, result.end_reason, result.steps)


def _leave_for_operator(session_id: str, request_id: str, result: LoopResult) -> str:
    """Our code, not the model, sets a request the agent did not finish to pending_operator."""
    status = requests.get(request_id)["status"]
    if status not in ("new", "in_progress"):
        return ""
    note = f"The agent ended ({result.end_reason}) with the request still {status}."
    ctx = CallContext(session_id, result.steps + 1, "workflow", NONSTANDARD, request_id=request_id)
    done = get_registry().call("requests_set_status", {"request_id": request_id, "status": "pending_operator",
                                                       "note": note}, ctx)
    if done.is_error:
        return f"{note} Its status could not be set, so check it on /requests/{request_id}."
    return f"{note} It is now pending_operator, left for you."


def route_request(session_id: str, request: dict) -> Route:
    if requests.source_of(request["thread_id"]) == "hr_feed":
        fields = hr_fields(request["text"])
        use_case = HR_EVENTS.get(fields.get("event", "").lower())
        if use_case and EMPLOYEE_ID.fullmatch(fields.get("employee", "")):
            return Route(use_case, [f"HR feed event {fields['event']}"], fields=fields)
        return Route(NONSTANDARD, ["the HR feed message did not parse: the operator is told"],
                     note="The HR feed message did not parse, so the agent handled it: check it before trusting it.")
    requester = directory.get_employee(request["requester_id"]) if request["requester_id"] else None
    if requester is None:
        return Route(NONSTANDARD, ["the sender is not in the directory"])
    return decide(request, requester, describe_request(session_id, request))


def describe_request(session_id: str, request: dict) -> dict:
    """The routing call's description of one request, or {} if it does not parse."""
    content = f'<request from="{request["requester_id"]}" trust="untrusted">\n{request["text"]}\n</request>'
    response = runtime.client(session_id).create(system=_prompt("route_request"),
                                                 messages=[{"role": "user", "content": content}])
    runtime.trace(session_id).model_call(0, response, purpose="route_request", request_id=request["id"])
    return _json(response.text)


def decide(request: dict, requester: dict, answer: dict) -> Route:
    """U1 only if every condition holds; otherwise U4, with each reason it failed."""
    why = []
    if answer.get("intent") != "grant":
        why.append(f"the intent is {answer.get('intent') or 'unreadable'}, not a grant")
    subject = str(answer.get("subject") or "").strip()
    if not _is_requester(subject, requester):
        why.append(f"the subject ({subject or 'none'}) is not the requester")
    others = people_named(request["text"], exclude=requester["id"])
    if others:
        why.append(f"the text names someone besides the requester ({', '.join(others)})")
    mentioned = [g for g in answer.get("groups_mentioned") or [] if isinstance(g, str)]
    group_id = None
    if len(mentioned) != 1:
        why.append(f"{len(mentioned)} groups are mentioned, not one")
    else:
        matches = resolve_group(mentioned[0])
        if len(matches) != 1:
            why.append(f"{mentioned[0]!r} matches {len(matches)} groups of the catalog, not one")
        else:
            group_id = matches[0]
            decision = catalog.policy_for(requester["id"], group_id)
            group = catalog.get_group(group_id)
            if decision["decision"] not in ("birthright", "allowed"):
                why.append(f"{group_id} is {decision['decision']} for {requester['id']}: {decision['reason']}")
            elif group["needs_manager"] or group["sensitivity"] == "high":
                why.append(f"{group_id} needs the manager")
    if why:
        return Route(NONSTANDARD, why, group_id=group_id)
    return Route(STANDARD_GRANT, [f"{group_id} is allowed for {requester['id']}, asked for themself"],
                 group_id=group_id)


def _is_requester(subject: str, requester: dict) -> bool:
    if subject.lower() in SELF:
        return True
    if EMPLOYEE_ID.fullmatch(subject):
        return subject.upper() == requester["id"]
    found = directory.search_employees(subject, limit=2)
    return len(found) == 1 and found[0]["id"] == requester["id"]


def people_named(text: str, exclude: str) -> list[str]:
    """The directory's people, other than `exclude`, whose full name or ID the text contains."""
    lowered = " ".join(str(text).lower().split())
    with db.connect() as conn:
        people = conn.execute("SELECT id, name FROM employees").fetchall()
    return sorted(p["id"] for p in people if p["id"] != exclude and (
        re.search(rf"\b{re.escape(p['name'].lower())}\b", lowered) or p["id"].lower() in lowered))


def resolve_group(mention: str) -> list[str]:
    """The catalog groups one mention names: by ID; by a group's name; or by one system
    and one access level that pick a single group."""
    lowered = " ".join(mention.lower().replace("-", " ").split())
    ids = [g.upper() for g in GROUP_ID.findall(mention)]
    if ids:
        return [g for g in dict.fromkeys(ids) if catalog.get_group(g)]
    groups = catalog.search_groups("", limit=1000)
    by_name = [g["id"] for g in groups if re.search(rf"\b{re.escape(g['name'].lower())}\b", lowered)]
    if by_name:
        return by_name
    systems = {g["system_id"] for g in groups
               if any(re.search(rf"\b{re.escape(n)}\b", lowered)
                      for n in (g["system_name"].lower(), *SYSTEM_ALIASES.get(g["system_id"], ())))}
    levels = [lv for lv, words in LEVELS.items() if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words)]
    if len(systems) != 1 or len(levels) != 1:
        return []
    return [g["id"] for g in groups if g["system_id"] in systems and g["level"] == levels[0]]


def hr_fields(text: str) -> dict:
    """The HR feed's fixed fields, "EVENT: new_starter; EMPLOYEE: SYN-EMP-0215; ...", with
    lower-case names."""
    out = {}
    for part in str(text).replace("SYNTHETIC.", "").split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip().lower()] = value.strip()
    return out


# -- what the agent reads -----------------------------------------------------------------

def _case(request: dict, route: Route) -> str:
    """Our records first, then the requester's own text, labelled as theirs and untrusted."""
    person = directory.get_employee(request["requester_id"]) if request["requester_id"] else None
    who = (f"{person['name']} ({person['id']}), {person['title']}, role {person['role']}, manager "
           f"{person['manager_id'] or 'none'}" if person else "not in the directory")
    return (f"Request {request['id']}, received {request['received_at']} on thread {request['thread_id']}.\n"
            f"The requester, from the directory: {who}.\n"
            f"Why it came to you: {'; '.join(route.why)}.\n\n"
            f'<request from="{request["requester_id"] or "unknown"}" trust="untrusted">\n{request["text"]}\n'
            f"</request>")


def _with_history(session_id: str, text: str) -> str:
    """Within one session the conversation is the state (Milestone 1)."""
    earlier = session.history(session_id)[:-1][-2 * HISTORY_TURNS:]
    if not earlier:
        return text
    past = "\n".join(f"{t['role']}: {t['text']}" for t in earlier)
    return f"Earlier in this session:\n{past}\n\nThe operator now says:\n{text}"


def _as_reply(result: LoopResult) -> Reply:
    return Reply(result.text, result.actions)


def _json(text: str) -> dict:
    """The first JSON object in a model's text, or {}."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()
