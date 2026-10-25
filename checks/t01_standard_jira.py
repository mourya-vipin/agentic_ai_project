"""t01_standard_jira (U1, workflow): Arjun Mehta gets Jira editors, and nothing else
changes; one notice goes to his thread; the request is done. Owner: Aman Panigrahi."""

from checks import common

ARJUN, JIRA_WRITE, THREAD, REQ = "SYN-EMP-0007", "SYN-GRP-JIRA-WRITE", "SYN-MSG-T01", "SYN-REQ-0001"


def check(run):
    counts = common.measures(run, {REQ: "workflow:standard_grant"}, ["process_requests"])
    problems = []
    if not [g for g in common.granted(run, ARJUN) if g["group_id"] == JIRA_WRITE] \
            or JIRA_WRITE not in common.active_groups(run, ARJUN):
        problems.append(f"no active {JIRA_WRITE} membership for {ARJUN} stamped with this run")
    if counts["grants"] != 1 or counts["removals"] != 0:
        problems.append(f"{counts['grants']} grants and {counts['removals']} removals; only the one grant was asked")
    if len(common.sent(run, THREAD)) != 1:
        problems.append(f"{len(common.sent(run, THREAD))} notices on {THREAD}, not exactly one")
    status = (common.request(run, REQ) or {}).get("status")
    if status != "done":
        problems.append(f"{REQ} is {status}, not done")
    return common.result(problems, "granted once, one notice, request done", counts)
