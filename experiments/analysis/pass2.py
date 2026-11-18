"""Pass 2's numbers, per arm: what the ledger entry and the table of passes give that the
summarizer does not (Part 4). Owner: Shruti Tewaney.

    python experiments/analysis/pass2.py results/pass2-before results/pass2-after
    python experiments/analysis/pass2.py results/pass2-before results/pass2-after --json docs/milestones/m2_pass2.json

It counts the runs the summarizer counts (analyze.runs_of: the latest finished run of each
task and sweep) and prints, per arm, each run and then the median of the arm:

- passed; model calls; plan versions, revisions and failed checkpoints; the legal hold's
  proposals (access_propose_change calls that returned a pending ID);
- critique rounds failed;
- tokens, uncached tokens (all but cache reads, as analyze.py counts them) and cost;
- the run's turn (turn_latency_s; a run with several turns gives their median).

Then, for the two arms given, after minus before at the median, and as a share of before.
It reads results/ only and spends no tokens.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze import one_run, runs_of, trace_of  # noqa: E402

MEASURES = ("model_calls", "plans", "revisions", "failed_checkpoints", "proposals", "critique_failed",
            "tokens", "uncached", "cost", "turn_s")


def _proposals(events: list[dict]) -> int:
    count = 0
    for e in events:
        if e.get("type") != "tool_call" or e.get("name") != "access_propose_change" or e.get("is_error"):
            continue
        if "pending_id" in str(e.get("output") or ""):
            count += 1
    return count


def run_row(record: dict, events: list[dict]) -> dict:
    r = one_run(record, events)
    return {"run_id": r["run_id"], "passed": r["passed"], "model_calls": r["model_calls"], "plans": r["plans"],
            "revisions": r["revisions"], "failed_checkpoints": r["failed_checkpoints"],
            "proposals": _proposals(events), "critique_failed": r["critique_failed"], "tokens": r["tokens"],
            "uncached": r["uncached"], "cost": r["cost"],
            "turn_s": statistics.median(r["turns"]) if r["turns"] else None}


def arm(folder: Path) -> dict:
    rows = [run_row(rec, trace_of(folder, rec)) for rec in runs_of(folder)]
    median = {m: statistics.median([r[m] for r in rows if r[m] is not None]) if rows else None for m in MEASURES}
    return {"arm": folder.name, "runs": rows, "passed": sum(r["passed"] for r in rows), "median": median,
            "tokens_total": sum(r["tokens"] for r in rows), "cost_total": round(sum(r["cost"] for r in rows), 6)}


def _fmt(m: str, v) -> str:
    if v is None:
        return "n/a"
    if m == "cost":
        return f"${v:.4f}"
    if m == "turn_s":
        return f"{v:.1f} s"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def report(arms: list[dict]) -> str:
    out = []
    for a in arms:
        out += [f"## {a['arm']}: {a['passed']} of {len(a['runs'])} passed; {a['tokens_total']:,} tokens, "
                f"${a['cost_total']:.4f} for the arm", "",
                "| Run | Passed | " + " | ".join(MEASURES) + " |", "|---" * (len(MEASURES) + 2) + "|"]
        for r in a["runs"]:
            out.append(f"| {r['run_id']} | {'yes' if r['passed'] else 'no'} | "
                       + " | ".join(_fmt(m, r[m]) for m in MEASURES) + " |")
        out += ["| median | | " + " | ".join(_fmt(m, a["median"][m]) for m in MEASURES) + " |", ""]
    if len(arms) == 2:
        before, after = arms[0]["median"], arms[1]["median"]
        out.append(f"After minus before, at the median ({arms[1]['arm']} - {arms[0]['arm']}):")
        for m in MEASURES:
            if before[m] is None or after[m] is None:
                continue
            share = f" ({(after[m] - before[m]) / before[m]:+.0%})" if before[m] else ""
            out.append(f"- {m}: {_fmt(m, before[m])} -> {_fmt(m, after[m])}{share}")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("arms", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    arms = [arm(folder) for folder in args.arms]
    print(report(arms))
    if args.json:
        args.json.write_text(json.dumps(arms, indent=1) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
