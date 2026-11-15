"""The executor for the planned agent: it runs the plan's steps in order, fills
"$fill" arguments, checks each step's outcome and the checkpoint, and refuses any
call off the plan (Lecture 2, slide 16).

Owner: Shruti Tewaney. Ported from Shruti's Assignment 2 (ways/planned.py: the
Executor, MAX_REPLANS, MAX_REVISIONS, the checkpoint and the off-plan refusal), on
our registry, bound and end reasons.

The run, in order:

  1. Plan: the planner sees the operator's instruction, the tool list, and IDs and
     counts our code read (planner.build_context). A plan that fails the validator
     is re-planned with its messages, at most MAX_REPLANS times; then the run ends
     plan_rejected, with no tool call and no write stamped with this run. A plan the
     planner submits with no steps says the instruction is not the one bulk change
     these tools support (removing ended contractors' access): the run ends
     unsupported_instruction at once, with nothing run and no re-plan.
  2. Log: trace.plan(1, plan) before the first tool call; orphan_flag if any.
  3. Execute the steps in order through the registry, each call naming its plan
     step. A step with "$fill" arguments gets one model call (purpose="fill") offered
     that step's tool alone; a call to any other tool is refused and traced
     (off_plan), and the step is filled again, once, then halted. A step that fails
     permanently, or whose observable outcome does not hold, halts the run.
  4. The checkpoint, after every access_list_memberships step: our code fails it
     when a membership is in a protected group and no later step proposes its
     removal. Only then is the plan revised, from step results alone, validated by
     the same rules and logged as a new version, at most MAX_REVISIONS times a run.
  5. The gated steps, last: each manager's note (reply_send, manager_note/plan_listed)
     is filled, stamped with the disclosure line and checked by the critique before the
     operator sees it. With approval.bulk_change = plan, every remaining note is prepared
     first and one approval lists them all (agent/approvals.py: request_plan); per_action
     asks for each note as it comes. Either way the turn ends awaiting_approval, and the
     operator's answer resumes the run here (resume), from its checkpoint.
  6. The diff (plan_diff) at the end of every run that had a plan; the repeat check
     on every call. The executor writes trace.end itself, with one of its reasons.

The run is checkpointed (agent/checkpoints.py) after the plan and after every step, so
a run waiting for the operator survives a restart. Until the router passes a run key,
the executor opens its own run.
"""

from __future__ import annotations

import json
import re
import time

from agent import approvals, checkpoints, context, critique, detectors, disclosure, planner, reviser, validator
from agent.bounds import LOOP_TOKEN_BUDGET, STEP_LIMIT, WALL_CLOCK, Bound
from agent.loop import LoopResult
from agent.registry import CallContext, get_registry
from harness import runtime
from harness.turns import Reply
from policy import hook
from sandbox import db

USE_CASE = "bulk_change"
MAX_REPLANS = 2            # re-plans after a rejected plan, then plan_rejected: A2's bound, which 1 of 65 needed
MAX_REVISIONS = 2          # revisions a run, accepted and rejected alike: one per protected surprise, and a retry
MAX_FILL_ATTEMPTS = 2      # tries at one step the model fills: the first and one retry (= the critique's rounds)
CHECKPOINT = "protected_memberships_have_a_step"
CHECKPOINT_TOOL = "access_list_memberships"
# Not orphans when nothing later uses them: the writes, whose effect is their point,
# and the membership listings, which the checkpoint reads.
ORPHAN_EXEMPT = (CHECKPOINT_TOOL, "access_revoke", "access_revoke_all", "access_propose_change", "reply_draft",
                 "reply_send")
GATED = planner.SENDS      # the steps the operator approves first: at the plan or per action
RESULT_CHARS = 3000        # the most of one dependency's result a fill call sees
RECORD_ID = re.compile(r"SYN-[A-Z]+-[A-Z0-9]+(?:-[A-Z0-9]+)*")

