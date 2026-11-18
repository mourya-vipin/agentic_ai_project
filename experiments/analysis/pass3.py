"""Pass 3's numbers, per arm: what the ledger entry and the table of passes give that the
summarizer does not (Part 4). Owner: Vipin Mourya.

    python experiments/analysis/pass3.py results/pass3-before results/pass3-after
    python experiments/analysis/pass3.py results/pass3-before results/pass3-after --tool access_propose_change
    python experiments/analysis/pass3.py results/pass3-before results/pass3-after --json docs/milestones/m2_pass3.json

It counts the runs the summarizer counts (the latest finished run of each task and sweep,
as analyze.py counts them) and prints, per arm:

- the runs, those that passed their check, by task; total tokens and tokens per run;
  model calls per run; the median and slowest turn (turn_latency_s);
- every switch whose setting differs between the arms, and the setting in each arm;
- for strict mode or examples: per run, the malformed calls (rejected at the registry's
  boundary, or a protocol error: no such tool, arguments not an object), and the wrong
  arguments (calls that passed the boundary and then failed the same way every time:
  an unknown ID, a group the person cannot have; error_class permanent, not denied by the
  hook); each by tool (--tool picks one out);
- the made-up group IDs: calls refused because the group ID they name belongs to no group
  ("No such group" from the tool, "No group '...'" from the hook, which then marks the call
  denied, so the wrong arguments above leave it out), by tool and by task, and the runs
  with one;
- for deferred loading or code calls: input tokens per model call (mean and median),
  tools offered and definition tokens per call (from each context line; a trace from
  before they were recorded says so), the searches (tools_search) and those that found
  nothing, calls of a tool the call was not offered (a protocol error after a deferral),
  and the code calls (code_run) per run.

It reads results/ only and spends no tokens; on free arms (results/_free/) it tests the
script and is not evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze import runs_of, trace_of  # noqa: E402  (Aman Panigrahi's: the runs the summarizer counts)


UNKNOWN_GROUP = re.compile(r"\bNo (?:such )?group\b[ :']")   # the tool's and the hook's wording


def _mean(values) -> float | None:
    return round(statistics.mean(values), 1) if values else None


def _median(values) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _per_run(n: int, total: int) -> float | None:
    return round(total / n, 3) if n else None


def arm(folder: Path, tool: str | None = None) -> dict:
    records = runs_of(folder)
    n = len(records)
    by_task = defaultdict(lambda: [0, 0])
    tokens, model_calls, turns = [], [], []
    malformed, wrong = Counter(), Counter()
    made_up, made_up_tasks, made_up_runs = Counter(), Counter(), set()
    input_per_call, offered, definition = [], [], []
    context_lines = context_recorded = 0
    searches = missed = code_runs = 0
    for record in records:
        by_task[record["task"]][0] += 1
        by_task[record["task"]][1] += bool(record.get("passed"))
        tokens.append((record.get("tokens") or {}).get("total", 0))
        model_calls.append(record.get("model_calls") or 0)
        turns += [t for t in record.get("turn_latency_s") or [] if t is not None]
        for x in trace_of(folder, record):
            kind = x.get("type")
            if kind == "model_call":
                input_per_call.append((x.get("usage") or {}).get("input_tokens", 0))
            elif kind == "context":
                context_lines += 1
                if "tools_offered" in x:
                    context_recorded += 1
                    offered.append(x["tools_offered"])
                    definition.append(x.get("definition_tokens") or 0)
            elif kind == "tool_search":
                searches += 1
                missed += not x.get("matched")
            elif kind == "tool_call":
                name = x.get("name") or "?"
                if name == "code_run":
                    code_runs += 1
                if x.get("is_error") and UNKNOWN_GROUP.search(str(x.get("output") or x.get("summary") or "")):
                    made_up[name] += 1
                    made_up_tasks[record["task"]] += 1
                    made_up_runs.add(record["run_id"])
                if x.get("rejected") or x.get("error_class") == "protocol":
                    malformed[name] += 1
                elif x.get("is_error") and x.get("error_class") == "permanent" and not x.get("denied"):
                    wrong[name] += 1
    pick = (lambda c: {tool: c.get(tool, 0)}) if tool else dict
    return {
        "runs": n,
        "passed": sum(p for _, p in by_task.values()),
        "by_task": {task: {"runs": r, "passed": p} for task, (r, p) in sorted(by_task.items())},
        "tokens_total": sum(tokens), "tokens_per_run": _mean(tokens),
        "model_calls_per_run": _mean(model_calls),
        "turn_median_s": _median(turns), "turn_slowest_s": round(max(turns), 1) if turns else None,
        "malformed_per_run": _per_run(n, sum(malformed.values())), "malformed_by_tool": pick(malformed),
        "wrong_args_per_run": _per_run(n, sum(wrong.values())), "wrong_args_by_tool": pick(wrong),
        "made_up_group_ids": sum(made_up.values()), "made_up_group_ids_per_run": _per_run(n, sum(made_up.values())),
        "made_up_group_ids_by_tool": pick(made_up), "made_up_group_ids_by_task": dict(sorted(made_up_tasks.items())),
        "runs_with_a_made_up_group_id": len(made_up_runs),
        "input_tokens_per_call": {"mean": _mean(input_per_call), "median": _median(input_per_call),
                                  "calls": len(input_per_call)},
        "tools_offered_per_call": _mean(offered), "definition_tokens_per_call": _mean(definition),
        "context_lines_with_tools": f"{context_recorded} of {context_lines}",
        "searches": searches, "searches_missed": missed,
        "code_runs_per_run": _per_run(n, code_runs),
        "switches": _switches(records),
    }


def _switches(records: list[dict]) -> dict:
    """Each switch's setting in this arm: one value, or every value seen when runs differ."""
    seen = defaultdict(set)
    for record in records:
        for name, value in (record.get("switches") or {}).items():
            seen[name].add(json.dumps(value, sort_keys=True))
    return {name: (json.loads(next(iter(v))) if len(v) == 1 else sorted(json.loads(s) for s in v))
            for name, v in sorted(seen.items())}


