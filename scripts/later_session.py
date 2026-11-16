"""A later console session that reads the memory store before its first model call (Part 1,
task 7). Owner: Aman Panigrahi.

    python scripts/later_session.py      # no model call that costs tokens; writes traces/s-m2-later-session.jsonl

On a scratch sandbox seeded with the demo day, an earlier session's records are in the memory
store, with their provenance: a preference and a fact that the operator stated, a fact that a
newer one superseded, and one run that still waits for a manager's reply. Then a new console
session gets its first message, on a scripted model (harness.scripted:SaysDone). The trace that
M2.md cites shows the session's start: memory_read (what it returned and skipped) comes before
the first model_call, and the reply shows the operator each record with its source and age.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SESSION = "s-m2-later-session"
EARLIER = "s-m2-earlier-session"


def main() -> int:
    from harness.paths import STATE_ENV
    with tempfile.TemporaryDirectory() as scratch:
        os.environ[STATE_ENV] = scratch
        import channel
        from agent import bounds, checkpoints, memory
        from harness import runtime, turns
        from harness.trace import read_trace
        from sandbox import seed

        runtime.configure(backend="harness.scripted:SaysDone")
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        trace, setup = runtime.trace(SESSION), runtime.trace(EARLIER)
        for old in (trace.path, setup.path):        # the earlier session's lines are only the setup
            old.unlink(missing_ok=True)
        for text, kind, subject in (
                ("When a request is for someone else, do not ask their manager on the channel: draft a manager "
                 "confirmation for me to send instead.", "preference", None),
                ("Victor Lam's legal hold stays until Legal says it can go.", "fact", "SYN-EMP-0162"),
                ("Victor Lam's legal hold stays until the end of the month.", "fact", "SYN-EMP-0162")):
            memory.write(memory.Record(kind=kind, text=text, source="operator", subject=subject, confirmed=True,
                                       written_at="2026-11-09T10:00:00Z", session_id=EARLIER))
        waits = checkpoints.new_run(EARLIER, "nonstandard_request", "Arjun asks for SYN-GRP-GITHUB-ENG for Dev Rao")
        waits.findings = {"request_id": "SYN-REQ-0001"}
        checkpoints.end_turn(waits, bounds.WAITING_REPLY,
                             waiting_for={"thread_id": "SYN-MAIL-SYN-EMP-0060", "deadline": "2026-10-23T09:00:00Z"})
        reply = turns.operator_turn(SESSION, "Good morning. What is still open from before?")
        lines = read_trace(trace.path)
        kinds = [x["type"] for x in lines]
        read, call = kinds.index("memory_read") + 1, kinds.index("model_call") + 1
        setup.path.unlink(missing_ok=True)
        print(reply.text)
        print(f"trace: {trace.path.relative_to(ROOT)}; memory_read on line {read}, the first model_call on line {call}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
