"""The check for the starter's test task: the agent wrote back on the input's thread."""

import channel


def check(run):
    thread_id = run.task["input"]["thread"]
    sent = [m for m in channel.messages(thread_id, run_id=run.run_id) if not m.from_outside]
    return (bool(sent), f"the agent sent {len(sent)} message(s) on {thread_id}")


def check_with_measures(run):
    """The same check, returning named counts as well."""
    passed, reason = check(run)
    sent = len([m for m in channel.messages(run.task["input"]["thread"], run_id=run.run_id) if not m.from_outside])
    return passed, reason, {"messages_sent": sent, "asked": sent > 0}


def check_with_bad_measures(run):
    """Measures that are not counts: the run fails, and says why."""
    return True, "fine", {"messages_sent": "two"}
