"""A refusal the hook logs for a send without the disclosure line (Part 2, task 8). Owner: Vipin Mourya.

    python scripts/disclosure_refusal.py      # no model call; writes traces/s-m2-disclosure-refusal.jsonl

On a scratch sandbox seeded with the demo day, one console session calls the registry directly
with a reply whose text was built without the line, as a caller that skipped disclosure.stamp()
would, then the same reply stamped. The first is refused before anything runs (hook_denied,
"no disclosure") and the thread is unchanged. The second passes the disclosure check and goes on to
the approval step, since a reply the model wrote always waits for the operator; until the gate is
built, its tool refuses it (approval_unavailable). The trace is the evidence M2.md cites.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SESSION = "s-m2-disclosure-refusal"


def main() -> int:
    from harness.paths import STATE_ENV
    with tempfile.TemporaryDirectory() as scratch:
        os.environ[STATE_ENV] = scratch
        import channel
        from agent import disclosure
        from agent.registry import CallContext, get_registry
        from harness import runtime
        from sandbox import seed

        runtime.configure()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        trace = runtime.trace(SESSION)
        if trace.path.exists():
            trace.path.unlink()
        registry = get_registry()
        args = {"recipient_employee_id": "SYN-EMP-0031", "request_id": "SYN-REQ-0005",
                "body": "SYNTHETIC. Hi Leo, your request is with the IT team; we will reply here."}
        ctx = CallContext(SESSION, 1, "model", "nonstandard_request", request_id="SYN-REQ-0005",
                          run_key=f"{SESSION}#1")
        with runtime.in_session(SESSION):
            before = len(channel.messages("SYN-MSG-D05"))
            refused = registry.call("reply_send", args, ctx)
            after = len(channel.messages("SYN-MSG-D05"))
            stamped = registry.call("reply_send", disclosure.stamp_call("reply_send", args), ctx)
        print(f"without the line: {'refused' if refused.is_error else 'sent'} ({refused.content[:80]}); "
              f"thread {before} -> {after} messages")
        print(f"with the line: {stamped.end_reason or ('refused' if stamped.is_error else 'sent')}")
        print(f"trace: {trace.path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
