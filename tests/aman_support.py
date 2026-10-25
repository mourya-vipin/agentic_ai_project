"""Shared by tests/test_routing.py and tests/test_bound.py: one run of a test task on a
fake model, and the lines of its trace. Owner: Aman Panigrahi."""

from __future__ import annotations

from harness.trace import read_trace
from tests.support import ScratchTest


class TaskRunTest(ScratchTest):
    def run_on(self, task: str, backend: str, overrides=None) -> dict:
        if not backend.count(":") and not backend.endswith("stops"):
            backend = f"tests.fakes:{backend}"
        self.record = self.run_task(f"tasks/{task}.yaml", "aman", backend=backend, overrides=overrides)
        found = list((self.tmp / "results").rglob(f"{self.record['run_id']}.trace.jsonl"))
        self.assertEqual(len(found), 1, "the run's trace is in the test's scratch folder")
        self.lines = read_trace(found[0])
        return self.record

    def of(self, kind: str) -> list[dict]:
        return [x for x in self.lines if x["type"] == kind]

    def routes(self, scope: str) -> list[dict]:
        return [x for x in self.of("route") if x.get("scope") == scope]

    def first_turn(self, kind: str) -> list[dict]:
        """The lines of one kind in the run's first operator turn, up to its reply."""
        out = []
        for line in self.lines:
            if line["type"] == "reply":
                break
            if line["type"] == kind:
                out.append(line)
        return out
