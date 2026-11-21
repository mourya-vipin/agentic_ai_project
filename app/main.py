"""The app: your product's pages (app/product.py) and the operator's pages.

    /console    the operator's chat: sessions and their history. Each message goes to
                agent.service.operator_turn through harness.turns, the path run.py takes.
                Beside the chat, a panel (agent.service.session_panel) shows the session's
                runs, what each waits for, and what the memory holds; its buttons send the
                operator's commands (/stop, /resume, /cancel, /rollback) as messages.
    /queue      the work waiting: your sandbox's queue, the threads waiting for an
                outside reply, and what waits for approval.
    /threads    the outside channel's conversations. You can write as the stranger,
                to try a conversation by hand; in experiments the task file plays
                them. Each message goes once to agent.service.on_outside_reply, or to
                a turn waiting on the thread in channel.wait_for_reply.
    /approvals  what waits for the operator's answer, from pending_approvals(): empty
                until you build an approval gate.
    /traces     the run records in results/ (the free ones in results/_free/ marked
                free) and every session's trace.

Change your product's pages freely: they read and write your domain data through
sandbox/ modules, the same ones your agent's tools wrap. These pages keep their
contract: they reach the agent only through harness.turns, and app/ imports
nothing from agent/ but agent.service. `make check` tests it.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import channel
from agent import service
from app import product, security, templates
from harness import records, runtime, turns
from harness.paths import results_dir, traces_dir
from harness.trace import SessionTrace, read_trace

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")
CONSOLE_LINES = ("operator", "reply", "progress", "outside", "error", "approval")


@asynccontextmanager
async def lifespan(_app):
    runtime.configure()          # settings, prices and switches, loaded once at start-up
    service.on_start()           # a claim left by a process that stopped during a turn is released
    yield


app = FastAPI(title="Project", lifespan=lifespan)
security.install(app)            # Host and Origin checks before any page (app/security.py, Vipin Mourya)
app.include_router(product.router)


def page(request: Request, name: str, **context):
    return templates.TemplateResponse(request, name, {"page": name.split(".")[0], **context})


# The console shows the operator's commands and the run keys in words. They change only what
# the page shows: the trace, and what the agent receives, keep the command as it was sent.
COMMAND_WORDS = {"stop": "Stop run {n}", "resume": "Resume run {n}", "cancel": "Cancel run {n}",
                 "rollback": "Undo run {n}"}
COMMAND_TEXT = re.compile(r"^\s*/(?P<name>stop|resume|cancel|rollback)\b\s*#?(?P<n>\d{1,6})?\s*$", re.IGNORECASE)
PACKET_TEXT = re.compile(r"^\s*/(?P<name>answer|packet)\b\s*(?P<id>ESC-\d{1,6})\s*(?P<text>.*)$",
                         re.IGNORECASE | re.DOTALL)
RUN_KEY_TEXT = re.compile(r"\b[\w.-]+#(\d{1,6})\b")
SESSION_TIME = re.compile(r"^s-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}\b")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def command_words(text: str | None) -> str | None:
    """An operator's command in words ("/stop 1" is "Stop run 1"), or None for a message that is not one."""
    match = COMMAND_TEXT.match(text or "")
    if match:
        name, n = match["name"].lower(), match["n"]
        if n:
            return COMMAND_WORDS[name].format(n=n)
        return "Stop all runs" if name == "stop" else None
    match = PACKET_TEXT.match(text or "")
    if match:
        if match["name"].lower() == "packet":
            return f"Open packet {match['id']}"
        return f"Answer {match['id']}: {match['text'].strip()}".rstrip(": ")
    return None


def run_names(text: str | None) -> str:
    """A run key in words: "s-20261116-101500-demo#1" is "run 1"."""
    return RUN_KEY_TEXT.sub(r"run \1", text or "")


def session_label(session_id: str | None) -> str:
    """A session's start time in words ("16 Nov, 10:15"), from the ID that /console/new gives it."""
    match = SESSION_TIME.match(session_id or "")
    if not match or not 1 <= int(match[2]) <= 12:
        return session_id or ""
    return f"{int(match[3])} {MONTHS[int(match[2]) - 1]}, {match[4]}:{match[5]}"


USE_CASE_WORDS = {"standard_grant": "Standard grant", "onboarding": "New starter", "offboarding": "Leaver",
                  "nonstandard_request": "Non-standard request", "operator_review": "Your question",
                  "bulk_change": "Bulk change"}
