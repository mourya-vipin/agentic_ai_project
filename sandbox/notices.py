"""Template notices: the only code that writes on the channel. Checks its key before
every send.

Owner: Vipin Mourya. Signatures as agreed; bodies come with the sandbox."""

from __future__ import annotations


TEMPLATES: dict[str, str] = {}


def send_template(thread_id, template_id, facts: dict, *, key, stamp) -> dict:
    raise NotImplementedError


def send_as_operator(pending_id) -> dict:
    raise NotImplementedError
