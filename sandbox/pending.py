"""Pending records: what sits below the approval line, for the operator to do by hand.
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def create(kind, *, request_id, employee_id, key, stamp, action=None, group_id=None,
           thread_id=None, body=None, reason=None) -> str:
    raise NotImplementedError


def open_items(kind: str | None = None) -> list[dict]:
    raise NotImplementedError


def by_request(request_id: str) -> list[dict]:
    raise NotImplementedError


def close(pending_id, status, *, stamp) -> None:
    raise NotImplementedError
