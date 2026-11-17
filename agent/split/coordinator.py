"""U6 as a split, on the m2-split branch (Part 3): a coordinator and two roles, split by tool surface.

Owner: Shruti Tewaney. Runs when `topology` is `split` (agent/router.py: bulk_change).

    the coordinator   our code: it reads the facts (planner.build_context), records the end state
                      the sandbox must reach (checks.expected_end), calls each role in turn, takes
                      only returns that keep the contract, puts every note up as one approval at
                      the plan, sends them once approved, and reads the end state back (the cheap
                      check no role's own check replaces)
    the access role   one per contractor: Milestone 1's bounded loop (agent/loop.py) with the
                      identity tools alone (ACCESS_TOOLS). It cannot write to anyone
    the comms role    one per manager: one model call offered `reply_send` alone, given nothing but
                      the access roles' returns for that manager's contractors. It cannot change
                      access; its note is checked by the critique and approved by the operator
                      before the coordinator sends it

Every agent's calls go through the one registry and hook, so the policy, the keys and the stop hold
for all of them. Each role has its own run key (agent/checkpoints.py: new_run), named with its parent
on an `agent_start` line; its tool calls and model calls carry that key, so one trace shows who did
what and the tokens of each. One budget (checks.SharedBudget) is shared by them all.

What a return cannot carry is lost: the comms role sees only the access roles' `result`, never their
conversation, so a group a role read but did not record is a group no note can name.
"""

from __future__ import annotations

import json
import re

from agent import (approvals, checkpoints, context, control, critique, disclosure, loop, planner)
from agent.bounds import LOOP_TOKEN_BUDGET, Bound
from agent.loop import LoopResult
from agent.registry import CallContext, get_registry
from agent.split import checks, contract
from harness import runtime
from harness.trace import read_trace
from harness.turns import Reply
from policy import hook
from sandbox import db

USE_CASE = "bulk_change"
PROMPTS = planner.PROMPTS.parent / "split" / "prompts"
ACCESS_TOOLS = ["access_list_memberships", "access_revoke_all", "access_propose_change"]
COMMS_TOOLS = ["reply_send"]
MAX_NOTE_TRIES = 2

PLAN_DONE, NOTHING_TO_DO = "plan_done", "nothing_to_do"
AWAITING_APPROVAL, APPROVAL_DENIED = approvals.AWAITING_APPROVAL, approvals.APPROVAL_DENIED
STOPPED, SPLIT_BUDGET = control.END_STOPPED, checks.SPLIT_BUDGET
SPLIT_DIVERGENT = "split_divergent"          # every role returned, and the combined end state is wrong
STEP_HALTED = "step_halted"
ENDED_WELL = (PLAN_DONE, NOTHING_TO_DO, APPROVAL_DENIED)


