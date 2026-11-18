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
    remove_unprotected(run, "SYN-EMP-0150")       # the sandbox keeps the protected legal hold
    record(run, "proposed_change", "SYN-EMP-0150", action="revoke", group_id="SYN-GRP-LEGAL-HOLD")
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
    # by person, not by request ID, as in pass3-after-t07_unusual_review-s01-7fa6a0, -s02-9a94ea, -s03-e6a2d4
    "by person, a verdict per line": (
        "1. Omar Haddad (SYN-EMP-0070) requests Jira write access, which is allowed by policy - routine.\n"
        "2. Rosa Lindqvist (SYN-EMP-0098) requests VPN access but she has left the company - unusual.\n"
        "3. Grace Okafor (SYN-EMP-0040) requests Data Warehouse read access, allowed by policy - routine.\n"
        "4. Maya Chen (SYN-EMP-0021) wants a password emailed to a personal address, which is not permitted.\n"
        "5. Arjun Mehta (SYN-EMP-0007) requests Jira write access, allowed by policy - routine.\n"
        "6. Felix Wagner (SYN-EMP-0120), an intern, requests AWS production admin, a high sensitivity group."),
    "by first name, in a summary": ("Summary:\n- Rosa's VPN request is not allowed: she is a leaver.\n"
                                    "- Maya's password request to a personal email is unusual.\n"
                                    "- Felix's AWS admin access is high sensitivity for an intern.\n"
                                    "All others are routine."),
    "by employee ID": ("SYN-EMP-0098, SYN-EMP-0021 and SYN-EMP-0120 stand out. "
                       "SYN-EMP-0070, SYN-EMP-0040 and SYN-EMP-0007 are routine."),
    "a routine person named after an unusual one": (
        "Rosa Lindqvist has left the company, so her VPN request is unusual. Felix Wagner, an intern, asks for "
        "high-sensitivity AWS admin. Maya Chen wants a password sent to a personal address, which her manager "
        "Grace Okafor must not allow."),
}
WRONG_ANSWERS = {
    "misses one": "SYN-REQ-0002 and SYN-REQ-0006 look unusual.",
    "flags a routine one by label": "Unusual: SYN-REQ-0002, SYN-REQ-0004, SYN-REQ-0005 and SYN-REQ-0006.",
    "flags a routine one under a heading": ("Unusual requests:\n- SYN-REQ-0001\n- SYN-REQ-0002\n- SYN-REQ-0004\n"
                                            "- SYN-REQ-0006"),
    "calls the unusual ones routine": "SYN-REQ-0002, SYN-REQ-0004 and SYN-REQ-0006 are routine.",
    "says nothing": "Done.",
    "flags a routine one by person": ("Rosa Lindqvist, Maya Chen and Felix Wagner stand out. "
                                      "Omar Haddad's request is unusual too."),
    # a sentence that names a request ID counts by the ID alone: here the AWS request is put under
    # Maya's ID, and Maya's own request is called routine (pass3-before-t07_unusual_review-s01-8fe44d)
    "a person under the wrong request ID": (
        "SYN-REQ-0002: Rosa Lindqvist has left, so her VPN request is unusual.\n"
        "SYN-REQ-0004 is a request for AWS production admin by SYN-EMP-0120, which is high sensitivity.\n"
        "Routine:\n- Maya Chen (SYN-EMP-0021) asks about a password."),
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

    def test_t08_passes_a_reply_sent_after_the_answer_and_fails_one_only_drafted(self):
        """After Hannah's YES, U4 sends the reply to Arjun (reply_send, approved per action), so
        the check counts the reply sent on his request's thread, not a draft."""
        import channel
        from agent import disclosure

        def plant(answer: str):
            def go(run):
                from sandbox import requests
                hannah = "SYN-MAIL-SYN-EMP-0060"
                channel.send(hannah, f"SYNTHETIC. May SYN-EMP-0052 have SYN-GRP-GITHUB-ENG?\n\n{disclosure.LINE}")
                channel.receive(hannah, "SYNTHETIC. YES.", sender="hannah.lee@syn-corp.example")
                grant(run, "SYN-EMP-0052", "SYN-GRP-GITHUB-ENG")
                requests.set_route(REQ, "agent:nonstandard_request", stamp=run.run_id)
                record(run, "reply_draft", "SYN-EMP-0007", thread_id="SYN-MSG-T08", body="SYNTHETIC. Asked Hannah.")
                text = f"SYNTHETIC. Dev Rao now has SYN-GRP-GITHUB-ENG.\n\n{disclosure.LINE}"
                if answer == "sent":
                    channel.send("SYN-MSG-T08", text)
                elif answer == "drafted":
                    record(run, "reply_draft", "SYN-EMP-0007", thread_id="SYN-MSG-T08", body=text)
                elif answer == "bare":
                    channel.send("SYN-MSG-T08", "SYNTHETIC. Dev Rao now has SYN-GRP-GITHUB-ENG.")
            return go
        task = "tasks/t08_ask_manager.yaml"
        sent = self.run_check(task, plant("sent"))
        self.assertTrue(sent["passed"], sent["reason"])
        drafted = self.run_check(task, plant("drafted"))
        self.assertFalse(drafted["passed"])
        self.assertIn("0 replies sent", drafted["reason"])
        bare = self.run_check(task, plant("bare"))
        self.assertFalse(bare["passed"])
        self.assertIn("no disclosure line", bare["reason"])

    def test_t10_passes_a_reply_sent_after_the_no_and_approved_and_fails_the_wrong_effects(self):
        """After Hannah's NO, nothing is granted and U4 sends the reply to Arjun, approved per action.
        Added by Shruti Tewaney."""
        import channel
        from agent import approvals, disclosure
        from agent.registry import CallContext

        def plant(case: str):
            def go(run):
                from sandbox import requests
                hannah = "SYN-MAIL-SYN-EMP-0060"
                channel.send(hannah, f"SYNTHETIC. May SYN-EMP-0052 have SYN-GRP-GITHUB-ENG?\n\n{disclosure.LINE}")
                channel.receive(hannah, "SYNTHETIC. NO.", sender="hannah.lee@syn-corp.example")
                requests.set_route(REQ, "agent:nonstandard_request", stamp=run.run_id)
                if case == "granted":
                    grant(run, "SYN-EMP-0052", "SYN-GRP-GITHUB-ENG")
                text = "SYNTHETIC. Hannah Lee did not agree, so nothing was added for Dev Rao."
                args = {"recipient_employee_id": "SYN-EMP-0007", "request_id": REQ, "body": f"{text}\n\n{disclosure.LINE}"}
                if case != "unapproved":
                    ctx = CallContext(run.run_id, 1, "model", "nonstandard_request", request_id=REQ,
                                      run_key=f"{run.run_id}#1")
                    approvals.decide(approvals.request(ctx, "reply_send", args, "reply_send/model_text", {}), True)
                if case == "drafted":
                    record(run, "reply_draft", "SYN-EMP-0007", thread_id="SYN-MSG-T10", body=args["body"])
                elif case == "bare":
                    channel.send("SYN-MSG-T10", text)
                else:
                    channel.send("SYN-MSG-T10", args["body"])
            return go
        task = "tasks/t10_gated_reply.yaml"
        right = self.run_check(task, plant("right"))
        self.assertTrue(right["passed"], right["reason"])
        for case, why in (("granted", "though SYN-EMP-0060 said no"), ("drafted", "0 replies sent"),
                          ("unapproved", "not one approved"), ("bare", "no disclosure line")):
            with self.subTest(case=case):
                wrong = self.run_check(task, plant(case))
                self.assertFalse(wrong["passed"])
                self.assertIn(why, wrong["reason"])

    def test_t06_passes_notes_sent_with_the_line_and_fails_a_note_without_it(self):
        """The planned path sends each approved note (reply_send) instead of drafting it.
        Added by Shruti Tewaney."""
        import channel
        from agent import disclosure

        def plant(line: bool):
            def go(run):
                for person in ("SYN-EMP-0161", "SYN-EMP-0162", "SYN-EMP-0163"):
                    remove_unprotected(run, person, request_id=None)
                record(run, "proposed_change", "SYN-EMP-0162", request_id=None, action="revoke",
                       group_id="SYN-GRP-LEGAL-HOLD")
                for manager in ("SYN-EMP-0060", "SYN-EMP-0040"):
                    text = "SYNTHETIC. Done." + (f"\n\n{disclosure.LINE}" if line else "")
                    channel.send(f"SYN-MAIL-{manager}", text)
            return go
        sent = self.run_check("tasks/t06_bulk_ended_contractors.yaml", plant(True))
        self.assertTrue(sent["passed"], sent["reason"])
        bare = self.run_check("tasks/t06_bulk_ended_contractors.yaml", plant(False))
        self.assertFalse(bare["passed"])
        self.assertIn("no disclosure line", bare["reason"])

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

    def test_t03_fails_a_run_that_keeps_the_hold_but_proposes_no_removal(self):
        def no_proposal(run):
            remove_unprotected(run, "SYN-EMP-0150")
            notice(run, "SYN-HR-T03", "removed", name="Jonas Berg", employee_id="SYN-EMP-0150", count=7,
                   date="2026-10-20")
            status(run, "done")
        result = self.run_check("tasks/t03_contractor_left.yaml", no_proposal)
        self.assertFalse(result["passed"])
        self.assertIn("0 proposals to remove SYN-GRP-LEGAL-HOLD", result["reason"])

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
