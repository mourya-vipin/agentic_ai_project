"""Who reads what our system puts in front of a person, and how often (Part 2, task 9).
Owner: Vipin Mourya.

    python experiments/analysis/asks.py results/m2-build
    python experiments/analysis/asks.py results/m2-build --traces traces/ --per-day 40 --minutes 30
    python experiments/analysis/asks.py results/m2-build --json docs/milestones/m2_asks.json

Each run.py run resets its state folder, so the approval log (agent/approvals.py) and the
escalation store (agent/escalation.py) are gone once a run ends. Their trace lines stay, and
this script reads those: the runs the summarizer counts (the latest finished run of each
task and sweep, as analyze.py counts them), and any console sessions given with --traces.

It prints, per arm, one row per thing a person reads:

- each approval class: asks (one per approval ID; the same call asked again is one),
  asks per run, the decisions (approved, denied, withdrawn) and who made them, the
  approval rate, and the median seconds from put-up to answer;
- the escalation: packets, and those past the daily cap (held, no packet sent);
- the stop's notice: notices before a run's first call, and again on a resume;
- the strangers: every message the agent sent (reply_send, channel_ask_template,
  notices_send_template), by tool.

Seconds are a person's only where a person answered: a decision whose `who` is not
"operator" (a task file's scripted answer) is counted, but its seconds are left out of
the median, since a script answers at once. Console sessions (--traces) are where a
person answered.

--per-day N scales asks per run to a day of N runs; --minutes M then gives the seconds
per ask: M x 60 / asks a day. Both are the write-up's assumptions, stated by whoever
runs the script. Spends no tokens; reads results/ and traces/ only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze import runs_of, trace_of  # noqa: E402  (Aman Panigrahi's: the runs the summarizer counts)

SENDS = ("reply_send", "channel_ask_template", "notices_send_template")
PERSON = "operator"


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sessions(folders: list[Path], trace_paths: list[Path]) -> list[tuple[str, list[dict]]]:
    """(label, trace lines) for every counted run of each arm, then each console session."""
    out = []
    for folder in folders:
        for record in runs_of(folder):
            out.append((folder.name, trace_of(folder, record)))
    for path in trace_paths:
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for f in files:
            out.append(("console", _lines(f)))
    return out


def count(runs: list[list[dict]]) -> dict:
    """The rows of one arm, from its runs' trace lines."""
    asked: dict[str, str] = {}                    # approval_id -> action class
    decided: dict[str, dict] = {}                 # approval_id -> the last decision line
    packets, over_cap, answered = 0, 0, 0
    notices = Counter()
    sent = Counter()
    for lines in runs:
        for x in lines:
            kind = x.get("type")
            if kind == "approval_requested":
                asked.setdefault(x["approval_id"], x.get("action_class") or "?")
            elif kind == "approval_decided":
                decided[x["approval_id"]] = x
            elif kind == "escalation":
                packets += 1
                over_cap += bool(x.get("over_cap"))
            elif kind == "escalation_answered":
                answered += 1
            elif kind == "run_notice":
                notices["resumed" if x.get("resumed") else "first"] += 1
            elif kind == "tool_call" and x.get("name") in SENDS and not x.get("is_error") \
                    and not x.get("awaiting_approval"):              # a call put up for approval sent nothing
                sent[x["name"]] += 1
    n = len(runs)
    classes = defaultdict(lambda: {"asks": 0, "decisions": Counter(), "who": Counter(), "seconds": []})
    for approval_id, action_class in asked.items():
        row = classes[action_class]
        row["asks"] += 1
        d = decided.get(approval_id)
        if d is None:
            row["decisions"]["unanswered"] += 1
            continue
        row["decisions"][d.get("decision")] += 1
        row["who"][d.get("who") or "?"] += 1
        if d.get("who") == PERSON and d.get("seconds") is not None:
            row["seconds"].append(float(d["seconds"]))
    approvals = {}
    for action_class in sorted(classes):
        row = classes[action_class]
        yes, no = row["decisions"].get("approved", 0), row["decisions"].get("denied", 0)
        approvals[action_class] = {
            "asks": row["asks"], "asks_per_run": round(row["asks"] / n, 3) if n else None,
            "decisions": dict(row["decisions"]), "who": dict(row["who"]),
            "approval_rate": round(yes / (yes + no), 3) if yes + no else None,
            "median_seconds_person": round(statistics.median(row["seconds"]), 1) if row["seconds"] else None,
            "person_decisions": len(row["seconds"]),
        }
    total = sum(r["asks"] for r in approvals.values())
    return {
        "runs": n,
        "approvals": approvals,
        "asks": total,
        "asks_per_run": round(total / n, 3) if n else None,
        "escalation": {"packets": packets, "over_cap": over_cap, "answered": answered,
                       "per_run": round(packets / n, 3) if n else None},
        "stop_notice": {"first": notices["first"], "resumed": notices["resumed"],
                        "per_run": round(sum(notices.values()) / n, 3) if n else None},
        "strangers": {"sent": dict(sent), "per_run": round(sum(sent.values()) / n, 3) if n else None},
    }


