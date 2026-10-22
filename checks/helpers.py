"""Helpers for checks: read the sandbox and the channel for one run."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import channel


def thread(run, thread_id: str, with_input: bool = False) -> list:
    """This run's exchange on a thread, oldest first: the agent's messages (sender
    "agent"), any the operator sent by hand ("operator") and the stranger's
    replies. The task's input, which the seed wrote on
    the thread before the run, is left out unless with_input=True, so the first
    message from outside is the stranger's first reply."""
    return [m for m in channel.messages(thread_id, run_id=run.run_id) if with_input or not m.seeded]


def sent_by_agent(run, thread_id: str) -> list:
    """What the agent sent on a thread during this run."""
    return [m for m in thread(run, thread_id) if m.sender == channel.AGENT]


class _Closing(sqlite3.Connection):
    """A connection that also closes when its `with` block ends."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect(run, filename: str) -> sqlite3.Connection:
    """A read-only connection to a SQLite file of your sandbox, under the run's state folder.
    `with connect(run, "x.sqlite3") as db:` closes it at the end of the block."""
    path = Path(run.state_dir) / filename
    if not path.exists():
        raise FileNotFoundError(f"the sandbox has no {filename}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, factory=_Closing)
