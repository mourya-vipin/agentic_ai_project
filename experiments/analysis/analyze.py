"""Every number our write-up gives that the summarizer does not, from the run records
and the traces beside them. Owner: Aman Panigrahi.

    python experiments/analysis/analyze.py results/baseline
    python experiments/analysis/analyze.py results/baseline --json docs/milestones/m1_baseline.json
    python experiments/analysis/analyze.py results/pass1-before results/pass1-after --json docs/milestones/m1_pass1.json

It counts the runs the summarizer counts (the latest finished run of each task and
sweep) and prints, per arm:

- the passes per task and per sweep, with counts;
- tokens and cost per run, mean and median, and the longest run's model calls;
- the median and the slowest turn, from every record's turn_latency_s;
- how the runs ended (end_reasons); the runs our bound ended (step_limit,
  loop_token_budget, wall_clock); and false finishes: runs whose last end reason
  says they finished (done, answered, plan_done, workflow_done) but whose check failed;
- runs with a repeated call (repeat_call); calls rejected at the registry's boundary,
  denied by its hook, or failed as protocol errors; distinct tool-call sequences;
- the plan: plans that failed validation, revisions, failed checkpoints, refused
  off-plan calls, orphan flags, and runs ended at plan_rejected, revision_bound or
  step_halted;
- the critique: rounds, failed rounds, drafts held (critique_held), and misses
  (drafts it passed in a run whose check then failed);
- misroutes (the checks' measures.misrouted);
- tokens and cost per component: each model call's usage summed by its purpose,
  never a difference between whole runs (A13). The critique's check is code and makes
  no model call, shown as a zero; the author's calls right after a failed round are
  shown apart (they are also inside their component's row);
- cost per use case: a model call counts for the use case it served (a request's
  routing call for that request's use case, an operator turn's routing call for the
  turn's use case when it is a question or a bulk change);
- m: the mean cost of a run whose cases all took the agent path over the mean cost of
  a run whose cases all took a workflow; and each path's turns: median, slowest, and
  how many took over 10 s (Lecture 1, slide 34).

Across arms, f, when two arms set one use case's control switch differently: the share
of that use case's tasks the agent passed in at least two of three sweeps and the
workflow in at most one (at least 2/3 and at most 1/3 of the sweeps for other counts).

It reads results/ only, spends no tokens, and works on free arms too (results/_free/),
which test the script and are not evidence.
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

from harness import records  # noqa: E402  (the repository root is on the path only from here)

FINISHED = ("done", "answered", "plan_done", "workflow_done")
OUR_BOUND = ("step_limit", "loop_token_budget", "wall_clock")
PLAN_ENDS = ("plan_rejected", "revision_bound", "step_halted")
SLOW_TURN_S = 10.0          # a turn over this is slow (Lecture 1, slide 34)
CRITIQUE_HELD = "critique_held"
QUESTION, BULK = "operator_review", "bulk_change"
INTENT_USE_CASE = {"question": QUESTION, "bulk_change": BULK}     # an operator turn's own use case
PLANNED = ("plan", "revise", "fill")                                # the planned path's calls, all U6
COMPONENT = {"route_operator": "routing (operator turn)", "route_request": "routing (request)",
             "plan": "planner", "revise": "reviser", "fill": "executor fill"}
SHARED_ROUTING = "routing of request turns (shared)"                 # one call for every request in the turn
CRITIQUE_CHECK = "critique check (code, no model call)"
AFTER_FAIL = "author's calls after a failed critique round"
USE_CASES = ("standard_grant", "onboarding", "offboarding", "nonstandard_request", QUESTION, BULK)   # U1 to U6
COMPONENTS = ("routing (operator turn)", "routing (request)", *(f"loop: {u}" for u in USE_CASES),
              "planner", "reviser", "executor fill")


def _in_order(names, order) -> list[str]:
    return sorted(names, key=lambda n: (order.index(n) if n in order else len(order), n))


# -- reading ------------------------------------------------------------------------------

def runs_of(folder: Path) -> list[dict]:
    """The runs the summarizer counts: the latest finished run of each (task, sweep)."""
    latest = {}
    for record in records.read(folder):
        if records.finished(record):
            latest[(record["task"], record["sweep"])] = record
    return [latest[key] for key in sorted(latest)]


def trace_of(folder: Path, record: dict) -> list[dict]:
    path = folder / f"{record['run_id']}.trace.jsonl"
    if not path.exists() and record.get("trace"):
        path = ROOT / record["trace"]
    events = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# -- one run ------------------------------------------------------------------------------

def _usage(event: dict) -> dict:
    u = event.get("usage") or {}
    tokens = sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                          "cache_creation_input_tokens"))
    return {"calls": 1, "tokens": tokens, "cost": event.get("cost_usd") or 0.0}


def _add(into: dict, usage: dict) -> None:
    for k in ("calls", "tokens", "cost"):
        into[k] = into.get(k, 0) + usage[k]


def one_run(record: dict, events: list[dict]) -> dict:
    """What one run did, from its record and its trace."""
    routes = {e.get("request_id"): e.get("route", "") for e in events
              if e.get("type") == "route" and e.get("scope") == "request"}
    components, use_cases, case_paths = defaultdict(dict), defaultdict(dict), defaultdict(set)
    cases, after_fail = Counter(), {}
    waiting_turn, failed_round = [], False
    for e in events:
        kind = e.get("type")
        if kind == "model_call":
            purpose = e.get("purpose") or "unknown"
            usage = _usage(e)
            _add(components[COMPONENT.get(purpose, f"loop: {purpose}")], usage)
            if failed_round:
                _add(after_fail, usage)
                failed_round = False
            if purpose == "route_operator":
                waiting_turn.append(usage)        # its use case is known at the turn's route line
                continue
            if purpose == "route_request":
                use_case = routes.get(e.get("request_id"), "").partition(":")[2] or "unrouted"
            elif purpose in PLANNED:
                use_case = BULK
            else:
                use_case = purpose
            _add(use_cases[use_case], usage)
        elif kind == "route" and e.get("scope") == "operator":
            use_case = INTENT_USE_CASE.get(e.get("intent"))
            for usage in waiting_turn:
                _add(use_cases[use_case or SHARED_ROUTING], usage)
            waiting_turn = []
            if use_case:
                cases[use_case] += 1
                case_paths[use_case].add("agent")
        elif kind == "route" and e.get("scope") == "request":
            path, _, use_case = (e.get("route") or "").partition(":")
            cases[use_case] += 1
            case_paths[use_case].add(path)
        elif kind == "critique" and e.get("verdict") == "fail":
            failed_round = True
    for usage in waiting_turn:
        _add(use_cases[SHARED_ROUTING], usage)

    types = Counter(e.get("type") for e in events)
    critiques = [e for e in events if e.get("type") == "critique"]
    tool_calls = record.get("tool_calls") or []
    paths = {p for found in case_paths.values() for p in found}
    ends = record.get("end_reasons") or ([record["end_reason"]] if record.get("end_reason") else [])
    passed = bool(record.get("passed"))
    return {
        "run_id": record["run_id"], "task": record["task"], "sweep": record["sweep"], "passed": passed,
        "tokens": record["tokens"]["total"], "cost": record["cost_usd"],
        "model_calls": record.get("model_calls", types["model_call"]),
        "turns": record.get("turn_latency_s") or [],
        "end_reasons": ends,
        "false_finish": bool(ends) and ends[-1] in FINISHED and not passed,
        "our_bound": [r for r in ends if r in OUR_BOUND],
        "repeat_calls": types["repeat_call"],
        "rejected": sum(1 for c in tool_calls if c.get("rejected")),
        "denied": sum(1 for c in tool_calls if c.get("denied")),
        "protocol_errors": sum(1 for c in tool_calls if c.get("error_class") == "protocol"),
        "sequence": [c.get("name") for c in tool_calls],
        "plans": types["plan"],
        "invalid_plans": sum(1 for e in events if e.get("type") == "plan_check" and not e.get("valid")),
        "revisions": types["revision"],
        "failed_checkpoints": sum(1 for e in events if e.get("type") == "checkpoint" and not e.get("passed")),
        "off_plan": types["off_plan"],
        "orphan_flags": types["orphan_flag"],
        "critique_rounds": len(critiques),
        "critique_failed": sum(1 for e in critiques if e.get("verdict") == "fail"),
        "critique_passed": sum(1 for e in critiques if e.get("verdict") == "pass"),
        "misrouted": (record.get("measures") or {}).get("misrouted", 0),
        "components": dict(components), "use_cases": dict(use_cases), "after_fail": after_fail,
        "cases": dict(cases), "case_paths": {k: sorted(v) for k, v in case_paths.items()},
        "path": "agent" if paths == {"agent"} else "workflow" if paths == {"workflow"} else
                "mixed" if paths else "none",
        "switches": record.get("switches") or {},
        "model": record.get("model"),
    }


# -- one arm ------------------------------------------------------------------------------

def _stats(values: list[float]) -> dict:
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None}


def ids(rows: list[dict]) -> list[str]:
    return [r["run_id"] for r in rows]


def _sum(rows: list[dict]) -> dict:
    out = {"calls": 0, "tokens": 0, "cost": 0.0}
    for row in rows:
        _add(out, {k: row.get(k, 0) for k in out})
    return out


def arm(folder: Path) -> dict:
    runs = [one_run(r, trace_of(folder, r)) for r in runs_of(folder)]
    n = len(runs)
    tasks = sorted({r["task"] for r in runs})
    sweeps = sorted({r["sweep"] for r in runs})
    turns = [(t, r["run_id"]) for r in runs for t in r["turns"]]
    slowest = max(turns, default=None)
    longest = max(runs, key=lambda r: r["model_calls"], default=None)

    components = defaultdict(list)
    for r in runs:
        for name, usage in r["components"].items():
            components[name].append(usage)
    component_rows = {name: _sum(components[name]) for name in _in_order(components, COMPONENTS)}
    component_rows[CRITIQUE_CHECK] = {"calls": 0, "tokens": 0, "cost": 0.0}
    after_fail = _sum([r["after_fail"] for r in runs])

    use_cases, cases, case_paths = defaultdict(list), Counter(), defaultdict(set)
    for r in runs:
        for name, usage in r["use_cases"].items():
            use_cases[name].append(usage)
        cases.update(r["cases"])
        for name, found in r["case_paths"].items():
            case_paths[name].update(found)
    use_case_rows = {}
    for name in _in_order(set(use_cases) | set(cases), USE_CASES):
        total = _sum(use_cases.get(name, []))
        use_case_rows[name] = {**total, "path": "/".join(sorted(case_paths.get(name, []))) or "-",
                               "cases": cases.get(name, 0),
                               "cost_per_case": total["cost"] / cases[name] if cases.get(name) else None}

    by_path = {}
    for path in ("agent", "workflow", "mixed"):
        rows = [r for r in runs if r["path"] == path]
        if rows:
            turns_here = [t for r in rows for t in r["turns"]]
            by_path[path] = {"runs": len(rows), "tasks": sorted({r["task"] for r in rows}),
                             "tokens": _stats([r["tokens"] for r in rows]), "cost": _stats([r["cost"] for r in rows]),
                             "turns": {**_stats(turns_here), "slowest": max(turns_here, default=None),
                                       "over_slow": sum(t > SLOW_TURN_S for t in turns_here)}}
    m = None
    if "agent" in by_path and "workflow" in by_path and by_path["workflow"]["cost"]["mean"]:
        m = by_path["agent"]["cost"]["mean"] / by_path["workflow"]["cost"]["mean"]

    sequences = {t: len({tuple(r["sequence"]) for r in runs if r["task"] == t}) for t in tasks}
    critiqued = [r for r in runs if r["critique_rounds"]]
    missed = [r for r in runs if r["critique_passed"] and not r["passed"]]
    switch_sets = {json.dumps(r["switches"], sort_keys=True) for r in runs}
    return {
        "arm": folder.name, "folder": str(folder), "free": records.is_free_folder(folder),
        "runs": n, "passes": sum(r["passed"] for r in runs), "tasks": tasks, "sweeps": sweeps,
        "models": sorted({str(r["model"]) for r in runs}),
        "switches": json.loads(next(iter(switch_sets))) if len(switch_sets) == 1 else None,
        "per_task": {t: [sum(r["passed"] for r in runs if r["task"] == t), sum(r["task"] == t for r in runs)]
                     for t in tasks},
        "per_sweep": {str(s): [sum(r["passed"] for r in runs if r["sweep"] == s), sum(r["sweep"] == s for r in runs)]
                      for s in sweeps},
        "task_use_cases": {t: sorted({c for r in runs if r["task"] == t for c in r["cases"]}) for t in tasks},
        "tokens_per_run": _stats([r["tokens"] for r in runs]),
        "cost_per_run": _stats([r["cost"] for r in runs]),
        "turns": {**_stats([t for t, _ in turns]), "slowest": slowest[0] if slowest else None,
                  "slowest_run": slowest[1] if slowest else None},
        "longest_run": {"run_id": longest["run_id"], "model_calls": longest["model_calls"]} if longest else None,
        "model_calls": _stats([r["model_calls"] for r in runs]),
        "end_reasons": dict(Counter(x for r in runs for x in r["end_reasons"]).most_common()),
        "last_end_reasons": dict(Counter(r["end_reasons"][-1] for r in runs if r["end_reasons"]).most_common()),
        "ended_at_our_bound": ids([r for r in runs if r["our_bound"]]),
        "false_finishes": ids([r for r in runs if r["false_finish"]]),
        "repeat_calls": {"events": sum(r["repeat_calls"] for r in runs),
                         "runs": ids([r for r in runs if r["repeat_calls"]])},
        "boundary": {name: {"calls": sum(r[name] for r in runs), "runs": ids([r for r in runs if r[name]])}
                     for name in ("rejected", "denied", "protocol_errors")},
        "distinct_sequences": {"per_task": sequences, "total": sum(sequences.values())},
        "plan": {name: sum(r[name] for r in runs) for name in
                 ("plans", "invalid_plans", "revisions", "failed_checkpoints", "off_plan", "orphan_flags")}
                | {"runs_planned": len([r for r in runs if r["plans"]]),
                   "ended": {x: ids([r for r in runs if x in r["end_reasons"]]) for x in PLAN_ENDS}},
        "critique": {"rounds": sum(r["critique_rounds"] for r in runs),
                     "failed_rounds": sum(r["critique_failed"] for r in runs),
                     "passed_drafts": sum(r["critique_passed"] for r in runs),
                     "runs_with_critique": len(critiqued),
                     "rounds_per_critiqued_run": _stats([r["critique_rounds"] for r in critiqued]),
                     "held": ids([r for r in runs if CRITIQUE_HELD in r["end_reasons"]]),
                     "misses": {"drafts": sum(r["critique_passed"] for r in missed), "runs": ids(missed)}},
        "misroutes": {"count": sum(r["misrouted"] for r in runs), "runs": ids([r for r in runs if r["misrouted"]])},
        "components": component_rows, "after_failed_round": after_fail,
        "use_cases": use_case_rows, "by_path": by_path, "m": m,
        "per_run": [{k: r[k] for k in ("run_id", "task", "sweep", "passed", "tokens", "cost", "model_calls",
                                       "turns", "end_reasons", "path", "false_finish", "repeat_calls",
                                       "critique_rounds", "cases")} for r in runs],
    }


# -- across arms: f -------------------------------------------------------------------------

def f_values(arms: list[dict]) -> list[dict]:
    """f for every pair of arms that set one use case's control switch to agent and workflow."""
    out = []
    names = sorted({k for a in arms for k in (a["switches"] or {}) if k.startswith("control.")})
    for name in names:
        use_case = name.split(".", 1)[1]
        for agent in [a for a in arms if (a["switches"] or {}).get(name) == "agent"]:
            for workflow in [a for a in arms if (a["switches"] or {}).get(name) == "workflow"]:
                tasks = sorted(t for t in set(agent["per_task"]) & set(workflow["per_task"])
                               if use_case in agent["task_use_cases"][t] + workflow["task_use_cases"][t])
                rows = []
                for t in tasks:
                    (ak, an), (wk, wn) = agent["per_task"][t], workflow["per_task"][t]
                    rows.append({"task": t, "agent": [ak, an], "workflow": [wk, wn],
                                 "counts": ak >= 2 * an / 3 and wk <= wn / 3})
                out.append({"switch": name, "agent_arm": agent["arm"], "workflow_arm": workflow["arm"],
                            "tasks": rows, "f": sum(r["counts"] for r in rows) / len(rows) if rows else None})
    return out


