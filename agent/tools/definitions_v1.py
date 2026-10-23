"""The first-draft tool definitions (Part 1, task 4), the ones the model reads until
Part 3's rewrite. Owner: Vipin Mourya.

Written as a first draft is: plain names, a line of description each, the result's
shape in a sentence, and the error message the tool returns. Kept unchanged once the
rewrite exists: the failure labels and the definition-cost script compare against
them. Only the tools a model may call are here; notices_send_template and
credentials_issue are for our workflows and the model never sees them.
"""

from __future__ import annotations


def _tool(name, description, properties, required=()):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": list(required)}}


S = {"type": "string"}

DEFINITIONS: list[dict] = [
    _tool("lookup_employee", "Look up an employee. Returns their record. Error if not found.",
          {"employee_id": S, "email": S}),
    _tool("search_employees", "Search the directory. Returns matching people.",
          {"query": S, "dept": S}, ["query"]),
    _tool("list_ended_contractors", "List contractors whose contract has ended. Returns a list of people.",
          {"before": S}),
    _tool("get_access", "Get the groups a person is in. Returns a list of memberships. Error if no such person.",
          {"employee_id": S, "include_removed": {"type": "boolean"}}, ["employee_id"]),
    _tool("find_group", "Find a group. Returns matching groups.", {"query": S, "system_id": S}, ["query"]),
    _tool("check_policy", "Check whether a person may have a group. Returns the policy decision.",
          {"employee_id": S, "group_id": S}, ["employee_id", "group_id"]),
    _tool("get_request", "Get a request. Returns the request. Error if not found.", {"request_id": S}, ["request_id"]),
    _tool("list_requests", "List today's requests.", {}),
    _tool("grant_access", "Give a person access to a group. Returns the result. Error if not allowed.",
          {"employee_id": S, "group_id": S, "request_id": S, "expires_at": S}, ["employee_id", "group_id"]),
    _tool("remove_access", "Remove a person from a group. Returns the result.",
          {"employee_id": S, "group_id": S, "request_id": S, "reason": S}, ["employee_id", "group_id"]),
    _tool("remove_all_access", "Remove all of a person's access. Returns what was removed.",
          {"employee_id": S, "request_id": S, "reason": S}, ["employee_id"]),
    _tool("propose_access", "Propose an access change for IT to approve. Returns the proposal.",
          {"action": S, "employee_id": S, "group_id": S, "request_id": S, "reason": S},
          ["action", "employee_id", "group_id"]),
    _tool("draft_reply", "Draft a reply to someone. Returns the draft.",
          {"recipient_employee_id": S, "request_id": S, "body": S}, ["recipient_employee_id", "body"]),
    _tool("draft_manager_confirmation", "Draft a message asking a manager to confirm access. Returns the draft.",
          {"employee_id": S, "group_id": S, "request_id": S, "body": S}, ["employee_id", "group_id"]),
    _tool("update_request", "Update a request's status. Returns the request.",
          {"request_id": S, "status": S, "note": S}, ["request_id", "status"]),
]

# first-draft name -> canonical name (agent/tools/impl.py)
ALIASES: dict[str, str] = {
    "lookup_employee": "directory_get_employee",
    "search_employees": "directory_search_employees",
    "list_ended_contractors": "directory_list_contractors_ended",
    "get_access": "access_list_memberships",
    "find_group": "catalog_search_groups",
    "check_policy": "policy_check_access",
    "get_request": "requests_get",
    "list_requests": "requests_list_today",
    "grant_access": "access_grant",
    "remove_access": "access_revoke",
    "remove_all_access": "access_revoke_all",
    "propose_access": "access_propose_change",
    "draft_reply": "reply_draft",
    "draft_manager_confirmation": "manager_confirmation_draft",
    "update_request": "requests_set_status",
}
