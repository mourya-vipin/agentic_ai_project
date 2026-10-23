"""Systems, groups and the role policy (reads only).
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def get_group(group_id: str) -> dict | None:
    raise NotImplementedError


def search_groups(query: str = "", system_id: str | None = None, limit: int = 10) -> list[dict]:
    raise NotImplementedError


def policy_for(employee_id: str, group_id: str) -> dict:
    """{"decision": "birthright" | "allowed" | "needs_manager" | "forbidden", "reason": str}"""
    raise NotImplementedError


def birthright_groups(role: str) -> list[str]:
    raise NotImplementedError
