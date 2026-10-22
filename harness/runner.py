"""run.py's engine: one arm, over a set of tasks, for some sweeps.

For every run, the runner (not your code, not the model):
  - empties the channel and calls your sandbox's reset(), then seed(task), so the
    run starts from that task's inputs alone; what the seed writes on the channel
    is marked seeded;
  - plays the task's people through harness.turns, the path the app takes: each
    operator turn goes to agent.service.operator_turn; when a turn ends waiting on
    a thread (Reply.waiting_for), that thread's scripted stranger answers, and the
    answer goes to agent.service.on_outside_reply; a stranger answers a turn
    waiting in channel.wait_for_reply at once, and that answer is not delivered
    again; a stranger never asked sends their never_asked message after the
    operator's last turn; and the task's scripted approvals answer what
    agent.service.pending_approvals lists;
  - meters every model call on the run's behalf with one client, against the
    harness's hard ceiling, and writes the run's trace beside its record;
  - runs the task's check, which reads the sandbox, and writes the record,
    results/<arm>/<run_id>.json (harness/records.py lists its fields). A run on a
    scripted model (never-stops, harness.scripted:..., or a module:Class of your
    own that says free = True) is a test on a fake model, not evidence: its record
    goes to results/_free/<arm>/, which make check and the summarizer leave out.

Running the same command again skips each test task and sweep the arm has finished,
and warns if they ran from a commit whose code differs; it redoes the rest: a run the daily
quota or a rate limit stopped, or one whose agent was not built yet, is not
finished. An arm holds one configuration: if results/<arm>/ has runs with other
switch settings, another backend or another model, the runner refuses, and you name
a new arm. discard() moves an arm's folder to results/discarded/, to start it over
after a code change.

Runs are one at a time, in their own state folder (state/runs/), so a sweep never
touches the app's data. --jobs N runs N at once, each worker in a state folder of
its own, if sandbox/seed.py says PARALLEL_SAFE = True.
"""

from __future__ import annotations

import importlib
import os
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from harness import ROOT, records, runtime, tasks, turns
from harness import switches as switch_file
from harness.backends import REAL_BACKENDS, DailyQuotaReached, RateLimited
from harness.client import BudgetExceeded
from harness.paths import STATE_ENV, state_dir
from harness.pricing import PriceSheet
from harness.settings import load_settings
from harness.trace import SessionTrace, now

FREE_BACKENDS = {"never-stops"}
SCRIPTED_PREFIX = "harness.scripted:"
MAX_DELIVERIES = 20      # outside replies handed to the agent after one turn, at most
QUOTA_MESSAGE = "daily limit reached; run the same command again when it resets; finished runs are kept"


class RunError(SystemExit):
    pass


def is_free(backend: str) -> bool:
    """Whether runs on this backend are tests on a fake model, not evidence: never-stops,
    harness.scripted:..., or a module:Class of your own whose class (or object) says
    free = True. Raises RunError when a module:Class cannot be loaded."""
    if backend in FREE_BACKENDS or backend.startswith(SCRIPTED_PREFIX):
        return True
    if ":" not in backend:
        return False
    return getattr(_backend_target(backend), "free", False) is True


def _backend_target(backend: str):
    module, _, name = backend.partition(":")
    try:
        return getattr(importlib.import_module(module), name)
    except Exception as exc:  # a typo, or the module's own error: said plainly, before any run
        raise RunError(f"--backend {backend}: cannot load it ({type(exc).__name__}: {exc}). Name a class as "
                       "module:Class, such as tests.fakes:BadPlanner") from exc


def _sandbox():
    from sandbox import seed
    return seed


# -- one run ------------------------------------------------------------------------------

@dataclass
class Played:
    """What the scripted people did in one run."""
    people: list = field(default_factory=list)
    approvals: dict = field(default_factory=lambda: {"asked": 0, "answered": 0, "unanswered": 0})
    notes: list = field(default_factory=list)


def play(task: dict, run_id: str, played: Played | None = None) -> Played:
    """Play a task's people through harness.turns, the path the app takes. Raises what
    the agent raises; `played` keeps what happened up to then."""
    import channel
    from harness import turns

    played = played if played is not None else Played()
    script = channel._script
    played.people = script.install(task.get("outside") or [])
    answers = tasks.scripted_answers(task)
    try:
        for text in task["operator"]:
            _after(turns.operator_turn(run_id, text), script, answers, played)
        for person in played.people:
            if person.asked == 0 and person.never_asked is not None:
                thread = _concrete_thread(channel, person)
                if thread is None:
                    played.notes.append(f"{person.name} was never asked, but their thread {person.thread!r} "
                                        "is a pattern no message matches; they stayed silent")
                    continue
                person.wrote_unasked += 1
                _after(turns.outside_reply(thread, person.never_asked, sender=person.name), script, answers, played)
    finally:
        for thread in script.threads_written:
            person = script.person(thread)
            if person is not None and person.asked == 0:
                played.notes.append(f"the agent wrote on {thread} without waiting for a reply there, so its "
                                    "scripted stranger did not answer (right for a notice; for a question, end the "
                                    "turn with Reply(waiting_for=thread_id))")
        script.clear()
    return played


