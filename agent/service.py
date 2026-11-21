"""The agent's one entry point. The app and run.py reach the agent only through
these functions, by the same path (harness/turns.py), so a scripted run is what
the operator's console does, minus the browser. `make check` tests it.

    operator_turn(session_id, text)   a message from the operator, at the console or from a test task file
    on_outside_reply(thread_id, text) a stranger's message on one of your channel's threads
    pending_approvals()               what waits for the operator's answer (the approvals page)
    decide_approval(id, approved, note)  the operator's answer
    session_panel(session_id)         what the console shows beside a session: its runs, and
                                      what the memory holds (it reads; it changes nothing)
    sessions_waiting()                how many runs wait in each session (the console's list)

The console keeps taking messages while a turn runs, so operator_turn can be called
again for a session whose earlier turn has not returned: a message mid-task.

Each turn returns a Reply: the text the operator reads, one line per action taken,
and `waiting_for`, the thread the agent waits on for an outside reply, if any. To
ask a stranger, send on their thread (channel.send) and end the turn with
Reply(..., waiting_for=thread_id). Their answer comes back through
on_outside_reply(thread_id, text): in the app when someone writes on the threads
page, in run.py when the test task's scripted stranger answers.
channel.wait_for_reply is there for a turn that would rather wait in-process; a
reply it takes is not delivered again to on_outside_reply.

Route each case to its path here: the agent path for the use cases the gates give
it, workflows for the rest. The routing is your own code. Read the switches with
harness.runtime.switch(name), such as switch("control.refunds"), so the console
and run.py take the same path. Get your model client and your trace from
harness.runtime:

    from harness import runtime
    client = runtime.client(session_id)
    trace = runtime.trace(session_id)

Text from the outside channel is the stranger's, never the operator's: it enters
your model calls labelled as theirs.
"""

from __future__ import annotations

from agent import approvals, checkpoints, commands, control, inbox, memory, notes, router, session
from harness import runtime
from harness.turns import Reply  # noqa: F401  (what each turn returns)
from policy import limits


def operator_turn(session_id: str, text: str) -> Reply:
    """One message from the operator in this session: give a task, add to it, ask
    about it. Route the case to its path, as your routing and the switches say, and
    reply.

    Owner: Aman Panigrahi. Our code does these steps in this order:
      1. A command of the operator (/stop, /cancel, /resume, /rollback) gets its answer
         from agent/commands.py. No model sees it, and it does not go into the conversation.
      2. A limit that the message lifts or states becomes a rule that the hook reads before
         each call (policy/limits.py). The reply starts with what our code recorded. The
         router gets the recorded rule as a fact, so the rule alone is not taken for a task.
      3. If a run of the session is live in this process, the message goes to the inbox
         (agent/inbox.py): it adds to the run's task, replaces it, waits, or is refused.
         Otherwise the router sends the message to its path.
      4. At the end of the turn, a preference or a fact that the message states for later is
         kept, with its source and age (agent/memory.py). The reply ends with what was kept.
    The turn writes the async checkpoints before it replies, so that the operator never
    reads a result that the run store does not hold."""
    try:
        command = commands.handle(session_id, text)
        if command is not None:
            return command
        recorded, rule = _limits(session_id, text)
        live = inbox.live_run(session_id)
        if live is not None:
            reply = inbox.post(session_id, text, live)
            session.add(session_id, "operator", text)
            session.add(session_id, "agent", reply.text)
        else:
            reply = router.handle_operator(session_id, text, rule=rule)
        kept = memory.from_text(session_id, text)
    finally:
        checkpoints.flush()
    after = [f"Kept for later sessions as {kept.id} (a {kept.kind}, from you): {kept.text}"] if kept else []
    if not recorded and not after:
        return reply
    return Reply("\n".join([*recorded, reply.text, *after]).strip(), reply.actions, reply.waiting_for)


def _limits(session_id: str, text: str) -> tuple[list[str], str | None]:
    """Lift, then record, the limits that the message gives. A message can lift the old
    rules and state a new one, and the new rule stays. One line for each change, and the
    new rule as the router reads it (or None)."""
    lines, stated = [], None
    lifted = limits.lift_from_text(session_id, text)
    if lifted:
        lines.append(f"Rules lifted: {', '.join(lifted)}.")
    rule = limits.from_text(session_id, text)
    if rule is not None:
        until = "until you lift it" if rule.until == limits.OPERATOR_LIFTS else f"until {rule.until}"
        lines.append(f'Rule {rule.id} is recorded. It holds {until}: "{rule.text}"')
        stated = f'rule {rule.id}, which holds {until}: "{rule.text}"'
    return lines, stated


