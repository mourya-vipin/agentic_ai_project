"""The analysis script of state, the stranger and memory (experiments/analysis/state.py), on small
arms built here: what it counts, and what it leaves out. No tokens. Owner: Aman Panigrahi."""

import json
import sys
from pathlib import Path

from tests.support import ScratchTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "analysis"))

import state  # noqa: E402
from analyze import runs_of, trace_of  # noqa: E402

ASK = {"type": "tool_call", "name": "channel_ask_template", "is_error": False}


def write_arm(folder: Path, runs: list[tuple[dict, list[dict]]]) -> Path:
    """One record and one trace for each run, each in a sweep of its own: the summarizer counts
    only the latest run of each task and sweep."""
    folder.mkdir(parents=True, exist_ok=True)
    for n, (fields, lines) in enumerate(runs, start=1):
        run_id = f"{folder.name}-r{n}"
        record = {"run_id": run_id, "arm": folder.name, "task": fields.get("task", "t01"), "sweep": n,
                  "outcome": "completed", "passed": fields.get("passed", True)}
        (folder / f"{run_id}.json").write_text(json.dumps(record))
        (folder / f"{run_id}.trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return folder


def arm_of(folder: Path) -> dict:
    return state.count([(r, trace_of(folder, r)) for r in runs_of(folder)])


class StateTest(ScratchTest):
    def test_questions_waits_and_replies_per_task(self):
        folder = write_arm(self.tmp / "arm", [
            ({"task": "t08"}, [ASK, {**ASK, "is_error": True},                          # refused: not a question
                               {"type": "end", "terminal_reason": "waiting_reply"},
                               {"type": "outside", "handled_by": "on_outside_reply"},
                               {"type": "outside", "handled_by": "on_outside_reply"},
                               {"type": "outside_for_operator", "why": "asks for a person"}]),
            ({"task": "t08", "passed": False}, [{"type": "reply_deadline"}]),
            ({"task": "t01"}, [])])
        tasks = arm_of(folder)["tasks"]
        self.assertEqual(tasks["t08"], {"runs": 2, "passed": 1, "questions": 1, "runs_that_asked": 1, "waits": 1,
                                        "replies_delivered": 2, "for_the_operator": 1, "past_deadline": 1})
        self.assertEqual((tasks["t01"]["runs"], tasks["t01"]["questions"]), (1, 0))

    def test_memory_writes_reads_and_the_lifetime(self):
        lines = [{"type": "memory_write", "how": "insert"}, {"type": "memory_write", "how": "merge"},
                 {"type": "memory_declined"}, {"type": "memory_evicted"},
                 {"type": "memory_read", "purpose": "session_start", "returned": ["M1"]},
                 {"type": "memory_read", "purpose": "call", "returned": []}]
        row = arm_of(write_arm(self.tmp / "arm", [({}, lines), ({}, []), ({}, []), ({}, [])]))
        m = row["memory"]
        self.assertEqual((m["written"], m["written_by_how"], m["declined"], m["evicted"]),
                         (2, {"insert": 1, "merge": 1}, 1, 1))
        self.assertEqual((m["reads"], m["reads_by_purpose"], m["reads_that_returned_a_record"]),
                         (2, {"session_start": 1, "call": 1}, 1))
        self.assertEqual(m["records_per_run"], 0.5)                                    # 2 records in 4 runs
        life = state.lifetime(row, per_day=20)
        self.assertEqual((life["records_a_day"], life["days_to_fill"]), (10.0, state.MAX_RECORDS / 10))
        self.assertIsNone(state.lifetime(row, per_day=None))

    def test_the_later_session_acts_on_the_record_only_with_the_draft_and_no_question(self):
        draft = {"type": "tool_call", "name": "manager_confirmation_draft", "is_error": False}
        folder = write_arm(self.tmp / "arm", [
            ({"task": state.LATER_SESSION}, [draft]),
            ({"task": state.LATER_SESSION, "passed": False}, [draft, ASK]),            # asked as well
            ({"task": state.LATER_SESSION, "passed": False}, [{**draft, "is_error": True}])])
        self.assertEqual(arm_of(folder)["later_session"], {"runs": 3, "acted_on_the_record": 1, "passed": 1})

    def test_the_recovery_record_counts_effects_that_differ_from_the_run_with_no_crash(self):
        record = self.tmp / "recovery.json"
        record.write_text(json.dumps({"cases": [
            {"task": "tasks/t01_standard_jira.yaml", "died_at": {"tool": "access_grant", "write": 2},
             "effects": [{"effect": "a", "before": 0, "after_crash": 1, "after_resume": 1, "no_crash": 1}]},
            {"task": "tasks/t03_contractor_left.yaml", "died_at": {"tool": "notices_send_template", "write": 3},
             "effects": [{"effect": "b", "before": 0, "after_crash": 1, "after_resume": 2, "no_crash": 1},
                         {"effect": "c", "before": 0, "after_crash": 0, "after_resume": 0, "no_crash": 1}]}]}))
        found = state.recovery(record)
        self.assertEqual((found["cases"], found["differs"], found["more_than_once"]), (2, 2, 1))
        self.assertEqual(found["tasks"], ["t01_standard_jira", "t03_contractor_left"])

    def test_the_committed_build_gives_the_numbers_that_m2_cites(self):
        root = Path(__file__).resolve().parents[1]
        row = arm_of(root / "results" / "m2-build")
        self.assertEqual((row["runs"], row["memory"]["written"], row["memory"]["reads"]), (33, 3, 89))
        self.assertEqual([row["tasks"][t]["questions"] for t in ("t05_on_behalf", "t08_ask_manager", "t10_gated_reply")],
                         [3, 3, 3])
        self.assertEqual(row["later_session"]["acted_on_the_record"], 3)
