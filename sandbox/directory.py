"""The staff directory (reads only). Owner: Vipin Mourya.
Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def get_employee(employee_id: str) -> dict | None:
    raise NotImplementedError


def by_email(email: str) -> dict | None:
    raise NotImplementedError


def search_employees(query: str, dept: str | None = None, limit: int = 10) -> list[dict]:
    raise NotImplementedError


def manager_of(employee_id: str) -> dict | None:
    raise NotImplementedError


def contractors_ended(before: str) -> list[dict]:
    raise NotImplementedError
