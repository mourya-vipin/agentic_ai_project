"""The mock identity API: who belongs to which group.
It accepts each key once and returns the first result to a repeat.
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


class TransientError(Exception):
    """Raised while a faults row for the tool has remaining > 0, before any write."""


def memberships(employee_id: str, active_only: bool = True) -> list[dict]:
    raise NotImplementedError


def members(group_id: str) -> list[dict]:
    raise NotImplementedError


def grant(employee_id, group_id, *, request_id, key, stamp, actor, expires_at=None) -> dict:
    raise NotImplementedError


def revoke(employee_id, group_id, *, request_id, key, stamp, actor, reason) -> dict:
    raise NotImplementedError


def by_request(request_id: str) -> list[dict]:
    raise NotImplementedError
