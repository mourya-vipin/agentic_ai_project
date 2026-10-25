"""The detectors (Lecture 2, slides 49 to 52): the repeat check on every agent-path
run, the orphan check on every plan, and the diff of a plan against what ran.

Owner: Shruti Tewaney. Ported from Shruti's Assignment 2 (agent/detectors.py and
agent/diff.py): the repeat and orphan checks as they were; the diff with our labels
(out_of_order for A2's reordered) and a step's tool_call lines matched by plan_step
alone, since every call the executor makes names its step.

    RepeatTracker().see(tool, args)   live, on every call of the loop and the executor
    repeat_check(lines)               the same over a trace, for the analysis script
    orphan_check(plan, exempt)        steps whose result nothing later uses
    diff(plan, lines)                 every plan step executed, skipped or out of order
"""

from __future__ import annotations

import hashlib
import json


def _canonical(value):
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, str):
        text = " ".join(value.split())
        return text.upper() if text.upper().startswith("SYN-") else text
    return value


def call_hash(tool: str, args: dict) -> str:
    """The same tool with the same arguments, in canonical form, hashes the same."""
    material = json.dumps([tool, _canonical(args or {})], sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


class RepeatTracker:
    """One per turn of the agent path. Every call goes through see() before it runs."""

    def __init__(self):
        self._first: dict[str, int] = {}
        self.calls = 0
        self.repeats = 0

    def see(self, tool: str, args: dict, step: int | None = None) -> dict | None:
        """The earlier call's {"step": n, "hash": h} if this call repeats one exactly
        (same tool, same canonical arguments), else None. The caller writes
        trace.event("repeat_call", ...). `step` is the caller's step number; by default
        the count of calls seen."""
        self.calls += 1
        h = call_hash(tool, args)
        if h in self._first:
            self.repeats += 1
            return {"step": self._first[h], "hash": h}
        self._first[h] = self.calls if step is None else step
        return None


def repeat_check(lines: list[dict]) -> list[list]:
    """Each group of exact repeats among a trace's tool_call lines, as their step numbers."""
    groups: dict[str, list] = {}
    for line in lines:
        if line.get("type") == "tool_call":
            groups.setdefault(call_hash(line.get("name"), line.get("input") or {}), []).append(line.get("step"))
    return [steps for steps in groups.values() if len(steps) > 1]


def orphan_check(plan: dict, exempt: tuple[str, ...] = ()) -> list:
    """Hallucinated steps (slide 49): the ids of steps, the last aside, that no later
    step lists in depends_on. Steps whose tool is in `exempt` are not flagged: a write,
    whose effect is its point, and a read the checkpoint itself reads."""
    steps = plan.get("steps") or []
    orphans = []
    for i, step in enumerate(steps[:-1]):
        if step.get("tool") in exempt:
            continue
        if not any(step.get("id") in (s.get("depends_on") or []) for s in steps[i + 1:]):
            orphans.append(step.get("id"))
    return orphans


def diff(plan: dict, lines: list[dict]) -> dict:
    """The plan against what ran (slide 11). Each plan step is executed (its tool ran,
    in plan order), out_of_order (it ran before a step the plan puts ahead of it), or skipped (it
    never ran: the run halted or hit a bound first); failed lists the executed steps
    whose every call returned an error. extra holds the step numbers of tool_call lines
    that match no plan step."""
    steps = plan.get("steps") or []
    by_id = {s.get("id"): s for s in steps}
    matched: dict = {s.get("id"): [] for s in steps}
    extra = []
    for at, line in enumerate(lines):
        if line.get("type") != "tool_call":
            continue
        step = by_id.get(line.get("plan_step"))
        if step is not None and step.get("tool") == line.get("name"):
            matched[step.get("id")].append((at, line))
        else:
            extra.append(line.get("step"))
    executed, skipped, out_of_order, failed = [], [], [], []
    latest = -1
    for step in steps:
        sid = step.get("id")
        found = matched[sid]
        if not found:
            skipped.append(sid)
            continue
        first = found[0][0]
        (executed if first > latest else out_of_order).append(sid)
        latest = max(latest, first)
        if all(line.get("is_error") for _, line in found):
            failed.append(sid)
    return {"executed": executed, "skipped": skipped, "out_of_order": out_of_order, "failed": failed,
            "extra": extra}
