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

Milestone 2. Each request, each question and each bulk change is one run
(agent/checkpoints.py). The router opens the run, gives its key to the path, and
saves the run when the turn ends (checkpoints.end_turn). If the operator stopped or
cancelled the run during the turn, and the run is not done, it is saved as stopped or
cancelled. resume() continues a saved run of the workflows or of the loop, after an
approval, a reply, /resume or the operator's answer to an escalation. The two routing calls are built with context.assemble (kind "router"). They
get no notes, and a call that cannot fit gives the safe route: a question, or U4.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import channel
from agent import approvals, checkpoints, context, control, executor, inbox, loop, memory, session, workflows
from agent import bounds
from agent.bounds import Bound
from agent.loop import LoopResult
from agent.registry import CallContext, get_registry
from harness import runtime
from harness.turns import Reply
from sandbox import catalog, clock, db, directory, requests

STANDARD_GRANT, ONBOARDING, OFFBOARDING = "standard_grant", "onboarding", "offboarding"
NONSTANDARD, OPERATOR_REVIEW, BULK_CHANGE = "nonstandard_request", "operator_review", "bulk_change"
INTENTS = ("process_requests", "bulk_change", "question")
PROMPTS = Path(__file__).parent / "prompts"
BOUND = Bound()
HISTORY_TURNS = 3                       # earlier messages the operator router sees; U5's loop sees this many turns

READS = ["directory_get_employee", "directory_search_employees", "access_list_memberships", "catalog_search_groups",
         "policy_check_access", "requests_get", "requests_list_today"]
U4_WRITES = ["access_grant", "access_revoke", "access_propose_change", "reply_draft", "manager_confirmation_draft",
             "requests_set_status"]
TOOLS = {
    OPERATOR_REVIEW: READS + ["directory_list_contractors_ended"],
    # U4 asks the stranger who alone has the fact: the person's manager, on a request made on
    # someone else's behalf (Milestone 2, Part 1, task 5). U6 asks no one.
    NONSTANDARD: READS + U4_WRITES + ["channel_ask_template"],
    BULK_CHANGE: READS + U4_WRITES + ["directory_list_contractors_ended", "access_revoke_all"],
}

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
PROCESS_STOPPED = "process_stopped"     # the end reason of a read-only run that a stopped process cut
PAUSED_WORDS = {"stopped": "stopped (use /resume or /cancel)", "waiting_reply": "waiting for a reply",
                "waiting_approval": "waiting for your approval", "held": "held for a person"}


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
    question about work already done. First, each run of the session that a stopped
    process left as running carries on (_carry_on_crashed)."""
    carried = _past_deadline(session_id) + _carry_on_crashed(session_id)
    earlier = session.history(session_id)[-HISTORY_TURNS:]
    first = _session_start(session_id) if not earlier else None
    session.add(session_id, "operator", text)
    intent, request_ids, reason = route_operator(session_id, text, earlier)
    runtime.trace(session_id).event("route", scope="operator", intent=intent, request_ids=request_ids, reason=reason)
    if intent == "process_requests":
        reply = process_requests(session_id, request_ids)
    elif intent == "bulk_change":
        reply = _as_reply(bulk_change(session_id, text))
    else:
        _agent_path(OPERATOR_REVIEW)
        content = _with_history(session_id, text)
        subjects = _subjects(text)
        reply = _as_reply(_in_run(session_id, OPERATOR_REVIEW, content, lambda run_key: loop.run_loop(
            session_id, use_case=OPERATOR_REVIEW, system=_prompt(OPERATOR_REVIEW), user_content=content,
            tools=TOOLS[OPERATOR_REVIEW], bound=BOUND, run_key=run_key,
            memory=memory.for_call(session_id, subjects, OPERATOR_REVIEW, run_key=run_key)),
            notice=text, subjects=subjects))
    if first is not None:
        carried = [first, *carried]
    after = _after_turn(session_id)
    if after:
        reply = Reply("\n".join([reply.text, *(r.text for r in after)]).strip(),
                      list(reply.actions) + [a for r in after for a in r.actions],
                      reply.waiting_for or next((r.waiting_for for r in after if r.waiting_for), None))
    if carried:
        waits = [reply.waiting_for] + [r.waiting_for for r in carried]
        reply = Reply("\n".join([*(r.text for r in carried), reply.text]).strip(),
                      [a for r in carried for a in r.actions] + list(reply.actions),
                      next((w for w in waits if w), None))
    session.add(session_id, "agent", reply.text)
    return reply


def _session_start(session_id: str) -> Reply | None:
    """The first turn of a console session, before its first model call: read the memory
    store (memory.read_for_session writes memory_read) and the runs that still wait, and show
    the operator what was found, with each record's source and age. None when there is nothing.
    In a run.py run, only the runs of this run count."""
    records = memory.read_for_session(session_id)
    in_run = runtime.current_run() is not None
    waits = [cp for cp in checkpoints.waiting() if not in_run or cp.session_id == session_id]
    if not records and not waits:
        return None
    lines = []
    if records:
        lines.append("From earlier sessions, our memory holds:")
        lines += [f"- {memory.as_line(r)}" for r in records]
    if waits:
        lines.append("Runs that still wait:")
        lines += [f"- run {cp.run_key} ({cp.use_case}): {PAUSED_WORDS.get(cp.status, cp.status)}"
                  + (f" on {cp.waiting_for.get('thread_id')}" if (cp.waiting_for or {}).get("thread_id") else "")
                  for cp in waits]
    return Reply("\n".join(lines))


def _subjects(text: str) -> list[str]:
    """The people and groups that an operator's message names: by ID, or a person by full name."""
    ids = [i.upper() for i in (*EMPLOYEE_ID.findall(text), *GROUP_ID.findall(text))]
    return list(dict.fromkeys(ids + people_named(text, exclude="")))


