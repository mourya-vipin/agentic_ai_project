"""The harness's own tests. They spend no tokens: `make test`."""

import json
import threading
import time
import unittest
from unittest import mock

import channel
from harness import records, runner, runtime, tasks, turns
from harness import switches as switch_file
from harness.backends import (DailyQuotaReached, RateLimited, from_openai_response, retry_delay,
                              to_openai_messages, with_retries)
from harness.client import BudgetExceeded, ModelClient, Usage
from harness.paths import STATE_ENV
from harness.pricing import PriceSheet
from harness.scripted import NeverStops, SaysDone, input_for
from harness.summarize import arm_summary, table
from harness.trace import SessionTrace, read_trace
from agent import service
from harness.turns import Reply
from checks.helpers import thread
from tests.support import FIXTURE_TASK, ScratchTest, check_run, fixed_run


class ClientTests(unittest.TestCase):
    def test_price_every_token_class(self):
        prices = PriceSheet()
        usage = Usage(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=1_000_000)
        price = prices.models["claude-opus-5"]
        self.assertAlmostEqual(prices.cost("claude-opus-5", usage), price.input + price.cache_read)
        self.assertIsNone(prices.cost("not-a-model", usage))
        self.assertIsNotNone(prices.find("claude-haiku-4-5-20251001"))

    def test_harness_ceiling_stops_the_next_call(self):
        client = ModelClient(NeverStops(), PriceSheet(), session_ceiling=500)
        with self.assertRaises(BudgetExceeded):
            for _ in range(100):
                client.create(messages=[{"role": "user", "content": "x" * 400}], tools=None)

    def test_scripted_models(self):
        tool = {"name": "lookup", "input_schema": {"type": "object", "required": ["id", "count", "kind"],
                "properties": {"id": {"type": "string"}, "count": {"type": "integer"},
                               "kind": {"type": "string", "enum": ["a", "b"]}}}}
        self.assertEqual(input_for(tool), {"id": "x", "count": 1, "kind": "a"})
        client = ModelClient(NeverStops(), PriceSheet())
        response = client.create(messages=[{"role": "user", "content": "go"}], tools=[tool])
        self.assertEqual((response.stop_reason, response.tool_calls[0].name), ("tool_use", "lookup"))
        done = ModelClient(SaysDone(), PriceSheet()).create(messages=[{"role": "user", "content": "go"}], tools=[tool])
        self.assertEqual((done.stop_reason, done.text, done.cost_usd), ("end_turn", "Done.", 0.0))


class BackendTests(unittest.TestCase):
    """The OpenAI-compatible translation and the rate-limit handling, with no network."""

    def test_a_tool_loop_translates_both_ways(self):
        content, stop, model, usage = from_openai_response({
            "model": "some-model-2026-01-01",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": "Looking.", "tool_calls": [{"id": "call_1", "type": "function",
                                                       "function": {"name": "lookup", "arguments": '{"id": "SYN-1"}'}}]}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 300}},
        }, "some-model")
        self.assertEqual(stop, "tool_use")
        self.assertEqual(content[1], {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"id": "SYN-1"}})
        self.assertEqual((usage.input_tokens, usage.cache_read_input_tokens), (600, 300))
        wire = to_openai_messages("Be brief.", [
            {"role": "user", "content": "Find SYN-1."}, {"role": "assistant", "content": content},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "found"}]}])
        self.assertEqual([m["role"] for m in wire], ["system", "user", "assistant", "tool"])

    def test_rate_limits_wait_and_quotas_stop(self):
        self.assertEqual(retry_delay(Exception("Quota exceeded. Please retry in 36.2s."), 0), 37.2)

        class Limited(Exception):
            status_code = 429
        waits = []
        with self.assertRaises(RateLimited):
            with_retries(lambda: (_ for _ in ()).throw(Limited("slow down")), sleep=waits.append)
        self.assertEqual(len(waits), 6)
        with self.assertRaises(DailyQuotaReached):
            with_retries(lambda: (_ for _ in ()).throw(Limited("GenerateRequestsPerDayPerProjectPerModel")),
                         sleep=waits.append)


