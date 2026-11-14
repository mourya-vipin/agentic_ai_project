"""The boundary's rules for each canonical tool (Lecture 3, slide 22): what its
arguments must be before it runs, and what its result must hold before the model
sees it. Owner: Vipin Mourya.

They belong to the tool, not to its definition, so the first draft and the rewrite
are checked the same way. A rule that fails becomes an execution error carrying its
repair fact: what was wrong, what would be right, and whether a retry can work.
"""

from __future__ import annotations

import re

from sandbox import catalog, clock, directory, requests

EMPLOYEE = ("id", re.compile(r"^SYN-EMP-\d{4}$"), "an employee ID such as SYN-EMP-9001")
GROUP = ("id", re.compile(r"^SYN-GRP-[A-Z0-9-]+$"), "a group ID such as SYN-GRP-EXAMPLE-WRITE")
REQUEST = ("id", re.compile(r"^SYN-REQ-\d{4}$"), "a request ID such as SYN-REQ-9001")
SYSTEM = ("id", re.compile(r"^SYN-SYS-[A-Z0-9-]+$"), "a system ID such as SYN-SYS-EXAMPLE")
CREDENTIAL = ("id", re.compile(r"^SYN-CRED-\d{4}$"), "a credential ID such as SYN-CRED-0251")
DATE = ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$"), "a date as YYYY-MM-DD")
TEXT = ("text", None, "text")
FLAG = ("bool", None, "true or false")
IDS = ("ids", GROUP[1], "a list of group IDs such as [\"SYN-GRP-EXAMPLE-WRITE\"]")


def closest_employees(text: str, limit: int = 3) -> list[str]:
    """Up to `limit` people whose name, email or ID is near `text`, as "Name (SYN-EMP-…)".
    Words under four letters are skipped: "all" would match inside "Callum"."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(text)) if len(w) >= 4]
    found: dict[str, str] = {}
    for query in [str(text).strip()] + words:
        for p in directory.search_employees(query, limit=limit):
            found.setdefault(p["id"], f"{p['name']} ({p['id']})")
        if len(found) >= limit:
            break
    return list(found.values())[:limit]


def closest_groups(text: str, limit: int = 3) -> list[str]:
    """Up to `limit` group IDs near `text`, by the words in it ("jira_edit" -> Jira groups)."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(text).replace("SYN-GRP-", "")) if len(w) >= 3]
    found: dict[str, None] = {}
    for query in [str(text).strip()] + words:
        for g in catalog.search_groups(query, limit=limit):
            found.setdefault(g["id"], None)
        if len(found) >= limit:
            break
    return list(found)[:limit]


def hint(kind: str, value) -> str:
    """The context the model lacks for a bad value: the closest valid IDs, or today's date."""
    if kind == "employee":
        near = closest_employees(value)
        return f" Closest: {', '.join(near)}." if near else " Find the ID with directory_search_employees."
    if kind == "group":
        near = closest_groups(value)
        return f" Closest: {', '.join(near)}." if near else " Find the ID with catalog_search_groups."
    if kind == "date":
        return f" Today is {clock.today()}."
    return ""


def enum(*values):
    return ("enum", tuple(values), " or ".join(f'"{v}"' for v in values))


def text(limit: int):
    return ("text", limit, f"text of at most {limit} characters")


# tool -> (required arguments, {argument: rule})
ARGS = {
    "directory_get_employee": ((), {"employee_id": EMPLOYEE, "email": text(120), "name": text(80)}),
    "directory_search_employees": (("query",), {"query": text(80), "dept": text(40)}),
    "directory_list_contractors_ended": ((), {"before": DATE}),
    "access_list_memberships": (("employee_id",), {"employee_id": EMPLOYEE, "include_removed": FLAG}),
    "catalog_search_groups": (("query",), {"query": text(80), "system_id": SYSTEM}),
    "policy_check_access": (("employee_id",), {"employee_id": EMPLOYEE, "group_ids": IDS, "group_id": GROUP}),
    "requests_get": (("request_id",), {"request_id": REQUEST}),
    "requests_list_today": ((), {}),
    "tools_search": (("query",), {"query": text(120)}),
    "code_run": (("program",), {"program": text(4000)}),
    "access_grant": (("employee_id", "group_id"), {"employee_id": EMPLOYEE, "group_id": GROUP,
                                                   "request_id": REQUEST, "expires_at": DATE}),
    "access_revoke": (("employee_id", "group_id"), {"employee_id": EMPLOYEE, "group_id": GROUP,
                                                    "request_id": REQUEST, "reason": text(300)}),
    "access_revoke_all": (("employee_id",), {"employee_id": EMPLOYEE, "request_id": REQUEST, "reason": text(300)}),
    "access_propose_change": (("action", "employee_id", "group_id"),
                              {"action": enum("grant", "revoke"), "employee_id": EMPLOYEE, "group_id": GROUP,
                               "request_id": REQUEST, "reason": text(300)}),
    "reply_draft": (("recipient_employee_id", "body"), {"recipient_employee_id": EMPLOYEE, "request_id": REQUEST,
                                                        "body": text(3000)}),
    "manager_confirmation_draft": (("employee_id", "group_id"), {"employee_id": EMPLOYEE, "group_id": GROUP,
                                                                 "request_id": REQUEST, "body": text(3000)}),
    "requests_set_status": (("request_id", "status"), {"request_id": REQUEST, "status": enum(*requests.STATUSES),
                                                       "note": text(300)}),
    "channel_ask_template": (("template", "employee_id", "group_id"),
                             {"template": enum("manager_confirm"), "employee_id": EMPLOYEE, "group_id": GROUP,
                              "request_id": REQUEST}),
    "reply_send": (("recipient_employee_id", "body"), {"recipient_employee_id": EMPLOYEE, "request_id": REQUEST,
                                                       "body": text(3200)}),
    "notices_send_template": (("thread_id", "template_id"), {"thread_id": text(80), "request_id": REQUEST,
                                                             "template_id": text(40)}),
    "credentials_issue": (("employee_id",), {"employee_id": EMPLOYEE, "system_id": SYSTEM}),
    "credentials_record_handover": (("employee_id", "credential_id"), {"employee_id": EMPLOYEE,
                                                                       "credential_id": CREDENTIAL,
                                                                       "request_id": REQUEST}),
}

