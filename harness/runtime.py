"""What your agent asks the harness for: its model client, its trace, the switches.

    from harness import runtime

    client = runtime.client(session_id)     # the metered model client
    trace = runtime.trace(session_id)       # the session's trace writer
    if runtime.switch("plan.refunds") == "on": ...   # a switch's setting for this run or this app
    run = runtime.current_run()             # in a run.py run: run_id, arm, task_id, sweep; None in the app
    session = runtime.current_session()     # in the app: the console session's ID; None in a run.py run

Stamp every write your system makes with one of the two, so you can tell whose it
is: the run's ID in a run.py run (a check reads only that run's effects), and in the
app, where there is no run, the console session's ID:

    run = runtime.current_run()
    stamp = run.run_id if run else runtime.current_session()

In the app, current_session() is the console session the turn came from; while
on_outside_reply runs, the session that last wrote on that thread as the agent
(thread-<thread_id> if none did). Outside a turn, such as on your product's own
pages, it is None.

A write made by hand on your product's pages has no run and no console session:
stamp it with a marker of your own, such as manual:

    stamp = run.run_id if run else (runtime.current_session() or "manual")

The app and run.py set this up at start-up; your agent never builds a client or a
trace of its own, so every model call is metered, priced and counted in the run
record, and every run uses the switches it records.

In a run.py run, every session ID maps to the run: one client, one trace, one
token count for everything the run does, whichever thread or turn it comes from.
In the app, each console session has its own client and its own trace file.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from harness import ROOT
from harness import switches as switch_file
from harness.backends import REAL_BACKENDS, make_backend
from harness.client import ModelClient
from harness.paths import traces_dir
from harness.pricing import PriceSheet
from harness.settings import Settings, load_settings
from harness.trace import SessionTrace

BACKEND_ENV = "PROJECT_BACKEND"   # make app BACKEND=never-stops: the app on a scripted model
_MISSING = object()
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class _Config:
    settings: Settings
    prices: PriceSheet
    backend: str | None
    switches: dict


@dataclass
class RunScope:
    """One run.py run. The runner opens it; current_run() returns it."""
    run_id: str
    arm: str
    task_id: str
    sweep: int
    trace: SessionTrace
    backend: str
    switches: dict
    settings: Settings
    prices: PriceSheet
    wait_s: float = 0.0          # time spent in channel.wait_for_reply during this run
    throttled_s: float = 0.0     # time the backend slept out a rate limit or an overload during this run
    _client: ModelClient | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def client(self) -> ModelClient:
        with self._lock:
            if self._client is None:
                self._client = ModelClient(make_backend(self.backend, self.settings), self.prices,
                                           session_ceiling=self.settings.session_token_ceiling)
            return self._client

    def totals(self) -> dict:
        if self._client is None:
            return ModelClient(None, self.prices).totals()
        return self._client.totals()

    def add_wait(self, seconds: float) -> None:
        with self._lock:
            self.wait_s += seconds

    def add_throttled(self, seconds: float) -> None:
        with self._lock:
            self.throttled_s += seconds


_config: _Config | None = None
_clients: dict[str, ModelClient] = {}
_lock = threading.Lock()
_run: RunScope | None = None
_session: contextvars.ContextVar[str | None] = contextvars.ContextVar("session", default=None)


def configure(*, backend: str | None = None, overrides: list[str] | None = None, root: Path = ROOT) -> None:
    """Load settings.yaml, pricing.yaml and switches.yaml. The app calls it at start-up;
    run.py's runner calls it before the first run. `backend` beats the environment's
    PROJECT_BACKEND, which beats settings.yaml's provider."""
    global _config
    settings = load_settings(root / "settings.yaml")
    loaded = switch_file.load(root / "switches.yaml")
    with _lock:
        _config = _Config(settings, PriceSheet(root / "pricing.yaml"),
                          backend or os.environ.get(BACKEND_ENV) or settings.provider,
                          switch_file.apply(loaded, overrides))
        _clients.clear()


def _cfg() -> _Config:
    if _config is None:
        configure()
    return _config


def backend_name() -> str | None:
    return _run.backend if _run else _cfg().backend


def switches() -> dict:
    """Every switch's setting: the run's own in a run.py run, else switches.yaml's."""
    return dict(_run.switches if _run else _cfg().switches)


def switch(name: str, default=_MISSING):
    """One switch's setting. Asking for a switch switches.yaml does not have is an error,
    unless you give a default."""
    current = switches()
    if name in current:
        return current[name]
    if default is _MISSING:
        raise KeyError(f"switches.yaml has no switch {name!r} (it has {', '.join(current) or 'none'})")
    return default


def current_run() -> RunScope | None:
    """The run.py run in progress (run_id, arm, task_id, sweep), or None in the app."""
    return _run


def current_session() -> str | None:
    """The console session's ID, in the app, for stamping the app's own writes; None in
    a run.py run (stamp those with current_run().run_id) and outside a turn."""
    return None if _run is not None else _session.get()


def client(session_id: str | None = None) -> ModelClient:
    """The metered model client for this session (in a run.py run, the run's one client)."""
    if _run is not None:
        return _run.client()
    cfg = _cfg()
    if not cfg.backend or (cfg.backend in REAL_BACKENDS and not cfg.settings.model):
        raise RuntimeError("settings.yaml: choose your provider and model first, then run make ping; "
                           "or start the app on a scripted model: make app BACKEND=never-stops")
    key = session_id or current_session() or "default"
    with _lock:
        if key not in _clients:
            _clients[key] = ModelClient(make_backend(cfg.backend, cfg.settings), cfg.prices,
                                        session_ceiling=cfg.settings.session_token_ceiling)
        return _clients[key]


def safe_name(text: str) -> str:
    return _SAFE.sub("_", str(text)).strip("._") or "session"


def trace(session_id: str | None = None) -> SessionTrace:
    """The trace writer for this session (in a run.py run, the run's trace)."""
    if _run is not None:
        return _run.trace
    sid = session_id or current_session() or "default"
    return SessionTrace(traces_dir() / f"{safe_name(sid)}.jsonl", sid)


@contextlib.contextmanager
def in_session(session_id: str | None):
    """The harness marks which session a turn belongs to, for current_session() and the channel's stamps."""
    token = _session.set(session_id)
    try:
        yield
    finally:
        _session.reset(token)


@contextlib.contextmanager
def run_scope(scope: RunScope):
    """The runner opens one per run. Runs in one process are sequential."""
    global _run
    if _run is not None:
        raise RuntimeError(f"run {_run.run_id} is still open")
    _run = scope
    try:
        yield scope
    finally:
        _run = None
