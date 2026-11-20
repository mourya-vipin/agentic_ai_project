"""State, the stranger and memory: the numbers of M2.md's Part 1 (tasks 2, 5, 7 and 9) and of the
decision table's rows on state, which the summarizer does not print. Owner: Aman Panigrahi.

    python experiments/analysis/state.py results/m2-build
    python experiments/analysis/state.py results/m2-build results/pass3-before results/pass3-after \
        results/m2-fixes-check --per-day 35
    python experiments/analysis/state.py --recovery docs/milestones/m2_recovery_2026-11-18.json
    python experiments/analysis/state.py results/m2-build --per-day 35 --json docs/milestones/m2_state.json

It reads the runs that the summarizer counts (analyze.runs_of: the latest finished run of each
task and sweep). For each arm it prints:

- each task: its runs and how many passed; the questions to a stranger (channel_ask_template
  calls that went out) and the runs that asked; the turns that ended to wait for a reply
  (waiting_reply); the replies delivered (outside lines handed to on_outside_reply), and those
  that went to the operator instead of a run (outside_for_operator: a HUMAN, or a reply that no
  run waits for); and the waits that passed their deadline (reply_deadline)
- the memory: the records written (memory_write lines, by how), declined and evicted; the reads
  (memory_read lines, by purpose), and the reads that returned a record; the records written per
  run. With --per-day N: the records a day at N runs a day, and the days until the store holds
  MAX_RECORDS (agent/memory.py), when nothing is evicted and no record is merged
- the later session, t09: the runs that acted on the earlier session's record, that is, that
  drafted the manager confirmation (manager_confirmation_draft) and asked no one on the channel

--recovery reads the recovery test's record (tests/test_recovery.py, scripts/recovery_states.py):
each case, the write it died at, the effects it changed, and each effect whose count after the
resume is not the count of the run with no crash ("differs"); "more than once" adds the counts
above that run's. Both are 0 when every case recovered.

Spends no tokens; reads results/ and the recovery record only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze import runs_of, trace_of  # noqa: E402

from agent.memory import MAX_RECORDS  # noqa: E402

LATER_SESSION = "t09_later_session"


def _made(line: dict, name: str) -> bool:
    """A tool_call line of this tool whose write went out: no error, and not put up for approval."""
    return (line.get("type") == "tool_call" and line.get("name") == name and not line.get("is_error")
            and not line.get("awaiting_approval"))


def count(runs: list[tuple[dict, list[dict]]]) -> dict:
    """The numbers of one arm, from (record, trace lines) for each of its counted runs."""
    tasks: dict[str, dict] = defaultdict(lambda: {"runs": 0, "passed": 0, "questions": 0, "runs_that_asked": 0,
                                                   "waits": 0, "replies_delivered": 0, "for_the_operator": 0,
                                                   "past_deadline": 0})
    writes, declined, reads = Counter(), 0, Counter()
    evicted, reads_with_a_record = 0, 0
    later = {"runs": 0, "acted_on_the_record": 0, "passed": 0}
    for record, lines in runs:
        row = tasks[record["task"]]
        row["runs"] += 1
        row["passed"] += int(bool(record.get("passed")))
        asked = sum(1 for x in lines if _made(x, "channel_ask_template"))
        row["questions"] += asked
        row["runs_that_asked"] += int(asked > 0)
        row["waits"] += sum(1 for x in lines if x.get("type") == "end" and x.get("terminal_reason") == "waiting_reply")
        row["replies_delivered"] += sum(1 for x in lines if x.get("type") == "outside"
                                        and x.get("handled_by") == "on_outside_reply")
        row["for_the_operator"] += sum(1 for x in lines if x.get("type") == "outside_for_operator")
        row["past_deadline"] += sum(1 for x in lines if x.get("type") == "reply_deadline")
        for x in lines:
            kind = x.get("type")
            if kind == "memory_write":
                writes[x.get("how") or "?"] += 1
            elif kind == "memory_declined":
                declined += 1
            elif kind == "memory_evicted":
                evicted += 1
            elif kind == "memory_read":
                reads[x.get("purpose") or "?"] += 1
                reads_with_a_record += int(bool(x.get("returned")))
        if record["task"] == LATER_SESSION:
            later["runs"] += 1
            later["passed"] += int(bool(record.get("passed")))
            later["acted_on_the_record"] += int(any(_made(x, "manager_confirmation_draft") for x in lines)
                                                and not asked)
    n = len(runs)
    written = sum(writes.values())
    return {
        "runs": n,
        "tasks": {task: dict(row) for task, row in sorted(tasks.items())},
        "memory": {"written": written, "written_by_how": dict(writes), "declined": declined, "evicted": evicted,
                   "reads": sum(reads.values()), "reads_by_purpose": dict(reads),
                   "reads_that_returned_a_record": reads_with_a_record,
                   "records_per_run": round(written / n, 3) if n else None},
        "later_session": later,
    }


def lifetime(row: dict, per_day: float | None) -> dict | None:
    """Records a day at per_day runs a day, and the days until the store holds MAX_RECORDS."""
    per_run = row["memory"]["records_per_run"]
    if not per_day or not per_run:
        return None
    a_day = per_run * per_day
    return {"runs_a_day": per_day, "records_a_day": round(a_day, 2), "store_size": MAX_RECORDS,
            "days_to_fill": round(MAX_RECORDS / a_day, 1)}


def recovery(path: Path) -> dict:
    """Each case of the recovery test's record, and the effects that differ from the run with no crash."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    out = []
    for case in cases:
        effects = case.get("effects") or []
        differs = [e["effect"] for e in effects if e["after_resume"] != e["no_crash"]]
        out.append({"task": Path(case["task"]).stem, "died_at": case.get("died_at"), "effects": len(effects),
                    "differs": len(differs), "more_than_once": sum(max(0, e["after_resume"] - e["no_crash"])
                                                                   for e in effects)})
    return {"cases": len(out), "tasks": sorted({c["task"] for c in out}),
            "differs": sum(c["differs"] for c in out), "more_than_once": sum(c["more_than_once"] for c in out),
            "by_case": out}


