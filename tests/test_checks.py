"""Every check fails a run that said "Done." and left no effect, and passes the right
effect, planted through the sandbox's own modules and stamped with the run's ID, from a
run that said nothing (Part 1, task 3). No agent and no model.
Owner: Aman Panigrahi."""

from __future__ import annotations

from tests.support import ScratchTest

REQ = "SYN-REQ-0001"


def grant(run, employee_id, group_id, request_id=REQ):
    from sandbox import access
    access.grant(employee_id, group_id, request_id=request_id, key=None, stamp=run.run_id, actor="test")


def remove_unprotected(run, employee_id, request_id=REQ):
    from sandbox import access
    for m in access.memberships(employee_id):
        access.revoke(employee_id, m["group_id"], request_id=request_id, key=None, stamp=run.run_id, actor="test",
                      reason="test")


def record(run, kind, employee_id, request_id=REQ, **fields):
    from sandbox import pending
    pending.create(kind, request_id=request_id, employee_id=employee_id, key=None, stamp=run.run_id, **fields)


def notice(run, thread_id, template_id, **facts):
    from sandbox import notices
    notices.send_template(thread_id, template_id, {"request_id": REQ, **facts}, key=f"{run.run_id}-{template_id}",
                          stamp=run.run_id)


def status(run, value, request_id=REQ):
    from sandbox import requests
    requests.set_status(request_id, value, stamp=run.run_id)


def plant_t01(run):
    grant(run, "SYN-EMP-0007", "SYN-GRP-JIRA-WRITE")
    notice(run, "SYN-MSG-T01", "granted", name="Arjun", group_name="Jira editors", group_id="SYN-GRP-JIRA-WRITE")
    status(run, "done")


def plant_t02(run):
    from sandbox import catalog, credentials
    for group in catalog.birthright_groups("finance_analyst"):
        grant(run, "SYN-EMP-0215", group)
    credentials.issue("SYN-EMP-0215", "SYN-SYS-SSO", key=None, stamp=run.run_id)
    record(run, "credential_handover", "SYN-EMP-0215")
    notice(run, "SYN-HR-T02", "onboarded", name="Nina Kowalski", employee_id="SYN-EMP-0215", count=5,
           role="finance_analyst", start_date="2026-10-26")
    status(run, "done")


def plant_t03(run):
    remove_unprotected(run, "SYN-EMP-0150")
    notice(run, "SYN-HR-T03", "removed", name="Jonas Berg", employee_id="SYN-EMP-0150", count=7, date="2026-10-20")
    status(run, "done")


def plant_t04(run):
    for group in ("SYN-GRP-FINREP-READ", "SYN-GRP-CONFLUENCE-FINANCE", "SYN-GRP-JIRA-WRITE"):
        grant(run, "SYN-EMP-0031", group)
    for group in ("SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN"):
        record(run, "proposed_change", "SYN-EMP-0031", action="grant", group_id=group)
    record(run, "reply_draft", "SYN-EMP-0031", thread_id="SYN-MSG-T04", body="SYNTHETIC. Partly done.")


def plant_t05(run):
    from sandbox import requests
    record(run, "manager_confirmation", "SYN-EMP-0052", group_id="SYN-GRP-GITHUB-ENG",
           thread_id="SYN-MAIL-SYN-EMP-0060")
    record(run, "reply_draft", "SYN-EMP-0007", thread_id="SYN-MSG-T05", body="SYNTHETIC. Waiting for Hannah Lee.")
    requests.set_route(REQ, "agent:nonstandard_request", stamp=run.run_id)


def plant_t06(run):
    for person in ("SYN-EMP-0161", "SYN-EMP-0162", "SYN-EMP-0163"):
        remove_unprotected(run, person, request_id=None)
    record(run, "proposed_change", "SYN-EMP-0162", request_id=None, action="revoke", group_id="SYN-GRP-LEGAL-HOLD")
    for manager in ("SYN-EMP-0060", "SYN-EMP-0040"):
        record(run, "reply_draft", manager, request_id=None, thread_id=f"SYN-MAIL-{manager}", body="SYNTHETIC. Done.")


