"""The pre-call hook: the autonomy policy, enforced outside the model (Lecture 5, slides 13 to 17).

Owner: Vipin Mourya.

The registry calls policy_hook before every tool call, the model's and our code's, with
the arguments already checked at the boundary. In order:

    1. stop          control.stopped(run_key): every call of a stopped run is refused, first
    2. classify      the call, with its arguments and circumstance, into an action class
    3. prohibited    a class in the prohibited column is refused, whatever any approval says
    4. limits        a rule the operator stated in this session (policy/limits.py) that blocks it
    5. disclosure    a send whose text lacks the disclosure line (agent/disclosure.py)
    6. approval      a class that requires approval, unless an approved plan or decision covers
                     this exact call, raises AskApproval for the registry to put up
    7. allow

It fails closed: the registry denies the call on any exception but AskApproval, so a
class missing from autonomy.yaml, a sandbox read that fails or a broken policy file
denies it. The model never sees the policy file.
"""

from __future__ import annotations

import importlib
import re
from functools import lru_cache
from pathlib import Path

import yaml

from agent import disclosure
from agent.tools import impl
from sandbox import access, catalog, notices

POLICY_FILE = Path(__file__).with_name("autonomy.yaml")
COLUMNS = ("independent", "requires_approval", "prohibited")

# the argument that carries the text a tool writes or sends, per tool
TEXT_ARGS = {
    "access_propose_change": ("reason",),
    "reply_draft": ("body",),
    "manager_confirmation_draft": ("body",),
    "reply_send": ("body",),
    "access_revoke": ("reason",),
    "access_revoke_all": ("reason",),
}
SENDS = ("reply_send", "channel_ask_template", "notices_send_template")   # each reaches a stranger
SECRET = re.compile(r"FAKE-[0-9a-f]{8,}", re.IGNORECASE)                  # sandbox/base_data.fake_secret
PENDING_RECORDS = ("access_propose_change", "reply_draft", "manager_confirmation_draft", "credentials_record_handover")


class Denied(Exception):
    """The call does not run. `repair` is what the caller reads, when the reason has a next step."""

    def __init__(self, reason: str, action_class: str | None = None, repair: str | None = None):
        super().__init__(reason)
        self.reason, self.action_class, self.repair = reason, action_class, repair


class AskApproval(Exception):
    """The call waits for the operator: the registry hands it to agent/approvals.py."""

    def __init__(self, action_class: str, facts: dict, repair: str | None = None):
        super().__init__(f"{action_class} requires the operator's approval")
        self.action_class, self.facts, self.repair = action_class, facts, repair


@lru_cache(maxsize=1)
def policy() -> dict[str, str]:
    """Each action class (or class pattern, such as read/*) mapped to its column."""
    data = yaml.safe_load(POLICY_FILE.read_text()) or {}
    out: dict[str, str] = {}
    for column in COLUMNS:
        for name in data.get(column) or []:
            if name in out:
                raise ValueError(f"autonomy.yaml: {name} is in both {out[name]} and {column}")
            out[str(name)] = column
    unknown = set(data) - set(COLUMNS)
    if unknown:
        raise ValueError(f"autonomy.yaml: unknown column(s) {sorted(unknown)}; the columns are {COLUMNS}")
    return out


def column(action_class: str) -> str | None:
    """The column of an action class: its own entry first, then its tool's pattern (read/*)."""
    table = policy()
    if action_class in table:
        return table[action_class]
    return table.get(action_class.split("/", 1)[0] + "/*")


def classify(tool: str, args: dict, ctx) -> str:
    """The action class of one call, from its tool, its arguments and the sandbox's records.
    A tool this function does not know raises, so the call is denied."""
    for name in TEXT_ARGS.get(tool, ()):
        if SECRET.search(str(args.get(name) or "")):
            return "credential_in_message/any"
    if tool in impl.TOOLS and not impl.TOOLS[tool][2]:
        return f"read/{tool}"
    if tool == "requests_set_status":
        return "requests_set_status/any"
    if tool in PENDING_RECORDS:
        return "pending_record/any"
    if tool == "access_grant":
        # an unknown person or group gets the tool's own repair fact (a ToolError, which the registry returns)
        person, group = impl._employee(args.get("employee_id")), impl._group(args.get("group_id"))
        decision = catalog.policy_for(person["id"], group["id"])["decision"]
        if decision in ("birthright", "allowed"):
            return "access_grant/policy_allowed"
        if decision == "forbidden":
            return "access_grant/forbidden"
        return "access_grant/high_sensitivity" if group["sensitivity"] == "high" else "access_grant/manager_confirmed"
    if tool == "access_revoke":
        impl._employee(args.get("employee_id"))
        group = impl._group(args.get("group_id"))
        return "access_revoke/protected_group" if group["protected"] else "access_revoke/unprotected"
    if tool == "access_revoke_all":
        return "access_revoke/unprotected"          # it keeps every protected group by itself
    if tool == "notices_send_template":
        return "notices_send_template/requester"
    if tool == "credentials_issue":
        return "credentials_issue/new_starter"
    if tool == "channel_ask_template":
        return "ask_stranger/template"
    if tool == "reply_send":
        return "manager_note/plan_listed" if ctx.use_case == "bulk_change" and ctx.caller == "executor" \
            else "reply_send/model_text"
    raise ValueError(f"no action class for the tool {tool!r}: add it to policy/hook.py and autonomy.yaml")