def report(arms: dict[str, dict], per_day: float | None) -> str:
    lines = []
    for name, row in arms.items():
        lines.append(f"{name}: {row['runs']} runs")
        for task, t in row["tasks"].items():
            lines.append(f"  {task}: {t['passed']} of {t['runs']} passed; {t['questions']} questions to a stranger "
                         f"in {t['runs_that_asked']} runs; {t['waits']} waits, {t['replies_delivered']} replies delivered, "
                         f"{t['for_the_operator']} of them for the operator, {t['past_deadline']} past the deadline")
        m = row["memory"]
        lines.append(f"  memory: {m['written']} records written {m['written_by_how']}, {m['declined']} declined, "
                     f"{m['evicted']} evicted; {m['reads']} reads {m['reads_by_purpose']}, "
                     f"{m['reads_that_returned_a_record']} returned a record; {m['records_per_run']} records a run")
        life = lifetime(row, per_day)
        if life:
            lines.append(f"  lifetime: at {life['runs_a_day']:g} runs a day, {life['records_a_day']} records a day, "
                         f"so {life['store_size']} records in {life['days_to_fill']} days")
        later = row["later_session"]
        if later["runs"]:
            lines.append(f"  {LATER_SESSION}: acted on the record in {later['acted_on_the_record']} of "
                         f"{later['runs']} runs; {later['passed']} passed")
    return "\n".join(lines)


def report_recovery(found: dict) -> str:
    lines = [f"recovery: {found['cases']} cases on {', '.join(found['tasks'])}; {found['differs']} effects differ "
             f"from the run with no crash; {found['more_than_once']} more than once"]
    for c in found["by_case"]:
        died = c["died_at"] or {}
        lines.append(f"  {c['task']}, died at {died.get('tool')} (write {died.get('write')}): {c['effects']} effects "
                     f"changed, {c['differs']} differ")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("arms", nargs="*", type=Path, help="results/<arm> folders")
    parser.add_argument("--per-day", type=float, help="runs a day, for the memory store's lifetime")
    parser.add_argument("--recovery", type=Path, help="the recovery test's record (docs/milestones/m2_recovery_*.json)")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args(argv)
    if not args.arms and not args.recovery:
        parser.error("give at least one results/<arm> folder or --recovery")
    arms = {folder.name: count([(r, trace_of(folder, r)) for r in runs_of(folder)]) for folder in args.arms}
    if arms:
        print(report(arms, args.per_day))
    if len(arms) > 1:
        later = [a["later_session"] for a in arms.values()]
        print(f"all arms, {LATER_SESSION}: acted on the record in {sum(x['acted_on_the_record'] for x in later)} "
              f"of {sum(x['runs'] for x in later)} runs")
    found = recovery(args.recovery) if args.recovery else None
    if found:
        print(report_recovery(found))
    if args.json:
        payload = {name: {**row, "lifetime": lifetime(row, args.per_day)} for name, row in arms.items()}
        if found:
            payload["recovery"] = found
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
