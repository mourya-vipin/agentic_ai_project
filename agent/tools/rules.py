"""The boundary's rules for each canonical tool (Lecture 3, slide 22): what its
arguments must be before it runs, and what its result must hold before the model
sees it. Owner: Vipin Mourya.

They belong to the tool, not to its definition, so the first draft and the rewrite
are checked the same way. A rule that fails becomes an execution error carrying its
repair fact: what was wrong, what would be right, and whether a retry can work.
"""

from __future__ import annotations

import re

from sandbox import requests

EMPLOYEE = ("id", re.compile(r"^SYN-EMP-\d{4}$"), "an employee ID such as SYN-EMP-0031")
GROUP = ("id", re.compile(r"^SYN-GRP-[A-Z0-9-]+$"), "a group ID such as SYN-GRP-JIRA-WRITE")
REQUEST = ("id", re.compile(r"^SYN-REQ-\d{4}$"), "a request ID such as SYN-REQ-0005")
SYSTEM = ("id", re.compile(r"^SYN-SYS-[A-Z0-9-]+$"), "a system ID such as SYN-SYS-SSO")
CREDENTIAL = ("id", re.compile(r"^SYN-CRED-\d{4}$"), "a credential ID such as SYN-CRED-0251")
DATE = ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$"), "a date as YYYY-MM-DD")
TEXT = ("text", None, "text")
FLAG = ("bool", None, "true or false")
IDS = ("ids", GROUP[1], "a list of group IDs such as [\"SYN-GRP-JIRA-WRITE\"]")


def enum(*values):
    return ("enum", tuple(values), " or ".join(f'"{v}"' for v in values))


def text(limit: int):
    return ("text", limit, f"text of at most {limit} characters")


# tool -> (required arguments, {argument: rule})
ARGS = {
    "directory_get_employee": ((), {"employee_id": EMPLOYEE, "email": text(120)}),
    "directory_search_employees": (("query",), {"query": text(80), "dept": text(40)}),
    "directory_list_contractors_ended": ((), {"before": DATE}),
    "access_list_memberships": (("employee_id",), {"employee_id": EMPLOYEE, "include_removed": FLAG}),
    "catalog_search_groups": (("query",), {"query": text(80), "system_id": SYSTEM}),
    "policy_check_access": (("employee_id",), {"employee_id": EMPLOYEE, "group_ids": IDS, "group_id": GROUP}),
    "requests_get": (("request_id",), {"request_id": REQUEST}),
    "requests_list_today": ((), {}),
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
    "access_grant": ("status", "employee_id", "group_id"),
    "access_revoke": ("status", "employee_id", "group_id"),
    "access_revoke_all": ("employee_id", "removed", "kept_protected"),
    "access_propose_change": ("pending_id", "status"),
    "reply_draft": ("pending_id", "status", "thread_id"),
    "manager_confirmation_draft": ("pending_id", "status", "manager_id"),
    "requests_set_status": ("request_id", "status"),
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
                problems.append(f"{name} has {', '.join(bad)}; give {says}")
            fixed[name] = norm
        elif kind == "enum":
            norm = str(value).strip().lower()
            if norm not in test:
                problems.append(f"{name} is {value!r}; give {says}")
            fixed[name] = norm
        elif kind in ("id", "date"):
            norm = str(value).strip().upper() if kind == "id" else str(value).strip()
            if not test.match(norm):
                problems.append(f"{name} is {value!r}; give {says}")
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
