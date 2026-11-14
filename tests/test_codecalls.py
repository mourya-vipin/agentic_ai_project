"""Code that calls the tools (Lecture 3, slides 26 and 27; Part 1, task 4): each call a program
makes passes through the registry, a write in it is keyed, a call the hook refuses is refused,
none of its results along the way reaches the context, a program that raises returns an error the
model can repair from, and one that reaches for the network or a file is stopped. No tokens.
Owner: Vipin Mourya."""

import json
from unittest import mock

import channel
from agent import codecalls, context
from agent.registry import CallContext, Registry
from agent.router import NONSTANDARD, OPERATOR_REVIEW, TOOLS
from harness import runtime
from harness.trace import read_trace
from sandbox import access, seed
from tests.support import ScratchTest

SID, RK, LEO, REQ = "s-code", "s-code#1", "SYN-EMP-0031", "SYN-REQ-0005"


def ctx(use_case=OPERATOR_REVIEW):
    return CallContext(SID, 1, "model", use_case, request_id=REQ if use_case == NONSTANDARD else None, run_key=RK)


class CodeCallsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure(overrides=["code_calls.operator_review=on"])
        self.registry = Registry()

    def tearDown(self):
        runtime.configure()
        super().tearDown()

    def offer(self, use_case=OPERATOR_REVIEW):
        return context.assemble("loop", system="You review today's requests.", session_id=SID, run_key=RK,
                                task="Anything unusual today?", history=[{"role": "user", "content": "Go."}],
                                tool_names=TOOLS[use_case], registry=self.registry, use_case=use_case)

    def program(self, source, use_case=OPERATOR_REVIEW):
        return self.registry.call("code_run", {"program": source}, ctx(use_case))

    def lines(self, kind):
        return [x for x in read_trace(runtime.trace(SID).path) if x["type"] == kind]

    def test_the_switch_offers_the_program_tool_to_u5_only_when_on(self):
        self.assertIn("code_run", [t["name"] for t in self.offer().tools])
        runtime.configure()                                    # code_calls.operator_review as switches.yaml sets it
        self.assertNotIn("code_run", [t["name"] for t in self.offer().tools])

    def test_each_call_passes_through_the_registry_and_only_the_result_reaches_the_context(self):
        self.offer()
        result = self.program('rows = call("requests_list_today")["requests"]\n'
                              'result = {"count": len(rows), "first": rows[0]["id"] if rows else None}')
        self.assertFalse(result.is_error, result.content)
        inner = [x for x in self.lines("tool_call") if x["name"] == "requests_list_today"]
        self.assertEqual(len(inner), 1)
        self.assertEqual((inner[0]["origin"], inner[0]["transport"]), ("program", "mcp"))
        self.assertEqual(set(json.loads(result.content)), {"result", "calls", "tools"})
        self.assertNotIn("SYNTHETIC", result.content)          # no request's text came back, only the count
        self.assertEqual(self.lines("program")[-1]["status"], "done")

    def test_a_write_in_a_program_is_keyed_and_lands_once(self):
        self.offer(NONSTANDARD)
        grant = ('call("access_grant", employee_id="SYN-EMP-0031", group_id="SYN-GRP-FINREP-READ", '
                 'request_id="SYN-REQ-0005")\nresult = "done"')
        self.program(grant, NONSTANDARD)
        self.program(grant, NONSTANDARD)
        writes = [x for x in self.lines("tool_call") if x["name"] == "access_grant"]
        self.assertTrue(all(w["key"] for w in writes))
        self.assertEqual(len({w["key"] for w in writes}), 1)
        self.assertEqual(len([m for m in access.memberships(LEO) if m["group_id"] == "SYN-GRP-FINREP-READ"]), 1)

    def test_a_call_the_hook_refuses_is_refused_inside_the_program(self):
        self.offer(NONSTANDARD)
        result = self.program('try:\n    call("access_grant", employee_id="SYN-EMP-0031", '
                              'group_id="SYN-GRP-PAYROLL-ADMIN", request_id="SYN-REQ-0005")\n'
                              '    result = "granted"\nexcept ToolFailed as e:\n    result = "refused"', NONSTANDARD)
        self.assertEqual(json.loads(result.content)["result"], "refused")
        self.assertNotIn("SYN-GRP-PAYROLL-ADMIN", {m["group_id"] for m in access.memberships(LEO)})
        self.assertEqual(self.lines("hook_denied")[-1]["action_class"], "access_grant/unconfirmed")

    def test_a_program_may_call_only_the_tools_its_use_case_is_offered(self):
        self.offer()                                           # U5: reads only
        result = self.program('try:\n    call("access_grant", employee_id="SYN-EMP-0031", '
                              'group_id="SYN-GRP-FINREP-READ")\n    result = "granted"\n'
                              'except ToolFailed as e:\n    result = str(e)')
        self.assertIn("not a tool this program may call", json.loads(result.content)["result"])
        self.assertEqual([x for x in self.lines("tool_call") if x["name"] == "access_grant"], [])

    def test_a_program_that_raises_returns_an_error_the_model_can_repair_from(self):
        self.offer()
        result = self.program('rows = call("requests_list_today")["requests"]\nresult = 1 / 0')
        self.assertTrue(result.is_error)
        self.assertIn("raised ZeroDivisionError at line 2", result.content)
        self.assertIn("fix it and call code_run again", result.content)

    def test_a_program_that_reaches_for_the_network_or_a_file_is_stopped(self):
        self.offer()
        for source, said in (("import socket\nresult = 1", "imports socket"),
                             ('result = open("/etc/passwd").read()', "uses open"),
                             ("result = call.__globals__", "reaches for .__globals__"),
                             ('result = __import__("os").listdir("/")', "uses __import__")):
            with self.subTest(source=source):
                result = self.program(source)
                self.assertTrue(result.is_error)
                self.assertIn("stopped before it ran", result.content)
                self.assertIn(said, result.content)

    def test_a_program_that_runs_on_is_stopped_on_time(self):
        self.offer()
        with mock.patch.object(codecalls, "WALL_S", 1.0):
            result = self.program("n = 0\nwhile True:\n    n += 1")
        self.assertTrue(result.is_error)
        self.assertIn("stopped", result.content)
