"""Strict mode, as sent (Lecture 3, slides 20 and 21; Part 1, task 3). Owner: Vipin Mourya.

    python scripts/strict_mode_check.py            # one model call, about 1,000 tokens

Sends one request carrying every tool the decision table makes strict (each strict.<tool>
switch set on), with the definitions exactly as the registry sends them, and keeps the
vendor's answer: it accepts them, or names what it refuses. Writes
docs/milestones/m2_strict_mode_<date>.json with the date, the model, the tools as sent,
the answer, and the MCP SDK with the protocol revision it implements, checked the same day.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.registry import Registry  # noqa: E402
from harness import runtime  # noqa: E402

OUT = ROOT / "docs" / "milestones"


def strict_tools() -> list[str]:
    return sorted(name.split(".", 1)[1] for name, setting in runtime.switches().items()
                  if name.startswith("strict.") and setting == "on")


def main() -> int:
    runtime.configure()
    names = strict_tools()
    if not names:
        print("No strict.<tool> switch is on: nothing to send.")
        return 1
    sent = Registry().definitions(names)
    record = {
        "checked": date.today().isoformat(),
        "provider": runtime._cfg().settings.provider,
        "model": runtime._cfg().settings.model,
        "strict_switches_on": names,
        "tools_as_sent": sent,
        "mcp_sdk": {"package": "mcp", "version": importlib.metadata.version("mcp"),
                    "revision": __import__("mcp_types").LATEST_PROTOCOL_VERSION, "checked": date.today().isoformat()},
    }
    client = runtime.client("strict-mode-check")
    try:
        response = client.create(system="You check tool definitions. Answer in one word.",
                                 messages=[{"role": "user", "content": "Reply with the word OK. Call no tool."}],
                                 tools=sent)
        record["answer"] = "accepted"
        record["reply"] = response.text
        record["stop_reason"] = response.stop_reason
    except Exception as exc:          # the vendor's refusal is the answer we keep
        record["answer"] = "refused"
        record["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    record["tokens"] = client.total_tokens
    path = OUT / f"m2_strict_mode_{record['checked']}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"{record['answer']}: {', '.join(names)} on {record['model']} ({record['tokens']} tokens) -> "
          f"{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