ISO_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")


def use_case_words(name: str | None) -> str:
    """A use case's name in words: "nonstandard_request" is "Non-standard request"."""
    return USE_CASE_WORDS.get(name or "", (name or "").replace("_", " ").capitalize())


def when(stamp: str | None) -> str:
    """A time stamp in words: "2026-11-16T18:02:21.588+00:00" is "16 Nov, 18:02"."""
    match = ISO_TIME.match(stamp or "")
    if not match or not 1 <= int(match[2]) <= 12:
        return stamp or ""
    return f"{int(match[3])} {MONTHS[int(match[2]) - 1]}, {match[4]}:{match[5]}"


def plain(text: str | None) -> str:
    """A sandbox message in words. The "SYNTHETIC." mark goes (the banner already says that every
    record is synthetic), and an HR feed event "EVENT: contract_ended; EMPLOYEE: SYN-EMP-0150"
    is "Contract ended · employee SYN-EMP-0150"."""
    text = re.sub(r"^\s*SYNTHETIC\.\s*", "", text or "")
    if not text.startswith("EVENT:"):
        return text
    fields = [part.split(":", 1) for part in text.split(";") if ":" in part]
    event = " ".join(fields[0][1].split()).replace("_", " ").capitalize()
    rest = [f"{key.strip().lower().replace('_', ' ')} {' '.join(value.split()).replace('_', ' ')}" for key, value in fields[1:]]
    return " · ".join([event, *rest])


def person(sender: str | None) -> str:
    """A sender in words: "omar.haddad@syn-corp.example" is "Omar Haddad", "agent" is "The agent"."""
    sender = (sender or "").strip()
    if sender in ("agent", "operator"):
        return {"agent": "The agent", "operator": "You"}[sender]
    sender = sender.split("@", 1)[0]
    words = sender.replace(".", " ").replace("-", " ").replace("_", " ").split()
    return " ".join(w.upper() if len(w) <= 2 else w.capitalize() for w in words) or "Someone"


def initials(name: str | None) -> str:
    """Two letters for an avatar: "Omar Haddad" is "OH"."""
    parts = [p for p in (name or "").split() if p[:1].isalnum()]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


templates.env.filters.update(command_words=command_words, run_names=run_names, session_label=session_label,
                             use_case_words=use_case_words, when=when, plain=plain, person=person,
                             initials=initials)
templates.env.tests["run_key"] = lambda value: bool(RUN_KEY_TEXT.fullmatch(str(value or "")))


def _safe(value: str) -> str:
    if not SAFE_ID.match(value or ""):
        raise HTTPException(404, "no such item")
    return value


def _agent_error(exc: Exception) -> str:
    if isinstance(exc, NotImplementedError):
        return turns.NOT_BUILT.format(name=str(exc).split(":")[0] or "this function")
    return f"The agent raised {type(exc).__name__}: {exc}"


# -- the console ----------------------------------------------------------------------------

def _sessions() -> list[dict]:
    out = []
    try:
        waiting = service.sessions_waiting()
    except Exception:  # the agent is not built yet: the list shows no waits
        waiting = {}
    for path in traces_dir().glob("*.jsonl"):
        lines = read_trace(path)
        if not lines or lines[0].get("type") != "session_start" or lines[0].get("source") != "console":
            continue
        first = next((x.get("text", "") for x in lines if x.get("type") == "operator"), "")
        out.append({"id": lines[0]["session_id"], "started": lines[0]["at"], "turns":
                    sum(1 for x in lines if x.get("type") == "operator"), "first": first[:120],
                    "waiting": waiting.get(lines[0]["session_id"], 0)})
    return sorted(out, key=lambda s: s["started"], reverse=True)


def _history(session_id: str) -> list[dict]:
    path = traces_dir() / f"{runtime.safe_name(session_id)}.jsonl"
    return [x for x in read_trace(path) if x.get("type") in CONSOLE_LINES] if path.exists() else []


def _panel(session_id: str) -> dict:
    """The panel beside a session: its runs and the memory (agent.service.session_panel)."""
    try:
        return {"error": None, **service.session_panel(session_id)}
    except Exception as exc:
        return {"error": _agent_error(exc), "runs": [], "memory": [], "memory_on": False, "live": 0, "waiting": 0}


