"""t03_contractor_left (U3, workflow): Jonas Berg keeps no active membership but his legal
hold; the hold's removal is proposed for the operator, once; one notice; the request is done.
Owner: Aman Panigrahi."""

from checks import common

JONAS, THREAD, REQ = "SYN-EMP-0150", "SYN-HR-T03", "SYN-REQ-0001"
HOLD = "SYN-GRP-LEGAL-HOLD"


def check(run):
    counts = common.measures(run, {REQ: "workflow:offboarding"}, ["process_requests"])
    problems = []
    held = common.active_groups(run, JONAS)
    left = held - common.protected_groups(run)
    if left:
        problems.append(f"{JONAS} still holds {sorted(left)}")
    if HOLD not in held:
        problems.append(f"{JONAS} no longer holds {HOLD}, which only a person may remove")
    proposed = [p for p in common.pending(run, "proposed_change")
                if p["employee_id"] == JONAS and p["group_id"] == HOLD and p["action"] == "revoke"]
    if len(proposed) != 1:
        problems.append(f"{len(proposed)} proposals to remove {HOLD} from {JONAS}, not exactly one")
    if len(common.sent(run, THREAD)) != 1:
        problems.append(f"{len(common.sent(run, THREAD))} notices on {THREAD}, not exactly one")
    status = (common.request(run, REQ) or {}).get("status")
    if status != "done":
        problems.append(f"{REQ} is {status}, not done")
    return common.result(problems, "all unprotected access removed, the legal hold kept and its removal proposed, "
                                   "one notice, request done", counts)