PLAN_REJECTED = "plan_rejected"
REVISION_BOUND = "revision_bound"
PLAN_DONE = "plan_done"
STEP_HALTED = "step_halted"
NOTHING_TO_DO = "nothing_to_do"
UNSUPPORTED = "unsupported_instruction"
AWAITING_APPROVAL = approvals.AWAITING_APPROVAL
APPROVAL_DENIED = approvals.APPROVAL_DENIED
ENDED_WELL = (PLAN_DONE, NOTHING_TO_DO, UNSUPPORTED, APPROVAL_DENIED)   # the run is done, not failed


class _End(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def outcome_miss(expect: dict, data) -> str | None:
    """Why a step's observable outcome does not hold in its result, or None."""
    kind, field, value = expect.get("kind"), expect.get("field", ""), expect.get("value", "")
    if not isinstance(data, dict):
        return "the result is not an object"
    if kind == "record_written":
        return None if data.get("pending_id") else "no pending record was written"
    if field not in data:
        return f"the result has no field {field!r}"
    found = data[field]
    if kind in ("count_equals", "non_empty") and not isinstance(found, list):
        return f"{field} is not a list"
    if kind == "count_equals" and len(found) != int(value):
        return f"expected {value} items in {field}, found {len(found)}"
    if kind == "non_empty" and not found:
        return f"{field} is empty"
    if kind == "field_equals" and str(found).lower() != str(value).lower():
        return f"expected {field} = {value!r}, found {found!r}"
    return None


def summarize(step: dict, result) -> dict:
    """A step's result as the reviser may see it: status, counts and record IDs."""
    data = result.data if isinstance(result.data, dict) else {}
    ids = sorted(set(RECORD_ID.findall(json.dumps(data, default=str))))
    out = {"id": step["id"], "tool": step["tool"], "status": "failed" if result.is_error else "done",
           "counts": {k: len(v) for k, v in data.items() if isinstance(v, list)}, "ids": ids[:40]}
    if step["tool"] == CHECKPOINT_TOOL:
        out["protected"] = [m["group_id"] for m in data.get("memberships", []) if m.get("protected")]
    if result.is_error:
        out["error_class"] = result.error_class
    return out


class PlannedRun:
    def __init__(self, session_id: str, operator_text: str, bound: Bound, run_key: str | None = None):
        self.sid, self.text, self.bound = session_id, operator_text, bound
        self.run_key = run_key                  # C3.1, from the caller's checkpoints.new_run; on every call
        self.trace = runtime.trace(session_id)
        self.client = runtime.client(session_id)
        self.registry = get_registry()
        self.tracker = detectors.RepeatTracker()
        self.n = 0                              # model calls this turn, against bound.max_steps
        self.started = time.monotonic()
        self.tokens_at_start = self.client.total_tokens
        self.line0 = len(self.trace.lines())    # this turn's lines start here, for the diff
        self.context: dict = {}
        self.plan: dict | None = None
        self.version = 0
        self.done: list[int] = []
        self.data: dict[int, dict] = {}
        self.results: dict[int, dict] = {}
        self.revisions = 0
        self.actions: list[str] = []
        self.ready: dict[int, dict] = {}        # a gated step's final arguments: filled, stamped, checked
        self.asked: dict | None = None          # the plan approval: {"approval_id", "version", "steps"}
        self.checked: list[str] = []            # the critique's verdict on each note, for the screen

    # -- the checkpoint of the run ------------------------------------------------------------
    def _state(self) -> dict:
        return {"text": self.text, "context": self.context, "version": self.version, "done": self.done,
                "data": {str(k): v for k, v in self.data.items()},
                "results": {str(k): v for k, v in self.results.items()},
                "revisions": self.revisions, "actions": self.actions,
                "ready": {str(k): v for k, v in self.ready.items()}, "asked": self.asked,
                "checked": self.checked, "line0": self.line0}

    @classmethod
    def restore(cls, cp, bound: Bound) -> "PlannedRun":
        state = (cp.findings or {}).get("executor") or {}
        run = cls(cp.session_id, cp.task, bound, run_key=cp.run_key)
        run.plan, run.context = cp.plan, state.get("context") or {}
        run.version, run.done = state.get("version", 1), list(state.get("done") or [])
        run.data = {int(k): v for k, v in (state.get("data") or {}).items()}
        run.results = {int(k): v for k, v in (state.get("results") or {}).items()}
        run.revisions, run.actions = state.get("revisions", 0), list(state.get("actions") or [])
        run.ready = {int(k): v for k, v in (state.get("ready") or {}).items()}
        run.asked, run.checked = state.get("asked"), list(state.get("checked") or [])
        run.line0 = state.get("line0", run.line0)
        return run

    def _open(self) -> None:
        if self.run_key is None:
            self.run_key = checkpoints.new_run(self.sid, USE_CASE, self.text).run_key

    def _save(self, status: str) -> None:
        cp = checkpoints.load(self.run_key) if self.run_key else None
        if cp is None:
            return
        cp.status = status
        if status != checkpoints.WAITING_APPROVAL:
            cp.waiting_for = None              # a waiting run keeps what approvals.request wrote
        cp.plan = self.plan
        nxt = self._next() if self.plan else None
        cp.current_step = nxt["id"] if nxt else None
        cp.steps = [{"n": sid, "status": "done", "tool": self._step(sid)["tool"]} for sid in self.done]
        cp.findings = {**(cp.findings or {}), "executor": self._state()}
        checkpoints.save(cp, mode="sync")

    # -- the bound, checked before every model call and every step ------------------------
    def _check_bound(self, model_call: bool) -> None:
        if model_call and self.n >= self.bound.max_steps:
            raise _End(STEP_LIMIT, f"{self.n} model calls this turn; the limit is {self.bound.max_steps}")
        used = self.client.total_tokens - self.tokens_at_start
        if model_call and used >= self.bound.token_budget:
            raise _End(LOOP_TOKEN_BUDGET, f"{used} tokens this turn; the budget is {self.bound.token_budget}")
        elapsed = time.monotonic() - self.started
        if elapsed >= self.bound.wall_clock_s:
            raise _End(WALL_CLOCK, f"{elapsed:.1f} s this turn; the wall clock is {self.bound.wall_clock_s:.0f} s")

    def model(self, purpose: str, system: str, messages: list[dict], tools: list[dict]):
        """One model call, bounded, built by context.assemble and traced: the planner, reviser
        and filler all use it. The planner and the reviser are assembled as kind "planner", so
        they never get notes, memory or a stranger's words; their one tool (submit_plan) is not
        a registry tool, so it is passed as given. A fill call is kind "executor", with the
        notes, offered its step's one tool through the registry."""
        self._check_bound(model_call=True)
        if purpose == "fill":
            names = [t["name"] for t in tools]
            built = context.assemble("executor", system=system, session_id=self.sid, run_key=self.run_key,
                                     task=None, history=messages, tool_names=[self.registry.resolve(n) or n
                                                                              for n in names],
                                     registry=self.registry, use_case=USE_CASE)
            tools = built.tools
        else:
            built = context.assemble("planner", system=system, session_id=self.sid, run_key=self.run_key,
                                     task=None, history=messages, tool_names=None, registry=self.registry,
                                     use_case=USE_CASE)
        if built.unfit:
            raise _End(context.BUDGET_UNFIT, f"the {purpose} call needs {built.tokens} tokens; "
                                             f"the budget is {context.budget()}")
        self.n += 1
        response = self.client.create(system=built.system, messages=built.messages, tools=tools)
        self.trace.model_call(self.n, response, purpose=purpose)
        return response

    # -- the run ---------------------------------------------------------------------------
    def _bound_line(self) -> None:
        self.trace.event("bound", use_case=USE_CASE, path="planned", max_steps=self.bound.max_steps,
                         token_budget=self.bound.token_budget, wall_clock_s=self.bound.wall_clock_s,
                         max_replans=MAX_REPLANS, max_revisions=MAX_REVISIONS)

    def run(self) -> LoopResult:
        self._bound_line()
        try:
            self._open()
            self.context = planner.build_context(self.text)
            if not self.context["active_membership_counts"]:
                raise _End(NOTHING_TO_DO, f"no contractor whose contract ended before {self.context['today']} "
                                          "still holds access, and the instruction names no one")
            self.plan = self._valid_plan()
            self.version = 1
            self.trace.plan(1, self.plan, use_case=USE_CASE, step_limit=self.context["step_limit"])
            self._flag_orphans()
            self._save(checkpoints.RUNNING)
            self._steps()
            reason, detail = PLAN_DONE, f"plan v{self.version}: all {len(self.plan['steps'])} steps ran"
        except _End as end:
            reason, detail = end.reason, end.detail
        return self._finish(reason, detail)

    def resume(self, event: dict) -> LoopResult:
        """Carry on after the operator's answer (or /resume, or the next turn after a crash)."""
        self._bound_line()
        self.trace.event("resumed", run_key=self.run_key, event_kind=event.get("kind"), done=list(self.done))
        try:
            if event.get("kind") == "approval" and not event.get("approved"):
                raise _End(APPROVAL_DENIED, f"the operator denied {event.get('approval_id')}"
                                            + (f" ({event['note']})" if event.get("note") else "")
                                            + "; nothing it listed was sent")
            self._save(checkpoints.RUNNING)
            self._steps()
            reason, detail = PLAN_DONE, f"plan v{self.version}: all {len(self.plan['steps'])} steps ran"
        except _End as end:
            reason, detail = end.reason, end.detail
        return self._finish(reason, detail)

    def _finish(self, reason: str, detail: str) -> LoopResult:
        report = None
        if reason == AWAITING_APPROVAL:
            self._save(checkpoints.WAITING_APPROVAL)
        else:
            if self.plan is not None:
                report = detectors.diff(self.plan, self.trace.lines()[self.line0:])
                self.trace.event("plan_diff", version=self.version, **report)
            self._save(checkpoints.DONE if reason in ENDED_WELL else checkpoints.FAILED)
        self.trace.end(reason, detail[:500])
        return LoopResult(text=self._reply(reason, detail, report), actions=self.actions, end_reason=reason,
                          steps=self.n)

    def _steps(self) -> None:
        while (step := self._next()) is not None:
            if step["tool"] in GATED and self._mode() == "plan" and not self._plan_approved():
                self._ask_plan()
            self._run_step(step)
            self._save(checkpoints.RUNNING)

    def _mode(self) -> str:
        return str(runtime.switch("approval.bulk_change", "per_action"))

    def _valid_plan(self) -> dict:
        feedback = None
        for attempt in range(1, MAX_REPLANS + 2):
            plan = planner.make_plan(self.sid, self.text, self.context, feedback, call=self.model)
            if plan.get("steps") == [] and not plan.get("meta", {}).get("error"):
                # The planner's own answer that this is not a bulk change it can plan
                # (agent/prompts/planner.md): end now, rather than re-plan it into one.
                self.trace.event("plan_check", attempt=attempt, valid=False, supported=False,
                                 problems=["the planner wrote no steps: not a supported bulk change"], plan=plan)
                raise _End(UNSUPPORTED, "the planner found the instruction is not a removal of the access of "
                                        "contractors whose contract ended")
            problems = self._problems(plan)
            self.trace.event("plan_check", attempt=attempt, valid=not problems,
                             problems=validator.messages(problems)[:10], plan=plan)
            if not problems:
                return plan
            feedback = validator.messages(problems)
        raise _End(PLAN_REJECTED, f"no valid plan after {MAX_REPLANS + 1} tries: " + "; ".join(feedback)[:400])

    def _problems(self, plan: dict) -> list:
        return validator.validate(plan, list(planner.U6_TOOLS), self.context["step_limit"],
                                  set(self.context["known_ids"]))

    def _flag_orphans(self) -> None:
        orphans = detectors.orphan_check(self.plan, exempt=ORPHAN_EXEMPT)
        if orphans:
            self.trace.event("orphan_flag", version=self.version, steps=orphans)

    def _next(self) -> dict | None:
        return next((s for s in self.plan["steps"] if s["id"] not in self.done), None)

    # -- one step ----------------------------------------------------------------------------
    def _run_step(self, step: dict) -> None:
        self._check_bound(model_call=False)
        sid, tool = step["id"], step["tool"]
        position = [s["id"] for s in self.plan["steps"]].index(sid) + 1
        self.trace.progress(f"Step {position} of {len(self.plan['steps'])}: {step['action']}")
        if tool in GATED:
            if sid not in self.ready:                      # per action: prepared as it comes
                self.ready[sid] = self._prepare(step)
            args = self.ready[sid]
            result = self._call(step, args)
        else:
            args = validator.step_args(step)
            to_fill = [k for k, v in args.items() if isinstance(v, str) and planner.FILL in v]
            result, args = self._filled_call(step, args, to_fill) if to_fill else (self._call(step, args), args)
        if result.end_reason:
            raise _End(result.end_reason, f"step {sid} ({tool}): {result.content}")
        if result.is_error:
            raise _End(STEP_HALTED, f"step {sid} ({tool}) failed: {result.content}")
        miss = outcome_miss(step["expect"], result.data)
        if miss:
            raise _End(STEP_HALTED, f"step {sid} ({tool}) did not show its expected outcome: {miss}")
        self.done.append(sid)
        self.data[sid] = result.data
        self.results[sid] = summarize(step, result)
        self._record_action(tool, args, result.data)
        if tool == CHECKPOINT_TOOL:
            self._checkpoint(step, result.data)

    def _call(self, step: dict, args: dict):
        seen = self.tracker.see(step["tool"], args, step=step["id"])
        if seen:
            self.trace.event("repeat_call", tool=step["tool"], plan_step=step["id"], first=seen["step"],
                             hash=seen["hash"])
        return self.registry.call(step["tool"], args, self._ctx(step))

    def _ctx(self, step: dict) -> CallContext:
        return CallContext(self.sid, self.n, "executor", USE_CASE, plan_step=step["id"], run_key=self.run_key,
                           origin="agent")

    # -- the gated steps: prepared, checked, then put up ---------------------------------------
    def _prepare(self, step: dict) -> dict:
        """A gated step's final arguments: the note filled from the results, stamped with the
        disclosure line, and checked by the critique before the operator sees it. A note the
        critique still rejects after MAX_FILL_ATTEMPTS is held, and the run ends."""
        args = validator.step_args(step)
        to_fill = [k for k, v in args.items() if isinstance(v, str) and planner.FILL in v]
        error = None
        for attempt in range(1, MAX_FILL_ATTEMPTS + 1):
            filled, error = self._fill(step, args, to_fill, error, attempt) if to_fill else (args, None)
            if filled is None:
                continue
            final = disclosure.stamp_call(step["tool"], filled)
            problems = self._critique(step, final, attempt)
            if not problems:
                return final
            error = (f"The note was not sent (round {attempt} of {MAX_FILL_ATTEMPTS}): {' '.join(problems)} "
                     f"Revise it and call {step['tool']} again.")
            if not to_fill:
                break
        if error and error.startswith("The note was not sent"):
            raise _End(critique.CRITIQUE_HELD, f"the note of step {step['id']} was held and not sent: {error}")
        raise _End(STEP_HALTED, f"step {step['id']} ({step['tool']}): no usable call after {MAX_FILL_ATTEMPTS} "
                                f"tries: {error}")

    def _critique(self, step: dict, args: dict, attempt: int) -> list[str]:
        if runtime.switch("critique.requester_reply") != "external":
            return []
        recipient, request_id = str(args.get("recipient_employee_id")), args.get("request_id")
        problems = critique.check_draft(recipient_employee_id=recipient, request_id=request_id,
                                        body=disclosure.without_line(args.get("body")), stamp=db.current_stamp())
        self.trace.event("critique", round=attempt, verdict="fail" if problems else "pass",
                         reason=" ".join(problems)[:600], recipient=recipient, request_id=request_id,
                         plan_step=step["id"], stage="before_approval")
        if not problems:
            self.checked.append(f"step {step['id']} ({recipient}): passed on round {attempt}")
        return problems

    def _plan_approved(self) -> bool:
        if not self.asked or self.asked.get("version") != self.version:
            return False                         # never asked, or the plan was revised since: ask again
        found = approvals.get(self.asked["approval_id"])
        return bool(found) and found["status"] == approvals.APPROVED

    def _done_summary(self) -> list[str]:
        """What ran before the plan review, one line per person, from the step results."""
        lines = []
        for sid in self.done:
            tool, data = self._step(sid)["tool"], self.data.get(sid) or {}
            if tool == "access_revoke_all":
                lines.append(f"removed {len(data.get('removed') or [])} group(s) from {data.get('employee_id')}"
                             + (f", kept {', '.join(data['kept_protected'])} (protected)" if data.get("kept_protected")
                                else ""))
            elif tool == "access_revoke" and data.get("status") == "removed":
                lines.append(f"removed {data.get('group_id')} from {data.get('employee_id')}")
            elif tool == "access_propose_change":
                lines.append(f"proposed to {data.get('action')} {data.get('group_id')} for {data.get('employee_id')} "
                             f"({data.get('pending_id')}), waiting for the operator on /pending")
        return lines

    def _ask_plan(self) -> None:
        """Approval at the plan: prepare every gated step still to run, then put them up as one."""
        gated = [s for s in self.plan["steps"] if s["id"] not in self.done and s["tool"] in GATED]
        for s in gated:
            if s["id"] not in self.ready:
                self.ready[s["id"]] = self._prepare(s)
        listed = [{"plan_step": s["id"], "tool": s["tool"], "args": self.ready[s["id"]],
                   "action_class": hook.classify(s["tool"], self.ready[s["id"]], self._ctx(s))} for s in gated]
        facts = {"version": self.version, "use_case": USE_CASE, "instruction": self.text,
                 "done": self._done_summary(), "critique": "; ".join(self.checked) or "off (critique.requester_reply)"}
        approval_id = approvals.request_plan(self._ctx(gated[0]), listed, facts)
        self.asked = {"approval_id": approval_id, "version": self.version, "steps": [s["id"] for s in gated]}
        raise _End(AWAITING_APPROVAL, f"plan v{self.version}'s {len(gated)} gated step(s) wait for the operator's "
                                      f"approval {approval_id}")

    def _filled_call(self, step: dict, args: dict, to_fill: list[str]):
        sid, tool = step["id"], step["tool"]
        error, result = None, None
        for attempt in range(1, MAX_FILL_ATTEMPTS + 1):
            filled, error = self._fill(step, args, to_fill, error, attempt)
            if filled is None:
                continue
            args = filled
            result = self._call(step, args)
            if result.is_error and not result.end_reason and result.error_class == "permanent":
                error = result.content
                continue
            return result, args
        if result is not None:
            return result, args
        raise _End(STEP_HALTED, f"step {sid} ({tool}): no usable call after {MAX_FILL_ATTEMPTS} tries: {error}")

    def _fill(self, step: dict, args: dict, to_fill: list[str], error: str | None, attempt: int):
        """One fill call: (the step's arguments with each "$fill" value, None) or (None, why not)."""
        sid, tool = step["id"], step["tool"]
        response = self.model("fill", (planner.PROMPTS / "executor.md").read_text(),
                              [{"role": "user", "content": self._fill_request(step, args, to_fill, error)}],
                              self.registry.definitions([tool]))
        chosen = None
        for call in response.tool_calls:
            if self.registry.resolve(call.name) == tool:
                chosen = chosen or call
            else:   # off the plan: refused, never run
                self.trace.event("off_plan", step=self.n, plan_step=sid, asked_for=call.name, expected=tool,
                                 input=call.input)
        if chosen is None:
            return None, (f"Your call was refused: this step calls {tool} and no other tool."
                          if response.tool_calls else f"You called no tool: call {tool}.")
        filled = {k: chosen.input.get(k) for k in to_fill}
        unfilled = [k for k, v in filled.items() if v is None or (isinstance(v, str) and planner.FILL in v)]
        if unfilled:
            return None, f"Give a value for {', '.join(unfilled)}; it still reads \"$fill\" or is missing."
        self.trace.event("fill", step=self.n, plan_step=sid, attempt=attempt, filled=filled)
        return {**args, **filled}, None      # the plan's fixed arguments stand, whatever the call said

    def _fill_request(self, step: dict, args: dict, to_fill: list[str], error: str | None) -> str:
        fixed = {k: v for k, v in args.items() if k not in to_fill}
        results = "\n".join(f"step {d} ({self._step(d)['tool']}): {json.dumps(self.data[d], default=str)[:RESULT_CHARS]}"
                            for d in self._upstream(step) if d in self.data)
        text = (f"The operator's instruction:\n{self.text}\n\n"
                f"This step: {step['id']}, {step['action']}. Tool: {step['tool']}.\n"
                f"Fixed arguments (use them as given): {json.dumps(fixed)}\n"
                f"Arguments to fill: {', '.join(to_fill)}\n\n"
                f"Results of the steps this one depends on:\n{results or '(none)'}")
        if error:
            text += f"\n\nYour last call failed: {error}"
        return text

    def _upstream(self, step: dict) -> list[int]:
        """The steps this one depends on, directly or through other steps, in plan order:
        a note that depends on a proposal still sees the removal the proposal depends on."""
        seen, todo = set(), list(step["depends_on"])
        while todo:
            d = todo.pop()
            if d not in seen and any(s["id"] == d for s in self.plan["steps"]):
                seen.add(d)
                todo += self._step(d)["depends_on"]
        return [s["id"] for s in self.plan["steps"] if s["id"] in seen]

    def _step(self, sid: int) -> dict:
        return next(s for s in self.plan["steps"] if s["id"] == sid)

    # -- the checkpoint and the revision -------------------------------------------------------
    def _proposal_planned(self, plan: dict, employee: str, group: str) -> bool:
        for s in plan["steps"]:
            if s["id"] in self.done or s["tool"] != "access_propose_change":
                continue
            try:
                a = validator.step_args(s)
            except (ValueError, TypeError):
                continue
            if (str(a.get("action", "")).lower() == "revoke" and str(a.get("employee_id", "")).upper() == employee
                    and str(a.get("group_id", "")).upper() == group):
                return True
        return False

    def _checkpoint(self, step: dict, data: dict) -> None:
        employee = str(data["employee_id"]).upper()
        protected = [m["group_id"] for m in data["memberships"] if m.get("protected")]
        missing = [g for g in protected if not self._proposal_planned(self.plan, employee, g)]
        reason = None if not missing else (
            f"After step {step['id']}, {employee} holds the protected group(s) {', '.join(missing)}, which "
            "access_revoke_all keeps, and no later step proposes removing them (access_propose_change, action "
            "\"revoke\"), so the operator would never be asked.")
        self.trace.event("checkpoint", name=CHECKPOINT, plan_step=step["id"], passed=not missing, reason=reason)
        if missing:
            self._revise(reason, employee, missing)

    def _revise(self, failure: str, employee: str, missing: list[str]) -> None:
        asked = failure
        while True:
            if self.revisions >= MAX_REVISIONS:
                raise _End(REVISION_BOUND, f"{failure} The plan was not repaired within {MAX_REVISIONS} revisions.")
            self.revisions += 1
            new = reviser.revise_plan(self.sid, self.plan, list(self.done), self.results, asked, call=self.model)
            problems = validator.messages(self._problems(new))
            if not problems:
                still = [g for g in missing if not self._proposal_planned(new, employee, g)]
                if still:
                    problems = [f"the revision still has no access_propose_change step revoking "
                                f"{', '.join(still)} for {employee}"]
            self.trace.event("revision", checkpoint=CHECKPOINT, attempt=self.revisions, valid=not problems,
                             version=self.version + 1 if not problems else None, problems=problems[:10])
            if not problems:
                self.version += 1
                self.plan = new
                self.trace.plan(self.version, new, reason=failure, checkpoint=CHECKPOINT)
                self._flag_orphans()
                return
            asked = f"{failure} Your last revision was rejected: {'; '.join(problems)[:600]}"

    # -- what the operator reads ------------------------------------------------------------------
    def _record_action(self, tool: str, args: dict, data: dict) -> None:
        if tool == "access_revoke_all":
            self.actions += [f"removed {g} from {data['employee_id']}" for g in data["removed"]]
            self.actions += [f"kept protected {g} for {data['employee_id']}" for g in data["kept_protected"]]
        elif tool == "access_revoke" and data.get("status") == "removed":
            self.actions.append(f"removed {data['group_id']} from {data['employee_id']}")
        elif tool == "access_propose_change":
            self.actions.append(f"proposed to {data['action']} {data['group_id']} for {data['employee_id']} "
                                f"({data['pending_id']}), waiting for the operator")
        elif tool == "reply_draft":
            self.actions.append(f"drafted a note to {args.get('recipient_employee_id')} ({data['pending_id']}), "
                                "waiting for the operator to send it")
        elif tool == "reply_send":
            self.actions.append(f"sent the approved note to {args.get('recipient_employee_id')} "
                                f"(message {data.get('message_id')}, {data.get('status')})")

    def _reply(self, reason: str, detail: str, report: dict | None) -> str:
        if reason == NOTHING_TO_DO:
            return f"There is nothing to do: {detail}, so I wrote no plan and changed nothing."
        if reason == UNSUPPORTED:
            return ("I can run one kind of bulk change: removing the access of contractors whose contract ended, "
                    "with a note to each one's manager. Your instruction asks for something else, so I wrote no "
                    "plan and changed nothing. You can make the change by hand on /people.")
        if reason == PLAN_REJECTED:
            return ("I could not write a plan that passes our checks, so nothing was run and nothing changed. "
                    f"Why: {detail}")
        head = {PLAN_DONE: f"Done. The plan (version {self.version}) ran all its steps.",
                AWAITING_APPROVAL: f"Waiting for you: {detail}. Answer it on /approvals; nothing is sent before.",
                APPROVAL_DENIED: f"Stopped: {detail}. What ran before it stays.",
                STEP_HALTED: f"I stopped: {detail}",
                REVISION_BOUND: f"I stopped at the checkpoint: {detail}",
                "critique_held": f"I held a note and did not send it: {detail}"}.get(
            reason, f"I stopped at our bound ({reason}): {detail}")
        lines = [head]
        if self.actions:
            lines.append("What was done:\n" + "\n".join(f"- {a}" for a in self.actions))
        else:
            lines.append("Nothing was changed.")
        if report and report["skipped"]:
            lines.append(f"Not run: plan steps {', '.join(str(s) for s in report['skipped'])}.")
        return "\n\n".join(lines)


def resume(cp, event: dict) -> Reply:
    """checkpoints.resume's way back into a U6 run with a plan: after the operator's answer to
    its approval, /resume after a stop, or the next turn after a crash."""
    result = PlannedRun.restore(cp, Bound()).resume(event)
    return Reply(result.text, actions=list(result.actions))


def run_planned(session_id: str, operator_text: str, *, bound: Bound, run_key: str | None = None) -> LoopResult:
    """U6 as a planned agent (plan.bulk_change = "on"). Writes its own trace.end. `run_key`
    is the run's checkpoint key (C3.1): the hook's stop check and the per-run write keys read it."""
    return PlannedRun(session_id, operator_text, bound, run_key=run_key).run()
