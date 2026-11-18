"""The two analysis scripts of Part 2, task 9 and Pass 3 (experiments/analysis/asks.py and
pass3.py), on small arms built here: what they count, and what they leave out. No tokens.
Owner: Vipin Mourya."""

import json
import sys
from pathlib import Path

from tests.support import ScratchTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "analysis"))

import asks  # noqa: E402
import pass3  # noqa: E402


def write_arm(folder: Path, runs: list[tuple[dict, list[dict]]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for n, (fields, lines) in enumerate(runs, start=1):
        run_id = f"{folder.name}-r{n}"
        record = {"run_id": run_id, "arm": folder.name, "task": fields.get("task", f"t{n}"), "sweep": 1,
                  "outcome": "completed", "passed": fields.get("passed", True),
                  "tokens": {"total": fields.get("tokens", 1000)}, "model_calls": fields.get("model_calls", 2),
                  "turn_latency_s": fields.get("turns", [1.0]), "switches": fields.get("switches", {})}
        (folder / f"{run_id}.json").write_text(json.dumps(record))
        (folder / f"{run_id}.trace.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    return folder


class AsksTest(ScratchTest):
    def test_asks_by_class_with_a_persons_seconds_only_and_sends_that_went_out(self):
        lines = [
            {"type": "approval_requested", "approval_id": "APR-1", "action_class": "reply_send/model_text"},
            {"type": "approval_requested", "approval_id": "APR-1", "action_class": "reply_send/model_text"},  # again
            {"type": "approval_decided", "approval_id": "APR-1", "decision": "approved", "who": "operator", "seconds": 12},
            {"type": "approval_requested", "approval_id": "APR-2", "action_class": "reply_send/model_text"},
            {"type": "approval_decided", "approval_id": "APR-2", "decision": "denied", "who": "scripted", "seconds": 0},
            {"type": "tool_call", "name": "reply_send", "is_error": False, "awaiting_approval": "APR-1"},   # put up
            {"type": "tool_call", "name": "reply_send", "is_error": False, "awaiting_approval": None},      # sent
            {"type": "tool_call", "name": "reply_send", "is_error": True},                                 # refused
            {"type": "escalation", "over_cap": False}, {"type": "escalation", "over_cap": True},
            {"type": "run_notice", "resumed": False}, {"type": "run_notice", "resumed": True},
        ]
        folder = write_arm(self.tmp / "arm", [({}, lines), ({}, [])])
        row = asks.count([lines for _, lines in asks.sessions([folder], [])])
        a = row["approvals"]["reply_send/model_text"]
        self.assertEqual((row["runs"], a["asks"], a["asks_per_run"]), (2, 2, 1.0))
        self.assertEqual(a["decisions"], {"approved": 1, "denied": 1})
        self.assertEqual(a["approval_rate"], 0.5)
        self.assertEqual((a["median_seconds_person"], a["person_decisions"]), (12.0, 1))   # the script's 0 s left out
        self.assertEqual(row["strangers"]["sent"], {"reply_send": 1})
        self.assertEqual((row["escalation"]["packets"], row["escalation"]["over_cap"]), (2, 1))
        self.assertEqual((row["stop_notice"]["first"], row["stop_notice"]["resumed"]), (1, 1))
        d = asks.day(row, per_day=40, minutes=30)
        self.assertEqual(d["asks_a_day"], 60.0)              # (2 approvals + 1 packet sent) / 2 runs x 40
        self.assertEqual(d["seconds_per_ask"], 30.0)         # 30 x 60 / 60


    def test_a_step_a_stop_withdrew_and_put_up_again_after_resume_is_one_ask(self):
        lines = [
            {"type": "approval_requested", "approval_id": "APR-1", "action_class": "manager_note/plan_listed",
             "run_key": "s#1"},
            {"type": "stop_flag", "run_keys": ["s#1"]},                                   # withdrawn, never answered
            {"type": "approval_requested", "approval_id": "APR-2", "action_class": "manager_note/plan_listed",
             "run_key": "s#1"},                                                           # after /resume
            {"type": "approval_decided", "approval_id": "APR-2", "decision": "approved", "who": "operator",
             "seconds": 8, "run_key": "s#1"},
            {"type": "stop_flag", "run_keys": ["s#1"]},                                   # nothing waits now
        ]
        a = asks.count([lines])["approvals"]["manager_note/plan_listed"]
        self.assertEqual((a["asks"], a["withdrawn_by_stop"], a["decisions"]), (1, 1, {"approved": 1}))

    def test_the_same_approval_id_in_two_runs_is_two_asks(self):
        # each run's sandbox starts again at APR-0001 (results/m2-build: 13 asks, not 2)
        run = [{"type": "approval_requested", "approval_id": "APR-0001", "action_class": "reply_send/model_text"},
               {"type": "approval_decided", "approval_id": "APR-0001", "decision": "approved", "who": "scripted"}]
        a = asks.count([run, run, run])["approvals"]["reply_send/model_text"]
        self.assertEqual((a["asks"], a["decisions"]), (3, {"approved": 3}))

    def test_the_records_a_run_leaves_on_pending_are_counted_once_each(self):
        lines = [{"type": "tool_call", "name": "access_propose_change", "is_error": False, "output": '{"pending_id": "P-1"}'},
                 {"type": "tool_call", "name": "reply_draft", "is_error": False, "output": '{"pending_id": "P-2"}'},
                 {"type": "tool_call", "name": "reply_draft", "is_error": False,
                  "output": '{"pending_id": "P-2", "status": "waiting_for_operator"}'},            # the same key again
                 {"type": "tool_call", "name": "reply_draft", "is_error": True, "output": "held"}]        # never written
        p = asks.count([lines])["pending"]
        self.assertEqual((p["written"], p["per_run"]), ({"access_propose_change": 1, "reply_draft": 1}, 2.0))

    def test_escalation_answers_are_a_persons_seconds_in_console_sessions_only(self):
        lines = [{"type": "escalation", "over_cap": False}, {"type": "escalation_answered", "seconds": 90.0}]
        console, scripted = asks.count([lines], console=True), asks.count([lines])
        self.assertEqual((console["escalation"]["median_seconds_person"], console["escalation"]["person_answers"]),
                         (90.0, 1))
        self.assertEqual((scripted["escalation"]["answered"], scripted["escalation"]["median_seconds_person"]),
                         (1, None))

class Pass3Test(ScratchTest):
    def test_the_fixs_measures_per_arm_and_the_switches_that_differ(self):
        before = write_arm(self.tmp / "before", [
            ({"tokens": 3000, "passed": False, "switches": {"strict.access_propose_change": "off", "x": 1}}, [
                {"type": "model_call", "usage": {"input_tokens": 900}},
                {"type": "tool_call", "name": "access_propose_change", "rejected": True, "is_error": True},
                {"type": "tool_call", "name": "nope", "error_class": "protocol", "is_error": True},
                {"type": "tool_call", "name": "access_grant", "is_error": True, "error_class": "permanent"},
                {"type": "tool_call", "name": "access_grant", "is_error": True, "error_class": "permanent",
                 "denied": True},                                                    # the hook's: not the arguments
            ])])
        after = write_arm(self.tmp / "after", [
            ({"tokens": 2000, "switches": {"strict.access_propose_change": "on", "x": 1}}, [
                {"type": "model_call", "usage": {"input_tokens": 500}},
                {"type": "context", "tools_offered": 5, "definition_tokens": 700},
                {"type": "tool_search", "matched": False}, {"type": "tool_call", "name": "code_run"},
            ])])
        arms = {f.name: pass3.arm(f) for f in (before, after)}
        b, a = arms["before"], arms["after"]
        self.assertEqual((b["passed"], b["tokens_total"], a["tokens_total"]), (0, 3000, 2000))
        self.assertEqual(b["malformed_by_tool"], {"access_propose_change": 1, "nope": 1})
        self.assertEqual(b["wrong_args_by_tool"], {"access_grant": 1})
        self.assertEqual(b["context_lines_with_tools"], "0 of 0")
        self.assertEqual((a["tools_offered_per_call"], a["definition_tokens_per_call"]), (5, 700))
        self.assertEqual((a["searches"], a["searches_missed"], a["code_runs_per_run"]), (1, 1, 1.0))
        self.assertEqual(pass3.differing(arms), {"strict.access_propose_change": {"before": "off", "after": "on"}})
        self.assertEqual(pass3.arm(before, tool="access_grant")["malformed_by_tool"], {"access_grant": 0})

    def test_made_up_group_ids_are_counted_whether_the_tool_or_the_hook_refused_them(self):
        arm = write_arm(self.tmp / "build", [
            ({"task": "t07"}, [
                {"type": "tool_call", "name": "policy_check_access", "is_error": True, "error_class": "permanent",
                 "output": "No such group: SYN-GRP-JIRA-EDIT. Closest: SYN-GRP-JIRA-WRITE."},
                {"type": "tool_call", "name": "policy_check_access", "is_error": True, "error_class": "permanent",
                 "output": "No such group: SYN-GRP-VPN-ACCESS. Closest: SYN-GRP-VPN-USER."}]),
            ({"task": "t08"}, [
                {"type": "tool_call", "name": "channel_ask_template", "is_error": True, "error_class": "permanent",
                 "denied": True, "output": "No group 'SYN-GRP-GITHUB-ENGINEERS'. Closest: SYN-GRP-GITHUB-ENG."},
                {"type": "tool_call", "name": "channel_ask_template", "is_error": True, "error_class": "permanent",
                 "output": "There is nothing to ask: the policy allows SYN-GRP-DWH-READ."}]),   # a real group
            ({"task": "t01"}, [])])
        a = pass3.arm(arm)
        self.assertEqual((a["made_up_group_ids"], a["runs_with_a_made_up_group_id"]), (3, 2))
        self.assertEqual(a["made_up_group_ids_by_tool"], {"policy_check_access": 2, "channel_ask_template": 1})
        self.assertEqual(a["made_up_group_ids_by_task"], {"t07": 2, "t08": 1})
        self.assertEqual(a["wrong_args_by_tool"], {"policy_check_access": 2, "channel_ask_template": 1})  # not the hook's