# tool -> the keys its result must hold before the model sees it
RESULT_KEYS = {
    "directory_get_employee": ("id", "name", "role", "status"),
    "directory_search_employees": ("matches", "shown", "total"),
    "directory_list_contractors_ended": ("before", "contractors"),
    "access_list_memberships": ("employee_id", "memberships"),
    "catalog_search_groups": ("groups", "shown"),
    "policy_check_access": ("employee_id", "decisions"),
    "requests_get": ("id", "status", "changes", "pending"),
    "requests_list_today": ("day", "requests"),
    "tools_search": ("query", "found", "shown"),
    "code_run": ("result", "calls", "tools"),
    "access_grant": ("status", "employee_id", "group_id"),
    "access_revoke": ("status", "employee_id", "group_id"),
    "access_revoke_all": ("employee_id", "removed", "kept_protected"),
    "access_propose_change": ("pending_id", "status"),
    "reply_draft": ("pending_id", "status", "thread_id"),
    "manager_confirmation_draft": ("pending_id", "status", "manager_id"),
    "requests_set_status": ("request_id", "status"),
    "channel_ask_template": ("status", "message_id", "thread_id", "recipient_employee_id", "waiting_for"),
    "reply_send": ("status", "message_id", "thread_id", "recipient_employee_id"),
    "notices_send_template": ("status", "message_id"),
    "credentials_issue": ("credential_id", "employee_id"),
    "credentials_record_handover": ("pending_id", "status", "credential_id"),
}


def check_args(tool: str, args: dict) -> tuple[dict, list[str]]:
    """The arguments in their standard form, and every problem with them as a repair
    fact (empty when they fit). IDs are trimmed and upper-cased; enum values are
    compared case-insensitively and given in their own case."""
    required, rules = ARGS[tool]
    fixed, problems = {}, []
    for name in required:
        if args.get(name) in (None, "", []):
            problems.append(f"{name} is required: give {rules[name][2]}")
    for name, value in args.items():
        if name not in rules:
            if name != "facts":
                problems.append(f"{name} is not an argument of {tool}; it takes {', '.join(rules) or 'none'}")
            else:
                fixed[name] = value
            continue
        if value is None:
            fixed[name] = None          # an explicit null: the strict schema's "left out"
            continue
        kind, test, says = rules[name]
        if kind == "bool":
            if not isinstance(value, bool):
                problems.append(f"{name} is {value!r}; give {says}")
            fixed[name] = value
        elif kind == "ids":
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, list) or not values:
                problems.append(f"{name} is {value!r}; give {says}")
                continue
            norm = [str(v).strip().upper() for v in values]
            bad = [v for v in norm if not test.match(v)]
            if bad:
                near = sorted({g for v in bad for g in closest_groups(v, 2)})
                problems.append(f"{name} has {', '.join(bad)}; give {says}"
                                + (f". Closest: {', '.join(near)}" if near else ""))
            fixed[name] = norm
        elif kind == "enum":
            norm = str(value).strip().lower()
            if norm not in test:
                problems.append(f"{name} is {value!r}; give {says}")
            fixed[name] = norm
        elif kind in ("id", "date"):
            norm = str(value).strip().upper() if kind == "id" else str(value).strip()
            if not test.match(norm):
                which = "date" if kind == "date" else ("employee" if rules[name] is EMPLOYEE else
                                                       "group" if rules[name] is GROUP else "")
                problems.append(f"{name} is {value!r}; give {says}.{hint(which, value)}".replace("..", "."))
            fixed[name] = norm
        else:
            text_value = str(value)
            if test is not None and len(text_value) > test:
                problems.append(f"{name} is {len(text_value)} characters; give {says}")
            fixed[name] = text_value
    return fixed, problems


def check_result(tool: str, data) -> list[str]:
    """What the result lacks of its declared shape (empty when it fits)."""
    if not isinstance(data, dict):
        return [f"the result is {type(data).__name__}, not an object"]
    return [f"the result has no {k}" for k in RESULT_KEYS[tool] if k not in data]
