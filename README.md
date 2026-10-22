# Your project

This repository is your team's project: a local web application in which your
agent is one component. It starts with no domain; you add yours.

The starter is plumbing only: the metered model client, the traces, the runner,
the pages around the agent. Nothing in it implements what the course teaches.
Your tool dispatch, your loop and your analysis scripts are yours to write.

The web pages are a thin layer over the same code your experiments run. The
console page and `run.py` both reach the agent through one entry point,
`agent/service.py`. So a scripted run is exactly what a person at the console
does, minus the browser, and every run repeats.

When your system takes shape, write your own README above this guide (the course
pages say what it states), and keep the guide below it.

## What you need

- Python 3.10 or later, `git` and `make`.
- A model that supports tool calling, and its API key, as in the assignments.
  Never commit the key.

## Set up

```bash
make setup                     # creates .venv and installs the dependencies
source .venv/bin/activate      # in every new terminal
make test                      # the starter's own tests; they spend no tokens
```

Choose your model in `settings.yaml`: the `provider`, `model`, `base_url` and
`api_key_env` lines, as in your assignments. Add its line to `pricing.yaml` if the
sheet does not have it, with its source and the date you read it. Then:

```bash
export ANTHROPIC_API_KEY=...   # or the variable your api_key_env names
make ping                      # two tiny calls: your key, your model, tool calling, its price line
```

## The one command

```bash
make app        # resets the sandbox, seeds your demo data, then serves the app on http://localhost:8000
```

`make app` calls your sandbox's `seed(None)`, which loads your demo data: the
records staff work with, what your product's pages show, and strangers' messages
on the channel, so the console has work to do. `make serve` serves the app
without resetting the sandbox, to show that something survives a restart.

`make app BACKEND=never-stops` runs the app on a scripted model, spending no
tokens. `make seed TASK=tasks/<file>.yaml` seeds one test task's inputs instead of
the demo data.

Until you build your agent, the console answers that the agent is not built yet.

## The pages

| Page | What it shows |
|---|---|
| `/` | Your product's own pages: a placeholder until you write them |
| `/console` | The operator's chat with the agent: sessions and their history. Each message goes to `operator_turn` |
| `/queue` | The work waiting: your sandbox's queue (`sandbox/queue.py`), the threads your agent waits on, and what waits for approval |
| `/threads` | The outside channel's conversations. You can write as the stranger; each message reaches the agent once, through `on_outside_reply` |
| `/approvals` | What waits for the operator's answer, from `pending_approvals()`: empty until you build an approval gate. An item's `session_id` names the console session it is answered in |
| `/traces` | Every run record in `results/` (those in `results/_free/` marked free) and every session's trace |

Change `app/product.py` and `app/templates/home.html` freely; the other pages
keep their contract.

## How the parts connect

```
console page ┐
threads page ├──> harness/turns.py ──> agent/service.py ──> your loop, workflow, plan, tools
run.py       ┘                                                              │
                                                                            ▼
product pages (app/product.py) ──────────────────────────────> sandbox/ modules: your domain data
```

All data access lives in `sandbox/`. Your product's pages read and write the
domain data through `sandbox/` modules, and your agent's tools wrap the same
modules. `app/` imports the agent only as `agent.service`; `make check` fails if
it imports anything else from `agent/`.

`harness/turns.py` writes each turn into the session's trace and calls the
agent's functions. Your agent asks `harness.runtime` for its model client and
trace, so every call is metered and counted in the run record. `harness/` and
`run.py` are the starter's plumbing, not yours to edit.

```python
from harness import runtime

client = runtime.client(session_id)       # the metered model client
trace = runtime.trace(session_id)         # trace.model_call(...), trace.tool_call(...), trace.progress(...)
if runtime.switch("plan.refunds") == "on": ...   # a switch's setting, for this run or this app
run = runtime.current_run()               # in run.py: the run (run_id, arm, task_id, sweep); None in the app
session = runtime.current_session()       # in the app: the console session's ID; None in run.py
```

