"""Template notices: the only code in our system that writes on the channel.
Owner: Vipin Mourya.

A notice's words come from a fixed template and the request's own records, never
from the model, which is why it may go out without a person (above our approval
line). The channel takes no key, so we check before every send (Lecture 3,
slide 39): the key in notices_sent, then the thread itself for the same text from
this run (in the app, this console session). Between that read and the send,
another process could send the same notice; only one process writes per run.
"""

from __future__ import annotations

import channel
from harness import runtime
from harness.trace import now as wall_now
from sandbox import db, pending

SIGN_OFF = " IT access desk."
TEMPLATES = {
    "received": "SYNTHETIC. Hello {name}, we have your request {request_id} and the IT team is looking at it.",
    "pending_review": ("SYNTHETIC. Hello {name}, your request {request_id} needs a review by the IT team before "
                       "anything changes. We will reply here."),
    "granted": "SYNTHETIC. Hello {name}, your request {request_id} is done: you now have {group_name} ({group_id}).",
    "removed": ("SYNTHETIC. Request {request_id}: access for {name} ({employee_id}) has been removed from "
                "{count} group(s), effective {date}."),
    "onboarded": ("SYNTHETIC. Request {request_id}: {name} ({employee_id}) is set up with {count} standard group(s) "
                  "for the {role} role, ready for {start_date}. Sign-in details will be handed over in person."),
}


def render(template_id: str, facts: dict) -> str:
    if template_id not in TEMPLATES:
        raise ValueError(f"no notice template {template_id!r}; templates: {', '.join(sorted(TEMPLATES))}")
    try:
        return TEMPLATES[template_id].format(**facts) + SIGN_OFF
    except KeyError as exc:
        raise ValueError(f"the {template_id} notice needs the fact {exc.args[0]!r}") from None


def send_template(thread_id, template_id, facts: dict, *, key, stamp) -> dict:
    """{"status": "sent" | "already_sent", "message_id": int}"""
    text = render(template_id, facts)
    with db.connect() as conn:
        row = conn.execute("SELECT message_id FROM notices_sent WHERE key = ?", (key,)).fetchone()
    if row:
        return {"status": "already_sent", "message_id": row["message_id"]}
    earlier = _already_on_thread(str(thread_id), text)
    if earlier is not None:
        return {"status": "already_sent", "message_id": earlier}
    message = channel.send(str(thread_id), text)
    with db.connect() as conn:
        conn.execute("INSERT INTO notices_sent (key, thread_id, message_id, template_id, stamp, at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (key, str(thread_id), message.id, template_id, stamp, wall_now()))
        db.audit(conn, actor="workflow", action="notice", stamp=stamp, request_id=facts.get("request_id"),
                 detail={"template_id": template_id, "thread_id": str(thread_id), "message_id": message.id})
    return {"status": "sent", "message_id": message.id}


def send_as_operator(pending_id) -> dict:
    """The operator sends a reply draft by hand, from the product page: the message is
    the operator's, and the draft is closed."""
    item = pending.get(pending_id)
    if item is None or item["kind"] != "reply_draft" or item["status"] != "open":
        raise KeyError(f"no open reply draft {pending_id}")
    thread_id = item["thread_id"] or f"SYN-MAIL-{item['employee_id']}"
    message = channel.send(thread_id, item["body"], sender=channel.OPERATOR)
    pending.close(pending_id, "done", stamp="manual")
    return {"status": "sent", "message_id": message.id, "thread_id": thread_id}


def _already_on_thread(thread_id: str, text: str) -> int | None:
    run = runtime.current_run()
    session = runtime.current_session()
    for m in channel.messages(thread_id):
        mine = (run is not None and m.run_id == run.run_id) or (run is None and m.session_id == session)
        if mine and m.sender == channel.AGENT and m.text == text:
            return m.id
    return None