@app.get("/console", response_class=HTMLResponse)
def console_list(request: Request):
    return page(request, "console.html", sessions=_sessions(), session_id=None, history=[], panel=None)


@app.post("/console/new")
def console_new():
    session_id = f"s-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    SessionTrace(traces_dir() / f"{session_id}.jsonl", session_id).write(
        {"type": "session_start", "source": "console", "switches": runtime.switches()})
    return RedirectResponse(f"/console/{session_id}", status_code=303)


@app.get("/console/{session_id}", response_class=HTMLResponse)
def console_session(request: Request, session_id: str):
    _safe(session_id)
    return page(request, "console.html", sessions=_sessions(), session_id=session_id, history=_history(session_id),
                panel=_panel(session_id))


@app.get("/console/{session_id}/history", response_class=HTMLResponse)
def console_history(request: Request, session_id: str):
    _safe(session_id)
    return templates.TemplateResponse(request, "_history.html", {"history": _history(session_id)})


@app.get("/console/{session_id}/panel", response_class=HTMLResponse)
def console_panel(request: Request, session_id: str):
    _safe(session_id)
    return templates.TemplateResponse(request, "_panel.html", {"panel": _panel(session_id), "session_id": session_id})


@app.post("/console/{session_id}/turn")
def console_turn(request: Request, session_id: str, text: str = Form(...)):
    _safe(session_id)
    reply, error = None, None
    try:
        reply = turns.operator_turn(session_id, text)
    except Exception as exc:  # the trace has the error; the console shows it
        error = _agent_error(exc)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": error is None, "reply": reply.as_dict() if reply else None, "error": error})
    return RedirectResponse(f"/console/{session_id}", status_code=303)


# -- the queue -------------------------------------------------------------------------------

@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request):
    items, item_error, approvals, approval_error = [], None, [], None
    try:
        from sandbox import queue as sandbox_queue
        items = [dict(i) for i in sandbox_queue.waiting() or []]
    except Exception as exc:
        item_error = f"sandbox/queue.py raised {type(exc).__name__}: {exc}"
    try:
        approvals = turns.pending_approvals()
    except Exception as exc:
        approval_error = _agent_error(exc)
    waiting = [t for t in channel.threads() if t["waiting_for_outside"]]
    return page(request, "queue.html", items=items, item_error=item_error, waiting=waiting,
                approvals=approvals, approval_error=approval_error)


# -- the outside channel --------------------------------------------------------------------

def _threads() -> list[dict]:
    """The channel's threads, each with the person outside on it and the request that arrived on
    it, so the page can name them."""
    out = []
    for t in channel.threads():
        try:
            from sandbox import requests as sandbox_requests
            found = sandbox_requests.by_thread(t["thread_id"])
        except Exception:
            found = None
        them = next((m.sender for m in channel.messages(t["thread_id"]) if m.sender not in ("agent", "operator")), None)
        out.append(t | {"them": them, "request_id": found["id"] if found else None})
    return out


@app.get("/threads", response_class=HTMLResponse)
def threads(request: Request, thread: str | None = None):
    messages = [m.as_dict() for m in channel.messages(thread)] if thread else []
    return page(request, "threads.html", threads=_threads(), thread=thread, messages=messages, notice=None,
                owner=_thread_owner(thread))


def _thread_owner(thread: str | None) -> str | None:
    """The address of the person whose mailbox the thread is (SYN-MAIL-<employee ID>), or None.
    The page writes as that person unless the tester changes it: the hook counts a manager's
    answer only from the manager's own address (#72), so a YES sent as "outside" did nothing (Vipin
    Mourya's review)."""
    if not thread or not thread.startswith("SYN-MAIL-"):
        return None
    from sandbox import directory
    person = directory.get_employee(thread.removeprefix("SYN-MAIL-"))
    return person["email"] if person else None


@app.post("/threads/reply", response_class=HTMLResponse)
def thread_reply(request: Request, thread_id: str = Form(...), text: str = Form(...), sender: str = Form("outside")):
    notice = None
    try:
        reply = turns.outside_reply(thread_id, text, sender=(sender.strip() or "outside"))
        if reply is None:
            notice = "A turn waiting on this thread (wait_for_reply) took the message; on_outside_reply was not called."
        else:
            # this page plays the person outside, who never sees our app: the notice is for the tester
            notice = ("What the desk did with this message (only you, the tester, see this; the operator sees it on "
                      f"the console, the queue and /approvals): {reply.text}" if reply.text else
                      "The desk took the message. Only you, the tester, see this note.")
    except Exception as exc:
        notice = _agent_error(exc)
    return page(request, "threads.html", threads=_threads(), thread=thread_id,
                messages=[m.as_dict() for m in channel.messages(thread_id)], notice=notice,
                owner=_thread_owner(thread_id))


