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
  step_halted; and the diff, from every plan_diff line: plan steps executed, skipped,
  out of order and failed, and tool calls that match no step (extra), with the planned
  runs that had each;
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
  how many took over 10 s (Lecture 1, slide 34);
- per task: passes, and passes by substance for t07 (below); cost, uncached tokens
  (all but cache reads), all tokens and tool calls per run, mean and range; turns,
  median and range; the same for all the other tasks of the arm; and, where a task plans, each
  run's planning and revision time (the planner's call, the reviser's, and both) and
  tokens;
- gate 4 for U4: c (the workflow-path mean), f (the U4 runs that passed), and from
  them the extra cost per case, the cost per extra correct case, the break-even f and
  the blended cost, with A1's m (--a1-m) and with the arm's own m;
- our bound against what each loop used: the most model calls, tokens and seconds of
  any loop (from its bound line to its end line), its first prompt and what each step
  added, and the 95th percentile of a loop call's latency.

By substance: the t07 check finds a request only by its request ID (CHANGES.md,
Limited). A failed t07 run whose answer picks out the right requests by their
requesters' names or employee IDs is read again as the check reads it, once each name
and ID is followed by its request ID (from the run's own requests_list_today result).
Every other run counts as its check said.

Across arms, f, when two arms set one use case's control switch differently: the share
of that use case's tasks the agent passed in at least two of three sweeps and the
workflow in at most one (at least 2/3 and at most 1/3 of the sweeps for other counts).

It reads results/ only, spends no tokens, and works on free arms too (results/_free/),
which test the script and are not evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.bounds import Bound  # noqa: E402  (the repository root is on the path only from here)
from checks import t07_unusual_review as t07  # noqa: E402
from harness import records  # noqa: E402

FINISHED = ("done", "answered", "plan_done", "workflow_done")
OUR_BOUND = ("step_limit", "loop_token_budget", "wall_clock")
PLAN_ENDS = ("plan_rejected", "revision_bound", "step_halted")
DIFF_LABELS = ("executed", "skipped", "out_of_order", "failed", "extra")   # a plan_diff line's lists
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
PLANNING = ("plan", "revise")                                        # the planner's and reviser's calls
U4 = "nonstandard_request"
SUBSTANCE_TASK = "t07_unusual_review"
REQUEST_TOOLS = ("requests_list_today", "requests_get")

# Gate 4's inputs from outside our runs (M1.md, Part 1, task 5)
A1_M = 9.70                     # Shruti's A1 (shruti072003/agentic-ai-assignment1, reports/LAB_01.md, line 95)
WRONG_CASE_USD = 0.25 * 47.66   # 15 minutes, our assumption, at $47.66 an hour (US BLS, Occupational
                                # Outlook Handbook, network and computer systems administrators, 2025 median
                                # pay, read 2026-10-26)
SHARE_TO_AGENT = 0.2            # r, the share of cases routed to the agent: our assumption
CAUTIOUS_F = 0.5


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


def _prompt(event: dict) -> int:
    u = event.get("usage") or {}
    return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def _at(event: dict) -> datetime | None:
    return datetime.fromisoformat(event["at"]) if event.get("at") else None


def loops(events: list[dict]) -> list[dict]:
    """Each loop's use of our bound, from its bound line to its end line: model calls,
    tokens (prompt and output, as the loop counts them), seconds, each call's prompt
    tokens and latency."""
    out, now = [], None
    for e in events:
        kind = e.get("type")
        if kind == "bound":
            now = {"use_case": e.get("use_case"), "path": e.get("path", "loop"), "calls": 0, "tokens": 0,
                   "prompts": [], "latencies": [], "start": _at(e)}
        elif now is None:
            continue
        elif kind == "model_call":
            now["calls"] += 1
            now["tokens"] += _usage(e)["tokens"]
            now["prompts"].append(_prompt(e))
            now["latencies"].append(e.get("latency_s") or 0.0)
        elif kind == "end":
            start, end = now.pop("start"), _at(e)
            now["seconds"] = (end - start).total_seconds() if start and end else None
            now["end_reason"] = e.get("terminal_reason")
            out.append(now)
            now = None
    return out


def planning(events: list[dict]) -> dict:
    """The planner's and reviser's calls in one run: calls, seconds (the planner's, the
    reviser's and both), tokens and cost."""
    out = {"calls": 0, "seconds": 0.0, "plan_seconds": 0.0, "revise_seconds": 0.0, "tokens": 0, "cost": 0.0}
    for e in events:
        if e.get("type") == "model_call" and e.get("purpose") in PLANNING:
            _add(out, _usage(e))
            seconds = e.get("latency_s") or 0.0
            out["seconds"] += seconds
            out[f"{e['purpose']}_seconds"] += seconds
    return out


def plan_diff(events: list[dict]) -> dict | None:
    """The diff of each planned turn against its plan (the executor's plan_diff lines,
    agent/detectors.py: diff), summed over the run: how many plan steps were executed,
    skipped, out of order and failed, and how many tool calls matched no step (extra).
    None for a run that wrote no plan_diff line."""
    diffs = [e for e in events if e.get("type") == "plan_diff"]
    if not diffs:
        return None
    return {"diffs": len(diffs), **{label: sum(len(e.get(label) or []) for e in diffs) for label in DIFF_LABELS}}


def by_substance(record: dict, events: list[dict]) -> bool:
    """Whether a run passed by substance (the module's docstring): its check's verdict,
    except that a failed t07 run that wrote nothing is read again, its answer with each
    requester's name and employee ID followed by their request ID."""
    passed = bool(record.get("passed"))
    if passed or record["task"] != SUBSTANCE_TASK or (record.get("measures") or {}).get("writes"):
        return passed
    answers = [e.get("text") or "" for e in events if e.get("type") == "reply" and e.get("function") == "operator_turn"]
    if not answers:
        return False
    people = defaultdict(set)                      # (employee ID, name) -> the requests they made
    for e in events:
        if e.get("type") != "tool_call" or e.get("name") not in REQUEST_TOOLS:
            continue
        try:
            output = json.loads(e.get("output") or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for r in (output.get("requests", [output]) if isinstance(output, dict) else []):
            if isinstance(r, dict) and r.get("id") and r.get("requester_id"):
                people[(r["requester_id"], r.get("requester_name"))].add(r["id"])
    text = answers[0]
    for (employee_id, name), made in people.items():
        if len(made) != 1:
            continue                               # a person with two requests stands for neither
        request_id = next(iter(made))
        text = re.sub(rf"\b{re.escape(employee_id)}\b", f"{employee_id} ({request_id})", text)
        if name:
            text = re.sub(rf"\b{re.escape(name)}\b(?! \(SYN-EMP-)", f"{name} ({request_id})", text)
    found, flagged = t07.read_answer(text)
    return found == set(t07.UNUSUAL) and not flagged


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
        "by_substance": by_substance(record, events),
        "tokens": record["tokens"]["total"], "cost": record["cost_usd"],
        "uncached": record["tokens"]["total"] - (record["tokens"].get("cache_read") or 0),
        "planning": planning(events), "loops": loops(events),
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
        "tool_calls": len(tool_calls),
        "plans": types["plan"],
        "diff": plan_diff(events),
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


def _spread(values: list[float]) -> dict:
    return {**_stats(values), "min": min(values, default=None), "max": max(values, default=None)}


def _detail(rows: list[dict]) -> dict:
    return {"runs": len(rows), "passed": sum(r["passed"] for r in rows),
            "by_substance": sum(r["by_substance"] for r in rows),
            "cost": _spread([r["cost"] for r in rows]), "uncached": _spread([r["uncached"] for r in rows]),
            "tokens": _spread([r["tokens"] for r in rows]), "turns": _spread([t for r in rows for t in r["turns"]]),
            "tool_calls": _spread([r["tool_calls"] for r in rows])}


def per_task_detail(runs: list[dict], tasks: list[str]) -> dict:
    """Each task's runs, against the arm's other tasks; and each planned run's planning."""
    out = {}
    for t in tasks:
        mine = [r for r in runs if r["task"] == t]
        rest = _detail([r for r in runs if r["task"] != t])
        out[t] = {**_detail(mine),
                  "rest": {k: rest[k] for k in ("runs", "passed", "by_substance")}
                          | {"median_turn": rest["turns"]["median"]},
                  "planning": [{"run_id": r["run_id"], "sweep": r["sweep"], **r["planning"], "run_tokens": r["tokens"]}
                               for r in mine if r["planning"]["calls"]]}
    return out


def diff_totals(runs: list[dict]) -> dict:
    """The plan diff over an arm: each label summed over every run with a plan_diff line,
    and the runs that had a skipped step, a step out of order, a failed step or an extra call."""
    diffed = [r for r in runs if r["diff"]]
    return {"runs": len(diffed), **{label: sum(r["diff"][label] for r in diffed) for label in DIFF_LABELS},
            "runs_with": {label: ids([r for r in diffed if r["diff"][label]]) for label in DIFF_LABELS[1:]}}


def gate4(runs: list[dict], c: float | None, own_m: float | None, a1_m: float = A1_M,
          wrong_case: float = WRONG_CASE_USD, r: float = SHARE_TO_AGENT) -> dict:
    """Gate 4 for U4 (M1.md, Part 1, task 5), with A1's m and with the arm's own m. f is
    the share of the U4 cases on the agent path whose run passed: M1.md argues that a
    workflow gets those cases wrong."""
    u4 = [x for x in runs if "agent" in x["case_paths"].get(U4, [])]
    f = sum(x["passed"] for x in u4) / len(u4) if u4 else None

    def at(m):
        if c is None or m is None:
            return None
        extra = c * (m - 1)
        return {"m": m, "extra_per_case": extra, "per_extra_correct": extra / f if f else None,
                "per_extra_correct_cautious": extra / CAUTIOUS_F, "break_even_f": extra / wrong_case,
                "one_in": wrong_case / extra if extra > 0 else None,
                "blended": c * ((1 - r) + r * m), "agent_every_case": c * m}
    return {"c": c, "f": f, "u4_runs": len(u4), "u4_passed": sum(x["passed"] for x in u4), "cautious_f": CAUTIOUS_F,
            "wrong_case": wrong_case, "r": r, "a1": at(a1_m), "own": at(own_m)}


def _p95(values: list[float]) -> float | None:
    if len(values) < 2:
        return values[0] if values else None
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def headroom(runs: list[dict], bound: Bound = Bound()) -> dict | None:
    """Our bound against what each loop used (planned or not), and the agent loop's
    growth: each step adds to one conversation, so a loop whose first prompt is F tokens
    and grows by A a step uses about N*F + A*N*(N-1)/2 at the step limit N. A is each
    loop's mean growth, (last prompt - first prompt) / (calls - 1); the planned path's
    calls are not one conversation, so F and A leave them out."""
    every = [{**loop, "run_id": r["run_id"]} for r in runs for loop in r["loops"]]
    if not every:
        return None

    def most(key):
        top = max(every, key=lambda loop: loop[key] or 0)
        return {"value": top[key], "run_id": top["run_id"], "use_case": top["use_case"], "path": top["path"]}

    agent = [loop for loop in every if loop["path"] == "loop" and loop["prompts"]]
    first = _spread([loop["prompts"][0] for loop in agent])
    added = _spread([(loop["prompts"][-1] - loop["prompts"][0]) / (loop["calls"] - 1)
                     for loop in agent if loop["calls"] > 1])
    latencies = [t for loop in every for t in loop["latencies"]]
    n = bound.max_steps

    def at_limit(f, a):
        return None if f is None or a is None else n * f + a * n * (n - 1) / 2
    return {"bound": {"max_steps": n, "token_budget": bound.token_budget, "wall_clock_s": bound.wall_clock_s},
            "loops": len(every), "agent_loops": len(agent), "most_calls": most("calls"),
            "most_tokens": most("tokens"), "longest": most("seconds"), "first_prompt": first, "added_per_step": added,
            "at_step_limit": {"medians": at_limit(first["median"], added["median"]),
                              "maxima": at_limit(first["max"], added["max"])},
            "call_latency": {"n": len(latencies), "p95": _p95(latencies), "max": max(latencies, default=None)}}


def arm(folder: Path, a1_m: float = A1_M, wrong_case: float = WRONG_CASE_USD,
        share_to_agent: float = SHARE_TO_AGENT) -> dict:
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
        "runs": n, "passes": sum(r["passed"] for r in runs),
        "passes_by_substance": sum(r["by_substance"] for r in runs), "tasks": tasks, "sweeps": sweeps,
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
                   "ended": {x: ids([r for r in runs if x in r["end_reasons"]]) for x in PLAN_ENDS},
                   "diff": diff_totals(runs)},
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
        "per_task_detail": per_task_detail(runs, tasks),
        "gate4": gate4(runs, (by_path.get("workflow") or {}).get("cost", {}).get("mean"), m, a1_m, wrong_case,
                       share_to_agent),
        "bound_headroom": headroom(runs),
        "per_run": [{k: r[k] for k in ("run_id", "task", "sweep", "passed", "by_substance", "tokens", "uncached",
                                       "cost", "model_calls", "tool_calls", "turns", "end_reasons", "path",
                                       "false_finish", "repeat_calls", "critique_rounds", "diff", "cases")}
                    for r in runs],
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
    d = p["diff"]
    if d["runs"]:
        w = d["runs_with"]
        lines += [f"Plan diff ({d['runs']} planned runs, every plan_diff line summed): {d['executed']} steps executed, "
                  f"{d['skipped']} skipped, {d['out_of_order']} out of order, {d['failed']} failed; "
                  f"{d['extra']} calls that match no step (extra)",
                  f"Planned runs with a skipped step: {_ids(w['skipped'], d['runs'])}; a step out of order: "
                  f"{_ids(w['out_of_order'], d['runs'])}; a failed step: {_ids(w['failed'], d['runs'])}; "
                  f"an extra call: {_ids(w['extra'], d['runs'])}"]
    else:
        lines.append("Plan diff: no planned run in this arm")

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
    lines += ["", *detail_report(a), "", *gate4_report(a["gate4"]), "", *headroom_report(a["bound_headroom"])]
    return "\n".join(lines)


def _range(s: dict, show, unit="") -> str:
    return "n/a" if s["mean"] is None else f"{show(s['mean'])}{unit} ({show(s['min'])}–{show(s['max'])}{unit})"


def detail_report(a: dict) -> list[str]:
    lines = [f"Per task (cost, tokens and tool calls per run: mean, and range; turns: median, and range; uncached: "
             f"all but cache reads; by substance: t07 read again, as the module's docstring says): "
             f"{a['passes_by_substance']} of {a['runs']} by substance", "",
             "| Task | Passed | By substance | Cost per run | Uncached tokens per run | Tokens per run "
             "| Tool calls per run | Turns | The other tasks' median turn |", "|---|---|---|---|---|---|---|---|---|"]
    planned = []
    for t, d in a["per_task_detail"].items():
        turns, calls = d["turns"], d["tool_calls"]
        median = "n/a" if turns["median"] is None else (f"{turns['median']:.2f} s ({turns['min']:.2f}–"
                                                         f"{turns['max']:.2f} s)")
        tool_calls = "n/a" if calls["mean"] is None else (f"{_num(calls['mean'], 1)} ({_num(calls['min'])}–"
                                                          f"{_num(calls['max'])})")
        lines.append(f"| {t} | {d['passed']} of {d['runs']} | {d['by_substance']} of {d['runs']} | "
                     f"{_range(d['cost'], _money)} | {_range(d['uncached'], _num)} | "
                     f"{_range(d['tokens'], _num)} | {tool_calls} | {median} | {_num(d['rest']['median_turn'], 2)} s |")
        planned += d["planning"]
    if planned:
        lines += ["", "Planning and revision per planned run (the planner's and reviser's model calls)", "",
                  "| Run | Calls | Planner, s | Reviser, s | Both, s | Tokens | Of the run's tokens | Cost |",
                  "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {p['run_id']} | {p['calls']} | {p['plan_seconds']:.3f} | {p['revise_seconds']:.3f} | "
                  f"{p['seconds']:.1f} | {_num(p['tokens'])} | {_num(p['run_tokens'])} | {_money(p['cost'], 5)} |"
                  for p in planned]
    return lines


def gate4_report(g: dict) -> list[str]:
    head = (f"Gate 4, U4 ({U4}): c = {_money(g['c'], 5)} (the workflow-path mean); f = {g['u4_passed']} of "
            f"{g['u4_runs']} U4 runs passed = {_num(g['f'], 2)} (cautious: {g['cautious_f']}); a wrong case = "
            f"{_money(g['wrong_case'], 3)}; r = {g['r']}")
    shown = [(label, v) for label, v in (("A1's m", g["a1"]), ("this arm's m", g["own"])) if v]
    if not shown:
        return [head, "", "Gate 4: not applicable (this arm has no workflow-path runs, or no m)"]
    lines = [head, "", "| Quantity | " + " | ".join(f"With {label} = {v['m']:.2f}" for label, v in shown) + " |",
             "|---|" + "---|" * len(shown)]
    rows = (("Extra cost per case, c(m - 1)", lambda v: _money(v["extra_per_case"])),
            ("Per extra correct case, c(m - 1) / f", lambda v: _money(v["per_extra_correct"])),
            (f"The same at f = {g['cautious_f']}", lambda v: _money(v["per_extra_correct_cautious"])),
            ("Break-even f, c(m - 1) / a wrong case", lambda v: f"{v['break_even_f']:.5f} (one case in "
                                                                f"{_num(v['one_in'])})"),
            ("Blended cost, c[(1 - r) + rm]", lambda v: _money(v["blended"])),
            ("The agent on every case, cm", lambda v: _money(v["agent_every_case"])))
    lines += [f"| {label} | " + " | ".join(show(v) for _, v in shown) + " |" for label, show in rows]
    return lines


def headroom_report(h: dict | None) -> list[str]:
    if not h:
        return ["Our bound: no loop ran in this arm"]
    b = h["bound"]
    lines = [f"Our bound ({b['max_steps']} model calls, {b['token_budget']:,} tokens, {b['wall_clock_s']:g} s a turn) "
             f"against {h['loops']} loops, each from its bound line to its end line", "",
             "| Used in one loop | Most | Run | Use case | Bound |", "|---|---|---|---|---|"]
    for label, key, show, limit in (("Model calls", "most_calls", str, b["max_steps"]),
                                    ("Tokens", "most_tokens", _num, f"{b['token_budget']:,}"),
                                    ("Seconds", "longest", lambda x: _num(x, 1), f"{b['wall_clock_s']:g}")):
        top = h[key]
        lines.append(f"| {label} | {show(top['value'])} | {top['run_id']} | {top['use_case']} ({top['path']}) | "
                     f"{limit} |")
    f, a, at, lat = h["first_prompt"], h["added_per_step"], h["at_step_limit"], h["call_latency"]
    lines += ["", f"The agent loop ({h['agent_loops']} loops): first prompt F, median {_num(f['median'])}, most "
                  f"{_num(f['max'])} tokens; A, each loop's mean growth a step, median {_num(a['median'])}, most "
                  f"{_num(a['max'])} tokens",
              f"At {b['max_steps']} steps, N*F + A*N(N-1)/2: {_num(at['medians'])} tokens with the medians, "
              f"{_num(at['maxima'])} with the most",
              f"Loop model calls: {lat['n']}; latency 95th percentile {_num(lat['p95'], 2)} s, slowest "
              f"{_num(lat['max'], 2)} s; {b['max_steps']} calls at the 95th percentile: "
              f"{_num(lat['p95'] * b['max_steps'] if lat['p95'] is not None else None, 1)} s"]
    return lines


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
    p.add_argument("--a1-m", type=float, default=A1_M, help=f"gate 4's m from A1 (default {A1_M})")
    p.add_argument("--wrong-case", type=float, default=WRONG_CASE_USD,
                   help=f"gate 4's cost of a wrong case, in dollars (default {WRONG_CASE_USD:.2f})")
    p.add_argument("--share-to-agent", type=float, default=SHARE_TO_AGENT,
                   help=f"gate 4's r, the share of cases routed to the agent (default {SHARE_TO_AGENT})")
    a = p.parse_args(argv)
    arms = []
    for name in a.arms:
        folder = Path(name)
        if not folder.is_dir():
            print(f"{name}: no such folder", file=sys.stderr)
            return 2
        arms.append(arm(folder, a.a1_m, a.wrong_case, a.share_to_agent))
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
