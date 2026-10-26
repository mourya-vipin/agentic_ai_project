"""The workflows: our code picks each step and ends the run (Lecture 1, slide 19).

Owner: Aman Panigrahi. U1 standard_grant, U2 onboarding, U3 offboarding. Every step
calls the registry, which traces it.

Reused from Aman's Assignment 1 (amanpanigrahi2305/agentic-ai-assignment1,
ways/workflow.py): the shape of a fixed sequence in three parts, gather (read what
the case needs), choose (our code decides) and post (the writes, in a fixed order).
Changed to fit: no model call at all, since the router has already read the
request; the reads and writes are our registry's tools; a failed step ends the
workflow with workflow_refused and leaves the request to the operator.

    standard_grant  set in progress -> grant -> "granted" notice -> done
                    (a refused grant: pending_operator -> "pending_review" notice)
    onboarding      a grant per birthright group of the role -> the SSO credential ->
                    its hand-over record -> "onboarded" notice -> done
    offboarding     remove all access -> propose removing each protected group ->
                    "removed" notice -> done

The person and their role come from the directory, never from the message.
"""

from __future__ import annotations

from agent import bounds
from agent.loop import LoopResult, action_line
from agent.registry import CallContext, get_registry
from harness import runtime
from sandbox import catalog

LOGIN_SYSTEMS = ("SYN-SYS-SSO",)      # the systems a new starter gets a sign-in for; the rest sign in through SSO


class Refused(Exception):
    """A fixed step found a case this workflow must not handle."""


class _Steps:
    """Numbers each step, calls it through the registry, and keeps one action line per effect."""

    def __init__(self, session_id: str, use_case: str, request_id: str):
        self.session_id, self.use_case, self.request_id = session_id, use_case, request_id
        self.step, self.actions = 0, []

    def call(self, tool: str, **args) -> dict:
        self.step += 1
        ctx = CallContext(self.session_id, self.step, "workflow", self.use_case, request_id=self.request_id)
        result = get_registry().call(tool, args, ctx)
        if result.is_error:
            raise Refused(f"step {self.step}, {tool}: {result.content}")
        if result.key is not None and tool != "requests_set_status":
            self.actions.append(action_line(tool, args, result.data))
        return result.data


def _run(session_id: str, use_case: str, request_id: str, gather, choose, post, on_refused=None) -> LoopResult:
    steps = _Steps(session_id, use_case, request_id)
    trace = runtime.trace(session_id)
    try:
        gathered = gather(steps)
        chosen = choose(gathered)
        text = post(steps, gathered, chosen)
    except Refused as exc:
        reason = str(exc)
        try:
            steps.call("requests_set_status", request_id=request_id, status="pending_operator", note=reason[:300])
            if on_refused is not None:
                on_refused(steps)
        except Refused:
            pass                    # the trace has the failed call; the operator still reads the reason
        trace.end(bounds.WORKFLOW_REFUSED, reason[:300])
        return LoopResult(f"{request_id} ({use_case}): left for you. {reason}", steps.actions,
                          bounds.WORKFLOW_REFUSED, steps.step)
    trace.end(bounds.WORKFLOW_DONE, f"{use_case} {request_id}")
    return LoopResult(text, steps.actions, bounds.WORKFLOW_DONE, steps.step)


def _first_name(person: dict) -> str:
    return person["name"].split()[0]


# -- U1 ------------------------------------------------------------------------------------

def standard_grant(session_id: str, request_id: str, group_id: str) -> LoopResult:
    """One group the role policy allows the requester without a manager; the router
    read it from the request."""
    facts = {}

    def gather(steps):
        request = steps.call("requests_get", request_id=request_id)
        person = steps.call("directory_get_employee", employee_id=request["requester_id"])
        facts.update(thread=request["thread_id"], name=_first_name(person))
        return {"request": request, "person": person}

    def choose(gathered):
        return {"employee_id": gathered["person"]["id"], "group_id": group_id}

    def post(steps, gathered, chosen):
        steps.call("requests_set_status", request_id=request_id, status="in_progress")
        granted = steps.call("access_grant", request_id=request_id, **chosen)
        steps.call("notices_send_template", thread_id=facts["thread"], request_id=request_id, template_id="granted",
                   facts={"name": facts["name"], "request_id": request_id, "group_name": granted["group_name"],
                          "group_id": granted["group_id"]})
        steps.call("requests_set_status", request_id=request_id, status="done")
        return f"{request_id} (standard grant): {granted['status']} {group_id} to {chosen['employee_id']}; notice sent."

    def on_refused(steps):
        if facts:
            steps.call("notices_send_template", thread_id=facts["thread"], request_id=request_id,
                       template_id="pending_review", facts={"name": facts["name"], "request_id": request_id})

    return _run(session_id, "standard_grant", request_id, gather, choose, post, on_refused)


