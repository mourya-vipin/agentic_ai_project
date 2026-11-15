"""The stop, measured against its five conditions (Part 2, task 6). Owner: Shruti Tewaney.

    python scripts/stop_conditions.py      # no model call: a fake model (tests.fakes:SlowPlanner)

Five planned t06 runs, each in a second thread, each on a scratch sandbox. Each model call of the
fake takes 0.3 s. The checkpoint after Victor Lam's listing fails and the reviser's model call
starts; /stop is sent from the first thread while it runs. The run is then resumed and its plan
approved, as the operator would. For each run it reads, from the trace and the sandbox:

    watched    the run's notice comes before its first write, and again on the resume
    fast       seconds from the stop's flag to the first call the hook refused
    durable    the run is saved stopped after its last call, and the resume duplicates nothing
    safe       every write before the stop is in the run's journal, with its key
    outside    no write between the stop and the resume

It writes docs/milestones/m2_stop_conditions_<date>.json and the first run's trace to
traces/s-m2-stop.jsonl, which M2.md cites.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUNS = 5
TRACE_OUT = ROOT / "traces" / "s-m2-stop.jsonl"


def _at(line) -> float:
    return datetime.fromisoformat(line["at"]).timestamp()


def one_run(run_id: str) -> dict:
    import channel
    from agent import checkpoints, commands, control, executor
    from agent.bounds import Bound
    from harness.trace import read_trace
    from sandbox import db
    from tests import fakes
    from tests.support import fixed_run

    task = {"id": "t06_stop", "operator": [fakes.T06_TEXT], "outside": [], "check": "checks/example.py",
            "input": {"kind": "fact", "table": "employees", "id": "SYN-EMP-0164", "set": {"end_date": "2026-10-28"}}}

    def writes() -> int:
        with db.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

    with fixed_run(task, run_id, backend="tests.fakes:SlowPlanner") as run:
        worker = threading.Thread(target=lambda: executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound()))
        worker.start()
        while not any(x["type"] == "checkpoint" and not x["passed"] for x in read_trace(run.trace_path)):
            time.sleep(0.01)
        commands.handle(run_id, "/stop")
        worker.join(30)
        at_stop = read_trace(run.trace_path)
        key = checkpoints.live(run_id)[0].run_key
        saved = checkpoints.load(key)
        writes_at_stop = writes()
        time.sleep(0.2)                                   # the run stays stopped: nothing moves meanwhile
        writes_before_resume = writes()
        control.resume(key)
        fakes.approve_all()
        lines = read_trace(run.trace_path)
        with db.connect() as conn:
            twice = conn.execute("SELECT COUNT(*) AS n FROM (SELECT employee_id, group_id FROM memberships "
                                 "WHERE removed_at IS NOT NULL GROUP BY employee_id, group_id HAVING COUNT(*) > 1)"
                                 ).fetchone()["n"]
        notes_sent = {m: len([x for x in channel.messages(f"SYN-MAIL-{m}") if x.sender == channel.AGENT])
                      for m in (fakes.HANNAH, fakes.GRACE)}
        final = checkpoints.load(key).status
    stop_i = next(i for i, x in enumerate(at_stop) if x["type"] == "stop_flag")
    stop = at_stop[stop_i]
    refused = next(x for x in at_stop[stop_i:] if x["type"] == "tool_call" and x.get("denied"))
    calls = [x for x in at_stop if x["type"] == "tool_call"]
    first_write = next(i for i, x in enumerate(lines) if x["type"] == "tool_call" and x.get("key"))
    notices = [i for i, x in enumerate(lines) if x["type"] == "run_notice"]
    written = [x["key"] for x in at_stop[:stop_i] if x["type"] == "tool_call" and x.get("key") and not x.get("denied")]
    return {
        "run_id": run_id,
        "watched": {"notice_line": notices[0] + 1, "first_write_line": first_write + 1,
                    "notice_first": notices[0] < first_write and _at(lines[notices[0]]) <= _at(lines[first_write]),
                    "notice_again_on_resume": any(lines[i].get("resumed") for i in notices)},
        "fast": {"stop_line": stop_i + 1, "refused_call": refused["name"], "plan_step": refused.get("plan_step"),
                 "seconds": round(_at(refused) - _at(stop), 3)},
        "durable": {"saved_status": saved.status, "saved_after_last_call": saved.updated_at >= calls[-1]["at"],
                    "duplicate_removals": twice, "notes_sent": notes_sent, "final_status": final},
        "safe": {"writes_before_stop": len(written), "all_in_journal": all(k in saved.completed for k in written)},
        "outside": {"calls_run_after_stop": len([x for x in at_stop[stop_i:] if x["type"] == "tool_call"
                                                 and not x.get("denied")]),
                    "writes_between_stop_and_resume": writes_before_resume - writes_at_stop},
        "_trace": str(run.trace_path),
    }


def main() -> int:
    from harness.paths import RESULTS_ENV, STATE_ENV, TRACES_ENV
    with tempfile.TemporaryDirectory() as scratch:
        for env, sub in ((STATE_ENV, "state"), (RESULTS_ENV, "results"), (TRACES_ENV, "traces")):
            os.environ[env] = str(Path(scratch) / sub)
        from harness import runtime
        runtime.configure()
        runs = [one_run(f"s-m2-stop-{n}-{uuid.uuid4().hex[:6]}") for n in range(1, RUNS + 1)]
        shutil.copyfile(runs[0]["_trace"], TRACE_OUT)
    seconds = [r["fast"]["seconds"] for r in runs]
    summary = {"made_by": "scripts/stop_conditions.py", "date": date.today().isoformat(), "model": "tests.fakes:SlowPlanner (no tokens)",
               "model_call_seconds": 0.3, "runs": len(runs), "trace": str(TRACE_OUT.relative_to(ROOT)),
               "fast_seconds": {"median": statistics.median(seconds), "largest": max(seconds), "smallest": min(seconds),
                                "each": seconds},
               "all_watched": all(r["watched"]["notice_first"] and r["watched"]["notice_again_on_resume"] for r in runs),
               "all_durable": all(r["durable"]["saved_status"] == "stopped" and r["durable"]["saved_after_last_call"]
                                  and r["durable"]["duplicate_removals"] == 0 and set(r["durable"]["notes_sent"].values()) == {1}
                                  for r in runs),
               "all_safe": all(r["safe"]["all_in_journal"] and r["safe"]["writes_before_stop"] > 0 for r in runs),
               "all_outside": all(r["outside"]["calls_run_after_stop"] == 0 and r["outside"]["writes_between_stop_and_resume"] == 0
                                  for r in runs),
               "each": [{k: v for k, v in r.items() if k != "_trace"} for r in runs]}
    out = ROOT / "docs" / "milestones" / f"m2_stop_conditions_{date.today().isoformat()}.json"
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(f"fast: median {summary['fast_seconds']['median']} s, largest {summary['fast_seconds']['largest']} s over {len(runs)} stops")
    print(f"watched {summary['all_watched']}, durable {summary['all_durable']}, safe {summary['all_safe']}, "
          f"outside {summary['all_outside']}")
    print(f"wrote {out.relative_to(ROOT)} and {TRACE_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
