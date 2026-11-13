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
