"""The work waiting, for the app's queue page.

waiting() returns one dict per item your system has not finished: a new message
to handle, a request on hold, a record to review. Each needs an `id`; `summary`,
`status` and `since` are shown if present, and any other field is shown too.
The queue page adds the threads that wait for an outside reply and anything
waiting for approval, so leave those out.
"""

from __future__ import annotations


def waiting() -> list[dict]:
    """The starter's sandbox holds nothing yet."""
    return []
