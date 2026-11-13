"""The MCP server (Lecture 3, slides 29 to 34; Part 1, task 3): every tool call reaches the
server through the registry, the tool list comes back in the same order every time, a write
retried under its key leaves one effect, errors come back in their two kinds, and the
server keeps nothing between calls. A real child process; no tokens. Owner: Vipin Mourya."""

import os

import channel
from agent import mcp_client
from agent.registry import CallContext, Registry
from harness import runtime
from harness.trace import read_trace
from mcp_server import server
from sandbox import access, seed
from tests.support import ScratchTest

LEO, REQ = "SYN-EMP-0031", "SYN-REQ-0005"
GRANT = {"employee_id": LEO, "group_id": "SYN-GRP-FINREP-READ", "request_id": REQ}
READ = {"employee_id": LEO, "email": None, "name": None}


def ctx(caller="model", use_case="nonstandard_request", run_key="s-mcp#1"):
    return CallContext("s-mcp", 1, caller, use_case, request_id=REQ, run_key=run_key)


class McpTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()
        self.server = mcp_client.shared()

    def calls(self):
        return [x for x in read_trace(runtime.trace("s-mcp").path) if x["type"] == "tool_call"]

    def held(self, group):
        return [m for m in access.memberships(LEO) if m["group_id"] == group]

    def test_the_connection_adopts_the_stateless_revision(self):
        self.server.list_tools()
        self.assertEqual(self.server.revision, server.REVISION)
        self.assertEqual(server.REVISION, "2026-07-28")

    def test_every_tool_call_reaches_the_server_through_the_registry(self):
        self.registry.call("directory_get_employee", READ, ctx())                      # a read
        self.registry.call("access_grant", GRANT, ctx())                               # a write
        self.registry.call("notices_send_template", {"thread_id": "SYN-MSG-D05", "request_id": REQ,
                                                     "template_id": "received",
                                                     "facts": {"name": "Leo Martins", "request_id": REQ}},
                           ctx(caller="workflow", use_case="standard_grant"))           # our code's tool
        lines = self.calls()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(line["transport"], "mcp")
            self.assertIsNotNone(line["server_pid"])
            self.assertNotEqual(line["server_pid"], os.getpid())
        self.assertEqual(len(self.held("SYN-GRP-FINREP-READ")), 1)   # the effect landed in this test's sandbox

    def test_the_tool_list_comes_back_twice_in_the_same_order(self):
        first, second = self.server.list_tools(), self.server.list_tools()
        names = [t["name"] for t in first]
        self.assertEqual(first, second)
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(server.catalogue()))

    def test_a_write_retried_under_its_key_leaves_one_effect_even_across_a_new_server(self):
        first = self.registry.call("access_grant", GRANT, ctx())
        self.server.close()                                           # the child goes away between the tries
        again = self.registry.call("access_grant", GRANT, ctx())
        self.assertFalse(first.is_error, first.content)
        self.assertEqual(first.key, again.key)
        self.assertEqual(len(self.held("SYN-GRP-FINREP-READ")), 1)
        self.assertNotEqual(self.calls()[0]["server_pid"], self.calls()[1]["server_pid"])

    def test_an_execution_error_is_a_result_and_an_unknown_tool_a_protocol_error(self):
        context = self.registry._context(ctx(), None)
        reply = self.server.call("access_grant", {**GRANT, "employee_id": "SYN-EMP-9999"}, context)
        self.assertFalse(reply["ok"])
        self.assertIn("No employee 'SYN-EMP-9999'", reply["error"]["message"])
        with self.assertRaises(mcp_client.ServerError):
            self.server.call("drop_all_groups", {}, context)

    def test_the_registry_routes_a_transient_failure_from_the_server_as_in_m1(self):
        access.set_fault("access_grant", 1)
        result = self.registry.call("access_grant", GRANT, ctx())
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.calls()[-1]["attempts"], 2)

    def test_the_server_keeps_nothing_between_calls_the_critiques_rounds_live_in_the_registry(self):
        claim = {"recipient_employee_id": LEO, "request_id": REQ,
                 "body": "SYNTHETIC. Hi Leo, I granted you SYN-GRP-FINREP-WRITE. IT access desk."}
        first = self.registry.call("reply_draft", claim, ctx())
        self.server.close()                                           # a new process for the second round
        second = self.registry.call("reply_draft", claim, ctx())
        self.assertIn("round 1", first.content)
        self.assertEqual(second.end_reason, "critique_held")          # round 2 of 2: counted across processes
        critiques = [x for x in read_trace(runtime.trace("s-mcp").path) if x["type"] == "critique"]
        self.assertEqual([c["round"] for c in critiques], [1, 2])     # the server's trace lines, in our trace

    def test_execute_clears_what_a_tool_counted_once_the_call_returns(self):
        from agent import critique
        from agent.tools import impl
        impl._rounds[("x", "y", "z")] = 1
        out = server.execute("directory_get_employee", READ, self.registry._context(ctx(), None))
        self.assertTrue(out["ok"])
        self.assertEqual(impl._rounds, {})
        self.assertEqual(critique._last, {})
        self.assertEqual(out["state"], {"rounds": [], "last": []})     # a call without state starts from none

    def test_a_server_killed_between_calls_is_started_again_and_the_write_lands_once(self):
        import signal
        import time
        self.registry.call("directory_get_employee", READ, ctx())
        os.kill(self.calls()[-1]["server_pid"], signal.SIGKILL)
        time.sleep(0.2)
        first = self.registry.call("access_grant", GRANT, ctx())
        again = self.registry.call("access_grant", GRANT, ctx())
        self.assertFalse(first.is_error, first.content)
        self.assertFalse(again.is_error, again.content)
        self.assertEqual(len(self.held("SYN-GRP-FINREP-READ")), 1)