def run_one(task: dict, sweep: int, arm: str, *, backend: str, switch_settings: dict,
            overrides: list[str] | None = None, root: Path = ROOT, folder: Path | None = None) -> dict:
    import channel

    settings = load_settings(root / "settings.yaml")
    prices = PriceSheet(root / "pricing.yaml")
    folder = Path(folder or records.arm_dir(arm, free=is_free(backend)))
    task_id = task["id"]
    run_id = f"{arm}-{runtime.safe_name(task_id)}-s{sweep:02d}-{uuid.uuid4().hex[:6]}"
    trace_path = folder / f"{run_id}.trace.jsonl"
    trace = SessionTrace(trace_path, run_id, run_id=run_id)
    model = settings.model if backend in REAL_BACKENDS else None
    record = {
        "run_id": run_id, "arm": arm, "task": task_id, "task_file": task.get("file"), "sweep": sweep,
        "started": now(), "switches": dict(switch_settings), "overrides": list(overrides or []),
        "backend": backend, "model": model, "free": is_free(backend),
        "settings": settings.digest(), "price_sheet": prices.read_date, **records.source(root),
    }
    scope = runtime.RunScope(run_id, arm, task_id, sweep, trace, backend, dict(switch_settings), settings, prices)
    played = Played()
    outcome, error, latency = "completed", None, 0.0
    with runtime.run_scope(scope):
        trace.write({"type": "run_start", **{k: v for k, v in record.items() if k != "started"}})
        try:
            channel.reset()
            sandbox = _sandbox()
            with channel.seeding():
                sandbox.reset()
                sandbox.seed(task)
            trace.write({"type": "sandbox", "reset": True, "seeded": task_id, "state_dir": str(state_dir())})
        except Exception as exc:
            outcome, error = "crashed", f"the sandbox's reset or seed raised {type(exc).__name__}: {exc}"
        if outcome == "completed":
            start = time.monotonic()
            try:
                play(task, run_id, played)
            except NotImplementedError as exc:
                outcome, error = "not_built", f"NotImplementedError: {exc}"
            except DailyQuotaReached as exc:
                outcome, error = "quota", f"DailyQuotaReached: {exc}"
            except RateLimited as exc:
                outcome, error = "rate_limited", f"RateLimited: {exc}"
            except BudgetExceeded as exc:
                outcome, error = "token_budget", f"BudgetExceeded: {exc}"
            except Exception as exc:  # the sweep goes on; the trace and the record keep the evidence
                outcome, error = "crashed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"
            latency = time.monotonic() - start
        totals = scope.totals()
        wait, throttled = scope.wait_s, scope.throttled_s
    lines = trace.lines()
    replies = [{k: x.get(k) for k in ("function", "text", "actions", "waiting_for")}
               for x in lines if x.get("type") == "reply"]
    turn_latency = [round(max(0.0, float(x.get("latency_s") or 0.0) - float(x.get("wait_s") or 0.0)
                              - float(x.get("throttled_s") or 0.0)), 3)
                    for x in lines if x.get("type") == "reply" and x.get("function") in turns.TURNS]
    check = run_check(task, run_id, arm, sweep, root, replies=replies, trace_path=trace_path)
    measures = check.pop("measures")
    reasons = end_reasons(lines, outcome)
    record.update({
        "finished": now(),
        "model": model or (", ".join(totals["models"]) or None),
        "tokens": {"input": totals["input_tokens"], "output": totals["output_tokens"],
                   "cache_read": totals["cache_read_tokens"], "cache_write": totals["cache_write_tokens"],
                   "total": totals["total_tokens"]},
        "cost_usd": totals["cost_usd"], "unpriced_calls": totals["unpriced_calls"],
        "model_calls": totals["model_calls"],
        "latency_s": round(latency, 3), "wait_s": round(wait, 3), "throttled_s": round(throttled, 3),
        "latency_without_wait_s": round(max(0.0, latency - wait - throttled), 3), "turn_latency_s": turn_latency,
        "outcome": outcome, "end_reasons": reasons, "end_reason": reasons[-1] if reasons else None,
        "error": error,
        "outside": [{"thread": p.thread, "who": p.name, "asked": p.asked, "answered": p.answered,
                     "wrote_unasked": p.wrote_unasked} for p in played.people],
        "approvals": played.approvals, "notes": played.notes,
        "check": check, "passed": bool(check["passed"]), "measures": measures,
        "trace": str(trace_path.relative_to(root)) if trace_path.is_relative_to(root) else str(trace_path),
        "tool_calls": [{k: v for k, v in x.items() if k not in ("at", "type", "session_id", "run_id")}
                       for x in lines if x.get("type") == "tool_call"],
        "replies": replies,
    })
    trace.write({"type": "run_end", "outcome": outcome, "end_reason": record["end_reason"], "passed": record["passed"],
                 "tokens": record["tokens"], "latency_s": record["latency_s"], "wait_s": record["wait_s"],
                 "throttled_s": record["throttled_s"], "error": error})
    records.write(record, folder)
    return record