# -- U2 ------------------------------------------------------------------------------------

def onboarding(session_id: str, request_id: str, fields: dict) -> LoopResult:
    """A new starter from the HR feed: the router parsed its fields."""

    def gather(steps):
        request = steps.call("requests_get", request_id=request_id)
        person = steps.call("directory_get_employee", employee_id=fields.get("employee", ""))
        if person["status"] != "pending_start":
            raise Refused(f"{person['id']} is {person['status']} in the directory, not a new starter")
        return {"request": request, "person": person}

    def choose(gathered):
        return {"groups": catalog.birthright_groups(gathered["person"]["role"]), "systems": LOGIN_SYSTEMS}

    def post(steps, gathered, chosen):
        person = gathered["person"]
        for group in chosen["groups"]:
            steps.call("access_grant", employee_id=person["id"], group_id=group, request_id=request_id)
        issued = [steps.call("credentials_issue", employee_id=person["id"], system_id=s)["credential_id"]
                  for s in chosen["systems"]]
        for credential_id in issued:
            _handover(steps, person["id"], credential_id)
        steps.call("notices_send_template", thread_id=gathered["request"]["thread_id"], request_id=request_id,
                   template_id="onboarded",
                   facts={"request_id": request_id, "name": person["name"], "employee_id": person["id"],
                          "count": len(chosen["groups"]), "role": person["role"], "start_date": person["start_date"]})
        steps.call("requests_set_status", request_id=request_id, status="done")
        return (f"{request_id} (onboarding): {person['name']} ({person['id']}) has {len(chosen['groups'])} groups "
                f"for {person['role']}; credential {', '.join(issued)} waits for hand-over on /pending.")

    return _run(session_id, "onboarding", request_id, gather, choose, post)


def _handover(steps: _Steps, employee_id: str, credential_id: str) -> None:
    """A credential is handed over in person, never in a message: a pending record for
    the operator, written through the registry (credentials_record_handover), which
    keys and traces it like every other write."""
    data = steps.call("credentials_record_handover", employee_id=employee_id, credential_id=credential_id,
                      request_id=steps.request_id)
    steps.actions[-1] = (f"wrote the hand-over of {credential_id} for {employee_id}: {data['pending_id']}, "
                         "waiting for the operator")


# -- U3 ------------------------------------------------------------------------------------

def offboarding(session_id: str, request_id: str, fields: dict) -> LoopResult:
    """A contract that ended, or a leaver, from the HR feed: the router parsed its fields."""

    def gather(steps):
        request = steps.call("requests_get", request_id=request_id)
        person = steps.call("directory_get_employee", employee_id=fields.get("employee", ""))
        return {"request": request, "person": person}

    def choose(gathered):
        return {"employee_id": gathered["person"]["id"],
                "reason": f"HR feed: {fields.get('event')} {fields.get('end', '')}".strip()}

    def post(steps, gathered, chosen):
        person = gathered["person"]
        removed = steps.call("access_revoke_all", request_id=request_id, **chosen)
        for group in removed["kept_protected"]:
            steps.call("access_propose_change", action="revoke", employee_id=person["id"], group_id=group,
                       request_id=request_id, reason=chosen["reason"])
        steps.call("notices_send_template", thread_id=gathered["request"]["thread_id"], request_id=request_id,
                   template_id="removed",
                   facts={"request_id": request_id, "name": person["name"], "employee_id": person["id"],
                          "count": len(removed["removed"]), "date": fields.get("end") or person["end_date"]})
        steps.call("requests_set_status", request_id=request_id, status="done")
        kept = removed["kept_protected"]
        return (f"{request_id} (offboarding): removed {len(removed['removed'])} groups from {person['name']} "
                f"({person['id']})" + (f"; kept {', '.join(kept)}, its removal proposed for you" if kept else "")
                + "; notice sent.")

    return _run(session_id, "offboarding", request_id, gather, choose, post)
