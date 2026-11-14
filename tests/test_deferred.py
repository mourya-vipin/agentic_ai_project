"""Deferred loading (Lecture 3, slides 23 to 25; Part 1, task 4): with tools.loading=deferred, a
use case offered more than ten tools starts with its core tools and the search tool; the
definitions a search finds enter the next request; a search that finds nothing says so and is
logged; and a search never reaches a tool outside the use case. Through the MCP server; no tokens.
Owner: Vipin Mourya."""

from agent import context
from agent.registry import CallContext, Registry
from agent.router import NONSTANDARD, OPERATOR_REVIEW, TOOLS
from harness import runtime
from harness.trace import read_trace
from tests.support import ScratchTest

SID, RK = "s-def", "s-def#1"


def ctx():
    return CallContext(SID, 1, "model", NONSTANDARD, run_key=RK)


class DeferredTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure(overrides=["tools.loading=deferred"])
        self.registry = Registry()

    def tearDown(self):
        runtime.configure()
        super().tearDown()

    def assemble(self, use_case=NONSTANDARD):
        return context.assemble("loop", system="You are the IT access desk.", session_id=SID, run_key=RK,
                                task="Give Leo what Priya has.", history=[{"role": "user", "content": "Go."}],
                                tool_names=TOOLS[use_case], registry=self.registry, use_case=use_case)

    def names(self, assembled):
        return sorted(t["name"] for t in assembled.tools)

    def search(self, query):
        return self.registry.call("tools_search", {"query": query}, ctx())

    def searches(self):
        return [x for x in read_trace(runtime.trace(SID).path) if x["type"] == "tool_search"]

    def test_the_first_request_carries_only_the_core_tools_and_the_search(self):
        first = self.assemble()
        self.assertEqual(self.names(first), sorted([*context.CORE[NONSTANDARD], context.SEARCH_TOOL]))
        deferred = {x["id"] for x in first.left_out if x["what"] == "definition"}
        self.assertEqual(deferred, set(TOOLS[NONSTANDARD]) - set(context.CORE[NONSTANDARD]))

    def test_the_definitions_a_search_finds_enter_the_next_request(self):
        before = self.assemble()
        result = self.search("propose a change for the operator")
        self.assertFalse(result.is_error, result.content)
        self.assertIn("access_propose_change", [f["name"] for f in result.data["found"]])
        after = self.assemble()
        self.assertIn("access_propose_change", self.names(after))
        self.assertLess(before.tokens, after.tokens)
        self.assertEqual(self.searches()[-1]["matched"], True)

    def test_a_search_that_finds_nothing_says_so_and_is_logged(self):
        self.assemble()
        result = self.search("delete the database")
        self.assertEqual(result.data["found"], [])
        self.assertIn("No tool matched", result.data["note"])
        self.assertEqual(self.searches()[-1]["matched"], False)

    def test_a_search_never_reaches_a_tool_outside_the_use_case(self):
        self.assemble()
        result = self.search("list the contractors whose contract ended and remove all their access")
        names = [f["name"] for f in result.data["found"]]
        self.assertNotIn("directory_list_contractors_ended", names)     # U5's and U6's, not U4's
        self.assertNotIn("access_revoke_all", names)                    # U6's only
        self.assertNotIn("access_revoke_all", self.names(self.assemble()))

    def test_a_use_case_with_ten_tools_or_fewer_gets_every_definition(self):
        u5 = self.assemble(OPERATOR_REVIEW)
        self.assertEqual(self.names(u5), sorted(TOOLS[OPERATOR_REVIEW]))
        self.assertNotIn(context.SEARCH_TOOL, self.names(u5))

    def test_with_loading_all_every_definition_goes_out(self):
        runtime.configure()                                             # the switch as switches.yaml sets it
        self.assertEqual(self.names(self.assemble()), sorted(TOOLS[NONSTANDARD]))

    def test_the_search_runs_on_the_server_and_the_model_never_sees_the_offered_list(self):
        self.assemble()
        self.search("draft a reply")
        line = [x for x in read_trace(runtime.trace(SID).path) if x["type"] == "tool_call"][-1]
        self.assertEqual(line["transport"], "mcp")
        self.assertNotIn("_offered", line["input"])
