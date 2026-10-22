"""Run one arm of a pass: your test tasks, for some sweeps, with the arm's switches set.

    python run.py --tasks tasks/ --sweeps 3 --arm baseline
    python run.py --tasks tasks/ --sweeps 3 --arm plan-off \\
        --set control.refunds=agent plan.refunds=off critique.refund_reply=external
    python run.py --tasks tasks/ --sweeps 3 --arm plan-on \\
        --set control.refunds=agent plan.refunds=on critique.refund_reply=external   # several switches
    python run.py --tasks tasks/dev/ --arm dev-try                     # one sweep on the test task you tune prompts on
    python run.py --tasks tasks/example.yaml --arm smoke --backend never-stops    # a test: results/_free/smoke/
    python run.py --discard --arm plan-on                               # start the arm over

    python -m harness.summarize results/plan-off results/plan-on

Before every run it resets the sandbox and seeds that test task's inputs alone. It plays
the test task's people through agent/service.py, the same calls the app makes: when a
turn ends waiting on a thread, that thread's scripted stranger answers, through
on_outside_reply. It records each run in results/<arm>/<run_id>.json: every
switch's setting, the commit and whether the code had uncommitted changes, the
tokens and cost, the latency with and without the waits (for an outside reply,
and the backend's sleep through a rate limit, throttled_s), how the run ended,
every end reason your code wrote with trace.end (end_reasons, and the last as
end_reason), every tool call in full, the agent's replies, the agent's time for
each turn without those waits (turn_latency_s), and the check's result with any
counts it returned (measures). The check reads the sandbox for the truth.

A run on a scripted model (--backend never-stops, or harness.scripted:SaysDone)
is free, and a test, not evidence: it goes to results/_free/<arm>/, which make
check and the summarizer leave out. So does a run on a fake model of your own,
--backend tests.fakes:BadPlanner say, whose class says free = True. --set a=x b=y
sets several switches for the arm; name every setting the arm depends on.

--tasks takes folders or files. A folder means the .yaml files directly in it,
never its subfolders: --tasks tasks/ leaves out tasks/dev/ and tasks/sessions/
(long sessions), which you name when you want them. Running the same command
again skips the runs the arm has finished, and warns if they ran from a commit
whose code differs from yours. --discard moves results/<arm>/ to
results/discarded/<arm>-<time>/, so the arm can run again from scratch after a
code change. An arm is one configuration: to change a setting, name a new arm
(with --backend never-stops, --discard works on results/_free/<arm>/). --jobs N
runs N at once if sandbox/seed.py says PARALLEL_SAFE = True; otherwise runs go
one at a time.
"""

import argparse
import sys

from harness.runner import QUOTA_MESSAGE, RunError, discard, is_free, run_arm


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tasks", nargs="+", default=["tasks/"],
                   help="folders or test task files (default tasks/); a folder means the .yaml files directly in it, "
                        "never its subfolders")
    p.add_argument("--sweeps", type=int, default=1, help="how many times to run every test task (default 1)")
    p.add_argument("--arm", required=True, help="the arm's name: its runs go to results/<arm>/")
    p.add_argument("--set", dest="overrides", action="extend", nargs="+", default=[], metavar="NAME=VALUE",
                   help="set switches in switches.yaml for this arm: --set a=x sets one, --set a=x b=y several")
    p.add_argument("--backend", help="default: the provider in settings.yaml. never-stops or harness.scripted:SaysDone "
                                     "runs free, into results/_free/<arm>/, which make check and the summarizer "
                                     "leave out; module:Class names a model of your own, free too if its class "
                                     "says free = True")
    p.add_argument("--jobs", type=int, help="runs at once, if your sandbox is safe for it "
                                            "(default: jobs in settings.yaml)")
    p.add_argument("--discard", action="store_true",
                   help="move results/<arm>/ to results/discarded/<arm>-<time>/, so the arm can run again from "
                        "scratch after a code change; runs nothing (a free arm: results/_free/discarded/)")
    a = p.parse_args(argv)
    if a.sweeps < 1:
        p.error("--sweeps is 1 or more")
    try:
        if a.discard:
            discard(a.arm, free=is_free(a.backend) if a.backend else None)
            return 0
        rows = run_arm(a.tasks, a.arm, a.sweeps, a.overrides, a.backend, a.jobs)
    except RunError as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("nothing to run")
        return 0
    passed = sum(1 for r in rows if r["passed"])
    tokens = sum(r["tokens"]["total"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    print(f"\n{len(rows)} runs, {passed} passed their check, {tokens:,} tokens, ${cost:.4f}")
    if any(r["outcome"] == "not_built" for r in rows):
        print("agent/service.py is not built yet: those runs are recorded, and none of them counts.")
    limited = sum(1 for r in rows if r["outcome"] == "rate_limited")
    if limited:
        print(f"{limited} runs stopped by rate limits are not finished: run the same command again to redo them")
    if any(r["outcome"] == "quota" for r in rows):
        print(QUOTA_MESSAGE)
    for note in sorted({n for r in rows for n in r.get("notes") or []}):
        print(f"note: {note}")
    if rows[0].get("free"):
        print(f"free runs on a scripted model, in results/_free/{a.arm}/: a test, not evidence; "
              "make check and the summarizer leave them out")
    else:
        print(f"python -m harness.summarize results/{a.arm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