def _after_turn(session_id: str) -> list[Reply]:
    """When the turn ends, the messages that came mid-task go on (inbox.after_turn): the
    replacement first, as a new message, with what the replaced run did and the /rollback that
    undoes it; then each message that waited, as a new message, in order."""
    out = []
    for said in inbox.after_turn(session_id):
        if said["way"] == inbox.REPLACE:
            out.append(_what_it_did(said["run_key"]))
        out.append(handle_operator(session_id, said["text"]))
    return out


def _what_it_did(run_key: str) -> Reply:
    """What a replaced run wrote (its journal), newest last, so the operator can pick what to undo."""
    cp = checkpoints.load(run_key)
    done = [f"- {e['tool']} ({', '.join(f'{k}={v}' for k, v in sorted((e.get('args') or {}).items()) if k != 'body')})"
            for e in sorted((cp.completed if cp else {}).values(), key=lambda e: e.get("at") or "")]
    head = f"Run {run_key} stopped, because your message replaced its task."
    if not done:
        return Reply(f"{head} It wrote nothing.")
    run_no = run_key.rpartition("#")[2]
    return Reply("\n".join([f"{head} It had written:", *done, f"/rollback {run_no} undoes what can be undone."]))


def _past_deadline(session_id: str) -> list[Reply]:
    """The operator's next turn ends each run that waited for a stranger's reply past its
    deadline (bounds.REPLY_WORKING_DAYS on the sandbox's clock): it ends reply_deadline, its
    request goes to the operator (pending_operator), and the reply says so. In the app, a
    run of any console session counts; in a run.py run, only the runs of this run."""
    now, in_run, out = clock.now(), runtime.current_run() is not None, []
    for cp in checkpoints.waiting("reply"):
        deadline = (cp.waiting_for or {}).get("deadline")
        if (in_run and cp.session_id != session_id) or not deadline or now <= deadline:
            continue
        thread, rid = cp.waiting_for.get("thread_id"), (cp.findings or {}).get("request_id")
        runtime.trace(cp.session_id).event("reply_deadline", run_key=cp.run_key, thread_id=thread,
                                           deadline=deadline, now=now)
        checkpoints.end_turn(cp, bounds.REPLY_DEADLINE)
        left = _leave_for_operator(cp.session_id, rid, LoopResult("", [], bounds.REPLY_DEADLINE, cp.current_step or 0),
                                   cp.run_key, use_case=cp.use_case) if rid else ""
        out.append(Reply(f"Run {cp.run_no}{' for ' + rid if rid else ''} waited for a reply on {thread} until "
                         f"{deadline}, and none came, so it ended. {left}".strip(), [f"ended run {cp.run_key}"]))
    return out


