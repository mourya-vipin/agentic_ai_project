"""The arm table, computed from the run records alone.

    python -m harness.summarize results/control-agent results/control-workflow
    python -m harness.summarize results/control-agent results/control-workflow --measure questions_asked
    python -m harness.summarize results/control-agent --json

For each arm: the passes with their counts; each sweep's score (the share of its
tasks that passed); the mean of the sweep scores and their spread (the sample
standard deviation, your pilot's sigma); the tokens and cost per run; the
latency, median and slowest, with and without the waits (for an outside reply,
and the backend's sleep through a rate limit or an overload); and the slowest
turn, the agent's own time for one turn without those waits (the records'
turn_latency_s). Then the passes per task. The columns before Passes are the switches the arms set
differently; the line above the table names what they held constant.

--measure <name> adds one column: the sum, over each arm's counted runs, of the
count your checks returned under that name (the record's `measures`). Give it once
per name. Anything else, a share or a rate, comes from your own script in
experiments/analysis/, which reads the same records and traces.

Only finished runs count. A run the agent was not built for, or that a quota or
a rate limit stopped, is listed below the table and left out. Free runs on a
scripted model, in results/_free/, are left out altogether.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from harness import records


def _fmt(x: float | None, digits: int = 2, unit: str = "") -> str:
    return "n/a" if x is None else f"{x:.{digits}f}{unit}"


def arm_summary(folder: Path, measures: list[str] | None = None) -> dict:
    found = records.read(folder)
    runs = [r for r in found if records.finished(r)]
    unfinished = [r for r in found if not records.finished(r)]
    tasks = sorted({r["task"] for r in runs})
    sweeps = sorted({r["sweep"] for r in runs})
    by_pair = {}
    for r in runs:   # the latest finished run of each (task, sweep)
        by_pair[(r["task"], r["sweep"])] = r
    sweep_scores, incomplete = [], []
    for s in sweeps:
        in_sweep = [by_pair[(t, s)] for t in tasks if (t, s) in by_pair]
        score = sum(1 for r in in_sweep if r["passed"]) / len(in_sweep) if in_sweep else 0.0
        sweep_scores.append(score)
        if len(in_sweep) < len(tasks):
            incomplete.append(f"sweep {s} has {len(in_sweep)} of {len(tasks)} tasks")
    counted = list(by_pair.values())
    latencies = [r["latency_s"] for r in counted]
    without = [r.get("latency_without_wait_s", r["latency_s"]) for r in counted]
    turns = [t for r in counted for t in r.get("turn_latency_s") or []]
    switch_sets = {json.dumps(r.get("switches", {}), sort_keys=True) for r in counted}
    sums = {name: sum((r.get("measures") or {}).get(name, 0) for r in counted) for name in measures or []}
    lacking = {name: sum(1 for r in counted if name not in (r.get("measures") or {})) for name in measures or []}
    return {
        "arm": folder.name, "folder": str(folder),
        "runs": len(counted), "passes": sum(1 for r in counted if r["passed"]),
        "tasks": tasks, "sweeps": sweeps,
        "sweep_scores": sweep_scores,
        "mean": statistics.mean(sweep_scores) if sweep_scores else None,
        "spread": statistics.stdev(sweep_scores) if len(sweep_scores) > 1 else None,
        "tokens_per_run": statistics.mean(r["tokens"]["total"] for r in counted) if counted else None,
        "cost_per_run": statistics.mean(r["cost_usd"] for r in counted) if counted else None,
        "latency_median": statistics.median(latencies) if latencies else None,
        "latency_slowest": max(latencies) if latencies else None,
        "without_wait_median": statistics.median(without) if without else None,
        "without_wait_slowest": max(without) if without else None,
        "turn_slowest": max(turns) if turns else None,
        "per_task": {t: [sum(1 for s in sweeps if (t, s) in by_pair and by_pair[(t, s)]["passed"]),
                         sum(1 for s in sweeps if (t, s) in by_pair)] for t in tasks},
        "switches": json.loads(next(iter(switch_sets))) if len(switch_sets) == 1 else None,
        "mixed_switches": len(switch_sets) > 1,
        "models": sorted({str(r.get("model")) for r in counted}),
        "unfinished": [{"run_id": r["run_id"], "outcome": r["outcome"]} for r in unfinished],
        "incomplete": incomplete,
        "measures": sums,
        "measures_missing": {name: n for name, n in lacking.items() if n},
    }


def _count(x) -> str:
    return f"{x:,}" if isinstance(x, int) else f"{x:,.2f}"


def table(summaries: list[dict]) -> str:
    lines = []
    measured = [n for n in (summaries[0]["measures"] if summaries else {})]
    known = [s for s in summaries if s["switches"] is not None]
    names = sorted({k for s in known for k in s["switches"]})
    differ = [n for n in names if len({json.dumps(s["switches"].get(n)) for s in known}) > 1]
    same = [n for n in names if n not in differ]
    if known and same:
        held = ", ".join(f"{n}={known[0]['switches'].get(n)}" for n in same)
        lines.append(f"Held constant: {held}; model {', '.join(sorted({m for s in summaries for m in s['models']}))}.")
    if len({tuple(s["tasks"]) for s in summaries}) > 1:
        lines.append("WARNING: the arms did not run the same tasks.")
    if len({m for s in summaries for m in s["models"]}) > 1:
        lines.append("WARNING: the arms ran on different models.")
    for s in summaries:
        if s["mixed_switches"]:
            lines.append(f"WARNING: {s['arm']} holds runs with different switch settings.")
    lines.append("")
    head = ["Arm", *differ, "Passes", "Sweep scores", "Mean", "Spread (SD)", "Tokens per run", "Cost per run",
            "Latency median", "Slowest", "Median without waiting", "Slowest without waiting", "Slowest turn",
            *(f"{n} (sum)" for n in measured)]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "---|" * len(head))
    for s in summaries:
        switches = [str((s["switches"] or {}).get(n, "mixed")) for n in differ]
        row = [s["arm"], *switches, f"{s['passes']} of {s['runs']}",
               ", ".join(_fmt(x) for x in s["sweep_scores"]) or "none", _fmt(s["mean"]), _fmt(s["spread"]),
               "n/a" if s["tokens_per_run"] is None else f"{s['tokens_per_run']:,.0f}",
               "n/a" if s["cost_per_run"] is None else f"${s['cost_per_run']:.4f}",
               _fmt(s["latency_median"], unit=" s"), _fmt(s["latency_slowest"], unit=" s"),
               _fmt(s["without_wait_median"], unit=" s"), _fmt(s["without_wait_slowest"], unit=" s"),
               _fmt(s["turn_slowest"], unit=" s"),
               *(_count(s["measures"][n]) for n in measured)]
        lines.append("| " + " | ".join(row) + " |")
    tasks = sorted({t for s in summaries for t in s["tasks"]})
    if tasks:
        lines += ["", "Passes per task", "", "| Task | " + " | ".join(s["arm"] for s in summaries) + " |",
                  "|---|" + "---|" * len(summaries)]
        for t in tasks:
            cells = [f"{s['per_task'][t][0]} of {s['per_task'][t][1]}" if t in s["per_task"] else "not run"
                     for s in summaries]
            lines.append(f"| {t} | " + " | ".join(cells) + " |")
    notes = []
    for s in summaries:
        for text in s["incomplete"]:
            notes.append(f"{s['arm']}: {text}")
        if s["unfinished"]:
            kinds = sorted({u["outcome"] for u in s["unfinished"]})
            notes.append(f"{s['arm']}: {len(s['unfinished'])} runs not counted ({', '.join(kinds)})")
        for name, n in s["measures_missing"].items():
            notes.append(f"{s['arm']}: {n} of {s['runs']} runs report no {name}")
    if notes:
        lines += [""] + [f"Note: {n}" for n in notes]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("arms", nargs="+", help="results/<arm> folders")
    p.add_argument("--measure", action="append", default=[], metavar="NAME",
                   help="add a column: the sum per arm of the count your checks returned under NAME")
    p.add_argument("--json", action="store_true", help="print the numbers as JSON")
    a = p.parse_args(argv)
    summaries = []
    for arm in a.arms:
        folder = Path(arm)
        if not folder.is_dir():
            print(f"{arm}: no such folder", file=sys.stderr)
            return 2
        if records.is_free_folder(folder):
            print(f"{arm}: free runs on a scripted model, left out: they test your code, and are not evidence",
                  file=sys.stderr)
            continue
        summaries.append(arm_summary(folder, a.measure))
    if not summaries:
        print("no arms to summarize", file=sys.stderr)
        return 2
    print(json.dumps(summaries, indent=2) if a.json else table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
