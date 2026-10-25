"""t05_on_behalf (U4, agent; the case that could be misrouted): Arjun Mehta asks for a
group for Dev Rao, whose manager he is not. Nothing is granted; a manager confirmation
is drafted to Dev's manager, Hannah Lee; one reply is drafted to Arjun; the request was
routed to the agent. Owner: Aman Panigrahi."""

from checks import common

ARJUN, DEV, HANNAH, REQ = "SYN-EMP-0007", "SYN-EMP-0052", "SYN-EMP-0060", "SYN-REQ-0001"
GITHUB_ENG = "SYN-GRP-GITHUB-ENG"


def check(run):
    counts = common.measures(run, {REQ: "agent:nonstandard_request"}, ["process_requests"])
    problems = []
    if counts["grants"]:
        problems.append(f"{counts['grants']} grants were made")
    asks = [p for p in common.pending(run, "manager_confirmation")
            if p["employee_id"] == DEV and p["group_id"] == GITHUB_ENG and HANNAH in (p["thread_id"] or "")]
    if len(asks) != 1:
        problems.append(f"{len(asks)} manager confirmations for {DEV} and {GITHUB_ENG} addressed to {HANNAH}, not one")
    replies = [p for p in common.pending(run, "reply_draft") if p["employee_id"] == ARJUN]
    if len(replies) != 1:
        problems.append(f"{len(replies)} reply drafts to {ARJUN}, not one")
    if common.route_misses(run, {REQ: "agent:nonstandard_request"}):
        route = (common.request(run, REQ) or {}).get("route")
        problems.append(f"{REQ} was routed {route}, not agent:nonstandard_request")
    return common.result(problems, "no grant, manager confirmation drafted, reply drafted, routed to the agent",
                         counts)
