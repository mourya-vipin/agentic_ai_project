"""The failures a tool can report, in Lecture 3's three classes (slide 33).

Owner: Vipin Mourya.

    TransientError    a rate limit, a locked file: the registry retries it in code
    PermanentError    a missing record, a bad value, a refused action: retrying cannot help;
                      the message carries the repair fact the model needs
    ProtocolError     an unknown tool or a malformed request: for us, not the model

Every message says what was wrong, what would be right, and whether a retry can work.
"""

from __future__ import annotations


class ToolError(Exception):
    error_class = "permanent"
    retryable = False

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TransientError(ToolError):
    error_class = "transient"
    retryable = True


class PermanentError(ToolError):
    error_class = "permanent"


class ProtocolError(ToolError):
    error_class = "protocol"
