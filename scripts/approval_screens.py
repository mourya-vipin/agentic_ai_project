"""The appendix of M2.md: one approval screen per gated action class, as the approvals page shows
it (Part 2, task 4). Owner: Shruti Tewaney.

    python scripts/approval_screens.py               # prints the appendix, no model call (fakes)
    python scripts/approval_screens.py --write       # replaces the appendix at the end of M2.md

Each screen is made by our own code on a scratch sandbox, never state/:

    access_grant/manager_confirmed   Leo Martins to Finance Reports editors, after Grace Okafor's YES
    access_grant/high_sensitivity    Leo Martins to Payroll administrators, after her YES
    reply_send/model_text            t10's reply to Arjun Mehta, written from Hannah Lee's NO (tests.fakes)
    manager_note/plan_listed         t06's two notes, approved together at the plan (tests.fakes)

and laid out as app/templates/approvals.html lays it out: the title and the action, "What it will
do" (the dry run), then the lines with their words, the lines our code read and checked, and the
other lines, each label without its "(computed)" or "(their words)" mark and the sandbox's
"SYNTHETIC." mark dropped, as the page drops them. Each line is then marked computed or asserted.
"""

from __future__ import annotations

import argparse
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

M2 = ROOT / "docs" / "milestones" / "M2.md"
HEADING = "## Appendix · Approval screens"

# as app/templates/approvals.html
TITLES = {"reply_send": "Send this reply?", "access_grant": "Grant this access?",
          "manager_note": "Send these notes to the managers?"}
APART = ("id", "session_id", "action", "dry run", "waiting since")
GROUPS = (("Their words, as they wrote them", "their words"), ("What our code read and checked", "(computed)"),
          ("Other facts", ""))

# what each line is: computed by our code, or a person's words (asserted), or our fixed text
KIND = {
    "action": "computed: the hook's action class",
    "dry run": "computed: the call's effect, rendered by our code from its arguments",
    "role policy (computed)": "computed: the catalog's role policy",
    "already holds it (computed)": "computed: the identity store",
    "reach (computed)": "computed: the identity store",
    "manager's answer (computed)": "computed by code from the manager's reply; the reply itself is the manager's assertion",
    "the manager's reply (their words)": "asserted: the manager's own words, read by code from their thread after our question",
    "the request (computed)": "computed: the request record",
    "the request's text (their words)": "asserted: the requester's own words, not checked",
    "the answer it is written from (their words)": "asserted: the manager's own words, read by code from her thread after our question",
    "this text against the records (computed)": "computed: the critique's code check on the text as sent, run for this screen",
    "this run's checks so far (computed)": "computed: the run's own lines in its trace, from its first notice",
    "done before this, reversible (computed)": "computed: the step results",
    "the validator (computed)": "computed: the validator's result on this plan version",
    "the critique on each note (computed)": "computed: the critique's verdict on each note, before this screen",
    "not listed here": "fixed text: the gate's rule",
    "what still checks it": "fixed text: what our code runs after this screen",
    "secrets (computed)": "computed: the hook's check, which refuses any text carrying a secret",
    "the operator's instruction (their words)": "asserted: the operator's words",
}

SCREENS = [
    ("access_grant/manager_confirmed", "Leo Martins to Finance Reports editors, after his manager's YES"),
    ("access_grant/high_sensitivity", "Leo Martins to Payroll administrators, after his manager's YES"),
    ("reply_send/model_text", "t10's reply to Arjun Mehta, written from his teammate's manager's NO (per action)"),
    ("manager_note/plan_listed", "t06's two notes to managers, approved together (at the plan)"),
]

SHOWN: dict[str, dict] = {}