def _after(reply, script, answers, played: Played) -> None:
    """After a turn: while the turn's Reply waits on a thread, that thread's scripted
    stranger answers, through on_outside_reply; then the operator's scripted answers
    to what waits for approval; until nothing is left."""
    from harness import turns

    for _ in range(MAX_DELIVERIES):
        if reply is not None and reply.waiting_for:
            person = script.person(reply.waiting_for)
            answer = script.answer(reply.waiting_for) if person else None
            if person is None:
                played.notes.append(f"the agent waits on {reply.waiting_for}, where no scripted stranger writes")
            if answer is not None:
                reply = turns.outside_reply(reply.waiting_for, answer[1], sender=answer[0])
                continue
        try:
            pending = turns.pending_approvals()
        except NotImplementedError:
            pending = []
        if not pending:
            return
        played.approvals["asked"] += len(pending)
        if not answers:
            played.approvals["unanswered"] += len(pending)
            return
        approved = answers.pop(0)
        played.approvals["answered"] += 1
        reply = turns.decide_approval(str(pending[0].get("id")), approved, "scripted answer from the task file")
    played.notes.append(f"stopped handing replies to the agent after {MAX_DELIVERIES}")


def _concrete_thread(channel, person) -> str | None:
    if not any(c in person.thread for c in "*?["):
        return person.thread
    found = [t["thread_id"] for t in channel.threads() if person.matches(t["thread_id"])]
    return found[0] if found else None


def end_reasons(lines: list[dict], outcome: str) -> list[str]:
    """Every end reason the run's trace.end(...) calls wrote, in order, and token_budget
    last when the harness's ceiling stopped the run. The record's end_reason is the last."""
    ends = [x.get("terminal_reason") for x in lines if x.get("type") == "end"]
    return ends + ["token_budget"] if outcome == "token_budget" else ends


def run_check(task: dict, run_id: str, arm: str, sweep: int, root: Path = ROOT, *,
              replies: list | None = None, trace_path: Path | None = None) -> dict:
    """The task's check, on this run. It reads the sandbox; a check that raises fails the run.
    It returns True or False, (passed, reason), or (passed, reason, measures): measures is
    a dict of named counts, kept in the record as `measures`."""
    spec = task["check"]
    measures: dict = {}
    try:
        fn = tasks.check_function(spec, root)
        result = fn(tasks.RunInfo(run_id, task, arm, sweep, state_dir(), list(replies or []), trace_path))
        if isinstance(result, tuple):
            passed, reason = bool(result[0]), str(result[1]) if len(result) > 1 else ""
            if len(result) > 2:
                measures = _measures(result[2])
        else:
            passed, reason = bool(result), ""
    except Exception as exc:
        passed, reason, measures = False, f"the check raised {type(exc).__name__}: {exc}", {}
    return {"function": spec, "passed": passed, "reason": reason, "measures": measures}


