"""The build's runs, read for the first-failures table (Part 4, task 1). Owner: Shruti Tewaney.

    python experiments/analysis/first_failures.py results/m2-build
    python experiments/analysis/first_failures.py results/m2-build --json docs/milestones/m2_first_failures.json

It reads only the committed results: the runs the summarizer counts (analyze.runs_of: the latest
finished run of each task and sweep) and each one's trace. It names nothing in the lectures' words;
that is the reader's job. What it gives the reader:

- for each failing run: the check's reason, and every warning line of its trace in order, with its
  line number, the first of them marked: an error, a call the boundary rejected or the hook refused,
  a tool's own error, a critique round failed, a write in doubt, a resume refused, or an end that is
  not a success. The first thing that went wrong is usually the first of them, but the reader decides;
- for every run: what was slow, costly or asked often: refused or rejected calls, critique rounds
  failed, plan versions, approvals asked, escalations, model calls, tokens and the slowest turn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze import runs_of, trace_of  # noqa: E402

GOOD_ENDS = {"done", "answered", "plan_done", "workflow_done", "waiting_reply", "awaiting_approval",
             "nothing_to_do", "approval_denied"}


def warnings(events: list[dict]) -> list[dict]:
    """Every line of the trace that is a warning sign, in order, with its line number."""
    out = []
    for n, x in enumerate(events, 1):
        kind = x.get("type")
        what = None
        if kind == "error":
            what = f"error: {x.get('kind')} {str(x.get('message') or '')[:160]}"
        elif kind == "hook_denied":
            what = f"refused by the hook: {x.get('tool')} ({x.get('action_class')}): {str(x.get('error') or '')[:160]}"
        elif kind == "tool_call" and x.get("rejected"):
            what = f"rejected at the boundary: {x.get('name')} {str(x.get('output') or '')[:160]}"
        elif kind == "tool_call" and x.get("is_error") and not x.get("denied"):
            what = f"tool error: {x.get('name')} {str(x.get('output') or '')[:160]}"
        elif kind == "critique" and x.get("verdict") == "fail":
            what = f"critique failed (round {x.get('round')}, {x.get('recipient')}): {str(x.get('reason') or '')[:160]}"
        elif kind == "write_settled" and x.get("outcome") not in ("happened", "made"):
            what = f"write in doubt: {x.get('tool')} {x.get('outcome')}"
        elif kind in ("resume_refused", "off_plan", "repeat_call", "orphan_flag"):
            what = f"{kind}: {json.dumps({k: v for k, v in x.items() if k not in ('at', 'session_id', 'run_id', 'type')})[:160]}"
        elif kind == "checkpoint" and x.get("passed") is False:
            what = f"plan checkpoint failed at step {x.get('plan_step')}: {str(x.get('reason') or '')[:160]}"
        elif kind == "end" and x.get("terminal_reason") not in GOOD_ENDS:
            what = f"ended {x.get('terminal_reason')}: {str(x.get('detail') or '')[:160]}"
        if what:
            out.append({"line": n, "what": what})
    return out


def costs(record: dict, events: list[dict]) -> dict:
    count = lambda pred: sum(1 for x in events if pred(x))
    turns = record.get("turn_latency_s") or []
    tokens = record.get("tokens") or {}
    return {
        "refused_or_rejected": count(lambda x: x.get("type") == "tool_call" and (x.get("denied") or x.get("rejected"))),
        "tool_errors": count(lambda x: x.get("type") == "tool_call" and x.get("is_error")),
        "critique_fails": count(lambda x: x.get("type") == "critique" and x.get("verdict") == "fail"),
        "plan_versions": count(lambda x: x.get("type") == "plan"),
        "approvals_asked": (record.get("approvals") or {}).get("asked", 0),
        "escalations": count(lambda x: x.get("type") == "escalation"),
        "model_calls": record.get("model_calls"),
        "tokens": tokens.get("total") if isinstance(tokens, dict) else tokens,
        "slowest_turn_s": round(max(turns), 2) if turns else None,
    }


def read(folder: Path) -> list[dict]:
    rows = []
    for record in runs_of(folder):
        events = trace_of(folder, record)
        check = record.get("check") or {}
        rows.append({
            "task": record.get("task"), "sweep": record.get("sweep"), "run_id": record.get("run_id"),
            "passed": bool(record.get("passed")), "reason": check.get("reason") if isinstance(check, dict) else None,
            "end_reasons": record.get("end_reasons"), "warnings": warnings(events), "costs": costs(record, events),
        })
    return sorted(rows, key=lambda r: (str(r["task"]), r["sweep"] or 0))


def report(rows: list[dict]) -> str:
    out = [f"{sum(r['passed'] for r in rows)} of {len(rows)} runs passed their check.", "", "Failing runs:"]
    for r in rows:
        if r["passed"]:
            continue
        out.append(f"- {r['run_id']} ({r['task']}, sweep {r['sweep']}): {r['reason']}")
        out.append(f"  ends {r['end_reasons']}")
        for i, w in enumerate(r["warnings"][:8]):
            out.append(f"  {'FIRST ' if i == 0 else '      '}line {w['line']}: {w['what']}")
        if not r["warnings"]:
            out.append("  no warning line in the trace: the check's reason is the first sign")
    out += ["", "Every run's costs (refused or rejected calls, tool errors, critique fails, plan versions, approvals, "
                "escalations, model calls, tokens, slowest turn):"]
    for r in rows:
        c = r["costs"]
        out.append(f"- {r['task']} s{r['sweep']} {'pass' if r['passed'] else 'FAIL'}: {c['refused_or_rejected']} refused, "
                   f"{c['tool_errors']} tool errors, {c['critique_fails']} critique fails, {c['plan_versions']} plan "
                   f"versions, {c['approvals_asked']} asks, {c['escalations']} escalations, {c['model_calls']} model calls, "
                   f"{c['tokens']} tokens, slowest turn {c['slowest_turn_s']} s")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = read(args.folder)
    print(report(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