def on_outside_reply(thread_id: str, text: str) -> Reply:
    """A stranger's message on this thread, delivered here once. The channel has stored it.

    Owner: Aman Panigrahi. The run that waits for a reply on this thread continues from
    its checkpoint (checkpoints.resume), in this process or after a restart. The notes
    mark the thread as answered (agent/notes.py). A message goes to the operator instead,
    as a pending record on /pending and the queue (_for_the_operator), when its first word is
    HUMAN, a request for a person, or when no run waits on the thread: before, such a
    message stayed in the channel and no one was told. A run that a stop holds while it waits
    still waits on the thread, so its reply goes to checkpoints.resume, which refuses it, and
    /resume reads the thread."""
    run = next((cp for cp in checkpoints.waiting("reply") if (cp.waiting_for or {}).get("thread_id") == thread_id),
               None)
    from policy.hook import asks_for_a_person
    if asks_for_a_person(text):
        return _for_the_operator(thread_id, text, run, "asks for a person")
    session_id = run.session_id if run is not None else _this_session()
    if session_id:
        notes.thread_answered(session_id, thread_id)
    if run is None:
        return _for_the_operator(thread_id, text, None, "has no run that waits for it")
    try:
        return checkpoints.resume(run.run_key, {"kind": "reply", "thread_id": thread_id, "text": text})
    finally:
        checkpoints.flush()


def _for_the_operator(thread_id: str, text: str, run, why: str) -> Reply:
    """A stranger's message that no run takes, kept for the operator as a pending record
    ("outside_message"): /pending and the queue show it until the operator marks it done or
    dismisses it. A run that waits on the thread keeps waiting. So that a stranger cannot fill
    /pending (Vipin Mourya's review):

        which threads   a thread that we wrote on, a request's own thread, or the mailbox of a
                        person in the directory (SYN-MAIL-<employee ID>). A message on any other
                        thread stays on the threads page, and no record is made.
        how many        one open record for each thread and reason. A later message joins it:
                        the thread holds every message, and the reply names the record.
        the sender      as the message gives it: our code does not check the address, and the
                        queue says so.

    The key is the message's ID, so the same message twice is one record. If the thread does not
    have the message, the key comes from the text."""
    import hashlib

    import channel
    from sandbox import db, directory, pending, requests
    on_thread = channel.messages(thread_id)
    message = next((m for m in reversed(on_thread) if m.sender not in channel.OURS), None)
    request = (requests.get((run.findings or {}).get("request_id")) if run is not None else None) \
        or requests.by_thread(thread_id)
    traced = run.session_id if run is not None else _this_session()
    if not _known_thread(thread_id, on_thread, run, request):
        if traced:
            runtime.trace(traced).event("outside_not_kept", thread_id=thread_id, why="not a thread of ours")
        return Reply(f"Nothing of ours is on {thread_id}, and it is no one's mailbox in the directory, so the "
                     "message stays on the threads page and is not kept for the operator.")
    waits = f" Run {run.run_no} still waits for a reply on {thread_id}." if run is not None else ""
    joined = next((p for p in pending.open_items("outside_message")
                   if p["thread_id"] == thread_id and p["reason"] == why), None)
    if joined is not None:
        if traced:
            runtime.trace(traced).event("outside_for_operator", thread_id=thread_id, pending_id=joined["id"],
                                        why=why, joined=True, run_key=run.run_key if run is not None else None)
        return Reply(f"The message on {thread_id} {why}. It joins {joined['id']}, which already waits for the "
                     f"operator on /pending; the thread has every message.{waits}")
    sender = directory.by_email(message.sender) if message is not None else None
    which = message.id if message is not None else "text-" + hashlib.sha256((text or "").encode()).hexdigest()[:12]
    pid = pending.create("outside_message", request_id=request["id"] if request else None,
                         employee_id=sender["id"] if sender else None, key=f"outside-{thread_id}-{which}",
                         stamp=db.current_stamp(), thread_id=thread_id, body=text, reason=why)
    if traced:          # the threads page has no session: the pending record is what the operator reads
        runtime.trace(traced).event("outside_for_operator", thread_id=thread_id, pending_id=pid, why=why,
                                    run_key=run.run_key if run is not None else None)
    return Reply(f"The message on {thread_id} {why}, so it waits for the operator on /pending as {pid}.{waits}")


def _known_thread(thread_id: str, on_thread: list, run, request: dict | None) -> bool:
    """Whether a message on this thread can become a pending record (_for_the_operator)."""
    import channel
    from sandbox import directory
    if run is not None or request is not None or any(m.sender in channel.OURS for m in on_thread):
        return True
    return thread_id.startswith("SYN-MAIL-") and directory.get_employee(thread_id.removeprefix("SYN-MAIL-")) is not None


def on_start() -> None:
    """What the app does once, at start-up (app/main.py: lifespan). A claim on a request lives
    only within one turn of one process (router.process_requests releases it at the end). So a
    claim found at start-up is from a process that stopped during a turn, such as a kill before
    `make serve`, and our code releases it. Before, that request stayed "another console session
    is working on it" until `make app` reset the sandbox (Vipin Mourya's review)."""
    from sandbox import requests
    requests.release_all()