def outgoing_text(tool: str, args: dict) -> str | None:
    """The text a send puts on the channel, as our code will send it."""
    if tool == "notices_send_template":
        return notices.render(str(args.get("template_id")), dict(args.get("facts") or {}))
    if tool == "reply_send":
        return str(args.get("body") or "")
    if tool == "channel_ask_template":
        _, facts, _ = impl.ask_message(args)
        return notices.render("manager_confirm", facts)
    return None


def _module(name: str):
    """A teammate's module this hook reads, or None while it is not built yet."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:          # the module exists but something it imports is missing
            raise
        return None


def _limits():
    return importlib.import_module("policy.limits")


def policy_hook(tool: str, args: dict, ctx) -> None:
    # 1. the stop, before anything else (Part 2, task 6)
    control = _module("agent.control")
    if control is not None and ctx.run_key is not None and control.stopped(ctx.run_key):
        raise Denied("stopped", repair="This run was stopped by the operator, so no tool runs. "
                                       "Wait for the operator's /resume, /cancel or /rollback.")
    # 2. classify
    action_class = classify(tool, args, ctx)
    ctx.action_class = action_class
    where = column(action_class)
    if where is None:
        raise Denied(f"{action_class} is in no column of autonomy.yaml", action_class)
    # 3. prohibited: no approval turns it into a yes
    if where == "prohibited":
        raise Denied(f"prohibited: {action_class}", action_class, repair=_prohibited_repair(action_class, args))
    # 4. the operator's stated limits
    limits = _limits()
    for rule in limits.active(ctx.session_id):
        if limits.blocks(rule, action_class, args):
            raise Denied(f"limit: {rule.id}", action_class,
                         repair=f"The operator said: \"{rule.text}\". This call is blocked until they lift it.")
    # 5. the disclosure line on every send
    if tool in SENDS and not disclosure.has_line(outgoing_text(tool, args)):
        raise Denied("no disclosure", action_class,
                     repair="A message to a person must end with the disclosure line; nothing was sent.")
    # 6. approval
    if where == "requires_approval":
        approvals = _module("agent.approvals")
        if approvals is not None and approvals.covers(ctx.run_key, action_class, args):
            ctx.approved = True
            return
        raise AskApproval(action_class, _facts(tool, args, action_class))
    # 7. allow


def _prohibited_repair(action_class: str, args: dict) -> str:
    person, group = args.get("employee_id"), args.get("group_id")
    if action_class == "access_revoke/protected_group":
        return (f"{group} is protected, so it was not removed from {person}; nothing changed. Write its removal "
                "for the operator instead: access_propose_change with action \"revoke\", employee_id "
                f"{person}, group_id {group}, request_id {args.get('request_id') or 'null'} and a reason. "
                "A retry of this call will fail the same way.")
    if action_class == "access_grant/forbidden":
        reason = catalog.policy_for(str(person), str(group))["reason"]
        return (f"Cannot grant {group} to {person}: {reason}. It cannot be granted on request. "
                "A retry of this call will fail the same way.")
    if action_class == "credential_in_message/any":
        return ("The text carries a credential's secret, which never goes into a message or a record. "
                "Nothing was written. Name the credential by its ID; the operator hands it over in person.")
    return "This action is never allowed. A retry of this call will fail the same way."


def _facts(tool: str, args: dict, action_class: str) -> dict:
    """What the approval screen computes for this call, before any rationale (Part 2, task 4)."""
    facts: dict = {"tool": tool, "action_class": action_class, "args": dict(args)}
    if tool == "access_grant":
        person, group = str(args.get("employee_id")), str(args.get("group_id"))
        facts["policy"] = catalog.policy_for(person, group)
        facts["already_holds"] = group in {m["group_id"] for m in access.memberships(person)}
    text = outgoing_text(tool, args)
    if text is not None:
        facts["text_as_sent"] = text
    return facts