The trace's names are yours: a model call's purpose, an event's kind and fields
(`trace.event("critique", verdict="revise", round=2)`), a plan's shape
(`trace.plan(version, plan)`), and why a loop stopped: `trace.end(reason)` takes
any short name your team chooses, such as `plan_rejected` (letters, digits and
`_`, a letter first, at most 40 characters). `trace.tool_call` keeps any extra
fields you give it, such as a key or a cost, and the run record copies them.
`trace.model_call` keeps the first 2,000 characters of the model's text and takes
extra fields too: to keep the whole reply, so that a run can be replayed from what
you recorded, pass `content=response.content`. A trace file of your own in
`traces/`, such as `traces/<session_id>.spans.jsonl` for telemetry you emit
yourself, is listed on the traces page beside the sessions.

`harness.schema.errors(value, schema)` checks a JSON value against a JSON schema,
the part of it strict mode uses, and returns its problems, one line each.

Each turn returns a `Reply`: the text the operator reads, one line per action
taken, and `waiting_for`, the thread the agent waits on for an outside reply.
Every field is optional, so `Reply(waiting_for=thread_id)` works as written.

## The outside channel

Strangers' messages arrive on the channel: your seed puts them there. A message
written on the threads page goes to `on_outside_reply`, which returns an empty
`Reply()` until you build it. To ask a stranger, your agent writes on their
thread and ends its turn waiting on it:

```python
import channel
from harness.turns import Reply

channel.send(thread_id, "Which date suits you?")
return Reply("Asked for the date; waiting for the answer.", waiting_for=thread_id)
```

The reply reaches your agent through
`agent.service.on_outside_reply(thread_id, text)`: in the app when someone writes
on the threads page, in `run.py` when the test task's scripted stranger answers,
right after the turn. Each reply is delivered once.
`channel.replies(thread_id, since)` reads a thread without taking anything.

`channel.wait_for_reply(thread_id, timeout_s)` lets a turn wait in-process
instead: in `run.py` the scripted stranger answers it at once, and in the app it
takes the next message written on the thread while it waits. A reply it takes is
not delivered again to `on_outside_reply`; the trace shows it either way.

A reply the operator sends by hand from a product page is the operator's;
`sender` is `"agent"` unless you say otherwise:

```python
channel.send(thread_id, text, sender="operator")   # stored and shown as the operator's
```

`send` takes no key: it stores every message it is given, so a send repeated is a
second message. `channel.messages(thread_id, run_id)` reads what a thread already
holds. In the app a message's `run_id` is None and its `session_id` names the
console session that wrote it.

**Your own channel.** Keeping the starter's thread store is the expected choice.
If you replace it, with a local mailbox for example, implement the whole
interface in `channel/__init__.py`, because the runner, the checks' helpers and
the app use all of it:

- for your agent: `send(thread_id, text, sender)` (`"agent"`, or `"operator"`
  from a product page), `replies(thread_id, since)` and
  `wait_for_reply(thread_id, timeout_s)`;
- for the harness, your seed and the app: `receive(thread_id, text, sender)`,
  `deliver(message)`, `seeding()`, `messages(thread_id, run_id)`, `threads()`,
  `session_of(thread_id)` and `reset()`;
- `Message`, with its fields (`id`, `thread_id`, `sender`, `text`, `at`,
  `run_id`, `session_id`, `seeded`, `handled_by`) and `from_outside`;
- the scripted strangers: `send` reports every message to `_script.sent`, and
  `wait_for_reply` asks `_script.answer` before it waits.

## Your sandbox

Fill in `sandbox/seed.py`. `reset()` empties every store your system writes to;
`seed(task)` loads your base data, then:

- with `task` `None` (`make app`, `make seed`): your demo data, with strangers'
  messages on the channel (`channel.receive`);
- with a test task (`run.py` before every run, or `make seed TASK=...`): that
  test task's inputs alone (`task["input"]`, or each item of `task["inputs"]`).

Put the functions that read and write your domain data in `sandbox/` modules, one
per kind of record.

Keep every file your sandbox writes under `harness.paths.state_dir()`, and call
it when you need it, never at import time. It is `state/` in the app, never
committed (`make seed` rebuilds it), and a folder of its own in each run.

Stamp every write your system makes with the run's ID, so a check reads only that
run's effects. In the app, where `current_run()` is None, stamp it with
`runtime.current_session()`, the console session's ID (in `on_outside_reply`,
the session that last wrote on the thread). A write made by hand on a product
page has no run and no console session: stamp it with a marker of your own, such
as `manual`:

```python
run = runtime.current_run()
stamp = run.run_id if run else (runtime.current_session() or "manual")
```

The channel stamps its messages for you, and marks what your seed writes on it
as `seeded`: the input, not part of the exchange.