# What the console's panel says of each status, and the commands that it offers for it.
STATUS_WORDS = {
    checkpoints.RUNNING: "working",
    checkpoints.WAITING_REPLY: "waiting for a reply",
    checkpoints.WAITING_APPROVAL: "waiting for your approval",
    checkpoints.HELD: "held for a person",
    checkpoints.STOPPED: "stopped",
    checkpoints.DONE: "done",
    checkpoints.FAILED: "failed",
    checkpoints.CANCELLED: "cancelled",
}
PANEL_RUNS = 12


def _flagged(cp) -> bool:
    """Whether the operator's /stop flags this run (agent/control.py). A run that waits keeps
    its status when it is stopped, so the panel reads the flag too."""
    return cp.status not in checkpoints.ENDED and control.state(cp.run_key) == control.STOPPED


def _commands_for(cp) -> list[str]:
    """The commands (agent/commands.py) that make sense for a run in its status, as the
    operator types them. Each button on the panel sends one, through operator_turn."""
    n = cp.run_no
    if _flagged(cp):
        out = [f"/resume {n}", f"/cancel {n}"]
    elif cp.status == checkpoints.RUNNING:
        out = [f"/stop {n}"]
    elif cp.status == checkpoints.STOPPED:
        out = [f"/resume {n}", f"/cancel {n}"]
    elif cp.status in checkpoints.PAUSED:
        out = [f"/stop {n}", f"/cancel {n}"]
    else:
        out = []
    if cp.completed and cp.status != checkpoints.RUNNING:
        out.append(f"/rollback {n}")
    return out


def _run_line(cp) -> dict:
    wait = cp.waiting_for or {}
    flagged = _flagged(cp)
    words = STATUS_WORDS.get(cp.status, cp.status)
    return {
        "run_no": cp.run_no, "run_key": cp.run_key, "use_case": cp.use_case, "status": cp.status,
        "shown": checkpoints.STOPPED if flagged else cp.status, "stop_flag": flagged,
        "words": (f"stopped (it was {words})" if cp.status != checkpoints.STOPPED else words) if flagged else words,
        "end_reason": (cp.findings or {}).get("end_reason"),
        "task": (cp.task or "")[:160],
        "thread_id": wait.get("thread_id"), "deadline": wait.get("deadline"),
        "approval_id": wait.get("approval_id"), "packet_id": wait.get("packet_id"),
        "steps": len(cp.steps or []), "writes": len(cp.completed or {}),
        "crashed": cp.status == checkpoints.RUNNING and bool(cp.process) and cp.process != checkpoints.PROCESS,
        "updated_at": cp.updated_at, "commands": _commands_for(cp),
    }


def session_panel(session_id: str) -> dict:
    """What the console shows beside a session: its runs, newest first, each with its status,
    its wait (the thread and the deadline, the approval or the packet) and the commands that fit;
    and the records that the memory holds, each with its source and age. It only reads: no
    model call, no trace line, no save. Owner: Aman Panigrahi."""
    runs = [_run_line(cp) for cp in checkpoints.runs_of(session_id, PANEL_RUNS)]
    kept = [{"id": r.id, "kind": r.kind, "text": r.text, "source": r.source, "subject": r.subject,
             "confirmed": r.confirmed, "age": memory.age(r), "mine": r.session_id == session_id}
            for r in memory.held()]
    return {"runs": runs, "memory": kept, "memory_on": memory.enabled(),
            "live": sum(1 for r in runs if r["status"] == checkpoints.RUNNING),
            "waiting": sum(1 for r in runs if r["shown"] in checkpoints.PAUSED)}


def sessions_waiting() -> dict[str, int]:
    """How many runs wait (for a reply, an approval, a person, or /resume) in each session."""
    out: dict[str, int] = {}
    for cp in checkpoints.waiting():
        out[cp.session_id] = out.get(cp.session_id, 0) + 1
    return out


def _this_session() -> str | None:
    """The session of this turn: the run ID in a run.py run, else the console session."""
    run = runtime.current_run()
    return run.run_id if run is not None else runtime.current_session()


def pending_approvals() -> list[dict]:
    """What waits for the operator's answer, one dict each, with an `id`, the console
    `session_id` it came from, and the approval screen in reading order. A pending record
    our system writes, such as a drafted reply, is not an approval: it is on /pending.
    Written by Shruti Tewaney: the gate is agent/approvals.py."""
    return approvals.pending()


def decide_approval(approval_id: str, approved: bool, note: str = "") -> Reply | None:
    """The operator's answer to one approval: record it, and carry on the work that
    waited for it. Written by Shruti Tewaney: the gate is agent/approvals.py. As in
    operator_turn, the answer writes the async checkpoints before it replies."""
    try:
        return approvals.decide(approval_id, approved, note)
    finally:
        checkpoints.flush()
