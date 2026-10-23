"""One function per canonical tool, (args, ctx) -> dict, wrapping the sandbox/
modules. No function returns a credential's secret.

Owner: Vipin Mourya.
"""

from __future__ import annotations

TOOLS: dict = {}