def _carry_on_crashed(session_id: str) -> list[Reply]:
    """The operator's next turn in the session continues each run that a stopped process
    left as running (checkpoints.crashed): a crash, or a restart of the app. Nothing does
    this at start-up, so a person always starts it. A run in the exit mode (U5) only reads
    and saved no step, so it ends, and the operator asks again."""
    out = []
    for cp in checkpoints.crashed(session_id):
        runtime.trace(session_id).event("run_crashed", run_key=cp.run_key, use_case=cp.use_case,
                                        current_step=cp.current_step, process=cp.process)
        if checkpoints.mode_for(cp.use_case) == "exit":
            checkpoints.end_turn(cp, PROCESS_STOPPED)
            out.append(Reply(f"Run {cp.run_no} ({cp.use_case}) stopped when the process stopped. It only read, "
                             "so ask again if you still need its answer."))
        else:
            out.append(checkpoints.resume(cp.run_key, {"kind": "restart"}))
    return out


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
    built = context.assemble("router", system=_prompt("route_operator"), session_id=session_id, run_key=None,
                             task=content, history=[], tool_names=None)
    if built.unfit:
        return "question", [], "the routing call did not fit the context budget, so the read-only path"
    response = runtime.client(session_id).create(system=built.system, messages=built.messages, tools=built.tools)
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
        return _in_run(session_id, BULK_CHANGE, text,
                       lambda run_key: executor.run_planned(session_id, text, bound=BOUND, run_key=run_key),
                       path="planned")
    content, subjects = _with_history(session_id, text), _subjects(text)
    return _in_run(session_id, BULK_CHANGE, content, lambda run_key: loop.run_loop(
        session_id, use_case=BULK_CHANGE, system=_prompt(BULK_CHANGE), user_content=content,
        tools=TOOLS[BULK_CHANGE], bound=BOUND, run_key=run_key,
        memory=memory.for_call(session_id, subjects, BULK_CHANGE, run_key=run_key)),
        path="loop", notice=text, subjects=subjects)


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
    waits: list[str] = []
    for position, item in enumerate(todo):
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
            paused = _paused_run(session_id, item["id"])
            if paused is not None:
                where = f"run {paused.run_no}" if paused.session_id == session_id else f"run {paused.run_key}"
                lines.append(f"{item['id']}: {where} is {PAUSED_WORDS.get(paused.status, paused.status)}, "
                             "so this turn does not start it again.")
                continue
            runtime.trace(session_id).progress(f"Working on {item['id']}.")
            result = handle_request(session_id, item)
            lines.append(result.text)
            actions += result.actions
            if result.waiting_for:
                waits.append(result.waiting_for)
            if result.end_reason == bounds.REPLACED:
                rest = [t["id"] for t in todo[position + 1:]]
                if rest:
                    lines.append(f"Not started, because your new message replaced the task: {', '.join(rest)}.")
                break
        finally:
            requests.release(item["id"], session_id)
    # the turn names the first thread a run waits on: run.py lets that thread's stranger answer
    return Reply("\n".join(lines), actions, waits[0] if waits else None)


def handle_request(session_id: str, request: dict) -> LoopResult:
    """One request, on the path control.<use_case> names for its use case."""
    route = route_request(session_id, request)
    path = runtime.switch(f"control.{route.use_case}")
    requests.set_route(request["id"], f"{path}:{route.use_case}", stamp=db.current_stamp())
    runtime.trace(session_id).event("route", scope="request", request_id=request["id"],
                                    route=f"{path}:{route.use_case}", why=route.why)
    case, rid = _case(request, route), request["id"]
    found = dict(request_id=rid, route=f"{path}:{route.use_case}", group_id=route.group_id, fields=route.fields,
                 notice=f"{route.use_case} for {rid}")
    if path == "workflow" and route.use_case == STANDARD_GRANT:
        return _in_run(session_id, STANDARD_GRANT, case, lambda run_key: workflows.standard_grant(
            session_id, rid, route.group_id, run_key=run_key), **found)
    if path == "workflow" and route.use_case == ONBOARDING:
        return _in_run(session_id, ONBOARDING, case, lambda run_key: workflows.onboarding(
            session_id, rid, route.fields, run_key=run_key), **found)
    if path == "workflow" and route.use_case == OFFBOARDING:
        return _in_run(session_id, OFFBOARDING, case, lambda run_key: workflows.offboarding(
            session_id, rid, route.fields, run_key=run_key), **found)

    subjects = [s for s in (request["requester_id"], *people_named(request["text"], exclude=request["requester_id"] or ""),
                            route.group_id) if s]

    def agent(run_key: str) -> LoopResult:
        result = loop.run_loop(session_id, use_case=NONSTANDARD, system=_prompt(NONSTANDARD), user_content=case,
                               tools=TOOLS[NONSTANDARD], bound=BOUND, request_id=rid, run_key=run_key,
                               memory=memory.for_call(session_id, subjects, NONSTANDARD, run_key=run_key))
        left = _leave_for_operator(session_id, rid, result, run_key)
        text = " ".join(t for t in (f"{rid} (agent):", route.note, result.text, left) if t)
        return LoopResult(text, result.actions, result.end_reason, result.steps, waiting_for=result.waiting_for)

    return _in_run(session_id, NONSTANDARD, case, agent, **found, subjects=subjects)


