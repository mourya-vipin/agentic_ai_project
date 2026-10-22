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

from harness.turns import Reply  # noqa: F401  (what each turn returns)


def operator_turn(session_id: str, text: str) -> Reply:
    """One message from the operator in this session: give a task, add to it, ask
    about it. Route the case to its path, as your routing and the switches say, and
    reply."""
    raise NotImplementedError("operator_turn: route each case to its path here, the agent path for the use cases "
                              "the gates give it, workflows for the rest")


def on_outside_reply(thread_id: str, text: str) -> Reply:
    """A stranger's message on this thread, delivered here once.

    The message is stored, and this returns an empty Reply until you build the
    outside exchange, so a message typed on the threads page is ignored. Once you
    build it, the agent asks on the thread, ends its turn with
    Reply(waiting_for=thread_id), and carries on here when the answer arrives,
    finding the work that waits on this thread. Do not wait for `text` again."""
    return Reply()


def pending_approvals() -> list[dict]:
    """What waits for the operator's answer, one dict each, with an `id` and the
    facts the approval screen shows. Give it the `session_id` of the console session
    it came from, if any: the app then answers it in that session, so the answer and
    the work it resumes land in that session's trace and count against its budget. It stays empty until you build an approval
    gate; a pending record your system writes, such as a drafted reply for the
    operator to send by hand, is not an approval: list it on your product's
    pages."""
    return []


def decide_approval(approval_id: str, approved: bool, note: str = "") -> Reply | None:
    """The operator's answer to one approval: record it, and carry on the work that
    waited for it."""
    raise NotImplementedError("decide_approval: no approval gate is built yet")