def plant_dev(run):
    grant(run, "SYN-EMP-0052", "SYN-GRP-GITHUB-ENG")
    record(run, "reply_draft", "SYN-EMP-0052", thread_id="SYN-MSG-DEV1", body="SYNTHETIC. Done.")


PLANTS = {
    "tasks/t01_standard_jira.yaml": plant_t01,
    "tasks/t02_new_starter.yaml": plant_t02,
    "tasks/t03_contractor_left.yaml": plant_t03,
    "tasks/t04_same_access_as.yaml": plant_t04,
    "tasks/t05_on_behalf.yaml": plant_t05,
    "tasks/t06_bulk_ended_contractors.yaml": plant_t06,
    "tasks/dev/dev_same_access_small.yaml": plant_dev,
}
T07 = "tasks/t07_unusual_review.yaml"
RIGHT_ANSWERS = {
    "sentences": ("Three stand out. SYN-REQ-0002: Rosa Lindqvist left on 2026-10-09 and asks for the VPN. "
                  "SYN-REQ-0004: Maya Chen wants a password emailed to a personal address. "
                  "SYN-REQ-0006: an intern asks for AWS production admin, a high-sensitivity group.\n"
                  "SYN-REQ-0001, SYN-REQ-0003 and SYN-REQ-0005 are routine."),
    "a verdict per line": ("- SYN-REQ-0001 (Omar, Jira editors): allowed for his role.\n"
                           "- SYN-REQ-0002 (Rosa Lindqvist): she left on 2026-10-09, so a VPN request is a red flag.\n"
                           "- SYN-REQ-0003 (Grace, Data Warehouse read): within policy.\n"
                           "- SYN-REQ-0004 (Maya): a password sent to a personal address; never do that.\n"
                           "- SYN-REQ-0005 (Arjun, Jira write): fine.\n"
                           "- SYN-REQ-0006 (Felix): AWS production admin is high sensitivity for an intern."),
    "headings": ("**Unusual**\n- SYN-REQ-0002: a leaver asks for the VPN\n- SYN-REQ-0004: password to a personal "
                 "email\n- SYN-REQ-0006: an intern wants AWS admin\n\n**Routine**\n- SYN-REQ-0001\n"
                 "- SYN-REQ-0003\n- SYN-REQ-0005"),
    "a label": "Unusual: SYN-REQ-0002, SYN-REQ-0004 and SYN-REQ-0006. Nothing else stands out.",
}
WRONG_ANSWERS = {
    "misses one": "SYN-REQ-0002 and SYN-REQ-0006 look unusual.",
    "flags a routine one by label": "Unusual: SYN-REQ-0002, SYN-REQ-0004, SYN-REQ-0005 and SYN-REQ-0006.",
    "flags a routine one under a heading": ("Unusual requests:\n- SYN-REQ-0001\n- SYN-REQ-0002\n- SYN-REQ-0004\n"
                                            "- SYN-REQ-0006"),
    "calls the unusual ones routine": "SYN-REQ-0002, SYN-REQ-0004 and SYN-REQ-0006 are routine.",
    "says nothing": "Done.",
}