def _paused_run(session_id: str, request_id: str):
    """The run of this request that is stopped or waits, or None. A turn does not start
    such a request again: /resume, the reply or the approval continues it. In the app, a
    run of any console session counts. In a run.py run, only the runs of this run count,
    because each run.py run seeds the same request IDs again."""
    in_run = runtime.current_run() is not None
    return next((cp for cp in checkpoints.waiting() if (cp.findings or {}).get("request_id") == request_id
                 and (not in_run or cp.session_id == session_id)), None)


def _in_run(session_id: str, use_case: str, task: str, work, *, notice: str | None = None,
            **findings) -> LoopResult:
    """One run: open it, do the work with its key, and save the run when the turn ends.
    `work(run_key)` gives the LoopResult.

    The findings are saved before the work starts, so a path that loads the run keeps them.
    `notice` names the run for the operator, with its /stop, before its first call
    (control.notice). The planned path gives its own notice. During the turn, a path or the
    approval gate can save the run itself (a plan, a wait for an approval). The router then
    keeps that save, and does not write its own older copy over it. An error in the work
    saves the run as failed, and the error goes on to the caller."""
    cp = checkpoints.new_run(session_id, use_case, task)
    cp.findings = {name: value for name, value in findings.items() if value not in (None, {}, "")}
    cp.conversation = session.history(session_id)
    checkpoints.save(cp, mode="sync")
    if notice:
        control.notice(cp.run_key, notice)
    try:
        result = work(cp.run_key)
    except Exception:
        try:
            _settle(cp, "error")
        except Exception:  # noqa: BLE001  (the first error is the one that the caller must see)
            pass
        raise
    _settle(cp, result.end_reason)
    return result


def _end_of(run_key: str, end_reason: str) -> str:
    """The end reason to save. A run that is not done, and that the operator stopped or
    cancelled during the turn, is saved as stopped or cancelled, so that /resume finds it."""
    if end_reason in checkpoints.ENDS_DONE:
        return end_reason
    if control.state(run_key) == control.CANCELLED:
        return control.END_CANCELLED
    if control.stopped(run_key):
        return control.END_STOPPED
    return end_reason


def resume(cp, event: dict) -> Reply:
    """checkpoints.resume's way back into a run of the workflows or of the loop
    (checkpoints.RESUMERS): after the operator's answer to an approval, a stranger's reply,
    /resume after a stop, or the operator's answer to an escalation. The run continues
    under its own key, so a write that it made before gets the same key, and the target
    keeps its first result.

      - A denied approval ends the run with approval_denied, as on the planned path.
      - /resume of a run that waits for a reply reads the thread. If the reply came during
        the stop, the run continues with it. If not, the run waits again.
      - Otherwise the run is saved as running, the operator gets the notice that it resumed,
        and the workflow runs again, or the loop carries on (loop.carry_on).

    When the turn ends, the run is saved as in _in_run, and a request that the agent did
    not finish goes to the operator."""
    findings, trace = cp.findings or {}, runtime.trace(cp.session_id)
    rid, wait = findings.get("request_id"), cp.waiting_for or {}
    if event.get("kind") == "stop_lifted" and wait.get("kind") == "reply":
        heard = _reply_on(wait.get("thread_id"))
        if heard is None:
            trace.event("resumed", run_key=cp.run_key, event_kind="stop_lifted", waits_again=wait.get("thread_id"))
            return Reply(f"The stop on run {cp.run_no} is lifted. It waits for a reply on {wait.get('thread_id')} "
                         "again.", waiting_for=wait.get("thread_id"))
        event = {"kind": "reply", "thread_id": wait.get("thread_id"), "text": heard}
    control.notice(cp.run_key, f"{cp.use_case} for {rid}" if rid else cp.task, resumed=True)
    trace.event("resumed", run_key=cp.run_key, event_kind=event.get("kind"), use_case=cp.use_case)
    if event.get("kind") == "approval" and not event.get("approved"):
        detail = f"the operator denied {event.get('approval_id')}" + (f" ({event['note']})" if event.get("note") else "")
        trace.end(approvals.APPROVAL_DENIED, detail)
        result = LoopResult(f"Run {cp.run_no}: {detail}, so its call did not run.", [], approvals.APPROVAL_DENIED,
                            cp.current_step or 0)
        checkpoints.end_turn(cp, approvals.APPROVAL_DENIED)
    else:
        cp.status, cp.waiting_for = checkpoints.RUNNING, None
        checkpoints.save(cp, mode="sync")
        try:
            result = _again(cp, event)
        except Exception:
            try:
                _settle(cp, "error")
            except Exception:  # noqa: BLE001  (the first error is the one that the caller must see)
                pass
            raise
    text = result.text
    if rid and (cp.use_case == NONSTANDARD or result.end_reason == approvals.APPROVAL_DENIED):
        left = _leave_for_operator(cp.session_id, rid, result, cp.run_key, use_case=cp.use_case)
        path = "agent" if cp.use_case == NONSTANDARD else cp.use_case
        text = " ".join(t for t in (f"{rid} ({path}):", result.text, left) if t)
    _settle(cp, result.end_reason)
    return Reply(text, result.actions, result.waiting_for)