def differing(arms: dict[str, dict]) -> dict:
    """The switches whose settings differ between the arms: {switch: {arm: setting}}."""
    names = set().union(*(a["switches"] for a in arms.values())) if arms else set()
    out = {}
    for name in sorted(names):
        values = {label: a["switches"].get(name) for label, a in arms.items()}
        if len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
            out[name] = values
    return out


def report(arms: dict[str, dict]) -> str:
    out = []
    diff = differing(arms)
    out.append("switches that differ: " + ("; ".join(f"{k}: {v}" for k, v in diff.items()) or "none"))
    for label, a in arms.items():
        out.append("")
        out.append(f"== {label}: {a['passed']} of {a['runs']} runs passed ==")
        out.append("  by task: " + "; ".join(f"{t} {v['passed']}/{v['runs']}" for t, v in a["by_task"].items()))
        out.append(f"  tokens: {a['tokens_total']:,} in all, {a['tokens_per_run']} per run; "
                   f"{a['model_calls_per_run']} model calls per run; turn median {a['turn_median_s']} s, "
                   f"slowest {a['turn_slowest_s']} s")
        out.append(f"  malformed calls: {a['malformed_per_run']} per run {a['malformed_by_tool']}; "
                   f"wrong arguments: {a['wrong_args_per_run']} per run {a['wrong_args_by_tool']}")
        out.append(f"  made-up group IDs: {a['made_up_group_ids']} ({a['made_up_group_ids_per_run']} per run, in "
                   f"{a['runs_with_a_made_up_group_id']} runs) by tool {a['made_up_group_ids_by_tool']}, by task "
                   f"{a['made_up_group_ids_by_task']}")
        i = a["input_tokens_per_call"]
        out.append(f"  input tokens per model call: mean {i['mean']}, median {i['median']} ({i['calls']} calls); "
                   f"tools offered per call {a['tools_offered_per_call']}, definition tokens per call "
                   f"{a['definition_tokens_per_call']} (context lines with them: {a['context_lines_with_tools']})")
        out.append(f"  searches: {a['searches']}, {a['searches_missed']} found nothing; "
                   f"code calls: {a['code_runs_per_run']} per run")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("arms", nargs="+", type=Path, help="results/<arm> folders: before, then after")
    parser.add_argument("--tool", help="give the malformed and wrong-argument counts for this tool alone")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args(argv)
    arms = {folder.name: arm(folder, args.tool) for folder in args.arms}
    print(report(arms))
    if args.json:
        payload = {"arms": {k: {f: v for f, v in a.items() if f != "switches"} for k, a in arms.items()},
                   "switches_differ": differing(arms)}
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