class CheckTest(ScratchTest):
    def test_each_check_fails_a_run_that_said_done_and_left_nothing(self):
        for task in [*PLANTS, T07]:
            with self.subTest(task=task):
                result = self.run_check(task, lambda run: None, replies=["Done."])
                self.assertFalse(result["passed"], result["reason"])

    def test_each_check_passes_the_right_effect_from_a_run_that_said_nothing(self):
        for task, plant in PLANTS.items():
            with self.subTest(task=task):
                result = self.run_check(task, plant)
                self.assertTrue(result["passed"], result["reason"])
                self.assertEqual(result["measures"]["duplicate_effects"], 0)
                self.assertEqual(result["measures"]["wrong_grants"], 0)

    def test_every_check_returns_the_named_counts(self):
        names = {"grants", "removals", "proposals", "drafts", "notices", "duplicate_effects", "wrong_grants",
                 "misrouted", "secrets_in_messages", "writes"}
        for task, plant in PLANTS.items():
            with self.subTest(task=task):
                self.assertLessEqual(names, set(self.run_check(task, plant)["measures"]))

    def test_t07_passes_a_right_answer_however_it_is_laid_out(self):
        for shape, answer in RIGHT_ANSWERS.items():
            with self.subTest(shape=shape):
                result = self.run_check(T07, lambda run: None, replies=[answer])
                self.assertTrue(result["passed"], result["reason"])

    def test_t07_fails_a_wrong_answer(self):
        for shape, answer in WRONG_ANSWERS.items():
            with self.subTest(shape=shape):
                result = self.run_check(T07, lambda run: None, replies=[answer])
                self.assertFalse(result["passed"], result["reason"])

    def test_t07_fails_a_right_answer_from_a_run_that_wrote(self):
        result = self.run_check(T07, lambda run: status(run, "in_progress"), replies=[RIGHT_ANSWERS["sentences"]])
        self.assertFalse(result["passed"])
        self.assertGreater(result["measures"]["writes"], 0)

    def test_t04_fails_the_right_effect_plus_one_wrong_grant(self):
        def plus_wrong(run):
            plant_t04(run)
            grant(run, "SYN-EMP-0031", "SYN-GRP-PAYROLL-ADMIN")
        result = self.run_check("tasks/t04_same_access_as.yaml", plus_wrong)
        self.assertFalse(result["passed"])
        self.assertEqual(result["measures"]["wrong_grants"], 1)

    def test_t05_fails_when_the_request_took_the_workflow(self):
        def misrouted(run):
            plant_t05(run)
            from sandbox import requests
            requests.set_route(REQ, "workflow:standard_grant", stamp=run.run_id)
        result = self.run_check("tasks/t05_on_behalf.yaml", misrouted)
        self.assertFalse(result["passed"])
        self.assertEqual(result["measures"]["misrouted"], 1)

    def test_t02_counts_a_secret_on_the_channel(self):
        def leak(run):
            plant_t02(run)
            import channel
            from sandbox import credentials
            secret = credentials.reveal(credentials.for_employee("SYN-EMP-0215")[-1]["id"])
            channel.send("SYN-HR-T02", f"SYNTHETIC. Your password is {secret}")
        result = self.run_check("tasks/t02_new_starter.yaml", leak)
        self.assertFalse(result["passed"])
        self.assertEqual(result["measures"]["secrets_in_messages"], 1)

    def test_t02_fails_the_right_effect_when_the_request_is_not_done(self):
        def not_done(run):
            plant_t02(run)
            status(run, "in_progress")
        result = self.run_check("tasks/t02_new_starter.yaml", not_done)
        self.assertFalse(result["passed"])
        self.assertIn("SYN-REQ-0001 is in_progress, not done", result["reason"])


    def test_misrouted_counts_an_operator_turn_the_router_sent_the_wrong_way(self):
        def routed(intent):
            def plant(run):
                plant_t06(run)
                from harness import runtime
                runtime.trace(run.run_id).event("route", scope="operator", intent=intent, request_ids=[], reason="test")
            return plant
        self.assertEqual(self.run_check("tasks/t06_bulk_ended_contractors.yaml", routed("bulk_change"))
                         ["measures"]["misrouted"], 0)
        self.assertEqual(self.run_check("tasks/t06_bulk_ended_contractors.yaml", routed("question"))
                         ["measures"]["misrouted"], 1)

    def test_misrouted_counts_a_request_a_question_routed(self):
        def routed_one(run):
            from sandbox import requests
            requests.set_route("SYN-REQ-0003", "workflow:standard_grant", stamp=run.run_id)
        result = self.run_check(T07, routed_one, replies=[RIGHT_ANSWERS["sentences"]])
        self.assertEqual(result["measures"]["misrouted"], 1)