def _measures(value) -> dict:
    """A check's named counts: a dict of names to numbers (True and False count as 1 and 0)."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and k.strip() for k in value) \
            or not all(isinstance(v, (int, float)) for v in value.values()):
        raise ValueError(f"its measures must be a dict of names to numbers, such as {{'questions_asked': 2}}; "
                         f"it returned {value!r}"[:300])
    return {k: int(v) if isinstance(v, bool) else v for k, v in value.items()}


# -- an arm ---------------------------------------------------------------------------------

def prepare(tasks_targets: list[str], arm: str, overrides: list[str] | None, backend: str | None,
            root: Path = ROOT) -> tuple[list[dict], dict, str]:
    """The tasks, the switch settings and the backend for an arm, or a RunError saying what is wrong."""
    try:
        records.arm_dir(arm)
    except ValueError as exc:
        raise RunError(str(exc)) from exc
    try:
        settings = switch_file.apply(switch_file.load(root / "switches.yaml"), overrides)
    except switch_file.SwitchError as exc:
        raise RunError(str(exc)) from exc
    files = []
    for target in tasks_targets:
        try:
            files += tasks.task_files(Path(target) if Path(target).is_absolute() else root / target)
        except tasks.TaskError as exc:
            raise RunError(str(exc)) from exc
    if not files:
        raise RunError(f"no task files in {', '.join(tasks_targets)}")
    loaded, problems = [], []
    for path in files:
        try:
            loaded.append(tasks.load(path, root))
        except tasks.TaskError as exc:
            problems.append(str(exc))
    if problems:
        raise RunError("fix these task files first:\n  " + "\n  ".join(problems))
    ids = [t["id"] for t in loaded]
    if len(set(ids)) != len(ids):
        raise RunError(f"two task files share an id: {sorted({i for i in ids if ids.count(i) > 1})}")
    config = load_settings(root / "settings.yaml")
    backend = backend or os.environ.get(runtime.BACKEND_ENV) or config.provider
    if not backend or (backend in REAL_BACKENDS and not config.model):
        raise RunError("settings.yaml: choose your provider and model first, then run make ping; "
                       "or run free on a scripted model: --backend never-stops")
    return loaded, settings, backend


def _existing(folder: Path, switch_settings: dict, backend: str, model: str | None) -> list[dict]:
    found = records.read(folder) if folder.exists() else []
    for r in found:
        differs = []
        if r.get("switches") != switch_settings:
            changed = sorted(k for k in set(r.get("switches", {})) | set(switch_settings)
                             if r.get("switches", {}).get(k) != switch_settings.get(k))
            differs.append("switches " + ", ".join(f"{k}: {r.get('switches', {}).get(k)!r} there, "
                                                   f"{switch_settings.get(k)!r} now" for k in changed))
        if r.get("backend") != backend:
            differs.append(f"backend {r.get('backend')!r} there, {backend!r} now")
        if model and r.get("model") and r.get("model") != model:
            differs.append(f"model {r.get('model')!r} there, {model!r} now")
        if differs:
            raise RunError(f"{folder} holds runs of another configuration ({'; '.join(differs)}). "
                           "An arm is one configuration: give this one a new --arm name.")
    return found


def run_arm(tasks_targets: list[str], arm: str, sweeps: int, overrides: list[str] | None = None,
            backend: str | None = None, jobs: int | None = None, root: Path = ROOT, log=print) -> list[dict]:
    task_list, switch_settings, backend = prepare(tasks_targets, arm, overrides, backend, root)
    folder = records.arm_dir(arm, free=is_free(backend))
    model = load_settings(root / "settings.yaml").model if backend in REAL_BACKENDS else None
    existing = _existing(folder, switch_settings, backend, model)
    done = {(r["task"], r["sweep"]) for r in existing if records.finished(r)}
    todo = [(t, s) for s in range(1, sweeps + 1) for t in task_list if (t["id"], s) not in done]
    wanted = {(t["id"], s) for s in range(1, sweeps + 1) for t in task_list}
    skipping = [r for r in existing if records.finished(r) and (r["task"], r["sweep"]) in wanted]
    warning = commit_warning(arm, skipping, records.source(root)["commit"], root, backend=backend)
    if warning:
        log(warning)
    for r in existing:   # an unfinished run that is about to be redone moves aside
        if not records.finished(r) and any((t["id"], s) == (r["task"], r["sweep"]) for t, s in todo):
            aside = folder / "unfinished"
            aside.mkdir(exist_ok=True)
            Path(r["_path"]).replace(aside / Path(r["_path"]).name)
            trace = folder / f"{r['run_id']}.trace.jsonl"
            if trace.exists():
                trace.replace(aside / trace.name)
    skipped = sweeps * len(task_list) - len(todo)
    if skipped:
        log(f"skipping {skipped} runs this arm has already finished")
    runtime.configure(backend=backend, overrides=overrides, root=root)
    base = state_dir() / "runs"
    jobs = jobs or load_settings(root / "settings.yaml").jobs
    parallel = jobs > 1
    if parallel and not getattr(_sandbox(), "PARALLEL_SAFE", False):
        log("sandbox/seed.py does not say PARALLEL_SAFE = True: running one run at a time")
        parallel = False
    out: list[dict] = []
    if parallel:
        out = _run_parallel(todo, arm, backend, switch_settings, overrides, root, base, jobs, log, folder)
    else:
        previous = os.environ.get(STATE_ENV)
        os.environ[STATE_ENV] = str(base)
        try:
            for task, sweep in todo:
                record = run_one(task, sweep, arm, backend=backend, switch_settings=switch_settings,
                                 overrides=overrides, root=root, folder=folder)
                out.append(record)
                log(line(record))
                if record["outcome"] == "quota":
                    log(QUOTA_MESSAGE)
                    break
        finally:
            if previous is None:
                os.environ.pop(STATE_ENV, None)
            else:
                os.environ[STATE_ENV] = previous
    return out


def commit_warning(arm: str, skipping: list[dict], commit: str | None, root: Path = ROOT,
                   backend: str | None = None) -> str | None:
    """A warning when finished runs about to be skipped ran from a commit whose code
    differs from the commit you are on (a commit of results or the ledger alone does not count)."""
    if commit is None:
        return None
    changed = {c: records.code_changed(root, c, commit) for c in {r.get("commit") for r in skipping}}
    other = [r for r in skipping if changed[r.get("commit")]]
    if not other:
        return None
    then = sorted({str(r.get("commit") or "no commit")[:12] for r in other})
    return (f"warning: {len(other)} of the finished runs this skips ran from commit {', '.join(then[:3])}"
            f"{' and others' if len(then) > 3 else ''}, and your code has changed since (now at {commit[:12]}). "
            f"They are kept, and an arm's runs should come from one version of the code: if the change matters, "
            f"start the arm over with python run.py --discard --arm {arm}"
            + (f" --backend {backend}" if backend and is_free(backend) else ""))


def discard(arm: str, log=print, free: bool | None = None) -> Path:
    """Move results/<arm>/ to results/discarded/<arm>-<time>/, so the arm can be run
    again from scratch. Nothing counts a discarded run; the folder stays, to commit.
    A free arm (free=True, or None when only results/_free/<arm>/ exists) moves to
    results/_free/discarded/<arm>-<time>/."""
    try:
        folder = records.arm_dir(arm, free=bool(free))
        if free is None and not folder.is_dir() and records.arm_dir(arm, free=True).is_dir():
            folder = records.arm_dir(arm, free=True)
    except ValueError as exc:
        raise RunError(str(exc)) from exc
    if not folder.is_dir():
        raise RunError(f"{folder} does not exist: nothing to discard")
    count = len(records.read(folder))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = folder.parent / records.DISCARDED / f"{arm}-{stamp}"
    n = 2
    while dest.exists():
        dest, n = folder.parent / records.DISCARDED / f"{arm}-{stamp}-{n}", n + 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(dest)
    shown = dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest
    log(f"moved {count} run record{'s' if count != 1 else ''} of {arm} to {shown}/; "
        f"run the arm again to start it over")
    return dest


def _init_worker(base: str) -> None:
    os.environ[STATE_ENV] = str(Path(base) / f"job-{os.getpid()}")


def _work(task, sweep, arm, backend, switch_settings, overrides, root, folder) -> dict:
    runtime.configure(backend=backend, overrides=overrides, root=Path(root))
    return run_one(task, sweep, arm, backend=backend, switch_settings=switch_settings,
                   overrides=overrides, root=Path(root), folder=Path(folder))


def _run_parallel(todo, arm, backend, switch_settings, overrides, root, base, jobs, log, folder) -> list[dict]:
    out = []
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(str(base),)) as pool:
        futures = [pool.submit(_work, t, s, arm, backend, switch_settings, overrides, str(root), str(folder))
                   for t, s in todo]
        for future in as_completed(futures):
            if future.cancelled():
                continue
            record = future.result()
            out.append(record)
            log(line(record))
            if record["outcome"] == "quota":
                log(QUOTA_MESSAGE)
                for f in futures:
                    f.cancel()
    return out


def line(record: dict) -> str:
    verdict = "pass" if record["passed"] else "FAIL"
    text = (f"{record['run_id']:<40} {record['outcome']:<12} {verdict}  {record['tokens']['total']:>9,} tok  "
            f"${record['cost_usd']:.4f}  {record['latency_s']:.2f}s")
    if record["outcome"] not in ("completed",):
        text += f"  {str(record['error'] or '').splitlines()[0][:160]}"
    elif not record["passed"] and record["check"]["reason"]:
        text += f"  check: {record['check']['reason'][:160]}"
    return text

