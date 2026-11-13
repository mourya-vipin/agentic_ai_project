"""The line that tells a stranger an automated agent wrote the message (Lecture 5, slides 42 to 44).

Owner: Vipin Mourya.

Our code adds it, never a prompt: a line the model is told to add is only as reliable
as the model. Every send passes stamp() before it reaches the registry: the agent loop,
a workflow, the executor and a code-call program alike, so an approval screen's dry
run shows the line too. The hook (policy/hook.py) refuses a send whose text lacks it.
A message the operator writes and sends by hand is the operator's own and needs none.
"""

from __future__ import annotations

LINE = ("— Sent by the IT access desk's automated agent. To reach a person, reply HUMAN "
        "or write to it-desk@synthetic.example.")


def has_line(text: str | None) -> bool:
    """Whether the text carries the line, as its own last paragraph."""
    return bool(text) and str(text).rstrip().endswith(LINE)


def stamp(text: str) -> str:
    """The text with the line as its last paragraph, added once: stamping twice changes nothing."""
    body = str(text or "").rstrip()
    if body.endswith(LINE):
        return body
    return f"{body}\n\n{LINE}" if body else LINE


def without_line(text: str | None) -> str:
    """The text as its writer wrote it, for a check that reads the message's own claims."""
    body = str(text or "").rstrip()
    return body[: -len(LINE)].rstrip() if body.endswith(LINE) else body


# the argument that carries a send's text, per tool whose text the model writes
TEXT_OF_SEND = {"reply_send": "body"}


def stamp_call(tool: str, args: dict) -> dict:
    """The call's arguments with its message stamped: what the loop, the executor and a
    code-call program pass to the registry for a send whose text the model wrote. Any other
    call comes back as it was. Template sends are stamped where our code renders them."""
    name = TEXT_OF_SEND.get(tool)
    if name is None or not isinstance(args, dict) or args.get(name) is None:
        return args
    return {**args, name: stamp(args[name])}