def _again(cp, event: dict) -> LoopResult:
    """The path of the run, again: a workflow from its first step (its writes keep their
    keys), or the loop from its saved messages."""
    found = cp.findings or {}
    if cp.use_case == STANDARD_GRANT:
        return workflows.standard_grant(cp.session_id, found.get("request_id"), found.get("group_id"),
                                        run_key=cp.run_key)
    if cp.use_case == ONBOARDING:
        return workflows.onboarding(cp.session_id, found.get("request_id"), found.get("fields") or {},
                                    run_key=cp.run_key)
    if cp.use_case == OFFBOARDING:
        return workflows.offboarding(cp.session_id, found.get("request_id"), found.get("fields") or {},
                                     run_key=cp.run_key)
    return loop.carry_on(cp, event, system=_prompt(cp.use_case), tools=TOOLS[cp.use_case], bound=BOUND,
                         memory=memory.for_call(cp.session_id, found.get("subjects"), cp.use_case, run_key=cp.run_key))


def _reply_on(thread_id: str | None) -> str | None:
    """The stranger's words on the thread after our last message there, or None. In a run.py
    run, only the messages of this run count."""
    if not thread_id:
        return None
    run = runtime.current_run()
    found = channel.messages(thread_id, run.run_id if run is not None else None)
    ours = [m.id for m in found if m.sender in channel.OURS]
    after = [m.text for m in found if m.sender not in channel.OURS and m.id > (ours[-1] if ours else 0)]
    return "\n".join(after) or None


def _settle(cp, end_reason: str) -> None:
    """Save the run when its turn ends, with the status that the end reason gives. A path or
    the approval gate can save the run during the turn (a plan, a wait for an approval): that
    save stays, and the router does not write its own older copy over it."""
    latest = checkpoints.load(cp.run_key) or cp
    if latest.status == checkpoints.RUNNING:
        checkpoints.end_turn(latest, _end_of(cp.run_key, end_reason))


def _leave_for_operator(session_id: str, request_id: str, result: LoopResult, run_key: str | None = None,
                        use_case: str = NONSTANDARD) -> str:
    """Our code, not the model, sets a request the agent did not finish to pending_operator."""
    status = requests.get(request_id)["status"]
    if status not in ("new", "in_progress"):
        return ""
    note = f"The agent ended ({result.end_reason}) with the request still {status}."
    ctx = CallContext(session_id, result.steps + 1, "workflow", use_case, request_id=request_id,
                      run_key=run_key, origin="workflow")
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
    built = context.assemble("router", system=_prompt("route_request"), session_id=session_id, run_key=None,
                             task=content, history=[], tool_names=None)
    if built.unfit:
        return {}           # no description: decide() sends the request to U4, and the trace has the context line
    response = runtime.client(session_id).create(system=built.system, messages=built.messages, tools=built.tools)
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
    return Reply(result.text, result.actions, result.waiting_for)


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
