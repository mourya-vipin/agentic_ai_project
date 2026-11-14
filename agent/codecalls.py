"""Code that calls the tools (Lecture 3, slides 26 and 27; Part 1, task 4). Owner: Vipin Mourya.

For a use case that loops over many results (U5: "anything unusual in today's requests", which
filters every request of the day down to a few), the model may write one short program instead
of calling the tools one at a time:

    rows = call("requests_list_today")["requests"]
    result = [r["id"] for r in rows if r["requester_status"] != "active"]

The program runs in a process of its own (python -I -S, an empty environment, a scratch folder as
its working directory, a CPU limit and a wall-clock limit). It has no import, no open(), no
network and no names starting with an underscore; the checks below stop a program that reaches for
them before it runs. Its one way out is call(tool, **args), which comes back to this process and
goes through the registry like any other call: the boundary, the hook (origin "program"), the key
for a write, the MCP server and the call record. Only the program's final `result` enters the
model's context; every intermediate result stays in the trace.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from agent import disclosure
from agent.errors import PermanentError
from harness import runtime

TOOL = "code_run"
MAX_PROGRAM_CHARS = 4000
MAX_CALLS = 50
WALL_S = 20.0
CPU_S = 5
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview", "type", "object", "super",
                "classmethod", "staticmethod", "property"}
BANNED_ATTRS = {"gi_frame", "f_globals", "f_locals", "f_builtins", "f_back", "tb_frame", "cr_frame", "ag_frame",
                "func_globals", "mro"}
SAFE_BUILTINS = ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset", "int", "isinstance",
                 "len", "list", "map", "max", "min", "print", "range", "reversed", "round", "set", "sorted", "str",
                 "sum", "tuple", "zip", "ValueError", "KeyError", "TypeError", "Exception")

# the child: reads the program, runs it with only call() and SAFE_BUILTINS, one JSON line per message
RUNNER = r'''
import json, sys, traceback
_out, _in = sys.stdout, sys.stdin
def _send(o):
    _out.write(json.dumps(o, default=str) + "\n"); _out.flush()
class ToolFailed(Exception):
    pass
def call(tool, **args):
    _send({"call": tool, "args": args})
    reply = json.loads(_in.readline())
    if "error" in reply:
        raise ToolFailed(reply["error"])
    return reply["data"]
start = json.loads(_in.readline())
b = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
safe = {n: b[n] for n in start["builtins"]}
g = {"__builtins__": safe, "call": call, "ToolFailed": ToolFailed}
sys.stdout = sys.stderr      # print() inside the program never reaches the protocol
try:
    exec(compile(start["program"], "<program>", "exec"), g)
    _send({"done": g.get("result")})
except BaseException as exc:
    line = next((f.lineno for f in reversed(traceback.extract_tb(exc.__traceback__)) if f.filename == "<program>"), None)
    _send({"raised": type(exc).__name__, "message": str(exc)[:300], "line": line})
'''


def check(program: str) -> list[str]:
    """What stops a program before it runs: no import, no file, no network, no way around call()."""
    if len(program) > MAX_PROGRAM_CHARS:
        return [f"the program is {len(program)} characters; the most is {MAX_PROGRAM_CHARS}"]
    try:
        tree = ast.parse(program, "<program>")
    except SyntaxError as exc:
        return [f"line {exc.lineno}: {exc.msg}"]
    problems = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ", ".join(a.name for a in node.names) if isinstance(node, ast.Import) else node.module
            problems.append(f"line {line} imports {names}: a program has no imports, so no network and no files")
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("_")):
            problems.append(f"line {line} uses {node.id}, which a program does not have"
                            + (": it cannot read or write files" if node.id == "open" else ""))
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in BANNED_ATTRS):
            problems.append(f"line {line} reaches for .{node.attr}, which a program may not use")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.Lambda)):
            problems.append(f"line {line}: {type(node).__name__} is not allowed in a program")
    return problems


def _limits():           # in the child, before it runs: a CPU limit and no child processes of its own
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_S, CPU_S))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        pass


def run(program: str, ctx, registry, allowed: list[str]) -> dict:
    """Run one program. Returns {"result", "calls", "tools"}; raises PermanentError with a repair
    fact when the program is stopped, raises, runs too long or calls a tool it may not."""
    program = str(program or "")
    problems = check(program)
    trace = runtime.trace(ctx.session_id)
    if problems:
        trace.event("program", status="stopped", problems=problems, run_key=ctx.run_key)
        raise PermanentError("The program was stopped before it ran: " + "; ".join(problems[:3])
                             + ". A program only calls your tools with call(\"tool_name\", arg=value) and sets "
                               "result. Fix it and call code_run again.")
    allowed = [t for t in allowed if t not in (TOOL, "tools_search")]
    calls, used, start = 0, [], time.monotonic()
    with tempfile.TemporaryDirectory(prefix="program-") as scratch:
        child = subprocess.Popen([sys.executable, "-I", "-S", "-c", RUNNER], cwd=scratch, env={},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 preexec_fn=_limits if os.name == "posix" else None)
        timer = threading.Timer(WALL_S, child.kill)      # a program that blocks or loops is killed on time
        timer.start()
        try:
            child.stdin.write(json.dumps({"program": program, "builtins": SAFE_BUILTINS}) + "\n")
            child.stdin.flush()
            while True:
                if time.monotonic() - start > WALL_S:
                    raise _Stop(f"it ran past {WALL_S:g} s")
                line = child.stdout.readline()
                if not line:
                    raise _Stop(f"it ran past {WALL_S:g} s" if time.monotonic() - start >= WALL_S else
                                f"it ended without a result (it may have used more than {CPU_S} s of CPU)")
                message = json.loads(line)
                if "done" in message:
                    break
                if "raised" in message:
                    where = f" at line {message['line']}" if message.get("line") else ""
                    trace.event("program", status="raised", error=message["raised"], line=message.get("line"),
                                calls=calls, run_key=ctx.run_key)
                    raise PermanentError(f"Your program raised {message['raised']}{where}: {message['message']}. "
                                         "Nothing after that line ran; fix it and call code_run again.")
                calls += 1
                if calls > MAX_CALLS:
                    raise _Stop(f"it made more than {MAX_CALLS} tool calls")
                child.stdin.write(json.dumps(_one_call(message, ctx, registry, allowed, used)) + "\n")
                child.stdin.flush()
        except _Stop as stop:
            trace.event("program", status="stopped", problems=[str(stop)], calls=calls, run_key=ctx.run_key)
            raise PermanentError(f"The program was stopped: {stop}. Write a shorter one and call code_run again.")
        finally:
            timer.cancel()
            if child.poll() is None:
                child.kill()
            child.wait()
    trace.event("program", status="done", calls=calls, tools=sorted(set(used)), run_key=ctx.run_key)
    return {"result": message.get("done"), "calls": calls, "tools": sorted(set(used))}


class _Stop(Exception):
    pass


def _one_call(message: dict, ctx, registry, allowed: list[str], used: list[str]) -> dict:
    """One call(tool, **args) from the program, through the registry like any other call."""
    from agent.registry import CallContext

    tool = str(message.get("call") or "")
    args = message.get("args") if isinstance(message.get("args"), dict) else {}
    canon = registry.resolve(tool) or tool
    if canon not in allowed:
        return {"error": f"{tool} is not a tool this program may call; it may call {', '.join(allowed)}"}
    used.append(canon)
    inner = CallContext(ctx.session_id, ctx.step, ctx.caller, ctx.use_case, request_id=ctx.request_id,
                        run_key=ctx.run_key, origin="program")
    result = registry.call(canon, disclosure.stamp_call(canon, args), inner)
    return {"error": result.content} if result.is_error else {"data": result.data}
