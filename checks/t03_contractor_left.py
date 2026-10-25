"""t03_contractor_left (U3, workflow): Jonas Berg keeps no active membership but a
protected one; one notice; the request is done. Owner: Aman Panigrahi."""

from checks import common

JONAS, THREAD, REQ = "SYN-EMP-0150", "SYN-HR-T03", "SYN-REQ-0001"


def check(run):
    counts = common.measures(run, {REQ: "workflow:offboarding"})
    problems = []
    left = common.active_groups(run, JONAS) - common.protected_groups(run)
    if left:
        problems.append(f"{JONAS} still holds {sorted(left)}")
    if len(common.sent(run, THREAD)) != 1:
        problems.append(f"{len(common.sent(run, THREAD))} notices on {THREAD}, not exactly one")
    status = (common.request(run, REQ) or {}).get("status")
    if status != "done":
        problems.append(f"{REQ} is {status}, not done")
    return common.result(problems, "all unprotected access removed, one notice, request done", counts)
