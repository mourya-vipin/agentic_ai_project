"""Fake credentials. No tool ever returns a secret; only the product page's reveal does.
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def issue(employee_id, system_id, *, key, stamp) -> str:
    raise NotImplementedError


def reveal(credential_id: str) -> str:
    raise NotImplementedError


def for_employee(employee_id: str) -> list[dict]:
    raise NotImplementedError
