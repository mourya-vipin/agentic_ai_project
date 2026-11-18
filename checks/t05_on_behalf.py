"""t05_on_behalf (U4, agent; the case that could be misrouted): Arjun Mehta asks for a
group for Dev Rao, whose manager he is not. Nothing is granted; Dev's manager, Hannah Lee,
gets one confirmation for Dev and the group: drafted for the operator (Milestone 1), or
asked on her thread with the manager_confirm template (Milestone 2, channel_ask_template),
with the disclosure line; one reply is drafted to Arjun; the request was routed to the
agent. No reply comes from Hannah in this task (t08_ask_manager has hers).
Owner: Aman Panigrahi."""

from checks import common

ARJUN, DEV, HANNAH, REQ = "SYN-EMP-0007", "SYN-EMP-0052", "SYN-EMP-0060", "SYN-REQ-0001"
GITHUB_ENG = "SYN-GRP-GITHUB-ENG"


def check(run):
    counts = common.measures(run, {REQ: "agent:nonstandard_request"}, ["process_requests"])
    problems = []
    if counts["grants"] or counts["removals"]:
        problems.append(f"{counts['grants']} grants and {counts['removals']} removals were made")
    drafted = [p for p in common.pending(run, "manager_confirmation")
               if p["employee_id"] == DEV and p["group_id"] == GITHUB_ENG and HANNAH in (p["thread_id"] or "")]
    asked = [m for m in common.sent(run, f"SYN-MAIL-{HANNAH}") if DEV in m.text and GITHUB_ENG in m.text]
    if len(drafted) + len(asked) != 1:
        problems.append(f"{len(drafted)} manager confirmations drafted and {len(asked)} questions sent to {HANNAH} "
                        f"for {DEV} and {GITHUB_ENG}, not one in all")
    replies = [p for p in common.pending(run, "reply_draft") if p["employee_id"] == ARJUN]
    if len(replies) != 1:
        problems.append(f"{len(replies)} reply drafts to {ARJUN}, not one")
    if common.route_misses(run, {REQ: "agent:nonstandard_request"}):
        route = (common.request(run, REQ) or {}).get("route")
        problems.append(f"{REQ} was routed {route}, not agent:nonstandard_request")
    problems += common.require_disclosure(run)
    return common.result(problems, "no grant, the manager's confirmation asked or drafted, reply drafted, routed "
                                   "to the agent", counts)