# -- approvals -------------------------------------------------------------------------------

@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request):
    return _approvals_page(request, None)


@app.post("/approvals", response_class=HTMLResponse)
def approvals_decide(request: Request, approval_id: str = Form(...), decision: str = Form(...), note: str = Form("")):
    try:
        reply = turns.decide_approval(approval_id, decision == "approve", note, session_id=_approval_session(approval_id))
        notice = f"Recorded: {decision}." + (f" The agent replied: {reply.text}" if reply and reply.text else "")
    except Exception as exc:
        notice = _agent_error(exc)
    return _approvals_page(request, notice)


def _approval_session(approval_id: str) -> str | None:
    """The console session a waiting approval came from, if pending_approvals() names it
    as `session_id`: the answer is traced, metered and resumed in that session."""
    try:
        items = turns.pending_approvals()
    except Exception:
        return None
    return next((str(i["session_id"]) for i in items if str(i.get("id")) == approval_id and i.get("session_id")), None)


def _approvals_page(request: Request, notice: str | None):
    items, error = [], None
    try:
        items = turns.pending_approvals()
    except Exception as exc:
        error = _agent_error(exc)
    return page(request, "approvals.html", items=items, error=error, notice=notice)


# -- traces -----------------------------------------------------------------------------------

@app.get("/traces", response_class=HTMLResponse)
def traces(request: Request):
    runs = []
    for prefix, folders in (("", records.arms()), (f"{records.FREE}/", records.arms(records.free_dir()))):
        for folder in folders:
            for r in records.read(folder):
                runs.append({k: r.get(k) for k in ("run_id", "task", "sweep", "outcome", "passed", "started",
                                                   "latency_s", "cost_usd")}
                            | {"tokens": (r.get("tokens") or {}).get("total"), "arm": prefix + folder.name,
                               "link": f"/traces/run/{prefix}{folder.name}/{r['run_id']}"})
    runs.sort(key=lambda r: str(r["started"]), reverse=True)
    sessions = sorted(({"id": p.stem, "lines": sum(1 for _ in p.open()), "modified": time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))} for p in traces_dir().glob("*.jsonl")),
        key=lambda s: s["modified"], reverse=True)
    return page(request, "traces.html", runs=runs[:300], more=max(0, len(runs) - 300), sessions=sessions)


def _lines(path: Path) -> list[dict]:
    out = []
    for x in read_trace(path):
        rest = {k: v for k, v in x.items() if k not in ("at", "type", "session_id", "run_id")}
        out.append({"at": str(x.get("at", ""))[11:23], "type": x.get("type"),
                    "detail": json.dumps(rest, default=str)[:600]})
    return out


@app.get("/traces/run/{arm}/{run_id}", response_class=HTMLResponse)
def trace_run(request: Request, arm: str, run_id: str):
    return _run_page(request, results_dir() / _safe(arm), run_id)


@app.get("/traces/run/_free/{arm}/{run_id}", response_class=HTMLResponse)
def trace_free_run(request: Request, arm: str, run_id: str):
    return _run_page(request, records.free_dir() / _safe(arm), run_id)


def _run_page(request: Request, folder: Path, run_id: str):
    record_path, trace_path = folder / f"{_safe(run_id)}.json", folder / f"{run_id}.trace.jsonl"
    if not record_path.exists():
        raise HTTPException(404, "no such run")
    record = json.loads(record_path.read_text())
    return page(request, "trace.html", title=f"Run {run_id}", record=json.dumps(record, indent=2),
                lines=_lines(trace_path) if trace_path.exists() else [])


@app.get("/traces/session/{session_id}", response_class=HTMLResponse)
def trace_session(request: Request, session_id: str):
    path = traces_dir() / f"{_safe(session_id)}.jsonl"
    if not path.exists():
        raise HTTPException(404, "no such session")
    return page(request, "trace.html", title=f"Session {session_id}", record=None, lines=_lines(path))
