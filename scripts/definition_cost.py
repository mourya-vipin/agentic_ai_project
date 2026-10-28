"""What the tool definitions cost (Lecture 3, slide 23): the same one-message call
with and without them, the difference in the input tokens the API reports.
Owner: Vipin Mourya.

    python scripts/definition_cost.py                 # prints, and writes docs/milestones/m1_definition_cost.json
                                                      # and a dated copy, m1_definition_cost_<date>.json
    python scripts/definition_cost.py --calls 6.2     # also prices a run that carries them on 6.2 model calls

It builds the model client exactly as harness/ping.py does and sends the
definitions as our registry sends them (its fixed order; strict and examples as
the switches set them, once the rewrite has them). The first draft
(agent/tools/definitions_v1.py) and, when it exists, the rewrite
(agent/tools/definitions.py) are each measured. Each call is one short message, so
the only thing that differs between the calls is the definitions: the difference
is their cost, not a whole-run difference with other things changing beside it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import ROOT  # noqa: E402
from harness.backends import make_backend  # noqa: E402
from harness.client import ModelClient  # noqa: E402
from harness.pricing import PriceSheet  # noqa: E402
from harness.settings import load_settings  # noqa: E402

SYSTEM = "You are measuring a request's size. Answer with the single word ok."
MESSAGES = [{"role": "user", "content": "Answer with the single word ok. Do not call a tool."}]
OUT = ROOT / "docs" / "milestones" / "m1_definition_cost.json"


def definition_sets() -> dict[str, list[dict]]:
    from agent.registry import Registry
    from agent.tools import definitions_v1
    # the first draft as it was sent in the dev-v1 runs: no strict mode, no examples
    sets = {"first_draft": Registry(definitions_v1.DEFINITIONS, definitions_v1.ALIASES,
                                    apply_switches=False).definitions()}
    try:
        rewrite = importlib.import_module("agent.tools.definitions")
    except ModuleNotFoundError:
        return sets
    sets["rewrite"] = Registry(rewrite.DEFINITIONS, getattr(rewrite, "ALIASES", {})).definitions()
    return sets


def prompt_tokens(client: ModelClient, tools: list[dict] | None) -> tuple[int, float | None]:
    response = client.create(system=SYSTEM, messages=MESSAGES, tools=tools or None)
    return response.usage.prompt_tokens, response.cost_usd


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calls", type=float, default=None,
                   help="mean model calls per run that carry the definitions (from the baseline's traces)")
    a = p.parse_args(argv)
    settings = load_settings(ROOT / "settings.yaml")
    prices = PriceSheet(ROOT / "pricing.yaml")
    client = ModelClient(make_backend(settings.provider, settings), prices)
    price = prices.find(settings.model)

    base, _ = prompt_tokens(client, None)
    report = {"model": settings.model, "date": date.today().isoformat(), "no_tools_prompt_tokens": base,
              "sets": {}, "method": "one-message call with and without the definitions; difference in prompt tokens"}
    for name, tools in definition_sets().items():
        total, _ = prompt_tokens(client, tools)
        each = {}
        for tool in tools:
            alone, _ = prompt_tokens(client, [tool])
            each[tool["name"]] = alone - base
        entry = {"tools": len(tools), "definition_tokens": total - base, "per_tool": each}
        if len(tools) > 1:
            # Each tool alone carries the vendor's fixed overhead for a request with tools; all
            # together carry it once. So the sum alone minus the total is (n - 1) overheads.
            overhead = (sum(each.values()) - (total - base)) / (len(tools) - 1)
            entry["fixed_overhead_tokens"] = round(overhead, 1)
            entry["per_tool_without_overhead"] = {k: round(v - overhead, 1) for k, v in each.items()}
        if a.calls is not None:
            entry["per_run_tokens"] = round((total - base) * a.calls, 1)
        report["sets"][name] = entry
    report["spent_usd"] = round(client.cost_usd, 6)
    report["input_price_per_million"] = getattr(price, "input", None)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    # each measurement keeps a file of its own, so every figure the write-up cites can be read again
    OUT.with_name(f"{OUT.stem}_{report['date']}.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, entry in report["sets"].items():
        line = f"{name}: {entry['tools']} tools, {entry['definition_tokens']} tokens of definitions per call"
        if "per_run_tokens" in entry:
            line += f", {entry['per_run_tokens']} per run at {a.calls} calls"
        print(line)
    print(f"(no-tools call: {base} prompt tokens; measuring spent ${report['spent_usd']:.6f}; written to {OUT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
