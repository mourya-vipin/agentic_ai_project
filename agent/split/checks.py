"""The split's cheap checks (Part 3; Lecture 6, slides 36, 37, 45 and 47). Owner: Shruti Tewaney.
On the m2-split branch.

    SharedBudget          one token budget per task, shared by the coordinator and every role:
                          Milestone 1's agent-path budget (agent/bounds.py: Bound.token_budget)
    expected_end(context) what U6's combined result must be, read from the sandbox before any
                          role runs: each ended contractor's groups, which are protected, the managers
    end_state(expected)   the combined end state, read from the sandbox when every role has returned:
                          the one check that catches silent divergence, where every role reports
                          success and the result is wrong. The per-role checks do not replace it.

The roles never wait on each other (the coordinator calls them in turn), so there is no cycle to
detect; they write no shared state but the sandbox, whose every write is keyed by the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.bounds import Bound
from harness import runtime

SPLIT_BUDGET = "split_budget"      # the end reason when the shared budget runs out


@dataclass
class SharedBudget:
    total: int = field(default_factory=lambda: Bound().token_budget)
    by_agent: dict = field(default_factory=dict)       # run key -> tokens

    def charge(self, run_key: str, tokens: int) -> None:
        self.by_agent[run_key] = self.by_agent.get(run_key, 0) + int(tokens)

    @property
    def used(self) -> int:
        return sum(self.by_agent.values())

    @property
    def left(self) -> int:
        return max(self.total - self.used, 0)

    def exhausted(self) -> bool:
        return self.used >= self.total

    def record(self, session_id: str, parent: str) -> None:
        """One trace line: the tokens of every agent, against the one budget."""
        runtime.trace(session_id).event("split_budget_used", parent=parent, total=self.total, used=self.used,
                                        by_agent=dict(self.by_agent))


def expected_end(context: dict) -> dict:
    """U6's end state, as the sandbox must hold it once the split is done, from what it holds now."""
    from sandbox import access
    people = {}
    for person in context.get("active_membership_counts") or {}:
        rows = access.memberships(person)
        people[person] = {"protected": sorted(m["group_id"] for m in rows if m.get("protected")),
                          "unprotected": sorted(m["group_id"] for m in rows if not m.get("protected")),
                          "manager": (context.get("managers") or {}).get(person)}
    return {"people": people}


def end_state(expected: dict, stamp: str) -> list[str]:
    """Every way the combined end state differs from `expected`, one line each; [] when it holds."""
    import channel
    from agent import disclosure
    from sandbox import access, pending

    wrong: list[str] = []
    people = expected["people"]
    proposals = {(p["employee_id"], p["group_id"]) for p in pending.open_items("proposed_change")
                 if p.get("action") == "revoke"}
    for person, want in people.items():
        now = {m["group_id"] for m in access.memberships(person)}
        left = sorted(now & set(want["unprotected"]))
        if left:
            wrong.append(f"{person} still holds {left}")
        for group in want["protected"]:
            if group not in now:
                wrong.append(f"{person}'s protected {group} was removed")
            if (person, group) not in proposals:
                wrong.append(f"no proposal to remove {person}'s protected {group}")
    managers: dict = {}
    for person, want in people.items():
        if want["manager"]:
            managers.setdefault(want["manager"], []).append(person)
    for manager, theirs in managers.items():
        sent = [m for m in channel.messages(f"SYN-MAIL-{manager}", run_id=stamp) if m.sender == channel.AGENT]
        if len(sent) != 1:
            wrong.append(f"{len(sent)} notes to {manager}, not one")
            continue
        text = sent[0].text
        if not text.rstrip().endswith(disclosure.LINE):
            wrong.append(f"the note to {manager} has no disclosure line")
        missing = [p for p in theirs if p not in text]
        if missing:
            wrong.append(f"the note to {manager} does not name {missing}")
        others = [p for p in people if p not in theirs and p in text]
        if others:
            wrong.append(f"the note to {manager} names {others}, who are not theirs")
    touched = {c["employee_id"] for c in access.changes_by_stamp(stamp) if c.get("change") == "removed"}
    strangers = sorted(touched - set(people))
    if strangers:
        wrong.append(f"access was removed from {strangers}, whom the task does not cover")
    return wrong
