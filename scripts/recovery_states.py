"""Run the recovery test and keep what the sandbox held at each point of each case: before
the run, after the crash, after the resume, and in a run with no crash (Part 1, task 2).

Owner: Aman Panigrahi. No tokens: the models are fakes.

    python scripts/recovery_states.py      writes docs/milestones/m2_recovery_<today>.json
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    out = ROOT / "docs" / "milestones" / f"m2_recovery_{datetime.date.today().isoformat()}.json"
    env = {**os.environ, "RECOVERY_RECORD": str(out)}
    done = subprocess.run([sys.executable, "-m", "unittest", "tests.test_recovery"], cwd=ROOT, env=env)
    if done.returncode == 0:
        print(f"wrote {out.relative_to(ROOT)}")
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
