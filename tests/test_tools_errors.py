"""Errors a model can repair from (Lecture 3, slides 33 to 36): every tool, given one
bad argument through the registry, fails as an execution error that says what was
wrong, what would be right, and whether a retry can work. The two tools only our
code calls get theirs here too. No tokens. Owner: Vipin Mourya."""

import channel
from agent.registry import GENERIC_PROTOCOL, CallContext, Registry
from agent.tools import impl
from harness import runtime
from sandbox import seed
from tests.fakes import BAD_ARGS, BadArgs
from tests.support import ScratchTest

CODE_ONLY_BAD = {
    "notices_send_template": {"thread_id": "SYN-MSG-D01", "request_id": "SYN-REQ-0001", "template_id": "granted",
                              "facts": {"name": "Arjun Mehta"}},
    "credentials_issue": {"employee_id": "SYN-EMP-0215", "system_id": "single sign-on"},
}


class RepairableErrorsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def test_every_model_tool_has_a_bad_argument_to_try(self):
        model_tools = {n for n, (_, callers, _) in impl.TOOLS.items() if "model" in callers}
        self.assertEqual(set(BAD_ARGS), model_tools)

    def test_each_bad_argument_comes_back_as_a_repairable_execution_error(self):
        for tool, args in {**BAD_ARGS, **CODE_ONLY_BAD}.items():
            caller = "workflow" if tool in CODE_ONLY_BAD else "model"
            with self.subTest(tool=tool):
                result = self.registry.call(tool, args, CallContext("s-err", 1, caller, "nonstandard_request"))
                self.assertTrue(result.is_error, result.content)
                self.assertNotEqual(result.error_class, "protocol")
                self.assertNotEqual(result.content, GENERIC_PROTOCOL)
                self.assertTrue("same way" in result.content or "retry" in result.content.lower(), result.content)
                self.assertLessEqual(len(result.content), 400)

    def test_the_fake_calls_each_offered_tool_once_then_stops(self):
        fake, tools = BadArgs(), self.registry.definitions()
        messages = [{"role": "user", "content": "go"}]
        names = []
        while True:
            content, stop, _, _ = fake.create(system="", messages=messages, tools=tools)
            if stop == "end_turn":
                break
            names.append(content[0]["name"])
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": "result"}]
        self.assertEqual(names, [t["name"] for t in tools])