def day(row: dict, per_day: float | None, minutes: float | None) -> dict | None:
    """Asks and packets a day, at per_day runs; and seconds per ask, given the minutes a day."""
    if not per_day or row["asks_per_run"] is None:
        return None
    asks = (row["asks"] + row["escalation"]["packets"] - row["escalation"]["over_cap"]) / row["runs"] * per_day
    out = {"runs_a_day": per_day, "asks_a_day": round(asks, 1)}
    if minutes:
        out["minutes_a_day"] = minutes
        out["seconds_per_ask"] = round(minutes * 60 / asks, 1) if asks else None
    return out


def report(arms: dict[str, dict], per_day: float | None, minutes: float | None) -> str:
    out = []
    for label, row in arms.items():
        out.append(f"== {label}: {row['runs']} runs ==")
        out.append(f"approvals: {row['asks']} asks, {row['asks_per_run']} per run")
        for action_class, a in row["approvals"].items():
            out.append(f"  {action_class}: {a['asks']} asks ({a['asks_per_run']} per run); decisions {a['decisions']}; "
                       f"by {a['who']}; approval rate {a['approval_rate']}; median seconds (a person's, "
                       f"{a['person_decisions']} decisions) {a['median_seconds_person']}")
        e, s, m = row["escalation"], row["stop_notice"], row["strangers"]
        out.append(f"escalation: {e['packets']} packets ({e['over_cap']} past the cap, no packet sent), "
                   f"{e['answered']} answered, {e['per_run']} per run")
        out.append(f"stop notice: {s['first']} before a run's first call, {s['resumed']} on a resume, {s['per_run']} per run")
        out.append(f"strangers: {sum(m['sent'].values())} messages sent {m['sent']}, {m['per_run']} per run")
        d = day(row, per_day, minutes)
        if d:
            out.append(f"a day of {d['runs_a_day']:g} runs: {d['asks_a_day']} asks (approvals and packets sent)"
                       + (f"; at {d['minutes_a_day']:g} minutes a day, {d['seconds_per_ask']} s per ask"
                          if "seconds_per_ask" in d else ""))
        out.append("")
    return "\n".join(out).rstrip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("arms", nargs="*", type=Path, help="results/<arm> folders")
    parser.add_argument("--traces", nargs="*", type=Path, default=[], help="console session traces, files or folders")
    parser.add_argument("--per-day", type=float, help="runs a day, to scale asks per run to a day")
    parser.add_argument("--minutes", type=float, help="the operator's minutes a day for asks")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args(argv)
    if not args.arms and not args.traces:
        parser.error("give at least one results/<arm> folder or --traces")
    grouped: dict[str, list] = defaultdict(list)
    for label, lines in sessions(args.arms, args.traces):
        grouped[label].append(lines)
    arms = {label: count(runs) for label, runs in grouped.items()}
    print(report(arms, args.per_day, args.minutes))
    if args.json:
        payload = {label: {**row, "day": day(row, args.per_day, args.minutes)} for label, row in arms.items()}
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
