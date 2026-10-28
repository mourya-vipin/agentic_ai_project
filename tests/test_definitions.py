"""The rewritten tool definitions (Lecture 3, slides 10 to 23) and the results they
promise, replayed on the calls that failed in the dev-v1 runs. No tokens.
Owner: Vipin Mourya."""

import json

import channel
from agent.registry import CallContext, Registry
from agent.tools import definitions, impl
from harness import runtime
from sandbox import seed
from tests.support import ScratchTest

MODEL_TOOLS = sorted(n for n, (_, callers, _) in impl.TOOLS.items() if "model" in callers)
SERVICES = ("directory_", "access_", "catalog_", "policy_", "requests_", "reply_", "manager_")


def ctx(use_case="nonstandard_request", request_id=None):
    return CallContext("s-def", 1, "model", use_case, request_id=request_id)


class DefinitionsTest(ScratchTest):
    def setUp(self):
        super().setUp()
        channel.reset()
        with channel.seeding():
            seed.reset()
            seed.seed(None)
        runtime.configure()
        self.registry = Registry()

    def sent(self, overrides=None):
        runtime.configure(overrides=overrides)
        return {d["name"]: d for d in Registry().definitions()}

    # -- the definitions ------------------------------------------------------------------

    def test_every_tool_a_model_calls_is_rewritten_and_namespaced(self):
        names = [d["name"] for d in definitions.DEFINITIONS]
        self.assertEqual(sorted(names), MODEL_TOOLS)
        self.assertTrue(all(n.startswith(SERVICES) for n in names), names)

    def test_every_definition_is_a_closed_object_with_a_real_description(self):
        for d in definitions.DEFINITIONS:
            with self.subTest(tool=d["name"]):
                self.assertIs(d["input_schema"]["additionalProperties"], False)
                self.assertGreater(len(d["description"]), 120)
                for prop in d["input_schema"]["properties"].values():
                    self.assertTrue(prop.get("description"), prop)

    def test_every_list_tool_says_what_an_empty_result_means(self):
        for name in ("directory_search_employees", "directory_list_contractors_ended", "access_list_memberships",
                     "catalog_search_groups", "requests_list_today"):
            description = next(d["description"] for d in definitions.DEFINITIONS if d["name"] == name)
            self.assertIn("empty", description.lower(), name)

    def test_examples_are_rendered_only_while_their_switch_is_on(self):
        on = self.sent(["examples.access_grant=on"])["access_grant"]
        off = self.sent(["examples.access_grant=off"])["access_grant"]
        self.assertIn("Example, a typical call", on["description"])
        self.assertIn("2026-11-30", on["description"])                # then the unclear case
        self.assertNotIn("Example", off["description"])
        self.assertNotIn("examples", on)                              # never sent as a field of its own

    def test_strict_is_set_only_while_its_switch_is_on_and_the_schema_fits(self):
        on = self.sent(["strict.directory_get_employee=on"])["directory_get_employee"]
        off = self.sent(["strict.directory_get_employee=off"])["directory_get_employee"]
        self.assertIs(on.get("strict"), True)
        self.assertNotIn("strict", off)
        schema = on["input_schema"]
        self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))     # every property required
        self.assertTrue(all("null" in p["type"] for p in schema["properties"].values()))   # each optional, nullable
        self.assertEqual(sum(1 for d in self.sent().values() if d.get("strict")), 1)

    def test_a_null_for_the_unused_lookups_finds_the_person(self):
        found = self.registry.call("directory_get_employee", {"employee_id": "SYN-EMP-0012", "email": None,
                                                              "name": None}, ctx())
        self.assertEqual(found.data["name"], "Priya Nair")

    def test_no_example_or_hint_names_a_real_record(self):
        # the first examples used t04's requester and four of its groups (review, 2026-10-28)
        import re
        from agent.tools import rules
        from sandbox import catalog, directory, requests
        found = {"EMP": directory.get_employee, "GRP": catalog.get_group, "REQ": requests.get, "SYS": catalog.get_system}
        sent = self.sent(["examples.access_grant=on", "examples.policy_check_access=on"]).values()
        text = json.dumps(list(sent)) + " ".join(str(v) for v in vars(rules).values() if isinstance(v, tuple))
        ids = set(re.findall(r"SYN-(EMP|GRP|REQ|SYS)-([A-Z0-9][A-Z0-9-]*[A-Z0-9])", text))
        self.assertTrue(ids)
        self.assertEqual([f"SYN-{k}-{v}" for k, v in sorted(ids) if found[k](f"SYN-{k}-{v}")], [])

    def test_policy_examples_are_rendered_while_their_switch_is_on(self):
        on = self.sent(["examples.policy_check_access=on"])["policy_check_access"]["description"]
        self.assertIn("SYN-GRP-EXAMPLE-ADMIN", on)

    def test_the_first_draft_is_sent_as_written_never_with_the_rewrites_switches(self):
        from agent.tools import definitions_v1
        runtime.configure(overrides=["strict.directory_get_employee=on", "examples.access_grant=on"])
        first = Registry(definitions_v1.DEFINITIONS, definitions_v1.ALIASES, apply_switches=False).definitions()
        self.assertFalse(any("strict" in d for d in first))       # OpenAI refuses strict on an unfitted schema
        self.assertEqual([d["description"] for d in first],
                         [d["description"] for d in sorted(definitions_v1.DEFINITIONS, key=lambda d: d["name"])])

    def test_the_first_drafts_names_still_reach_their_tools(self):
        self.assertFalse(self.registry.call("get_access", {"employee_id": "SYN-EMP-0012"}, ctx()).is_error)

    # -- the results they promise, on dev-v1's failing calls -------------------------------

    def test_t07s_invented_group_ids_now_get_the_closest_real_ones(self):
        # results/dev-v1/dev-v1-t07_unusual_review-s01-93f217.trace.jsonl:17-22
        expected = {"jira_edit": "SYN-GRP-JIRA-WRITE", "vpn_access": "SYN-GRP-VPN-USER",
                    "data_warehouse_read": "SYN-GRP-DWH-READ", "aws_production_admin": "SYN-GRP-AWSPROD-ADMIN"}
        for bad, right in expected.items():
            with self.subTest(bad=bad):
                result = self.registry.call("policy_check_access", {"employee_id": "SYN-EMP-0070", "group_ids": [bad]},
                                            ctx("operator_review"))
                self.assertTrue(result.is_error)
                self.assertIn(right, result.content)

    def test_a_well_formed_but_unknown_group_is_an_error_not_forbidden(self):
        # results/dev-v1/dev-v1-t07_unusual_review-s01-93f217.trace.jsonl:34
        result = self.registry.call("policy_check_access", {"employee_id": "SYN-EMP-0021",
                                                            "group_ids": ["SYN-GRP-PASSWORD-EMAIL-EXT"]},
                                    ctx("operator_review"))
        self.assertTrue(result.is_error)
        self.assertIn("No such group", result.content)

    def test_many_groups_are_checked_in_one_call(self):
        # results/dev-v1/dev-v1-t04_same_access_as-s01-f5d118.trace.jsonl:14-23 took five calls
        groups = ["SYN-GRP-CONFLUENCE-FINANCE", "SYN-GRP-FINREP-READ", "SYN-GRP-FINREP-WRITE", "SYN-GRP-JIRA-WRITE",
                  "SYN-GRP-PAYROLL-ADMIN"]
        result = self.registry.call("policy_check_access", {"employee_id": "SYN-EMP-0031", "group_ids": groups}, ctx())
        self.assertEqual([d["decision"] for d in result.data["decisions"]],
                         ["allowed", "allowed", "needs_manager", "allowed", "needs_manager"])

    def test_a_person_can_be_found_by_name(self):
        # results/dev-aman-check/dev-aman-check-dev_same_access_small-s01-1e750c.trace.jsonl: a "query" argument
        found = self.registry.call("directory_get_employee", {"employee_id": None, "email": None, "name": "Priya Nair"},
                                   ctx())
        self.assertEqual(found.data["id"], "SYN-EMP-0012")
        self.assertIn("Call again with the employee_id", self.registry.call("directory_get_employee", {"employee_id": None, "email": None,
                                                                                         "name": "Ma"},
                                                                             ctx()).content)

    def test_todays_requests_carry_each_requester(self):
        # results/dev-v1/dev-v1-t07_unusual_review-s01-93f217.trace.jsonl:10-15 looked up six requesters
        rows = self.registry.call("requests_list_today", {}, ctx("operator_review")).data["requests"]
        felix = next(r for r in rows if r["requester_id"] == "SYN-EMP-0120")
        self.assertEqual((felix["requester_name"], felix["requester_role"]), ("Felix Wagner", "marketing_intern"))

    def test_an_empty_search_says_the_query_was_valid(self):
        result = self.registry.call("directory_search_employees", {"query": "Zebedee"}, ctx())
        self.assertEqual(result.data["matches"], [])
        self.assertIn("The query was valid", result.data["note"])

    def test_removing_all_access_returns_a_summary_to_quote(self):
        data = self.registry.call("access_revoke_all", {"employee_id": "SYN-EMP-0162", "reason": "contract ended"},
                                  CallContext("s-def", 1, "model", "bulk_change")).data
        self.assertIn("Kept SYN-GRP-LEGAL-HOLD", data["summary"])
        self.assertIn("SYN-GRP-JIRA-WRITE", data["summary"])

    def test_removing_all_access_says_how_a_kept_group_is_removed(self):
        data = self.registry.call("access_revoke_all", {"employee_id": "SYN-EMP-0162", "reason": "contract ended"},
                                  CallContext("s-def", 1, "model", "bulk_change")).data
        self.assertIn("access_propose_change", data["next_step"])
        self.assertNotIn("access_propose_change", data["summary"])     # the summary stays fit to quote

    def test_removing_a_protected_group_is_an_error_that_names_the_proposal(self):
        # results/dev-bulk-loop/dev-bulk-loop-t06_bulk_ended_contractors-s01-906ff5.trace.jsonl:25 read
        # {"status": "protected"} as a success, then ended with no proposal written
        bulk = CallContext("s-def", 1, "model", "bulk_change")
        self.registry.call("access_revoke_all", {"employee_id": "SYN-EMP-0162", "reason": "contract ended"}, bulk)
        result = self.registry.call("access_revoke", {"employee_id": "SYN-EMP-0162", "group_id": "SYN-GRP-LEGAL-HOLD",
                                                      "reason": "contract ended"}, bulk)
        self.assertTrue(result.is_error)
        self.assertIn('access_propose_change with action "revoke", employee_id SYN-EMP-0162, group_id '
                      "SYN-GRP-LEGAL-HOLD, request_id null", result.content)
        proposed = self.registry.call("access_propose_change", {"action": "revoke", "employee_id": "SYN-EMP-0162",
                                                                "group_id": "SYN-GRP-LEGAL-HOLD", "request_id": None,
                                                                "reason": "contract ended; legal hold"}, bulk)
        self.assertEqual(proposed.data["status"], "waiting_for_operator")

    def test_a_bulk_change_has_no_request_status_to_set(self):
        # the same trace, :26: requests_set_status on SYN-REQ-0001, which no bulk change has
        bulk = CallContext("s-def", 1, "model", "bulk_change")
        result = self.registry.call("requests_set_status", {"request_id": "SYN-REQ-0099", "status": "done"}, bulk)
        self.assertTrue(result.is_error)
        self.assertIn("bulk change is not a request", result.content)
        self.assertNotIn("requests_list_today", result.content)

    def test_an_empty_list_for_another_date_says_what_today_gives(self):
        # results/pass1-after/pass1-after-t06_bulk_ended_contractors-s01-22e631.trace.jsonl:8: before "2024-06-01"
        bulk = CallContext("s-def", 1, "model", "bulk_change")
        note = self.registry.call("directory_list_contractors_ended", {"before": "2024-06-01"}, bulk).data["note"]
        left = len(self.registry.call("directory_list_contractors_ended", {}, bulk).data["contractors"])
        self.assertGreater(left, 0)
        self.assertIn(f"Today is 2026-10-21; leave before out for today: {left} contractor(s)", note)
        self.assertNotIn("note", self.registry.call("directory_list_contractors_ended", {}, bulk).data)

    def test_a_bad_date_says_what_today_is(self):
        result = self.registry.call("access_grant", {"employee_id": "SYN-EMP-0031", "group_id": "SYN-GRP-FINREP-READ",
                                                     "request_id": "SYN-REQ-0005", "expires_at": "next month"},
                                    ctx(request_id="SYN-REQ-0005"))
        self.assertIn("Today is 2026-10-21", result.content)