def _make_screens() -> None:
    from tests.support import ScratchTest

    class Make(ScratchTest):
        def test_grants(self):
            import channel
            from agent import approvals, control
            from agent.registry import CallContext, Registry
            from harness import runtime
            from sandbox import seed
            leo, req, sid = "SYN-EMP-0031", "SYN-REQ-0005", "s-appendix"
            channel.reset()
            with channel.seeding():
                seed.reset()
                seed.seed(None)
            runtime.configure()
            registry, key = Registry(), f"{sid}#1"
            control.notice(key, "Leo's request")
            ctx = CallContext(sid, 1, "model", "nonstandard_request", request_id=req, run_key=key)
            for group in ("SYN-GRP-FINREP-WRITE", "SYN-GRP-PAYROLL-ADMIN"):
                asked = registry.call("channel_ask_template", {"template": "manager_confirm", "employee_id": leo,
                                                               "group_id": group, "request_id": req}, ctx)
                self.assertFalse(asked.is_error, asked.content)
                channel.receive(asked.data["thread_id"], "SYNTHETIC. YES, that is fine.", sender="Grace Okafor")
                registry.call("access_grant", {"employee_id": leo, "group_id": group, "request_id": req}, ctx)
            for item in approvals.pending():
                SHOWN[item["action"].split(" ")[0]] = item

        def test_t10(self):
            from agent import approvals
            self.run_task("tasks/t10_gated_reply.yaml", "t10", backend="tests.fakes:AsksTheManagerHearsNo")
            SHOWN["reply_send/model_text"] = approvals.log()[0]["shown"]

        def test_plan(self):
            from agent import approvals, executor
            from agent.bounds import Bound
            from tests import fakes
            from tests.support import fixed_run
            from tests.test_plan import T06
            run_id = f"plan-{uuid.uuid4().hex[:8]}"
            with fixed_run(T06, run_id, backend="tests.fakes:GoodPlannerWithHold"):
                executor.run_planned(run_id, fakes.T06_TEXT, bound=Bound())
                SHOWN["manager_note/plan_listed"] = approvals.pending()[0]

    result = unittest.TextTestRunner(stream=open("/dev/null", "w"), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Make))
    if not result.wasSuccessful():
        raise SystemExit("a screen could not be made: " + "; ".join(str(e[1])[-400:] for e in result.errors + result.failures))


def _cell(text) -> str:
    return str(text).replace("SYNTHETIC. ", "").replace("|", "\\|").replace("\n", "<br>")


def _label(key: str) -> str:
    label = key.replace(" (computed)", "").replace(" (their words)", "")
    return label[:1].upper() + label[1:]


def screen_table(action_class: str, item: dict) -> list[str]:
    action = str(item.get("action") or "")
    title = TITLES.get(action.split("/")[0], "Approve this step?")
    kind = KIND["action"] + (" of every listed step" if action_class == "manager_note/plan_listed" else "")
    rows = [("The title", "", f"**{title}** · {action}", kind),
            ("What it will do", "", _cell(item.get("dry run") or ""), KIND["dry run"])]
    for heading, marker in GROUPS:
        for k, v in item.items():
            if k in APART:
                continue
            if (marker and marker in k) or (not marker and "their words" not in k and "(computed)" not in k):
                rows.append((heading, _label(k), _cell(v), KIND.get(k, "unmarked")))
    out = ["| On the page | Line | As the page shows it | Computed or asserted |", "|---|---|---|---|"]
    last = None
    for heading, label, value, what in rows:
        out.append(f"| {heading if heading != last else ''} | {label} | {value} | {what} |")
        last = heading
    return out


def appendix() -> str:
    _make_screens()
    out = [HEADING, "", "Owner: Shruti Tewaney.", "",
           "One screen per gated action class, as the approvals page shows it: made by `agent/approvals.py` on a "
           "scratch sandbox and laid out under the page's headings by `scripts/approval_screens.py`. The page "
           "drops each label's \"(computed)\" or \"(their words)\" mark; the last column keeps it: computed (our "
           "code read it from the records), asserted (a person's words, not checked), or our fixed text. The "
           "model's rationale is never shown."]
    for action_class, about in SCREENS:
        out += ["", f"### `{action_class}`: {about}", ""] + screen_table(action_class, SHOWN[action_class])
    unmarked = [k for item in SHOWN.values() for k in item if k not in APART and k not in KIND]
    if unmarked:
        raise SystemExit(f"lines with no computed-or-asserted mark: {sorted(set(unmarked))}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="replace the appendix at the end of M2.md")
    args = parser.parse_args(argv)
    text = appendix()
    if args.write:
        doc = M2.read_text(encoding="utf-8")
        if doc.count(HEADING) != 1:
            raise SystemExit(f"{M2} needs exactly one {HEADING!r}")
        M2.write_text(doc[:doc.index(HEADING)] + text, encoding="utf-8")
        print(f"wrote the appendix of {M2.relative_to(ROOT)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
