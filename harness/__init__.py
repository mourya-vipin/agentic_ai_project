"""The project harness: the parts every team shares. You build on these; you do not edit them.

    settings   settings.yaml: provider, model, key variable, ceilings
    pricing    the dated price sheet, pricing.yaml
    client     the metered model client, with a hard ceiling
    backends   the real model providers, with rate-limit and quota handling
    scripted   scripted models, for testing without spending tokens
    schema     checks a JSON value against a schema (your plan's, your tools')
    formats    the shared names: run outcomes, the trace lines the harness writes
    trace      the session-scoped trace writer
    switches   switches.yaml, loaded at start-up, and run.py's --set overrides
    runtime    what your agent asks the harness for: its client, its trace, the switches, the run or session
    turns      the one path to the agent: the app and run.py both call agent/service.py through it
    tasks      reads and checks the task files in tasks/
    records    the run records in results/<arm>/
    runner     run.py's engine: reset, seed, play the scripted people, check, record
    summarize  the arm table: python -m harness.summarize results/<arm> ... [--measure <name>]
    check      the project's structure checks: make check
    seed       make seed: reset the sandbox and seed it
    ping       make ping: two tiny model calls
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
