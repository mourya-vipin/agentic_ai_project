"""Reset the sandbox, and seed it.

run.py calls reset() and then seed(task) before every run, with that run's task.
`make app` and `make seed` call seed(None): your demo data, for staff in the app
and for your product's pages. `make seed TASK=tasks/<file>.yaml` calls seed(task)
with that task, to try it by hand. The channel's threads are emptied before
reset() is called, and every message the seed writes on the channel is marked
seeded: the input, not part of the exchange.

Fill both in for your domain:

    reset()      empty every store your system writes to: a SQLite file under
                 state_dir(), a folder of JSON files, your mock API's data, a
                 local mailbox.
    seed(task)   load your base data (the records your agent works on, every one
                 synthetic and marked), then:
                   task None: your demo data, the work staff see waiting in the app
                   and on your product's pages, with strangers' messages on the
                   channel (channel.receive), so the console has work to do;
                   otherwise: that task's inputs alone, every one of them:
                   task["input"], or each item of task["inputs"] when the task
                   gives several, in whatever shape your task files give them.

Stamp every write your system makes with the run's ID (harness.runtime.current_run()),
so a check reads only that run's effects. In the app there is no run: stamp the
write with the console session's ID, harness.runtime.current_session(). A write made
by hand on your product's pages has neither: stamp it with a marker of your own,
such as manual.
"""

from __future__ import annotations

import channel
from harness.paths import state_dir  # noqa: F401  (where your sandbox's files go)

# True when reset() and seed() touch only files under state_dir(), so that run.py
# can give each parallel worker a state folder of its own (run.py --jobs N).
PARALLEL_SAFE = False


def reset() -> None:
    """Empty the sandbox. The starter's sandbox holds nothing yet."""


def seed(task: dict | None) -> None:
    """Load your base data, then your demo data (task None) or every input of the
    task. The starter has no base data and no demo data. As a start, each input
    with a `thread` and `text` arrives on the outside channel, as a message from
    `from`; change that to fit your domain."""
    if task is None:
        return
    for item in task["inputs"] if "inputs" in task else [task["input"]]:
        if isinstance(item, dict) and item.get("thread") and item.get("text"):
            channel.receive(str(item["thread"]), str(item["text"]), sender=str(item.get("from") or "outside"))