class _End(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class SplitRun:
    def __init__(self, session_id: str, text: str, bound: Bound, run_key: str | None):
        self.sid, self.text, self.bound = session_id, text, bound
        self.run_key = run_key or checkpoints.new_run(session_id, USE_CASE, text).run_key
        self.trace = runtime.trace(session_id)
        self.client = runtime.client(session_id)
        self.registry = get_registry()
        self.budget = checks.SharedBudget(total=bound.token_budget)
        self.context: dict = {}
        self.expected: dict | None = None
        self.returns: list[dict] = []            # the accepted returns, in order
        self.rejected: list[str] = []            # the run keys of returns the contract refused
        self.notes: dict[str, dict] = {}         # manager -> reply_send's final arguments
        self.checked: list[str] = []
        self.asked: str | None = None            # the plan approval's ID
        self.actions: list[str] = []
        self.line0 = len(self.trace.lines())

    # -- the saved state --------------------------------------------------------------------
    def _state(self) -> dict:
        return {"text": self.text, "context": self.context, "expected": self.expected, "returns": self.returns,
                "rejected": self.rejected, "notes": self.notes, "checked": self.checked, "asked": self.asked,
                "actions": self.actions, "by_agent": self.budget.by_agent, "total": self.budget.total,
                "line0": self.line0}

    @classmethod
    def restore(cls, cp) -> "SplitRun":
        state = (cp.findings or {}).get("split") or {}
        run = cls(cp.session_id, state.get("text") or cp.task, Bound(), cp.run_key)
        for name in ("context", "expected", "returns", "rejected", "notes", "checked", "asked", "actions", "line0"):
            if name in state:
                setattr(run, name, state[name])
        run.budget = checks.SharedBudget(total=state.get("total") or run.budget.total,
                                         by_agent=dict(state.get("by_agent") or {}))
        return run

    def _save(self, status: str) -> None:
        cp = checkpoints.load(self.run_key)
        if cp is None:
            return
        cp.status = status
        if status != checkpoints.WAITING_APPROVAL:
            cp.waiting_for = None
        cp.plan = {"topology": "split", "notes_to": sorted(self.notes)}   # resumes through executor.resume
        cp.findings = {**(cp.findings or {}), "split": self._state()}
        checkpoints.save(cp, mode="sync")

    # -- the turn ---------------------------------------------------------------------------
    def run(self) -> LoopResult:
        self.trace.event("bound", use_case=USE_CASE, path="split", token_budget=self.budget.total,
                         max_steps=self.bound.max_steps, wall_clock_s=self.bound.wall_clock_s)
        try:
            control.notice(self.run_key, self.text)
            self.context = planner.build_context(self.text)
            people = list(self.context.get("active_membership_counts") or {})
            if not people:
                raise _End(NOTHING_TO_DO, f"no contractor whose contract ended before {self.context.get('today')} "
                                          "still holds access")
            self.expected = checks.expected_end(self.context)
            self._save(checkpoints.RUNNING)
            for person in people:
                self._access(person)
            for manager, theirs in self._managers().items():
                self._comms(manager, theirs)
            self._ask()
            reason, detail = PLAN_DONE, "all roles returned"          # not reached: _ask ends the turn
        except _End as end:
            reason, detail = end.reason, end.detail
        return self._finish(reason, detail)

    def resume(self, event: dict) -> LoopResult:
        self.trace.event("resumed", run_key=self.run_key, event_kind=event.get("kind"), path="split")
        control.notice(self.run_key, self.text, resumed=True)
        try:
            if event.get("kind") == "approval" and not event.get("approved"):
                raise _End(APPROVAL_DENIED, f"the operator denied {event.get('approval_id')}; no note was sent")
            found = approvals.get(self.asked) if self.asked else None
            if not found or found["status"] != approvals.APPROVED:
                self._ask()                                          # withdrawn by a stop: ask again
            self._send()
            wrong = checks.end_state(self.expected, db.current_stamp())
            self.trace.event("end_state", run_key=self.run_key, passed=not wrong, problems=wrong[:20])
            if wrong:
                raise _End(SPLIT_DIVERGENT, "; ".join(wrong)[:500])
            reason, detail = PLAN_DONE, f"{len(self.returns)} returns accepted, {len(self.notes)} notes sent"
        except _End as end:
            reason, detail = end.reason, end.detail
        return self._finish(reason, detail)

    def _finish(self, reason: str, detail: str) -> LoopResult:
        if reason == AWAITING_APPROVAL:
            self._save(checkpoints.WAITING_APPROVAL)
        elif reason == STOPPED:
            self._save(checkpoints.STOPPED)
        else:
            self._save(checkpoints.DONE if reason in ENDED_WELL else checkpoints.FAILED)
        self.budget.record(self.sid, self.run_key)
        self.trace.end(reason, detail[:500])
        return LoopResult(self._reply(reason, detail), list(self.actions), reason, 0)

    # -- the roles --------------------------------------------------------------------------
    def _guard(self) -> None:
        if control.stopped(self.run_key):
            raise _End(STOPPED, "the operator stopped this run")
        if self.budget.exhausted():
            raise _End(SPLIT_BUDGET, f"the shared budget of {self.budget.total:,} tokens is spent")

    def _child(self, role: str, task: str) -> str:
        key = checkpoints.new_run(self.sid, USE_CASE, task).run_key
        self.trace.event("agent_start", agent=key, parent=self.run_key, role=role, task=task)
        return key

    def _settle_child(self, key: str, done: bool) -> None:
        cp = checkpoints.load(key)
        if cp is not None and cp.status not in checkpoints.ENDED:
            cp.status, cp.waiting_for = (checkpoints.DONE if done else checkpoints.FAILED), None
            checkpoints.save(cp, mode="sync")

    def _lines(self) -> list[dict]:
        return read_trace(self.trace.path)[self.line0:]

    def _access(self, person: str) -> None:
        self._guard()
        task = f"Remove all access of {person}, whose contract ended."
        key = self._child("access", task)
        bound = Bound(max_steps=self.bound.max_steps, token_budget=max(self.budget.left, 1),
                      wall_clock_s=self.bound.wall_clock_s)
        ended = loop.run_loop(self.sid, use_case=USE_CASE, system=(PROMPTS / "access.md").read_text(),
                              user_content=f"{task} The employee ID: {person}.", tools=ACCESS_TOOLS, bound=bound,
                              run_key=key)
        lines = self._lines()
        result, not_determined = access_result(lines, key, person)
        status = ("out_of_budget" if ended.end_reason == LOOP_TOKEN_BUDGET
                  else "candidate" if not_determined else "found")
        if status == "out_of_budget":
            not_determined = []
        ret = contract.build("access", run_key=key, parent=self.run_key, task=task, status=status,
                             result=result if status != "out_of_budget" else {},
                             not_searched=[] if any(s["tool"] == "access_list_memberships"
                                                    for s in contract.searched(lines, key))
                             else [f"{person}'s memberships"], not_determined=not_determined, lines=lines)
        self.budget.charge(key, ret["spent"]["tokens"])
        self._settle_child(key, status == "found")
        if contract.accept(ret, lines, budget_left=self.budget.left):
            self.returns.append(ret)
            self.actions += [f"removed {g} from {person}" for g in result.get("removed", [])]
            self.actions += [f"kept protected {g} for {person}, proposed its removal ({p})"
                             for g, p in result.get("proposals", {}).items()]
        else:
            self.rejected.append(key)
        if status == "out_of_budget":
            raise _End(SPLIT_BUDGET, f"the access role for {person} ran out of the shared budget")

    def _managers(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for person, manager in (self.context.get("managers") or {}).items():
            if manager and person in (self.context.get("active_membership_counts") or {}):
                out.setdefault(manager, []).append(person)
        return out

    def _comms(self, manager: str, theirs: list[str]) -> None:
        self._guard()
        given = [r["result"] for r in self.returns if r["role"] == "access" and r["result"].get("employee_id") in theirs]
        task = f"Write the note to {manager} about {', '.join(theirs)}."
        key = self._child("comms", task)
        error, args = None, None
        for attempt in range(1, MAX_NOTE_TRIES + 1):
            request = (f"The manager: {manager}.\nThe access roles' results for their contractors:\n"
                       f"{json.dumps(given, indent=1)}" + (f"\n\nYour last call failed: {error}" if error else ""))
            body, error = self._note_call(key, request, attempt)
            if body is None:
                continue
            args = disclosure.stamp_call("reply_send", {"recipient_employee_id": manager, "body": body})
            wrong = critique.check_draft(recipient_employee_id=manager, request_id=None, body=body,
                                         stamp=db.current_stamp())
            self.trace.event("critique", round=attempt, verdict="fail" if wrong else "pass", reason=" ".join(wrong)[:600],
                             recipient=manager, request_id=None, stage="before_approval", run_key=key)
            if not wrong:
                self.checked.append(f"{manager}: passed on round {attempt}")
                break
            error, args = (f"The note was not sent (round {attempt} of {MAX_NOTE_TRIES}): {' '.join(wrong)} "
                           "Revise it and call reply_send again."), None
        lines = self._lines()
        held = args is None
        ret = contract.build("comms", run_key=key, parent=self.run_key, task=task,
                             status="candidate" if held else "found",
                             result={"recipient_employee_id": manager, "body": args["body"] if args else ""},
                             not_determined=[f"a note the critique accepts: {error}"] if held else [], lines=lines)
        self.budget.charge(key, ret["spent"]["tokens"])
        self._settle_child(key, not held)
        if not contract.accept(ret, lines, budget_left=self.budget.left):
            self.rejected.append(key)
            return
        self.returns.append(ret)
        if held:
            raise _End(critique.CRITIQUE_HELD, f"the note to {manager} was held and not sent: {error}")
        self.notes[manager] = args

    def _note_call(self, key: str, request: str, attempt: int) -> tuple[str | None, str | None]:
        built = context.assemble("executor", system=(PROMPTS / "comms.md").read_text(), session_id=self.sid,
                                 run_key=key, task=None, history=[{"role": "user", "content": request}],
                                 tool_names=COMMS_TOOLS, notes=False, registry=self.registry, use_case=USE_CASE)
        if built.unfit:
            raise _End(context.BUDGET_UNFIT, f"the comms call needs {built.tokens} tokens")
        response = self.client.create(system=built.system, messages=built.messages, tools=built.tools)
        self.trace.model_call(attempt, response, purpose="comms", run_key=key)
        calls = [c for c in response.tool_calls if self.registry.resolve(c.name) == "reply_send"]
        if not calls:
            return None, "You called no tool: call reply_send once, with the note as body."
        body = str((calls[0].input or {}).get("body") or "").strip()
        return (body, None) if body else (None, "The body is empty: write the note.")

    # -- the plan approval and the sends ----------------------------------------------------
    def _ctx(self, n: int) -> CallContext:
        return CallContext(self.sid, n, "executor", USE_CASE, plan_step=n, run_key=self.run_key, origin="agent")

    def _ask(self) -> None:
        if control.stopped(self.run_key):
            raise _End(STOPPED, "the operator stopped this run before its plan review was put up")
        if not self.notes:
            raise _End(STEP_HALTED, "no note to put up: every comms return was refused")
        listed = [{"plan_step": n, "tool": "reply_send", "args": args,
                   "action_class": hook.classify("reply_send", args, self._ctx(n))}
                  for n, args in enumerate(self.notes.values(), start=1)]
        done = [f"{r['result']['employee_id']}: removed {len(r['result'].get('removed', []))} group(s)"
                + (f", kept {', '.join(r['result']['kept_protected'])} and proposed its removal"
                   if r["result"].get("kept_protected") else "")
                for r in self.returns if r["role"] == "access"]
        self.asked = approvals.request_plan(self._ctx(1), listed, {
            "version": 1, "use_case": f"{USE_CASE} (split)", "instruction": self.text, "done": done,
            "critique": "; ".join(self.checked)})
        raise _End(AWAITING_APPROVAL, f"{len(listed)} note(s) wait for the operator's approval {self.asked}")

    def _send(self) -> None:
        for n, (manager, args) in enumerate(self.notes.items(), start=1):
            result = self.registry.call("reply_send", args, self._ctx(n))
            if result.is_error:
                if control.stopped(self.run_key):
                    raise _End(STOPPED, f"the note to {manager} was refused: the operator stopped this run")
                raise _End(STEP_HALTED, f"the note to {manager} failed: {result.content[:200]}")
            self.actions.append(f"sent the approved note to {manager} (message {(result.data or {}).get('message_id')})")

    def _reply(self, reason: str, detail: str) -> str:
        head = {PLAN_DONE: "Done, as a split: every role's return kept the contract, and the end state checks out.",
                NOTHING_TO_DO: f"There is nothing to do: {detail}.",
                AWAITING_APPROVAL: f"Waiting for you: {detail}. Answer it on /approvals; nothing is sent before.",
                APPROVAL_DENIED: f"Stopped: {detail}. What ran before it stays.",
                SPLIT_DIVERGENT: f"Every role returned, but the end state is wrong: {detail}",
                SPLIT_BUDGET: f"Stopped at the shared budget: {detail}.",
                STOPPED: f"Stopped: {detail}."}.get(reason, f"Stopped ({reason}): {detail}")
        lines = [head]
        if self.actions:
            lines.append("What was done:\n" + "\n".join(f"- {a}" for a in self.actions))
        if self.rejected:
            lines.append(f"Returns refused by the contract: {', '.join(self.rejected)}.")
        return "\n\n".join(lines)


def access_result(lines: list[dict], key: str, person: str) -> tuple[dict, list[str]]:
    """An access role's result, built by our code from its own tool results; and what it left open."""
    removed, kept, proposals, not_determined = [], [], {}, []
    revoked = False
    for x in lines:
        if x.get("type") != "tool_call" or x.get("run_key") != key or x.get("is_error"):
            continue
        data = _json(x.get("output"))
        if x.get("name") == "access_revoke_all" and data.get("employee_id") == person:
            revoked = True
            removed, kept = list(data.get("removed") or []), list(data.get("kept_protected") or [])
        elif x.get("name") == "access_propose_change" and data.get("employee_id") == person:
            proposals[str(data.get("group_id"))] = data.get("pending_id")
    if not revoked:
        not_determined.append(f"whether {person}'s access was removed: no access_revoke_all of theirs succeeded")
    for group in kept:
        if group not in proposals:
            not_determined.append(f"a proposal to remove {person}'s protected {group}: none was written")
    return {"employee_id": person, "removed": removed, "kept_protected": kept, "proposals": proposals}, not_determined


def _json(text) -> dict:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", str(text or ""), re.S)
        try:
            value = json.loads(match.group(0)) if match else {}
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def run_split(session_id: str, text: str, *, bound: Bound, run_key: str | None = None) -> LoopResult:
    """U6 as a split (topology = split). Writes its own trace.end."""
    return SplitRun(session_id, text, bound, run_key).run()


def resume(cp, event: dict) -> Reply:
    """executor.resume hands a split run here: after the operator's answer, /resume, or a restart."""
    result = SplitRun.restore(cp).resume(event)
    return Reply(result.text, actions=list(result.actions))
