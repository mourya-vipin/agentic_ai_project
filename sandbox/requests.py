"""Access requests, one per message the seed puts on the channel.
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def create(thread_id, source, text, *, requester_id=None, subject_id=None) -> str:
    raise NotImplementedError


def get(request_id: str) -> dict | None:
    raise NotImplementedError


def by_thread(thread_id: str) -> dict | None:
    raise NotImplementedError


def open_requests(day: str | None = None) -> list[dict]:
    raise NotImplementedError


def set_status(request_id, status, *, stamp, note="") -> dict:
    raise NotImplementedError


def set_route(request_id, route, *, stamp) -> None:
    raise NotImplementedError
