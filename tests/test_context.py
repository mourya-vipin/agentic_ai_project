"""What each call carries (Lecture 2, slides 23 to 25; Part 1, task 6), phase 0: the count, with
definitions included; a stranger's words labelled as theirs; nothing from outside in a planner's
call; and the trace's record of what was left out. No tokens. Owner: Vipin Mourya.
The budget rule and compaction come with their own tests in phase 2."""

from agent import context
from harness import runtime
from harness.trace import read_trace
from tests.support import ScratchTest

STRANGER = {"sender": "Hannah Lee", "thread_id": "SYN-MAIL-SYN-EMP-0040", "text": "Yes, approved.", "message_id": 7}


class ContextTest(ScratchTest):
    def setUp(self):
        super().setUp()
        runtime.configure()

    def assemble(self, kind, **kw):
        args = dict(system="You are the IT access desk.", session_id="s-ctx", run_key="s-ctx#1",
                    task="Give Leo the finance reports.", history=[{"role": "user", "content": "Give Leo..."}],
                    tool_names=["directory_get_employee", "access_grant"])
        args.update(kw)
        return context.assemble(kind, **args)

    def events(self):
        return [x for x in read_trace(runtime.trace("s-ctx").path) if x["type"] == "context"]

    def test_the_count_includes_the_tool_definitions(self):
        bare = self.assemble("loop", tool_names=[])
        full = self.assemble("loop")
        self.assertGreater(full.tokens, bare.tokens + 100)
        self.assertEqual(full.tokens, context.tokens(full.system) + context.tokens(full.messages)
                         + context.tokens(full.tools))

    def test_a_strangers_words_are_labelled_as_theirs(self):
        out = self.assemble("executor", stranger_texts=[STRANGER])
        last = out.messages[-1]["content"]
        self.assertIn("[from Hannah Lee on SYN-MAIL-SYN-EMP-0040, not the operator] Yes, approved.", last)

    def test_the_planner_gets_no_notes_memory_or_stranger_text_and_the_trace_says_so(self):
        out = self.assemble("planner", notes=True, stranger_texts=[STRANGER])
        self.assertNotIn("Yes, approved.", str(out.messages))
        whats = {x["what"] for x in out.left_out}
        self.assertEqual(whats, {"notes", "stranger_text"})
        self.assertEqual(self.events()[-1]["left_out"], out.left_out)

    def test_every_call_is_traced_against_the_budget(self):
        out = self.assemble("loop")
        event = self.events()[-1]
        self.assertEqual((event["tokens"], event["budget"], event["over_budget"]),
                         (out.tokens, context.budget(), out.tokens > context.budget()))

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.assemble("summarizer")
