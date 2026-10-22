"""For tests: put the state, the results and the traces in a scratch folder, and run
one test task there, on a fake model, without spending a token.

    class MyTest(ScratchTest):
        def test_something(self):
            record = self.run_task("tasks/routine.yaml", "try-it", backend="tests.fakes:BadPlanner")

run_task runs one of your test task files with your own sandbox and check.
run_check tests one of your checks on an effect you plant, with no agent and no model.
After either returns, your sandbox modules read that run's sandbox, so a test can
assert on what it left (self.state_dir is its folder).
run_fixture is for the starter's own tests: it runs the starter's test task with a
stand-in seed, so those tests pass whatever your domain is.

fixed_run and check_run are for a test that stops a process and carries on in a
new one, or sends a message while a turn runs: run.py cannot, since it gives every
run a new ID and a fresh sandbox and plays each turn after the last returns.
fixed_run opens a run under an ID you choose, in each process; check_run runs the
test task's check on it."""

import contextlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from harness import ROOT
from harness.paths import RESULTS_ENV, STATE_ENV, TRACES_ENV

FIXTURE_TASK = ROOT / "tests" / "fixtures" / "task.yaml"


class ScratchTest(unittest.TestCase):
    """A test whose sandbox, results and traces live in a folder of its own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state" / "runs"    # the runs' sandbox, read after a run too
        self._env = mock.patch.dict(os.environ, {STATE_ENV: str(self.state_dir),
                                                 RESULTS_ENV: str(self.tmp / "results"),
                                                 TRACES_ENV: str(self.tmp / "traces")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def run_task(self, task_file, arm: str, backend: str = "never-stops", overrides=None) -> dict:
        """One run of one of your test task files, through the runner's own code (run_one),
        with your own sandbox (sandbox/seed.py's reset and seed) and its check.
        The sandbox, the record and the trace go in this test's scratch folder, never in
        state/, results/ or traces/. `backend` is a fake model (never-stops by default,
        or module:Class such as tests.fakes:BadPlanner); `overrides` sets switches as
        run.py's --set does (["plan=on"]). Returns the run record."""
        from harness import runner, runtime
        from harness.backends import REAL_BACKENDS

        if backend in REAL_BACKENDS:
            raise ValueError(f"run_task runs on a fake model; {backend!r} spends tokens. Use run.py for that")
        try:
            loaded, settings, backend = runner.prepare([str(task_file)], arm, overrides, backend)
        except runner.RunError as exc:
            raise ValueError(str(exc)) from exc
        if len(loaded) != 1:
            raise ValueError(f"run_task runs one task file; {task_file} holds {len(loaded)}")
        runtime.configure(backend=backend, overrides=overrides)
        with mock.patch.dict(os.environ, {STATE_ENV: str(self.state_dir)}):
            return runner.run_one(loaded[0], 1, arm, backend=backend, switch_settings=settings, overrides=overrides)

    def run_check(self, task_file, plant, replies=None) -> dict:
        """Test one of your checks on an effect you plant, with no agent and no model.
        It resets your sandbox and seeds your test task file with it (sandbox/seed.py),
        in this test's scratch folder, then opens a run with a made-up run ID, so your
        sandbox's writes and channel.send stamp it as in a real run. It calls
        plant(run), where `run` is the RunInfo the check receives (run.run_id is that
        ID): write there, through your own sandbox modules, the effect a right run
        leaves; write nothing to see the check on no effect. `replies` stands for what
        the run said: a list of texts, each an operator_turn reply (such as
        ["Done."] for a run that claims success), or reply dicts
        as the run record keeps them; none by default. Then it runs the task's
        check, as run.py does, and returns its result: `passed`, `reason` and
        `measures`, the counts it returned (a check that raises fails, and its reason
        says why)."""
        import channel
        from harness import runner, runtime, tasks
        from harness import switches as switch_file
        from harness.paths import state_dir
        from harness.pricing import PriceSheet
        from harness.settings import load_settings
        from harness.trace import SessionTrace

        path = Path(task_file)
        try:
            task = tasks.load(path if path.is_absolute() else ROOT / path)
        except tasks.TaskError as exc:
            raise ValueError(str(exc)) from exc
        arm, sweep = "check", 1
        run_id = f"{arm}-{runtime.safe_name(task['id'])}-s01-{uuid.uuid4().hex[:6]}"
        trace_path = self.tmp / "traces" / f"{run_id}.trace.jsonl"
        scope = runtime.RunScope(run_id, arm, task["id"], sweep, SessionTrace(trace_path, run_id, run_id=run_id),
                                 "never-stops", switch_file.apply(switch_file.load(), None),
                                 load_settings(ROOT / "settings.yaml"), PriceSheet(ROOT / "pricing.yaml"))
        with mock.patch.dict(os.environ, {STATE_ENV: str(self.state_dir)}):
            with runtime.run_scope(scope):
                channel.reset()
                sandbox = runner._sandbox()
                with channel.seeding():
                    sandbox.reset()
                    sandbox.seed(task)
                plant(tasks.RunInfo(run_id, task, arm, sweep, state_dir(), [], trace_path))
            said = [r if isinstance(r, dict) else {"function": "operator_turn", "text": str(r), "actions": [],
                                                   "waiting_for": None} for r in (replies or [])]
            return runner.run_check(task, run_id, arm, sweep, ROOT, replies=said, trace_path=trace_path)

    def run_fixture(self, arm: str, backend: str = "never-stops", overrides=None, check: str | None = None) -> dict:
        """One run of the test task, through the runner's own code (run_one), in state/runs.
        Your sandbox is left out: the test task's input is not in your format, so a
        stand-in seed puts it on the channel. On a scripted backend (the default), the
        record goes to results/_free/<arm>/."""
        import channel
        from harness import runner, runtime, tasks
        from harness import switches as switch_file

        class StandIn:
            PARALLEL_SAFE = False

            @staticmethod
            def reset():
                pass

            @staticmethod
            def seed(task):
                item = task["input"]
                channel.receive(item["thread"], item["text"], sender=item["from"])

        task = tasks.read(FIXTURE_TASK)
        task["file"] = "tests/fixtures/task.yaml"
        if check:
            task["check"] = check
        settings = switch_file.apply(switch_file.load(), overrides)
        runtime.configure(backend=backend, overrides=overrides)
        with mock.patch.dict(os.environ, {STATE_ENV: str(self.state_dir)}), \
                mock.patch.object(runner, "_sandbox", lambda: StandIn):
            return runner.run_one(task, 1, arm, backend=backend, switch_settings=settings, overrides=overrides)


def _load_task(task_file):
    from harness import tasks
    if isinstance(task_file, dict):          # a test task already read
        return task_file
    path = Path(task_file)
    try:
        return tasks.load(path if path.is_absolute() else ROOT / path)
    except tasks.TaskError as exc:
        raise ValueError(str(exc)) from exc


def _trace_path(run_id: str) -> Path:
    from harness import runtime
    from harness.paths import traces_dir
    return traces_dir() / f"{runtime.safe_name(run_id)}.trace.jsonl"


@contextlib.contextmanager
def fixed_run(task_file, run_id: str, *, backend: str = "never-stops", overrides=None, fresh: bool = True):
    """A run of one of your test task files under a run ID you choose, open until the
    block ends. Open it in every process of the test with the same run_id: the first
    with fresh=True, which resets your sandbox and seeds the test task, each later one
    with fresh=False, which keeps the sandbox as the last process left it. Inside it,
    harness.turns' operator_turn, outside_reply and decide_approval act as in a run.py
    run: your sandbox's writes and channel.send are stamped with run_id, and every
    process appends to one trace, <traces>/<run_id>.trace.jsonl. It yields the RunInfo
    a check receives. On a fake model only (never-stops, or module:Class).

    The folders are the ones the environment names, so in a ScratchTest they are the
    test's scratch folder, and a process the test starts (subprocess.run) inherits
    them. Nothing plays the test task's scripted turns: your test calls harness.turns
    itself, in the order and from the threads it needs."""
    import channel
    from harness import runner, runtime, tasks
    from harness import switches as switch_file
    from harness.backends import REAL_BACKENDS
    from harness.paths import state_dir
    from harness.pricing import PriceSheet
    from harness.settings import load_settings
    from harness.trace import SessionTrace

    if backend in REAL_BACKENDS:
        raise ValueError(f"fixed_run runs on a fake model; {backend!r} spends tokens")
    task = _load_task(task_file)
    trace_path = _trace_path(run_id)
    runtime.configure(backend=backend, overrides=overrides)
    scope = runtime.RunScope(run_id, "fixed", task["id"], 1, SessionTrace(trace_path, run_id, run_id=run_id),
                             backend, switch_file.apply(switch_file.load(), overrides),
                             load_settings(ROOT / "settings.yaml"), PriceSheet(ROOT / "pricing.yaml"))
    with runtime.run_scope(scope):
        if fresh:
            channel.reset()
            sandbox = runner._sandbox()
            with channel.seeding():
                sandbox.reset()
                sandbox.seed(task)
            scope.trace.write({"type": "sandbox", "reset": True, "seeded": task["id"], "state_dir": str(state_dir())})
        yield tasks.RunInfo(run_id, task, "fixed", 1, state_dir(), [], trace_path)


def check_run(task_file, run_id: str, replies=None) -> dict:
    """The test task's check on a fixed_run's run, as run.py runs it: it reads the
    sandbox for run_id's effects. `replies` stands for what the run said, as
    ScratchTest.run_check takes it. Returns `passed`, `reason` and `measures`."""
    from harness import runner

    said = [r if isinstance(r, dict) else {"function": "operator_turn", "text": str(r), "actions": [],
                                           "waiting_for": None} for r in (replies or [])]
    return runner.run_check(_load_task(task_file), run_id, "fixed", 1, ROOT, replies=said,
                            trace_path=_trace_path(run_id))


class Scripted:
    """A scripted model of the tests' own, named as module:Class like a team's own model.
    It does not say free = True, so its runs go to results/<arm>/, not results/_free/."""

    def __init__(self, settings=None):
        from harness.scripted import SaysDone
        self.inner = SaysDone(settings)

    def create(self, *, system, messages, tools):
        return self.inner.create(system=system, messages=messages, tools=tools)


class FreeScripted(Scripted):
    """The same, marked free: its runs are tests, in results/_free/<arm>/."""
    free = True