## Your test tasks

A **test task** is one realistic piece of work your system receives, written as a
file in `tasks/`, one YAML file each. `tasks/example.yaml` shows every field.
Keep the test task you tune prompts on in `tasks/dev/`. `tasks/sessions/` is for
long sessions. `--tasks tasks/` leaves both folders out. Where several things
arrive, a test task gives `inputs:`, a list, instead of `input:`, and may have
several strangers, one per thread. A test task in which your agent asks no one has
`outside: []`.

The strangers are scripted, as `tasks/example.yaml` shows: each answers when a
turn ends waiting on their thread.

The check is a function in `checks/` that decides pass or fail after the run. It
receives the run (`RunInfo`): its ID, the test task, the state folder, the
agent's replies to the operator (`replies`) and the run's trace file
(`trace_path`). It returns `True` or `False`, `(passed, reason)`, or
`(passed, reason, measures)`, where `measures` is a dict of named counts, such as
`{"questions_asked": 2}`, kept in the run record. A check reads the sandbox for
the truth, and reads the replies only to score an answer, or a claim of work,
against it. `checks/helpers.py` has the reads you need; its `thread()` leaves out
the seeded input. Test each check with `run_check` (below).

## Your switches

`switches.yaml` holds a switch for each part of your design a pass may turn on or
off: its `setting` and the `values` it may take. The app and `run.py` load the
file at start-up. Your agent reads a setting with `runtime.switch("name")`, and
`--set name=value` sets it for one arm.

The starter ships four flat switches: `control`, `plan`, `notes` and `critique`.
Replace a flat switch with a group of your own, one switch per place you set it:

```yaml
control:
  refunds: {setting: agent, values: [workflow, agent]}               # control.refunds
plan:
  refunds: {setting: "on", values: ["off", "on"]}                    # plan.refunds
critique:
  refund_reply: {setting: external, values: ["off", "external"]}     # critique.refund_reply
```

A group takes the place of the flat switch of its name. A switch never has
switches under it: the app, `run.py` and `make check` refuse a file where one
does, and say how to fix it.

- To add a value, such as `hybrid` to `control.refunds`, add it to `values`.
- A switch you add after runs exist carries `added: <date>`, the day you add it
  (YYYY-MM-DD), on the switch itself, never on its group. `make check` then
  accepts the earlier runs, which do not have it, and those of that day.

## Running an arm

An arm is your test tasks run for some sweeps, with the arm's switches set; its
runs go to `results/<arm>/`. To measure `plan.refunds` off and on:

```bash
python run.py --tasks tasks/ --sweeps 3 --arm plan-off --set plan.refunds=off
python run.py --tasks tasks/ --sweeps 3 --arm plan-on --set plan.refunds=on
```

`--set a=x b=y` sets several switches for one arm; every other switch keeps its
`switches.yaml` setting, and every run records them all.

The summarizer computes a first table from the run records alone:

```bash
python -m harness.summarize results/plan-off results/plan-on --measure questions_asked
```

It gives each arm's passes, sweep scores, tokens, cost, latency and the slowest
turn, and the passes per test task. `--measure <name>` adds the sum per arm of the
count your checks returned under that name. Compute any other number with your
own script in `experiments/analysis/`, from the same records and traces. That
folder is yours.

