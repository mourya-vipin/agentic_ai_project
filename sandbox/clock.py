"""The sandbox's fixed "now" (2026-10-21T09:00:00Z), so every run repeats.
Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


def now() -> str:
    raise NotImplementedError


def today() -> str:
    raise NotImplementedError
