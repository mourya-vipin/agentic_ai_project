"""The work waiting, for the app's queue page: what has arrived and each item's status (open
requests and open pending records), the runs held by an escalation, the runs waiting for the
operator or a stranger, and the stopped runs. The page itself adds the threads that wait for an
outside reply and the calls waiting for approval (app/main.py).
Owner: Shruti Tewaney (from Vipin Mourya).

Each item is {"id", "summary", "status", "since", ...}; the page shows any further key in its
"More" column. Held runs come first: each one stops a run until the operator answers.
"""

from __future__ import annotations

import importlib
import re

from sandbox import pending, requests

SUMMARY = 90


def waiting() -> list[dict]:
    return held() + runs() + arrived()


def arrived() -> list[dict]:
    """The requests still open, and the pending records the operator has not closed."""
    items = [{"id": r["id"], "summary": " ".join(r["text"].split())[:SUMMARY], "status": r["status"],
              "since": r["received_at"], "thread": r["thread_id"]} for r in requests.open_requests()]
    items += [{"id": p["id"], "summary": _outside(p) if p["kind"] == "outside_message" else
               f"{p['kind'].replace('_', ' ')}"
               + (f" ({p['action']} {p['group_id']})" if p["group_id"] else "")
               + (f" for {p['employee_id']}" if p["employee_id"] else ""),
               "status": "waiting for the operator", "since": p["created_at"], "request": p["request_id"]}
              for p in pending.open_items()]
    return items


def _outside(p: dict) -> str:
    """A stranger's message that no run takes (agent/service.py: on_outside_reply; added by Aman Panigrahi)."""
    who = p["employee_id"] or "someone outside"
    said = " ".join(re.sub(r"^\s*SYNTHETIC\.\s*", "", p["body"] or "").split())     # the sandbox's mark, as the app drops it
    return f"{who} wrote on {p['thread_id']} ({p['reason']}): {said}"[:SUMMARY * 2]


def held() -> list[dict]:
    """Every run an escalation holds (Part 2, task 5), the ones past the day's cap marked."""
    escalation = _module("agent.escalation")
    if escalation is None:
        return []
    return [{"id": p["id"], "summary": f"{p['signal']}: {p['why']}"[:SUMMARY],
             "status": ("held, over the day's cap: open it with /packet " + p["id"]) if not p["sent"]
             else "held: waits for /answer " + p["id"],
             "since": p["created_at"], "run": p["run_key"], "needs": p["needs"], "ends held at": p["deadline"]}
            for p in escalation.held()]


def runs() -> list[dict]:
    """The runs that wait for the operator's approval or a stranger's reply, and the stopped ones."""
    checkpoints, control = _module("agent.checkpoints"), _module("agent.control")
    items = []
    if checkpoints is not None:
        for cp in checkpoints.waiting():
            wait = cp.waiting_for or {}
            kind = wait.get("kind")
            if kind == "escalation":
                continue                                    # listed by held()
            what = {"approval": f"waits for the operator's approval {wait.get('approval_id')}",
                    "reply": f"waits for a reply on {wait.get('thread_id')}",
                    "stop": "stopped"}.get(kind, f"waits ({kind})")
            items.append({"id": cp.run_key, "summary": f"{cp.use_case}: {' '.join(str(cp.task).split())}"[:SUMMARY],
                          "status": what, "since": cp.updated_at, "step": cp.current_step})
    if control is not None:
        listed = {i["id"] for i in items}
        for flag in control.flags(control.STOPPED):
            if flag["run_key"] not in listed:
                items.append({"id": flag["run_key"], "summary": "stopped by the operator",
                              "status": "stopped: /resume, /cancel or /rollback it", "since": flag["stopped_at"]})
    return items


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return None