What `run.py` does for each run: it resets the sandbox and seeds that test task's
inputs alone, plays the test task's people through `agent/service.py`, runs the
check, and writes `results/<arm>/<run_id>.json` with its trace beside it. Cite a
trace line as the trace file and its line number, such as
`results/baseline/<run_id>.trace.jsonl:14`. The record holds every switch's
setting; the model; the commit the code ran from (`commit`) and `dirty`, true if
your code had uncommitted changes (what is not code: see "make check"); the
tokens (input and output, cached and uncached); the cost with the price sheet's
date; the latency with and without the waits (`latency_without_wait_s` leaves out
`wait_s`, the wait for an outside reply, and `throttled_s`, the time the backend
slept out a rate limit or an overload); the agent's own time for each turn
(`turn_latency_s`: one number per `operator_turn` and `on_outside_reply` call, in
order, without those waits, which each turn's `reply` line in the trace gives);
how the run ended (`outcome`); every end reason your code wrote with
`trace.end(...)`, in order (`end_reasons`, with `token_budget` last if the
harness's ceiling stopped the run), and the last of them (`end_reason`); every
tool call in full (`tool_calls`: its `id`, name, input and output); the agent's
replies to the operator (`replies`); and the check's result with its counts
(`measures`). Commit the run records your ledger links to.

- `--tasks` takes a folder or files. A folder means the `.yaml` files directly in
  it, never its subfolders.
- Running the same command again skips the runs the arm has finished, and warns
  when they ran from a commit whose code differs from the one you are on. A run
  stopped by your vendor's daily quota or a rate limit is redone, as is one whose
  agent was not built yet; those never count.
- To start an arm over after a code change, `python run.py --discard --arm <name>`
  moves `results/<name>/` to `results/discarded/<name>-<time>/`.
- An arm is one configuration: if `results/<arm>/` holds runs with other switch
  settings, another backend or another model, `run.py` refuses.
- `--jobs N` runs N at once, if `sandbox/seed.py` says `PARALLEL_SAFE = True`.
- A run on a scripted model (`--backend never-stops`, which calls a tool every
  time, or `harness.scripted:SaysDone`, which says it is done) or on a fake model
  of yours marked `free = True` is a test, not evidence: it goes to
  `results/_free/<arm>/`, which `make check` and the summarizer leave out.

`python run.py --help` lists every option.

## Testing on a fake model

A fake model is a class of your own that answers like a model and spends no
tokens, to cause a failure on purpose or test one path of your code. Keep yours
in `tests/fakes.py`, each marked `free = True`; a model of your own without the
mark counts like a real one, and its runs go to `results/<arm>/`.

```python
# tests/fakes.py
from harness.client import Usage


class BadPlanner:
    """Answers every call with text that is not a plan."""
    free = True                  # a test, not evidence: its runs go to results/_free/<arm>/

    def __init__(self, settings=None):
        pass

    def create(self, *, system, messages, tools):
        content = [{"type": "text", "text": "Step one: do everything at once."}]
        return content, "end_turn", "bad-planner", Usage(input_tokens=10, output_tokens=8)
```

`create` returns the content blocks (`text`, and `tool_use` to call a tool), the
stop reason (`end_turn` or `tool_use`), a model name and the token counts;
`harness/scripted.py` has two more to copy. Run it like any model,
`--backend tests.fakes:BadPlanner`, or in a test with `run_task`, from
`tests/support.py`: it runs one of your test task files once, with your own
sandbox and its check, and returns the run record. The sandbox, the record and
the trace go in the test's scratch folder, never in `state/`, `results/` or
`traces/`:

```python
# tests/test_plans.py
from tests.support import ScratchTest


class BadPlanTest(ScratchTest):
    def test_a_bad_plan_is_refused(self):
        record = self.run_task("tasks/refund_late_orders.yaml", "bad-plan", backend="tests.fakes:BadPlanner",
                               overrides=["plan.refunds=on"])      # overrides: as --set
        self.assertFalse(record["passed"])
        self.assertIn("plan_rejected", record["end_reasons"])    # your own end reason's name
```

`run_task` runs on `never-stops` by default, which calls the first tool it is
offered and never ends its turn: only your loop's bound stops it. It answers a
routing question with its first choice too; to route a case to the agent path
first, use a fake of your own. A run's last end reason may come from a later
turn, so look in `end_reasons`.

`run_check`, from the same file, tests a check with no agent and no model. It
seeds your test task in the test's scratch folder, opens a run with a made-up run
ID, and calls your `plant(run)`, where `run` is the `RunInfo` the check receives.
In `plant`, write the effect a right run leaves through your own `sandbox/`
modules, or nothing; `replies` stands for what the run said. It returns
`passed`, `reason` and `measures`.

```python
plant = lambda run: refunds.issue("SYN-ORD-0042", 18.50)                      # through your sandbox/ module
self.assertTrue(self.run_check("tasks/refund_plain.yaml", plant)["passed"])                # the right effect
self.assertFalse(self.run_check("tasks/refund_plain.yaml", lambda run: None,
                                replies=["Done."])["passed"])         # claims success, no effect
```

**A run across processes, or a message mid-turn.** `run.py` gives every run a
new run ID and a fresh sandbox, and plays each scripted turn after the last one
returns, handing the scripted strangers' answers and approvals to the run first.
A test that kills a process and carries on in a new one, or sends a message while
a turn runs, uses `fixed_run` and `check_run` from `tests/support.py` instead.
`fixed_run(task_file, run_id)` opens a run under a run ID you choose, in this
process: `fresh=True` (the default) resets your sandbox and seeds the test task,
and `fresh=False` keeps the sandbox as the last process left it. Inside it,
`harness.turns`' `operator_turn`, `outside_reply` and `decide_approval` act as in a
run.py run, stamped with that run ID, and every process appends to one trace.
Nothing plays the task's scripted turns: your test calls `harness.turns` itself.
`check_run(task_file, run_id)` then runs the test task's check. A process your
`ScratchTest` starts inherits its scratch folders.

```python
# tests/test_recovery.py
import subprocess
import sys
from tests.support import ScratchTest, check_run

STEP = """
import sys
from harness import turns
from tests.support import fixed_run
with fixed_run("tasks/broken_boiler.yaml", "recover", backend="tests.fakes:DiesMidWrite",
               fresh=sys.argv[1] == "first"):
    turns.operator_turn("recover", sys.argv[2])
"""


class RecoveryTest(ScratchTest):
    def test_a_killed_run_resumes_with_one_effect(self):
        first = subprocess.run([sys.executable, "-c", STEP, "first", "Handle the new request."])
        self.assertNotEqual(first.returncode, 0)                        # killed mid-write
        subprocess.run([sys.executable, "-c", STEP, "again", "Carry on."], check=True)
        result = check_run("tasks/broken_boiler.yaml", "recover")
        self.assertTrue(result["passed"], result["reason"])
```

After `run_task` or `run_check` returns, your `sandbox/` modules read that run's
sandbox, so a test can assert on what it left; `self.state_dir` is its folder.
`make test` runs your tests with the starter's. Most of the starter's tests use
`run_fixture` and a stand-in seed, so they pass whatever your domain is; its page
tests seed your demo data with `seed(None)`, so that must work.

## make check

`make check` tests the project's structure, without spending a token:

- **One path**: the console page and `run.py` reach the agent only through
  `agent/service.py` (a probe sends both to one function), and nothing in `app/`
  imports any other part of `agent/`.
- **Switches recorded**: every run record outside `results/_free/` carries every
  switch in `switches.yaml`; for a switch with `added: <date>`, only runs started
  after that day need it.
- **Tasks valid**: every test task file has every field, a unique id, one stranger
  per thread and a check that exists, and no check imports the agent.
- **Ledger**: every entry in `experiments/LEDGER.md`, one per pass, has every
  field of the template, links its `results/<arm>/` folders and never
  `results/_free/`, and every arm it links started after its prediction was
  committed. The prediction's date is the first commit in which this entry,
  found by its heading's pass number, has one: fixing a typo in it later changes
  nothing, and an arm run for an earlier entry runs again, under a new name. Once
  Results, Difference and Verdict no longer say `pending`, the entry's runs are
  checked too:
  - every arm it links has at least three sweeps of each of its test tasks;
  - every arm ran the same test tasks;
  - each arm's runs come from one version of the code, and none is `dirty`;
  - all its arms ran the same code, unless **Arms** says "branch".

  The same code, not necessarily the same commit: commits that differ only in
  `results/`, `traces/`, `experiments/`, `docs/`, `decisions/`, `ops/`,
  `eval/labels/`, `eval/thresholds/`, `README.md`, `ARCHITECTURE.md` or `CHANGES.md`
  hold the same code (a prompt in a `.md` file elsewhere is code), and changes there
  never make a run dirty.
  Each message names the arm, the files that differ, and what to do.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv` and installs the dependencies |
| `make test` | The starter's tests and yours; no tokens |
| `make ping` | Two tiny model calls: your key, model, tool calling and price line |
| `make seed` | Resets the sandbox and seeds your demo data (`TASK=tasks/<file>.yaml`: that test task's inputs) |
| `make app` | The one command: `make seed`, then serves the app on http://localhost:8000 |
| `make serve` | Serves the app without resetting the sandbox |
| `make check` | The structure checks: one path, switches recorded, tasks valid, the ledger |
| `python run.py --tasks tasks/ --sweeps 3 --arm <name> --set <switch>=<value> ...` | Runs one arm |
| `python run.py --discard --arm <name>` | Moves the arm's runs to `results/discarded/` |
| `python -m harness.summarize results/<arm> ... [--measure <name>]` | The arm table |