class TraceTests(ScratchTest):
    def test_lines_and_their_rules(self):
        """The names are the team's: any purpose, any plan shape, any event kind and
        fields, any short end reason. Only the harness's own line types are refused."""
        trace = SessionTrace(self.tmp / "s.jsonl", "s1")
        response = ModelClient(NeverStops(), PriceSheet()).create(messages=[{"role": "user", "content": "go"}])
        trace.model_call(1, response, purpose="draft_the_reply")
        trace.plan(1, ["look it up", "answer"])
        trace.plan("v2", {"goal": "answer", "tasks": [{"do": "look it up"}]})
        trace.event("critique", verdict="revise", round=2)
        trace.event("revision", checkpoint="x")
        trace.progress("Asked for the date.")
        trace.end("plan_rejected")
        trace.end("Waiting_on_customer_2", detail="asked twice")
        lines = read_trace(self.tmp / "s.jsonl")
        self.assertEqual([x["type"] for x in lines],
                         ["model_call", "plan", "plan", "critique", "revision", "progress", "end", "end"])
        self.assertEqual((lines[0]["purpose"], lines[1]["plan"], lines[3]["verdict"], lines[7]["terminal_reason"]),
                         ("draft_the_reply", ["look it up", "answer"], "revise", "Waiting_on_customer_2"))
        for bad in ("two words", "x" * 41, "", "3rd_try", "done!", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                trace.end(bad)
        trace.end("x" * 40)
        with self.assertRaises(ValueError):
            trace.event("operator", text="x")                # a line the harness writes
        with self.assertRaises(ValueError):
            trace.event("critique", run_id="x")              # a field the line already has
        with self.assertRaises(ValueError):
            trace.model_call(2, response, purpose=3)


class SwitchTests(ScratchTest):
    def test_switches_yaml_loads(self):
        for name, s in switch_file.load().items():
            self.assertTrue(not s.values or s.setting in s.values, name)

    def test_overrides(self):
        path = self.tmp / "switches.yaml"
        path.write_text('plan: {setting: "off", values: ["off", "on"]}\ncontrol: {setting: agent, values: [workflow, agent]}\n')
        loaded = switch_file.load(path)
        self.assertEqual(switch_file.apply(loaded, ["plan=on", "control=workflow"]), {"plan": "on", "control": "workflow"})
        for bad in (["plan=maybe"], ["plann=on"], ["plan"]):
            with self.assertRaises(switch_file.SwitchError):
                switch_file.apply(loaded, bad)
        with self.assertRaises(switch_file.SwitchError):
            switch_file.apply(loaded, ["plan=on", "plan=off"])     # one switch, set twice

    def test_bare_off_and_groups(self):
        path = self.tmp / "switches.yaml"
        path.write_text("plan: {setting: off, values: [off, on]}\nmitigations:\n"
                        "  memory_hardening: {setting: on, values: [off, on], added: 2026-10-20}\n"
                        "threshold: 3000\n")
        loaded = switch_file.load(path)
        self.assertEqual(switch_file.settings(loaded),
                         {"plan": "off", "mitigations.memory_hardening": "on", "threshold": 3000})
        self.assertEqual(loaded["mitigations.memory_hardening"].added, "2026-10-20")
        self.assertEqual(switch_file.apply(loaded, ["threshold=500"])["threshold"], 500)

    def test_a_switch_never_has_switches_under_it(self):
        path = self.tmp / "switches.yaml"
        path.write_text("plan:\n  setting: \"off\"\n  values: [\"off\", \"on\"]\n"
                        "  refunds: {setting: \"on\", values: [\"off\", \"on\"]}\n")
        with self.assertRaisesRegex(switch_file.SwitchError, "plan has a setting and switches under it; make it a "
                                                             "group, without setting, values or about, or a single switch"):
            switch_file.load(path)
        path.write_text("control:\n  added: 2026-10-20\n  refunds: {setting: agent, values: [workflow, agent]}\n")
        with self.assertRaisesRegex(switch_file.SwitchError, "added: belongs on a switch, not on the group control"):
            switch_file.load(path)
        path.write_text("control:\n  refunds: {setting: agent, values: [workflow, agent], added: 2026-10-20}\n")
        self.assertEqual(switch_file.load(path)["control.refunds"].added, "2026-10-20")


class ChannelTests(ScratchTest):
    def test_send_replies_and_wait(self):
        channel.reset()
        channel.receive("T-1", "Hello", sender="SYN-person-1")
        sent = channel.send("T-1", "Which date?")
        self.assertEqual([m.text for m in channel.replies("T-1")], ["Hello"])
        self.assertEqual(channel.replies("T-1", since=sent.id), [])
        self.assertIsNone(channel.wait_for_reply("T-1", timeout_s=0.1))
        channel.receive("T-1", "Tuesday", sender="SYN-person-1")
        self.assertEqual(channel.wait_for_reply("T-1", timeout_s=0.1).text, "Tuesday")
        self.assertEqual(channel.threads()[0]["messages"], 3)

    def test_a_scripted_person_answers_when_waited_on(self):
        channel.reset()
        person = channel._script.install([{"thread": "T-*", "who": "SYN-p", "replies": ["first"],
                                           "asked_again": "again", "never_asked": None}])[0]
        try:
            channel.send("T-2", "A notice, not a question")
            self.assertEqual(person.asked, 0)
            channel.send("T-2", "Question?")
            self.assertEqual(channel.wait_for_reply("T-2", 1).text, "first")
            channel.send("T-2", "And again?")
            reply = channel.wait_for_reply("T-2", 1)
            self.assertEqual((reply.text, reply.sender, person.asked), ("again", "SYN-p", 2))
            channel._script.install([{"thread": "T-3", "replies": [], "asked_again": None, "never_asked": None}])
            channel.send("T-3", "Anyone?")
            self.assertIsNone(channel.wait_for_reply("T-3", 0.1))
        finally:
            channel._script.clear()


    def test_a_reply_is_handled_once(self):
        channel.reset()
        with channel.seeding():
            channel.receive("T-4", "The input", sender="SYN-p")
        channel.send("T-4", "Which date?")
        delivered = []
        with mock.patch.object(service, "on_outside_reply", lambda t, x: delivered.append(x) or Reply("ok")):
            self.assertEqual(turns.outside_reply("T-4", "Tuesday", sender="SYN-p").text, "ok")
            self.assertIsNone(channel.wait_for_reply("T-4", 0.1))      # delivered already: not taken again
            taken = []
            waiter = threading.Thread(target=lambda: taken.append(channel.wait_for_reply("T-4", 5)))
            waiter.start()
            while not channel._waiting:
                time.sleep(0.01)
            self.assertIsNone(turns.outside_reply("T-4", "Wednesday", sender="SYN-p"))   # the waiting turn took it
            waiter.join()
        self.assertEqual((delivered, taken[0].text), (["Tuesday"], "Wednesday"))
        handled = [(m.text, m.seeded, m.handled_by) for m in channel.messages("T-4") if m.from_outside]
        self.assertEqual(handled, [("The input", True, None), ("Tuesday", False, "on_outside_reply"),
                                   ("Wednesday", False, "wait_for_reply")])


    def test_the_operator_sends_by_hand(self):
        channel.reset()
        channel.receive("T-5", "Where is my order?", sender="SYN-p")
        sent = channel.send("T-5", "It ships today.", sender="operator")
        self.assertEqual((sent.sender, sent.from_outside), ("operator", False))
        self.assertEqual([m.sender for m in channel.messages("T-5")], ["SYN-p", "operator"])
        self.assertEqual([m.text for m in channel.replies("T-5")], ["Where is my order?"])
        self.assertTrue(channel.threads()[0]["waiting_for_outside"])
        self.assertEqual(channel.send("T-5", "And a notice.").sender, "agent")
        with self.assertRaises(ValueError):
            channel.send("T-5", "x", sender="SYN-p")
        with self.assertRaises(ValueError):
            channel.receive("T-5", "x", sender="operator")


class TaskTests(ScratchTest):
    def test_the_test_task_is_valid(self):
        task = tasks.read(FIXTURE_TASK)
        (self.tmp / "checks").mkdir()
        (self.tmp / "checks" / "fixture.py").write_text("def check(run):\n    return False\n")
        task["check"] = "checks/fixture.py"
        self.assertEqual(tasks.problems(task, root=self.tmp), [])

    def test_problems_are_named(self):
        task = tasks.read(FIXTURE_TASK)
        del task["check"]
        task["outside"][0]["replies"] = [False]       # an unquoted no
        del task["outside"][0]["never_asked"]
        found = tasks.problems(task)
        self.assertIn("no check", found)
        self.assertTrue(any("quote" in p for p in found))
        self.assertTrue(any("never_asked" in p for p in found))
        self.assertTrue(tasks.check_problems("checks/missing.py"))
        self.assertTrue(tasks.check_problems("tests/fixtures/checks.py"))      # not in checks/

    def test_inputs_and_one_stranger_per_thread(self):
        task = tasks.read(FIXTURE_TASK)
        task["inputs"] = [task.pop("input"), {"thread": "SYN-thread-2", "text": "Another"}]
        task["outside"].append(dict(task["outside"][0]))
        found = tasks.problems(task)
        self.assertFalse(any("input" in p for p in found))
        self.assertTrue(any("one stranger per thread" in p for p in found))
        task["input"] = {"thread": "x"}
        self.assertIn("give input or inputs, not both", tasks.problems(task))

    def test_a_folder_means_its_own_files_only(self):
        (self.tmp / "tasks" / "dev").mkdir(parents=True)
        for name in ("tasks/a.yaml", "tasks/b.yml", "tasks/dev/c.yaml", "tasks/notes.txt"):
            (self.tmp / name).write_text("id: x\n")
        self.assertEqual([p.name for p in tasks.task_files(self.tmp / "tasks")], ["a.yaml", "b.yml"])


class RunnerTests(ScratchTest):
    def test_a_run_with_no_agent_is_recorded_and_fails_honestly(self):
        with mock.patch.object(service, "operator_turn", side_effect=NotImplementedError("operator_turn: stub")):
            record = self.run_fixture("smoke")
        self.assertEqual(record["outcome"], "not_built")
        self.assertFalse(record["passed"])
        self.assertIn("sent 0 message", record["check"]["reason"])
        self.assertEqual(set(record["switches"]), set(switch_file.settings(switch_file.load())))
        self.assertEqual((record["free"], record["end_reasons"], record["end_reason"], record["measures"]),
                         (True, [], None, {}))
        free = self.tmp / "results" / "_free" / "smoke"          # a scripted backend: a test, not evidence
        saved = json.loads((free / f"{record['run_id']}.json").read_text())
        self.assertEqual(saved["outcome"], "not_built")
        self.assertFalse((self.tmp / "results" / "smoke").exists())
        self.assertEqual(records.arms(self.tmp / "results"), [])
        types = [x["type"] for x in read_trace(free / f"{record['run_id']}.trace.jsonl")]
        self.assertEqual(types[:3], ["run_start", "sandbox", "operator"])

    def test_the_scripted_person_and_the_latency_without_waiting(self):
        def operator_turn(session_id, text):
            if text.startswith("Handle"):
                channel.send("SYN-thread-test", "What is the reference?")
                got = channel.wait_for_reply("SYN-thread-test", 1)
                channel.send("SYN-thread-test", "Thanks.")
                channel.send("SYN-thread-test", "One more question?")
                late = channel.wait_for_reply("SYN-thread-test", 0.2)     # asked_again is null: silence
                return Reply(f"got {got.text}; then {late}")
            return Reply("nothing open")
        with mock.patch.object(service, "operator_turn", operator_turn):
            record = self.run_fixture("person")
        self.assertEqual((record["outcome"], record["passed"]), ("completed", True))
        self.assertEqual(record["outside"][0]["asked"], 2)
        self.assertEqual(record["outside"][0]["answered"], 1)
        self.assertGreaterEqual(record["wait_s"], 0.2)
        self.assertAlmostEqual(record["latency_without_wait_s"], record["latency_s"] - record["wait_s"], places=2)
        turns_ = record["turn_latency_s"]                       # one per turn, without the wait
        self.assertEqual(len(turns_), 2)
        self.assertLess(turns_[0], 0.2)

    def test_a_rate_limit_wait_is_not_latency(self):
        class Limited(Exception):
            status_code = 429
        refused = []

        def answer():
            if not refused:
                refused.append(1)
                raise Limited("slow down")
            return "ok"

        def operator_turn(session_id, text):
            return Reply(with_retries(answer, sleep=lambda seconds: time.sleep(0.3)))
        with mock.patch.object(service, "operator_turn", operator_turn):
            record = self.run_fixture("throttled")
        self.assertGreaterEqual(record["throttled_s"], 0.3)
        self.assertAlmostEqual(record["latency_without_wait_s"],
                               record["latency_s"] - record["wait_s"] - record["throttled_s"], places=2)
        self.assertLess(record["turn_latency_s"][0], 0.3)           # the sleep is not the agent's time
        throttled = [x["throttled_s"] for x in read_trace(self.tmp / record["trace"]) if x["type"] == "reply"]
        self.assertGreaterEqual(throttled[0], 0.3)

    def test_run_check_tests_a_check_on_a_planted_effect(self):
        class StandIn:
            @staticmethod
            def reset():
                pass

            @staticmethod
            def seed(task):
                item = task["input"]
                channel.receive(item["thread"], item["text"], sender=item["from"])
        fixture = dict(tasks.read(FIXTURE_TASK), file="tests/fixtures/task.yaml")    # its check is outside checks/
        with mock.patch.object(runner, "_sandbox", lambda: StandIn), \
                mock.patch.object(tasks, "load", lambda path, root=None: dict(fixture)):
            right = self.run_check(FIXTURE_TASK, lambda run: channel.send(run.task["input"]["thread"], "On it."))
            none = self.run_check(FIXTURE_TASK, lambda run: None)
        self.assertEqual((right["passed"], right["reason"]), (True, "the agent sent 1 message(s) on SYN-thread-test"))
        self.assertEqual((none["passed"], none["measures"]), (False, {}))

    def test_the_record_carries_the_run_in_full(self):
        seen = []

        def operator_turn(session_id, text):
            seen.append((runtime.current_session(), runtime.current_run().run_id))
            if text.startswith("Handle"):
                trace = runtime.trace(session_id)
                trace.tool_call(1, {"id": "toolu_1", "name": "lookup", "input": {"q": "SYN"}}, "x" * 500)
                trace.end("looked_up")
                channel.send("SYN-thread-test", "What is the reference?")
                trace.end("asked_customer")
                return Reply("Asked.", waiting_for="SYN-thread-test")
            return Reply("SYN-REF-1 is the reference.")

        def on_outside_reply(thread_id, text):
            seen.append((runtime.current_session(), runtime.current_run().run_id))
            return Reply(f"heard {text}")
        with mock.patch.object(service, "operator_turn", operator_turn), \
                mock.patch.object(service, "on_outside_reply", on_outside_reply):
            record = self.run_fixture("full", check="tests/fixtures/checks.py:check_with_measures")
        self.assertIn("commit", record)
        self.assertIn("dirty", record)
        self.assertEqual(record["end_reasons"], ["looked_up", "asked_customer"])
        self.assertEqual(record["end_reason"], "asked_customer")
        self.assertEqual(record["measures"], {"messages_sent": 1, "asked": 1})
        self.assertNotIn("measures", record["check"])
        self.assertEqual(seen, [(None, record["run_id"])] * 3)      # current_session() is None in run.py
        self.assertEqual(record["tool_calls"][0]["id"], "toolu_1")
        self.assertEqual(record["tool_calls"][0]["output"], "x" * 500)
        self.assertEqual([(r["function"], r["text"]) for r in record["replies"]],
                         [("operator_turn", "Asked."), ("on_outside_reply", "heard The reference is SYN-REF-1."),
                          ("operator_turn", "SYN-REF-1 is the reference.")])
        run = tasks.RunInfo(record["run_id"], {}, "full", 1, self.tmp / "state" / "runs")
        with mock.patch.dict("os.environ", {STATE_ENV: str(self.tmp / "state" / "runs")}):
            exchange = thread(run, "SYN-thread-test")
        self.assertEqual([m.from_outside for m in exchange], [False, True])     # the seeded input is left out

    def test_a_reply_with_no_text_waits_on_a_thread(self):
        self.assertEqual(Reply(waiting_for="T-1").as_dict(), {"text": "", "actions": [], "waiting_for": "T-1"})
        heard = []

        def operator_turn(session_id, text):
            if text.startswith("Handle"):
                channel.send("SYN-thread-test", "What is the reference?")
                return Reply(waiting_for="SYN-thread-test")
            return Reply("done")
        with mock.patch.object(service, "operator_turn", operator_turn), \
                mock.patch.object(service, "on_outside_reply", lambda t, x: heard.append(x) or Reply()):
            record = self.run_fixture("no-text")
        self.assertEqual(heard, ["The reference is SYN-REF-1."])
        self.assertEqual([(r["function"], r["text"]) for r in record["replies"]],
                         [("operator_turn", ""), ("on_outside_reply", ""), ("operator_turn", "done")])
        self.assertEqual(len(record["turn_latency_s"]), 3)

    def test_a_fake_model_marked_free_runs_free(self):
        self.assertTrue(runner.is_free("tests.support:FreeScripted"))
        self.assertFalse(runner.is_free("tests.support:Scripted"))
        self.assertTrue(runner.is_free("never-stops") and runner.is_free("harness.scripted:SaysDone"))
        with self.assertRaises(runner.RunError):
            runner.is_free("tests.support:NoSuchModel")
        with mock.patch.object(service, "operator_turn", return_value=Reply("ok")):
            record = self.run_fixture("free-fake", backend="tests.support:FreeScripted")
        self.assertTrue(record["free"])
        self.assertTrue((self.tmp / "results" / "_free" / "free-fake" / f"{record['run_id']}.json").exists())
        self.assertFalse((self.tmp / "results" / "free-fake").exists())

    def test_a_check_with_bad_measures_fails_the_run(self):
        with mock.patch.object(service, "operator_turn", return_value=Reply("ok")):
            record = self.run_fixture("bad", check="tests/fixtures/checks.py:check_with_bad_measures")
        self.assertFalse(record["passed"])
        self.assertIn("measures must be a dict of names to numbers", record["check"]["reason"])
        self.assertEqual(record["measures"], {})

    def test_discard_moves_an_arm_aside(self):
        with mock.patch.object(service, "operator_turn", side_effect=NotImplementedError("stub")):
            self.run_fixture("gone")
        moved = runner.discard("gone", log=lambda *_: None)          # only a free arm of that name: it goes
        self.assertFalse((self.tmp / "results" / "_free" / "gone").exists())
        self.assertEqual((moved.parent.parent.name, moved.parent.name, len(records.read(moved))),
                         ("_free", "discarded", 1))
        self.assertTrue(moved.name.startswith("gone-"))
        with mock.patch.object(service, "operator_turn", side_effect=NotImplementedError("stub")):
            self.run_fixture("kept", backend="tests.support:Scripted")
        self.assertTrue((self.tmp / "results" / "kept").is_dir())   # a model of your own: not free
        moved = runner.discard("kept", log=lambda *_: None)
        self.assertEqual(moved.parent, self.tmp / "results" / "discarded")
        with self.assertRaises(runner.RunError):
            runner.discard("kept", log=lambda *_: None, free=True)
        with self.assertRaises(ValueError):
            records.arm_dir("discarded")
        warning = runner.commit_warning("a", [{"commit": "abc"}, {"commit": "def"}], "def", root=self.tmp)
        self.assertIn("1 of the finished runs", warning)
        self.assertIsNone(runner.commit_warning("a", [{"commit": "def"}], "def", root=self.tmp))

    def test_an_arm_is_one_configuration(self):
        with mock.patch.object(service, "operator_turn", side_effect=NotImplementedError("stub")):
            first = self.run_fixture("one")
        folder = self.tmp / "results" / "_free" / "one"
        settings = dict(first["switches"])
        self.assertEqual(len(runner._existing(folder, settings, "never-stops", None)), 1)
        with self.assertRaises(runner.RunError):
            runner._existing(folder, {**settings, "control": "workflow"}, "never-stops", None)
        with self.assertRaises(runner.RunError):
            runner._existing(folder, settings, "harness.scripted:SaysDone", None)


class SummarizeTests(ScratchTest):
    def test_the_arm_table(self):
        folder = self.tmp / "results" / "a"
        folder.mkdir(parents=True)
        outcomes = {("t1", 1): True, ("t2", 1): True, ("t1", 2): True, ("t2", 2): False,
                    ("t1", 3): False, ("t2", 3): False}
        for i, ((task, sweep), passed) in enumerate(sorted(outcomes.items())):
            (folder / f"r{i}.json").write_text(json.dumps({
                "run_id": f"r{i}", "task": task, "sweep": sweep, "passed": passed, "outcome": "completed",
                "started": f"2026-10-11T10:00:0{i}+00:00", "switches": {"plan": "on"}, "model": "m",
                "tokens": {"total": 100 * (i + 1)}, "cost_usd": 0.01, "latency_s": float(i + 1),
                "latency_without_wait_s": float(i), "turn_latency_s": [0.5, float(i) / 2]}))
        (folder / "r9.json").write_text(json.dumps({"run_id": "r9", "task": "t1", "sweep": 4, "passed": False,
                                                     "outcome": "quota", "started": "2026-10-11T11:00:00+00:00"}))
        s = arm_summary(folder)
        self.assertEqual((s["passes"], s["runs"]), (3, 6))
        self.assertEqual(s["sweep_scores"], [1.0, 0.5, 0.0])
        self.assertAlmostEqual(s["mean"], 0.5)
        self.assertAlmostEqual(s["spread"], 0.5)
        self.assertEqual((s["tokens_per_run"], s["latency_median"], s["latency_slowest"]), (350, 3.5, 6.0))
        self.assertEqual(s["unfinished"], [{"run_id": "r9", "outcome": "quota"}])
        self.assertIn("| a | 3 of 6 | 1.00, 0.50, 0.00 | 0.50 | 0.50 |", table([s]))
        self.assertEqual((s["without_wait_slowest"], s["turn_slowest"]), (5.0, 2.5))
        self.assertIn("| Slowest without waiting | Slowest turn |", table([s]))
        self.assertIn("| 5.00 s | 2.50 s |", table([s]))

    def test_a_measure_summed_per_arm(self):
        folder = self.tmp / "results" / "m"
        folder.mkdir(parents=True)
        for i, measures in enumerate([{"asked": 2}, {"asked": 1, "other": 9}, {}]):
            (folder / f"r{i}.json").write_text(json.dumps({
                "run_id": f"r{i}", "task": f"t{i}", "sweep": 1, "passed": True, "outcome": "completed",
                "started": f"2026-10-11T10:00:0{i}+00:00", "switches": {}, "model": "m", "tokens": {"total": 1},
                "cost_usd": 0.0, "latency_s": 1.0, "measures": measures}))
        s = arm_summary(folder, ["asked"])
        self.assertEqual((s["measures"], s["measures_missing"]), ({"asked": 3}, {"asked": 1}))
        text = table([s])
        self.assertIn("| asked (sum) |", text)
        self.assertIn("| 1.00 s | n/a | 3 |", text)          # no turn_latency_s in these records
        self.assertIn("m: 1 of 3 runs report no asked", text)
        self.assertNotIn("(sum)", table([arm_summary(folder)]))

    def test_free_runs_are_left_out(self):
        from harness import summarize
        free = self.tmp / "results" / "_free" / "smoke"
        free.mkdir(parents=True)
        (free / "r.json").write_text(json.dumps({"run_id": "r", "task": "t", "sweep": 1, "passed": True,
                                                  "outcome": "completed", "free": True}))
        self.assertTrue(records.is_free_folder(free))
        self.assertFalse(records.is_free_folder(self.tmp / "results" / "smoke"))
        self.assertEqual(records.arms(self.tmp / "results"), [])
        with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
            self.assertEqual(summarize.main([str(free)]), 2)


class RuntimeTests(ScratchTest):
    def test_current_session(self):
        self.assertIsNone(runtime.current_session())
        with runtime.in_session("s-console"):
            self.assertEqual(runtime.current_session(), "s-console")

    def test_switches_and_the_client(self):
        runtime.configure()
        for name, value in switch_file.settings(switch_file.load()).items():
            self.assertEqual(runtime.switch(name), value)
        self.assertEqual(runtime.switch("no-such-switch", default=None), None)
        with self.assertRaises(KeyError):
            runtime.switch("no-such-switch")
        runtime.configure(backend="never-stops")
        self.assertIsInstance(runtime.client("s1").backend, NeverStops)
        runtime.configure()


if __name__ == "__main__":
    unittest.main()


class FixedRunTests(ScratchTest):
    """fixed_run keeps one run ID and one sandbox across blocks, as across processes."""

    class StandIn:
        PARALLEL_SAFE = False

        @staticmethod
        def reset():
            pass

        @staticmethod
        def seed(task):
            item = task["input"]
            channel.receive(item["thread"], item["text"], sender=item["from"])

    def test_a_later_block_carries_on_the_same_run(self):
        thread_id = "SYN-thread-test"
        task = tasks.read(FIXTURE_TASK)
        task["file"] = "tests/fixtures/task.yaml"
        with mock.patch.object(runner, "_sandbox", lambda: self.StandIn):
            with fixed_run(task, "fixed-1") as run:
                self.assertEqual(run.run_id, "fixed-1")
                runtime.trace().write({"type": "note", "text": "first process"})
            self.assertFalse(check_run(task, "fixed-1")["passed"])
            with fixed_run(task, "fixed-1", fresh=False):
                channel.send(thread_id, "SYNTHETIC. The answer.")
                runtime.trace().write({"type": "note", "text": "second process"})
            result = check_run(task, "fixed-1")
            self.assertTrue(result["passed"], result["reason"])
            self.assertEqual(len(channel.messages(thread_id)), 2)       # the seeded input was kept
            notes = [line for line in read_trace(self.tmp / "traces" / "fixed-1.trace.jsonl")
                     if line.get("type") == "note"]
            self.assertEqual([n["run_id"] for n in notes], ["fixed-1", "fixed-1"])
            with fixed_run(task, "fixed-1"):                    # fresh again: a new sandbox
                pass
            self.assertFalse(check_run(task, "fixed-1")["passed"])

    def test_a_real_model_is_refused(self):
        with self.assertRaises(ValueError):
            with fixed_run(FIXTURE_TASK, "fixed-2", backend="anthropic"):
                pass