# -- printing -----------------------------------------------------------------------------

def _money(x, digits=4) -> str:
    return "n/a" if x is None else f"${x:.{digits}f}"


def _num(x, digits=0) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _ids(run_ids: list[str], n: int) -> str:
    return f"{len(run_ids)} of {n}" + (f" ({', '.join(run_ids)})" if run_ids else "")


def report(a: dict) -> str:
    n = a["runs"]
    lines = [f"## {a['arm']} ({a['folder']}): {n} runs, {len(a['tasks'])} tasks, {len(a['sweeps'])} sweeps, "
             f"model {', '.join(a['models'])}"]
    if a["free"]:
        lines.append("Free runs on a scripted model: they test the script, and are not evidence.")
    if a["switches"] is None:
        lines.append("WARNING: this arm holds runs with different switch settings.")
    lines += ["", f"Passes: {a['passes']} of {n}", "", "| Task | Passed |", "|---|---|"]
    lines += [f"| {t} | {k} of {m} |" for t, (k, m) in a["per_task"].items()]
    lines += ["", "| Sweep | Passed |", "|---|---|"]
    lines += [f"| {s} | {k} of {m} |" for s, (k, m) in a["per_sweep"].items()]

    tok, cost, turns, calls = a["tokens_per_run"], a["cost_per_run"], a["turns"], a["model_calls"]
    lines += ["", f"Tokens per run: mean {_num(tok['mean'])}, median {_num(tok['median'])} ({tok['n']} runs)",
              f"Cost per run: mean {_money(cost['mean'])}, median {_money(cost['median'])} ({cost['n']} runs)",
              f"Turns: median {_num(turns['median'], 2)} s, slowest {_num(turns['slowest'], 2)} s "
              f"({turns['slowest_run']}), over {turns['n']} turns in {n} runs",
              f"Model calls per run: mean {_num(calls['mean'], 2)}, median {_num(calls['median'], 1)} ({n} runs); "
              f"the longest run, {a['longest_run']['run_id']}, made {a['longest_run']['model_calls']}"
              if a["longest_run"] else "Model calls: none"]

    lines += ["", "| End reason | Times, over all turns | Runs it ended last |", "|---|---|---|"]
    lines += [f"| {x} | {k} | {a['last_end_reasons'].get(x, 0)} |" for x, k in a["end_reasons"].items()]
    lines += ["", f"Ended at our bound: {_ids(a['ended_at_our_bound'], n)}",
              f"False finishes (said finished, check failed): {_ids(a['false_finishes'], n)}",
              f"Runs with a repeated call: {_ids(a['repeat_calls']['runs'], n)}; "
              f"repeat_call events: {a['repeat_calls']['events']}"]
    for name, label in (("rejected", "Calls rejected at the boundary"), ("denied", "Calls denied by the hook"),
                        ("protocol_errors", "Protocol errors")):
        b = a["boundary"][name]
        lines.append(f"{label}: {b['calls']}, in {_ids(b['runs'], n)} runs")
    seq = a["distinct_sequences"]
    lines.append(f"Distinct tool-call sequences: {seq['total']} over {n} runs; per task: "
                 + ", ".join(f"{t} {k}" for t, k in seq["per_task"].items()))

    p = a["plan"]
    lines += ["", f"Plan: {p['plans']} plans in {p['runs_planned']} runs; {p['invalid_plans']} failed validation; "
              f"{p['revisions']} revisions; {p['failed_checkpoints']} failed checkpoints; "
              f"{p['off_plan']} refused off-plan calls; {p['orphan_flags']} orphan flags"]
    lines += [f"Runs ended at {x}: {_ids(r, n)}" for x, r in p["ended"].items()]

    c = a["critique"]
    per = c["rounds_per_critiqued_run"]
    lines += ["", f"Critique: {c['rounds']} rounds in {c['runs_with_critique']} runs "
              f"(mean {_num(per['mean'], 2)}, median {_num(per['median'], 1)} per run with a critique); "
              f"{c['failed_rounds']} failed rounds; {c['passed_drafts']} drafts passed",
              f"Drafts held (critique_held): {_ids(c['held'], n)}",
              f"Misses (a draft passed, the run's check failed): {c['misses']['drafts']} drafts, "
              f"in {_ids(c['misses']['runs'], n)} runs",
              f"Misroutes: {a['misroutes']['count']}, in {_ids(a['misroutes']['runs'], n)} runs"]

    lines += ["", f"Tokens and cost per component (each model call summed by its purpose; {n} runs)", "",
              "| Component | Calls | Tokens | Cost | Tokens per run | Cost per run |", "|---|---|---|---|---|---|"]
    for name, row in [*a["components"].items(), (AFTER_FAIL + " (also counted above)", a["after_failed_round"])]:
        lines.append(f"| {name} | {row['calls']} | {_num(row['tokens'])} | {_money(row['cost'], 5)} | "
                     f"{_num(row['tokens'] / n if n else None)} | {_money(row['cost'] / n if n else None, 5)} |")

    lines += ["", "Cost per use case (a call counts for the use case it served)", "",
              "| Use case | Path | Cases | Calls | Tokens | Cost | Cost per case |", "|---|---|---|---|---|---|---|"]
    for name, row in a["use_cases"].items():
        lines.append(f"| {name} | {row['path']} | {row['cases']} | {row['calls']} | {_num(row['tokens'])} | "
                     f"{_money(row['cost'], 5)} | {_money(row['cost_per_case'], 5)} |")

    lines += ["", "| Runs whose cases all took | Runs | Tasks | Tokens per run (mean) | Cost per run (mean) "
                  f"| Turns | Median turn | Slowest turn | Turns over {SLOW_TURN_S:.0f} s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for path, row in a["by_path"].items():
        t = row["turns"]
        lines.append(f"| {path} | {row['runs']} | {', '.join(row['tasks'])} | {_num(row['tokens']['mean'])} | "
                     f"{_money(row['cost']['mean'], 5)} | {t['n']} | {_num(t['median'], 2)} s | "
                     f"{_num(t['slowest'], 2)} s | {t['over_slow']} of {t['n']} |")
    if a["m"] is not None:
        agent, workflow = a["by_path"]["agent"], a["by_path"]["workflow"]
        lines.append(f"\nm = {_money(agent['cost']['mean'], 5)} / {_money(workflow['cost']['mean'], 5)} = "
                     f"{a['m']:.2f} (agent-path runs: {agent['runs']}; workflow-path runs: {workflow['runs']}; "
                     "on different cases, as no arm ran the same case both ways)")
    else:
        lines.append("\nm: not applicable (this arm has no runs on one of the two paths)")
    return "\n".join(lines)


def f_report(values: list[dict]) -> str:
    if not values:
        return "## f\n\nf: not applicable (no two arms set a use case's control switch to agent and to workflow)"
    lines = ["## f"]
    for v in values:
        lines += ["", f"{v['switch']}: agent in {v['agent_arm']}, workflow in {v['workflow_arm']}", "",
                  "| Task | Agent passed | Workflow passed | Counts for f |", "|---|---|---|---|"]
        lines += [f"| {r['task']} | {r['agent'][0]} of {r['agent'][1]} | {r['workflow'][0]} of {r['workflow'][1]} "
                  f"| {'yes' if r['counts'] else 'no'} |" for r in v["tasks"]]
        f = "n/a" if v["f"] is None else f"{v['f']:.2f}"
        lines.append(f"\nf = {sum(r['counts'] for r in v['tasks'])} of {len(v['tasks'])} tasks = {f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("arms", nargs="+", help="results/<arm> folders")
    p.add_argument("--json", metavar="OUT", help="also write every number to this JSON file")
    a = p.parse_args(argv)
    arms = []
    for name in a.arms:
        folder = Path(name)
        if not folder.is_dir():
            print(f"{name}: no such folder", file=sys.stderr)
            return 2
        arms.append(arm(folder))
    if not any(x["runs"] for x in arms):
        print("no runs to analyze", file=sys.stderr)
        return 2
    f = f_values(arms)
    print("\n\n".join([*(report(x) for x in arms), f_report(f)]))
    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"arms": arms, "f": f}, indent=2) + "\n")
        print(f"\nWrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
