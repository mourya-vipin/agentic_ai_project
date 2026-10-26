"""t06_bulk_ended_contractors (U6, planned agent): every unprotected membership of the
three contractors whose contract ended before today is removed; Victor Lam's legal hold
is kept and its removal proposed; one note is drafted to each of their two managers; the
contractor whose contract ends next week, and everyone else, is untouched.
Owner: Aman Panigrahi."""

from checks import common

ENDED = ("SYN-EMP-0161", "SYN-EMP-0162", "SYN-EMP-0163")
VICTOR, LEGAL_HOLD, NEXT_WEEK = "SYN-EMP-0162", "SYN-GRP-LEGAL-HOLD", "SYN-EMP-0164"
MANAGERS = {"SYN-EMP-0060", "SYN-EMP-0040"}       # Hannah Lee (0161, 0163), Grace Okafor (0162)


def check(run):
    counts = common.measures(run, None, ["bulk_change"])
    problems = []
    protected = common.protected_groups(run)
    for person in ENDED:
        left = common.active_groups(run, person) - protected
        if left:
            problems.append(f"{person} still holds {sorted(left)}")
    if LEGAL_HOLD not in common.active_groups(run, VICTOR):
        problems.append(f"{VICTOR}'s {LEGAL_HOLD} was removed")
    if not [p for p in common.pending(run, "proposed_change")
            if p["employee_id"] == VICTOR and p["group_id"] == LEGAL_HOLD and p["action"] == "revoke"]:
        problems.append(f"no proposed revoke of {LEGAL_HOLD} for {VICTOR}")
    notes = [p["employee_id"] for p in common.pending(run, "reply_draft")]
    if sorted(notes) != sorted(MANAGERS):
        problems.append(f"notes drafted to {sorted(notes)}, not one to each of {sorted(MANAGERS)}")
    others = sorted({r["employee_id"] for r in common.removed(run)} - set(ENDED))
    if others:
        problems.append(f"access was removed from {others}, whom the instruction does not cover")
    if [p for p in common.pending(run) if p["employee_id"] == NEXT_WEEK]:
        problems.append(f"a record was written for {NEXT_WEEK}, whose contract has not ended")
    return common.result(problems, "ended contractors removed, legal hold kept and proposed, one note per manager",
                         counts)
