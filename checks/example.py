"""The check for tasks/example.yaml. Replace it with your own checks.

It shows the shape: read the environment after the run, for this run's ID, and
decide. This one passes when the agent wrote back on the thread where the input
arrived. Yours should read the effect your task exists for: the record that now
exists, the message that was sent, the booking that was made.
"""

from __future__ import annotations

from checks.helpers import sent_by_agent


def check(run):
    thread_id = run.task["input"]["thread"]
    sent = sent_by_agent(run, thread_id)
    if not sent:
        return False, f"the agent sent nothing on thread {thread_id}"
    return True, f"the agent sent {len(sent)} message(s) on thread {thread_id}"
