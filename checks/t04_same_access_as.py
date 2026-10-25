"""t04_same_access_as (U4, agent): of Priya Nair's groups Leo Martins lacks, the three
his role allows are granted, the two that need a person are proposed and not granted,
and one reply is drafted under the request. Owner: Aman Panigrahi."""

from checks import common

LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0001"
ALLOWED = {"SYN-GRP-FINREP-READ", "SYN-GRP-CONFLUENCE-FINANCE", "SYN-GRP-JIRA-WRITE"}
NEEDS_A_PERSON = {"SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN"}   # needs_manager, and high sensitivity


def check(run):
    counts = common.measures(run, {REQ: "agent:nonstandard_request"})
    problems = []
    groups = [g["group_id"] for g in common.granted(run, LEO)]
    if sorted(groups) != sorted(ALLOWED):
        problems.append(f"granted {sorted(groups)}, not exactly {sorted(ALLOWED)}")
    proposed = [p["group_id"] for p in common.pending(run, "proposed_change")
                if p["employee_id"] == LEO and p["action"] == "grant"]
    if sorted(proposed) != sorted(NEEDS_A_PERSON):
        problems.append(f"proposed {sorted(proposed)}, not exactly {sorted(NEEDS_A_PERSON)}")
    replies = [p for p in common.pending(run, "reply_draft") if p["employee_id"] == LEO and p["request_id"] == REQ]
    if len(replies) != 1:
        problems.append(f"{len(replies)} reply drafts to {LEO} under {REQ}, not one")
    if counts["grants"] != len(groups):
        problems.append("grants were made to someone other than the requester")
    return common.result(problems, "three allowed groups granted, two proposed, one reply drafted", counts)
