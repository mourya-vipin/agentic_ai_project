"""The agent's one entry point. The app and run.py reach the agent only through
these functions, by the same path (harness/turns.py), so a scripted run is what
the operator's console does, minus the browser. `make check` tests it.

    operator_turn(session_id, text)   a message from the operator, at the console or from a test task file
    on_outside_reply(thread_id, text) a stranger's message on one of your channel's threads
    pending_approvals()               what waits for the operator's answer (the approvals page)
    decide_approval(id, approved, note)  the operator's answer

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

from agent import approvals, checkpoints, commands, notes, router
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
         each call (policy/limits.py). The reply starts with what our code recorded.
      3. The router sends the message to its path.
    The turn writes the async checkpoints before it replies, so that the operator never
    reads a result that the run store does not hold."""
    try:
        command = commands.handle(session_id, text)
        if command is not None:
            return command
        recorded = _limits(session_id, text)
        reply = router.handle_operator(session_id, text)
    finally:
        checkpoints.flush()
    if not recorded:
        return reply
    return Reply("\n".join([*recorded, reply.text]).strip(), reply.actions, reply.waiting_for)


def _limits(session_id: str, text: str) -> list[str]:
    """Lift, then record, the limits that the message gives. A message can lift the old
    rules and state a new one, and the new rule stays. One line for each change."""
    lines = []
    lifted = limits.lift_from_text(session_id, text)
    if lifted:
        lines.append(f"Rules lifted: {', '.join(lifted)}.")
    rule = limits.from_text(session_id, text)
    if rule is not None:
        until = "until you lift it" if rule.until == limits.OPERATOR_LIFTS else f"until {rule.until}"
        lines.append(f'Rule {rule.id} is recorded. It holds {until}: "{rule.text}"')
    return lines


def on_outside_reply(thread_id: str, text: str) -> Reply:
    """A stranger's message on this thread, delivered here once.

    The message is stored, and this returns an empty Reply until you build the
    outside exchange, so a message typed on the threads page is ignored. Once you
    build it, the agent asks on the thread, ends its turn with
    Reply(waiting_for=thread_id), and carries on here when the answer arrives,
    finding the work that waits on this thread. Do not wait for `text` again.

    Owner: Aman Panigrahi. The run that waits for a reply on this thread continues from
    its checkpoint (checkpoints.resume), in this process or after a restart. The notes
    mark the thread as answered (agent/notes.py). If no run waits on the thread, the
    message stays in the channel, and the reply is empty."""
    run = next((cp for cp in checkpoints.waiting("reply") if (cp.waiting_for or {}).get("thread_id") == thread_id),
               None)
    session_id = run.session_id if run is not None else _this_session()
    if session_id:
        notes.thread_answered(session_id, thread_id)
    if run is None:
        return Reply()
    try:
        return checkpoints.resume(run.run_key, {"kind": "reply", "thread_id": thread_id, "text": text})
    finally:
        checkpoints.flush()


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
    waited for it. Written by Shruti Tewaney: the gate is agent/approvals.py."""
    return approvals.decide(approval_id, approved, note)
