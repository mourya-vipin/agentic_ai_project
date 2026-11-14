"""What the tool definitions cost (Lecture 3, slide 23): the same one-message call
with and without them, the difference in the input tokens the API reports.
Owner: Vipin Mourya.

    python scripts/definition_cost.py                 # prints, and writes docs/milestones/m1_definition_cost.json
                                                      # and a dated copy, m1_definition_cost_<date>.json
    python scripts/definition_cost.py --calls 6.2     # also prices a run that carries them on 6.2 model calls
    python scripts/definition_cost.py --per-run results/baseline \
        --measured docs/milestones/m1_definition_cost_2026-10-25.json   # no model call: per run, from the traces

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


def per_run(arms: list[str], measured: Path, settings, prices) -> int:
    """What the definitions cost per run in recorded arms, priced by what each loop call carried:
    a loop call carries its use case's tools (agent/router.py: TOOLS), each at its own measured cost
    plus the fixed overhead once; routing calls carry none, and the planned path's calls only the
    plan tool. Reads the run records and traces and a saved measurement; no model call."""
    from agent.router import TOOLS
    from agent.tools import definitions_v1
    report = json.loads(measured.read_text())
    first_draft_name = {canon: name for name, canon in definitions_v1.ALIASES.items()}
    carried = {}            # set -> use case -> tokens one loop call carries
    for name, entry in report["sets"].items():
        own, overhead = entry["per_tool_without_overhead"], entry["fixed_overhead_tokens"]
        named = (lambda t: first_draft_name.get(t, t)) if name == "first_draft" else (lambda t: t)
        carried[name] = {uc: round(overhead + sum(own[named(t)] for t in tools)) for uc, tools in TOOLS.items()}
    price = prices.find(settings.model)
    print(f"priced from {measured.relative_to(ROOT)} ({report['date']}); a loop call carries, in tokens: "
          + "; ".join(f"{name} " + ", ".join(f"{uc} {n:,}" for uc, n in sets.items()) for name, sets in carried.items()))
    for arm in arms:
        latest = {}
        for f in sorted(Path(arm).glob("*.json")):
            record = json.loads(f.read_text())
            if record.get("finished"):
                slot = (record["task"], record["sweep"])
                if slot not in latest or record["started"] > latest[slot][0]["started"]:
                    latest[slot] = (record, f.with_name(f.stem + ".trace.jsonl"))
        loops, prompt, cached, by_task = {}, 0, 0, {}
        for record, trace in latest.values():
            task = by_task.setdefault(record["task"], {"runs": 0, "prompt": 0, "loops": {}})
            task["runs"] += 1
            for line in trace.read_text().splitlines():
                call = json.loads(line)
                if call.get("type") != "model_call":
                    continue
                usage = call.get("usage") or {}
                prompt += usage.get("prompt_tokens", 0)
                cached += usage.get("cache_read_input_tokens", 0)
                task["prompt"] += usage.get("prompt_tokens", 0)
                if call.get("purpose") in TOOLS:
                    loops[call["purpose"]] = loops.get(call["purpose"], 0) + 1
                    task["loops"][call["purpose"]] = task["loops"].get(call["purpose"], 0) + 1
        runs = len(latest)
        if not runs:
            print(f"{arm}: no finished runs")
            continue
        calls = sum(r["model_calls"] for r, _ in latest.values())
        cost = sum(r["cost_usd"] for r, _ in latest.values()) / runs
        print(f"\n{arm}: {runs} runs, {calls} model calls, {sum(loops.values())} loop calls carried definitions ("
              + ", ".join(f"{uc} {n}" for uc, n in sorted(loops.items())) + f"); mean run cost ${cost:.4f}; "
              f"{cached / prompt:.1%} of its prompt tokens were cache reads")
        for name, sets in carried.items():
            total = sum(sets[uc] * n for uc, n in loops.items())
            each = total / runs
            print(f"  {name}: {each:,.1f} tokens per run, {total / prompt:.1%} of the arm's prompt tokens; "
                  f"${each * price.cache_read / 1e6:.4f} if all read from the cache, "
                  f"${each * price.input / 1e6:.4f} if none")
            print("    per task: " + "; ".join(
                f"{t} {sum(sets[uc] * n for uc, n in d['loops'].items()) / d['runs']:,.0f}"
                f" ({sum(sets[uc] * n for uc, n in d['loops'].items()) / d['prompt']:.1%})"
                for t, d in sorted(by_task.items()) if d["loops"]))
        if {"first_draft", "rewrite"} <= set(carried):
            extra = sum((carried["rewrite"][uc] - carried["first_draft"][uc]) * n for uc, n in loops.items()) / runs
            print(f"  rewrite minus first_draft on the same calls: {extra:,.1f} tokens per run, "
                  f"${extra * price.cache_read / 1e6:.4f} to ${extra * price.input / 1e6:.4f}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calls", type=float, default=None,
                   help="mean model calls per run that carry the definitions (from the baseline's traces)")
    p.add_argument("--per-run", nargs="+", metavar="ARM", default=None,
                   help="results/<arm> folders: price their runs' loop calls from --measured, with no model call")
    p.add_argument("--measured", type=Path, default=None,
                   help="the dated measurement to price with, such as docs/milestones/m1_definition_cost_2026-10-25.json")
    p.add_argument("--out", type=Path, default=OUT,
                   help="where to write the measurement (and its dated copy beside it), such as "
                        "docs/milestones/m2_definition_cost.json; Milestone 1's file by default")
    a = p.parse_args(argv)
    out = a.out.resolve()
    settings = load_settings(ROOT / "settings.yaml")
    prices = PriceSheet(ROOT / "pricing.yaml")
    if a.per_run:
        if a.measured is None:
            p.error("--per-run needs --measured: the definitions the runs carried")
        return per_run(a.per_run, a.measured.resolve(), settings, prices)
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

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    # each measurement keeps a file of its own, so every figure the write-up cites can be read again
    out.with_name(f"{out.stem}_{report['date']}.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, entry in report["sets"].items():
        line = f"{name}: {entry['tools']} tools, {entry['definition_tokens']} tokens of definitions per call"
        if "per_run_tokens" in entry:
            line += f", {entry['per_run_tokens']} per run at {a.calls} calls"
        print(line)
    print(f"(no-tools call: {base} prompt tokens; measuring spent ${report['spent_usd']:.6f}; written to {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
