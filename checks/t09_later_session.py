"""t09_later_session (U4, agent; memory and the later session, Part 1, task 7): the earlier
session's preference says to draft a manager confirmation for a request made for someone
else, not to ask the manager on the channel. This session gets such a request, and nobody says
the preference again. The check reads the sandbox, the channel and the run's trace:

    the read      the store was read (memory_read, at the session's start) before the first
                  model call, and the preference reached the agent's call
    the effect    one manager confirmation for Dev Rao and the group, drafted to Hannah Lee;
                  no question sent on her thread; nothing granted
    the rest      one reply drafted to Arjun; the request routed to the agent; the disclosure
                  line on any message our side sent

Owner: Aman Panigrahi."""

from checks import common
from harness.trace import read_trace

ARJUN, DEV, HANNAH, REQ = "SYN-EMP-0007", "SYN-EMP-0052", "SYN-EMP-0060", "SYN-REQ-0001"
GITHUB_ENG = "SYN-GRP-GITHUB-ENG"


def _read_order(run) -> list[str]:
    """Problems with the read: it comes before the first model call, and the call got the record."""
    if run.trace_path is None or not run.trace_path.exists():
        return ["the run has no trace, so the read of the store cannot be checked"]
    lines = read_trace(run.trace_path)
    kinds = [x.get("type") for x in lines]
    reads = [i for i, x in enumerate(lines) if x.get("type") == "memory_read" and x.get("purpose") == "session_start"]
    calls = [i for i, k in enumerate(kinds) if k == "model_call"]
    problems = []
    if not reads:
        problems.append("no memory_read at the session's start")
    elif calls and reads[0] > calls[0]:
        problems.append("the store was read after the first model call, not before it")
    used = [x for x in lines if x.get("type") == "memory_read" and x.get("purpose") == "call" and x.get("returned")]
    if not used:
        problems.append("no model call of the agent got the preference")
    return problems


def check(run):
    counts = common.measures(run, {REQ: "agent:nonstandard_request"}, ["process_requests"])
    problems = _read_order(run)
    if counts["grants"]:
        problems.append(f"{counts['grants']} grants were made")
    drafted = [p for p in common.pending(run, "manager_confirmation")
               if p["employee_id"] == DEV and p["group_id"] == GITHUB_ENG and HANNAH in (p["thread_id"] or "")]
    if len(drafted) != 1:
        problems.append(f"{len(drafted)} manager confirmations for {DEV} and {GITHUB_ENG} drafted to {HANNAH}, not one")
    asked = [m for m in common.sent(run, f"SYN-MAIL-{HANNAH}") if DEV in m.text]
    if asked:
        problems.append(f"{len(asked)} questions were sent to {HANNAH}, which the earlier session's preference rules out")
    replies = [p for p in common.pending(run, "reply_draft") if p["employee_id"] == ARJUN]
    if len(replies) != 1:
        problems.append(f"{len(replies)} reply drafts to {ARJUN}, not one")
    if common.route_misses(run, {REQ: "agent:nonstandard_request"}):
        route = (common.request(run, REQ) or {}).get("route")
        problems.append(f"{REQ} was routed {route}, not agent:nonstandard_request")
    problems += common.require_disclosure(run)
    return common.result(problems, "read the earlier session's preference before the first model call, and drafted "
                                   "the manager confirmation as it says; reply drafted; routed to the agent", counts)
