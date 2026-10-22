"""make seed: empty the channel, reset the sandbox, and seed it, for the app.

    python -m harness.seed                          # seed(None): your demo data, for the app
    python -m harness.seed --task tasks/t01.yaml    # seed(task): one task's inputs, to try it in the app

`make app` runs this first. run.py does the same before every run, in its own
state folder, so the app's data and the experiments' never mix.
"""

from __future__ import annotations

import argparse
import sys

import channel
from harness import ROOT, tasks
from harness.paths import state_dir


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", help="a task file whose input to seed as well")
    a = p.parse_args(argv)
    task = None
    if a.task:
        try:
            task = tasks.load(ROOT / a.task if not a.task.startswith("/") else a.task)
        except tasks.TaskError as exc:
            print(f"seed: {exc}", file=sys.stderr)
            return 2
    from sandbox import seed
    channel.reset()
    with channel.seeding():
        seed.reset()
        seed.seed(task)
    print(f"sandbox reset and seeded in {state_dir()}" + (f", with the inputs of task {task['id']}" if task
                                                          else ", with your demo data"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
