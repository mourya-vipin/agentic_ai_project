"""Notes: a record outside the conversation that compaction never touches, kept up to date, so
the agent can tell what it has done and what is still open (Lecture 2, slide 28; Part 1, task 6).
Owner: Vipin Mourya.

    notes.after_call(ctx, "access_grant", args, data)   # the registry, after every successful write
    notes.thread_answered(session_id, thread_id)         # the service, when a stranger's reply lands
    notes.render(session_id)                             # what context.assemble puts in a call

Our code writes them, never the model, from each write's IDs and status alone: a request, a
person, a group, a pending record, a thread. Nothing a stranger or the model wrote goes in (no
body, reason or reply text), so a stranger's words can never reach the notes, and from there a
call. One kind of session keeps them, the operator's day at the console (notes.console); with it
off, nothing is recorded and no call sees them. They live in the sandbox (state_dir()/
access.sqlite3, table session_notes), so they survive a restart and make app's reset clears them.
"""

from __future__ import annotations

import json

from harness import runtime
from sandbox import clock, db

KIND = "console"
MAX_LINES = 40            # the newest are kept; the session's effects are in the sandbox too


def enabled(kind: str = KIND) -> bool:
    return str(runtime.switch(f"notes.{kind}", "off")) == "on"


def _line(tool: str, args: dict, data: dict) -> tuple[str, str] | None:
    """(section, line) for one successful write, from IDs and status only."""
    g, e, r = args.get("group_id"), args.get("employee_id"), args.get("request_id")
    on = f" on {r}" if r else ""
    status = data.get("status")
    if tool == "access_grant":
        return "done", f"{status or 'granted'}: {g} to {e}{on}"
    if tool == "access_revoke":
        return "done", f"{status or 'removed'}: {g} from {e}{on}"
    if tool == "access_revoke_all":
        kept = data.get("kept_protected") or []
        return "done", (f"removed from {e}{on}: {', '.join(data.get('removed') or []) or 'nothing'}"
                        + (f"; kept {', '.join(kept)} (protected)" if kept else ""))
    if tool == "access_propose_change":
        return "open", f"proposal {data.get('pending_id')}: {args.get('action')} {g} for {e}{on}, waiting for the operator"
    if tool == "reply_draft":
        return "open", f"reply draft {data.get('pending_id')} to {args.get('recipient_employee_id')}{on}, waiting for the operator"
    if tool == "manager_confirmation_draft":
        return "open", f"manager confirmation {data.get('pending_id')} for {g} to {e}{on}, waiting for the operator"
    if tool == "channel_ask_template":
        return "open", f"asked {data.get('recipient_employee_id')} on {data.get('thread_id')} about {g} for {e}{on}; waiting for a reply"
    if tool == "reply_send":
        return "done", f"sent message {data.get('message_id')} to {args.get('recipient_employee_id')} on {data.get('thread_id')}"
    if tool == "notices_send_template":
        return "done", f"notice {args.get('template_id')} on {args.get('thread_id')}{on}"
    if tool == "requests_set_status":
        return "done", f"{data.get('request_id') or r} set to {data.get('status')}"
    if tool == "credentials_issue":
        return "done", f"credential {data.get('credential_id')} issued to {e}"
    if tool == "credentials_record_handover":
        return "open", f"hand over {data.get('credential_id')} to {e} in person ({data.get('pending_id')})"
    return None


def read(session_id: str) -> dict:
    """{"done": [...], "open": [...], "updated_at"} for one session."""
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM session_notes WHERE session_id = ?", (str(session_id),)).fetchone()
    return json.loads(row["data"]) if row else {"done": [], "open": [], "updated_at": None}


def _write(session_id: str, notes: dict) -> None:
    notes["done"], notes["open"] = notes["done"][-MAX_LINES:], notes["open"][-MAX_LINES:]
    notes["updated_at"] = clock.now()
    with db.connect() as conn:
        conn.execute("INSERT INTO session_notes (session_id, kind, data, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(session_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                     (str(session_id), KIND, json.dumps(notes), notes["updated_at"]))


def after_call(ctx, tool: str, args: dict, data) -> None:
    """Record one successful write in its session's notes, when the switch is on."""
    if not enabled() or not isinstance(data, dict):
        return
    entry = _line(tool, args or {}, data)
    if entry is None:
        return
    notes = read(ctx.session_id)
    section, line = entry
    if line not in notes[section]:
        notes[section].append(line)
        if tool == "requests_set_status" and data.get("status") == "done":
            rid = data.get("request_id")
            notes["open"] = [o for o in notes["open"] if f" on {rid}" not in o]
        _write(ctx.session_id, notes)
        runtime.trace(ctx.session_id).event("notes_updated", section=section, line=line)


def thread_answered(session_id: str, thread_id: str) -> None:
    """A stranger answered on a thread: the open line says so, by the thread's ID only."""
    if not enabled():
        return
    notes = read(session_id)
    notes["open"] = [(o.replace("waiting for a reply", "answered") if f" on {thread_id} " in o else o)
                     for o in notes["open"]]
    _write(session_id, notes)


def render(session_id: str) -> str:
    """The notes as a call reads them, or "" when there are none."""
    notes = read(session_id)
    if not notes["done"] and not notes["open"]:
        return ""
    lines = ["[Notes kept by our code for this session, outside the conversation: what is done and what is open]"]
    lines += ["Done:"] + [f"- {d}" for d in notes["done"]] if notes["done"] else []
    lines += ["Open:"] + [f"- {o}" for o in notes["open"]] if notes["open"] else []
    return "\n".join(lines)
