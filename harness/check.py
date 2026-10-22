"""make check: the project's structure checks. They spend no tokens.

    One path            the console page and run.py reach the agent only through
                        agent/service.py. Read from the code: nothing in app/, run.py
                        or the harness imports any other part of agent/ (the product's
                        pages read and write the domain data through sandbox/ modules,
                        which the agent's tools wrap too). Then tried: with
                        operator_turn replaced by a probe, a message posted to the
                        console page and a task's scripted operator turn both reach it.
    Switches recorded   every run record in results/ carries every switch in
                        switches.yaml (a switch marked `added: <date>` only in the runs
                        started after that day). The free runs in results/_free/ are
                        left out.
    Tasks valid         every test task file in tasks/ (subfolders too) has every field,
                        a unique id, and a check function that exists; no check imports
                        agent/.
    Ledger              every entry in experiments/LEDGER.md, one per pass of the loop,
                        has every field, links its arms in results/ (never results/_free/),
                        and every arm it links started after its prediction was
                        committed. The prediction is dated by the first commit of the
                        ledger in which this entry, found by its heading's pass number,
                        has a prediction (so a later edit changes nothing). An entry whose
                        results, difference and verdict say "pending" is in progress;
                        once it is complete, every arm it links has at least three
                        sweeps of each of its tasks, the arms ran the same tasks, each
                        arm's runs come from one version of the code and none is dirty,
                        and all the arms ran the same code, unless Arms says "branch".
                        The same code, not necessarily the same commit: commits that
                        differ only in results/, traces/, experiments/, docs/,
                        decisions/, ops/, eval/labels/, eval/thresholds/,
                        README.md, ARCHITECTURE.md or CHANGES.md hold the same
                        code. Prompts in .md files elsewhere are code.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from harness import ROOT, records, tasks
from harness import switches as switch_file
from harness.paths import RESULTS_ENV, STATE_ENV, TRACES_ENV

# One ledger entry per pass of the improvement loop, with these fields.
LEDGER_FIELDS = ("Pass", "Owner", "Problem", "Evidence", "Why this fix", "Prediction", "Arms", "Held constant",
                 "Tasks and sweeps", "Results", "Difference", "Verdict", "Links")
MAY_BE_PENDING = ("Results", "Difference", "Verdict")
_FIELD = re.compile(r"^\s*(?:[-*]\s*)?\*\*(?P<label>[^*:]+):\*\*\s*(?P<rest>.*)$")
_ARM_LINK = re.compile(r"results/([A-Za-z0-9][A-Za-z0-9_.-]*)")
_FREE_LINK = re.compile(r"results/_free/[A-Za-z0-9_.-]*")
_PASS_NUMBER = re.compile(r"\bpass\s+(\d+[a-z]?)\b", re.IGNORECASE)
_BRANCH = re.compile(r"\bbranch", re.IGNORECASE)
LEDGER = "experiments/LEDGER.md"
MIN_SWEEPS = 3          # sweeps of each task, in every arm a complete entry links


# -- one path ---------------------------------------------------------------------------------

def agent_imports(path: Path) -> list[str]:
    """The modules of agent/ a file imports, other than agent.service."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError as exc:
        return [f"(does not parse: {exc})"]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] if node.module != "agent" else [f"agent.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            if (name == "agent" or name.startswith("agent.")) and name != "agent.service":
                found.append(name)
    return found


def check_one_path(root: Path = ROOT) -> tuple[list[str], str]:
    problems = []
    files = sorted((root / "app").rglob("*.py")) + [root / "run.py"] + sorted((root / "harness").glob("*.py"))
    for path in files:
        if path.exists():
            for name in agent_imports(path):
                problems.append(f"{path.relative_to(root)} imports {name}: app/ and run.py import the agent only "
                                "as agent.service. The product's pages read and write the domain data through "
                                "sandbox/ modules, and the agent's tools wrap those same modules")
    problems += _probe(root)
    return problems, "the console page and run.py reach the agent only through agent.service"


def _probe(root: Path) -> list[str]:
    """Replace agent.service.operator_turn with a probe; post to the console page, then
    play a task's operator turn as run.py does. Both must reach the probe."""
    from harness.turns import Reply

    calls: list[tuple[str, str]] = []

    def probe(session_id, text):
        calls.append((session_id, text))
        return Reply("probe")

    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        env = {STATE_ENV: f"{tmp}/state", RESULTS_ENV: f"{tmp}/results", TRACES_ENV: f"{tmp}/traces"}
        with mock.patch.dict(os.environ, env):
            try:
                from agent import service
            except Exception as exc:
                return [f"agent/service.py does not import: {type(exc).__name__}: {exc}"]
            try:
                from fastapi.testclient import TestClient

                from app.main import app
            except Exception as exc:
                return [f"the app does not import: {type(exc).__name__}: {exc}"]
            original = service.operator_turn
            with mock.patch.object(service, "operator_turn", probe), _rebound(root, original, probe):
                try:
                    with TestClient(app) as client:
                        response = client.post("/console/probe-console/turn", data={"text": "probe: console"},
                                               headers={"accept": "application/json"})
                    if response.status_code >= 400:
                        problems.append(f"posting to the console page failed: HTTP {response.status_code}")
                except Exception as exc:
                    problems.append(f"the app could not take a console turn: {type(exc).__name__}: {exc}")
                if ("probe-console", "probe: console") not in calls:
                    problems.append("a message posted to /console/<session>/turn never reached "
                                    "agent.service.operator_turn: the console page bypasses agent/service.py")
                try:
                    from harness import runner
                    runner.play({"id": "probe", "operator": ["probe: runner"], "outside": []}, "probe-run")
                except Exception as exc:
                    problems.append(f"the runner could not play a scripted turn: {type(exc).__name__}: {exc}")
                if ("probe-run", "probe: runner") not in calls:
                    problems.append("a task's scripted operator turn never reached agent.service.operator_turn")
    return problems


class _rebound:
    """Also replace the function wherever a module of the app, the runner or the harness
    holds it under another name (from agent.service import operator_turn)."""

    def __init__(self, root: Path, original, replacement):
        self.root, self.original, self.replacement, self.undo = root.resolve(), original, replacement, []

    def __enter__(self):
        for module in list(sys.modules.values()):
            file = getattr(module, "__file__", None)
            if not file or not Path(file).resolve().is_relative_to(self.root):
                continue
            for name, value in list(vars(module).items()):
                if value is self.original:
                    self.undo.append((module, name))
                    setattr(module, name, self.replacement)
        return self

    def __exit__(self, *exc):
        for module, name in self.undo:
            setattr(module, name, self.original)


def _results(root: Path) -> Path:
    return Path(os.environ.get(RESULTS_ENV) or root / "results")


# -- switches recorded ------------------------------------------------------------------------

def check_switches(root: Path = ROOT) -> tuple[list[str], str]:
    try:
        declared = switch_file.load(root / "switches.yaml")
    except switch_file.SwitchError as exc:
        return [str(exc)], ""
    problems, count = [], 0
    for folder in records.arms(_results(root)):
        for record in records.read(folder):
            count += 1
            have = record.get("switches") or {}
            started = str(record.get("started") or "")
            missing = [name for name, s in declared.items()
                       if name not in have and not (s.added and started and started[:10] <= s.added)]
            if missing:
                where = Path(record["_path"]).relative_to(root) if Path(record["_path"]).is_relative_to(root) else record["_path"]
                problems.append(f"{where}: no setting for {', '.join(missing)}; a switch added after "
                                "this run carries `added: <date>` on the switch itself")
    return problems, (f"{count} run record{'s' if count != 1 else ''}, each with every one of "
                      f"{len(declared)} switches")


# -- tasks valid ------------------------------------------------------------------------------

def check_tasks(root: Path = ROOT) -> tuple[list[str], str]:
    problems, ids = [], {}
    files = tasks.all_task_files(root)
    if not files:
        problems.append("tasks/ holds no task files")
    for path in files:
        rel = path.relative_to(root)
        try:
            task = tasks.read(path)
        except tasks.TaskError as exc:
            problems.append(str(exc))
            continue
        for p in tasks.problems(task, path, root):
            problems.append(f"{rel}: {p}")
        if "id" in task:
            ids.setdefault(str(task["id"]), []).append(str(rel))
    for task_id, where in ids.items():
        if len(where) > 1:
            problems.append(f"id {task_id} is used by {', '.join(where)}")
    for path in sorted((root / "checks").rglob("*.py")):
        for name in _imports(path):
            if name == "agent" or name.startswith("agent.") or name in ("harness.turns", "harness.runtime"):
                problems.append(f"{path.relative_to(root)} imports {name}: a check reads the sandbox, "
                                "never the agent")
    return problems, f"{len(files)} task file{'s' if len(files) != 1 else ''}"


def _imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return out


# -- the ledger --------------------------------------------------------------------------------

def ledger_entries(text: str) -> list[tuple[str, dict]]:
    """(heading, {field: value}) for every ## section except the template."""
    entries, heading, fields, label = [], None, {}, None
    for line in text.splitlines() + ["## (end)"]:
        if line.startswith("## "):
            if heading is not None and not heading.lower().startswith("template"):
                entries.append((heading, {k: v.strip() for k, v in fields.items()}))
            heading, fields, label = line[3:].strip(), {}, None
            continue
        if heading is None:
            continue
        match = _FIELD.match(line)
        if match:
            label = next((f for f in LEDGER_FIELDS if f.lower() == match["label"].strip().lower()), None)
            if label:
                fields[label] = match["rest"]
                continue
        if label:
            fields[label] += "\n" + line
    return entries


def _empty(value: str | None) -> bool:
    value = (value or "").strip()
    return not value or (value.startswith("<") and value.endswith(">"))   # the template's text, unchanged


def _predicted(value: str | None) -> bool:
    return not _empty(value) and (value or "").strip().lower() != "pending"


def pass_key(heading: str) -> str:
    """What matches an entry across the ledger's history: its heading's pass number
    ("Pass 2 · Notes" is pass 2), or the whole heading when it has none."""
    match = _PASS_NUMBER.search(heading)
    return f"pass {match.group(1).lower()}" if match else " ".join(heading.lower().split())


def check_ledger(root: Path = ROOT) -> tuple[list[str], str]:
    path = root / LEDGER
    if not path.exists():
        return ["experiments/LEDGER.md is missing"], ""
    entries = ledger_entries(path.read_text())
    problems, open_entries = [], []
    keys = [pass_key(heading) for heading, _ in entries]
    for key in sorted({k for k in keys if keys.count(k) > 1}):
        same = [heading for heading, _ in entries if pass_key(heading) == key]
        problems.append(f"{' and '.join(same)}: two entries with one pass number; give each pass a number of "
                        "its own, so make check can date each prediction")
    dates = None     # when each entry's prediction was first committed, read from git once
    for heading, fields in entries:
        missing = [f for f in LEDGER_FIELDS if _empty(fields.get(f))]
        if missing:
            problems.append(f"{heading}: no {', '.join(missing)}")
        pending = [f for f in MAY_BE_PENDING if (fields.get(f) or "").strip().lower().startswith("pending")]
        if pending:
            open_entries.append(heading)
        links = fields.get("Links") or ""
        for free in sorted(set(_FREE_LINK.findall(links))):
            problems.append(f"{heading}: links {free}, free runs on a scripted model: they test your code and "
                            "are not evidence; link the arms you ran on your model")
        arms = sorted(set(_ARM_LINK.findall(links)))
        by_arm = {}
        for arm in arms:
            folder = _results(root) / arm
            linked = records.read(folder) if folder.is_dir() else []
            if linked:
                by_arm[arm] = linked
            elif not pending:
                problems.append(f"{heading}: links results/{arm}, which holds no run records")
        if not arms and not pending and "Links" not in missing:
            problems.append(f"{heading}: Links names no results/<arm> folder")
        if by_arm and not _empty(fields.get("Prediction")):
            if dates is None:
                dates = _prediction_dates(root)
            problems += _prediction_first(heading, dates, by_arm)
        if by_arm and not pending:
            problems += _arm_runs(root, heading, fields.get("Arms") or "", by_arm)
    note = (f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'}"
            + (f", {len(open_entries)} in progress ({'; '.join(open_entries)})" if open_entries else "")
            if entries else "no entries yet")
    return problems, note


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _prediction_dates(root: Path) -> dict | str:
    """{pass key: the date of the first commit of the ledger in which that entry has a
    prediction}, or why git history cannot be read here. Editing a prediction later,
    or two entries whose predictions start alike, changes no entry's date."""
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
    except OSError:
        return "cannot read git history here, so the prediction's date cannot be checked"
    if top.returncode != 0:
        return "cannot read git history here, so the prediction's date cannot be checked"
    if Path(top.stdout.strip()).resolve() != root.resolve():
        return "this folder is not its own git repository, so the prediction's date cannot be checked"
    dates: dict = {}
    for line in _git(root, "log", "--reverse", "--format=%H %cI", "--", LEDGER).stdout.splitlines():
        sha, _, when = line.partition(" ")
        shown = _git(root, "show", f"{sha}:{LEDGER}")
        if shown.returncode != 0:
            continue
        for heading, fields in ledger_entries(shown.stdout):
            key = pass_key(heading)
            if key not in dates and _predicted(fields.get("Prediction")):
                dates[key] = when.strip()
    return dates


def _started(record: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record.get("started")))
    except ValueError:
        return None


def _prediction_first(heading: str, dates: dict | str, by_arm: dict) -> list[str]:
    """Every linked arm's runs started after the entry's prediction was committed."""
    if isinstance(dates, str):
        return [f"{heading}: {dates}"]
    committed = dates.get(pass_key(heading))
    if committed is None:
        return [f"{heading}: its prediction is not in any commit; commit and push it before the first run"]
    when = datetime.fromisoformat(committed)
    out = []
    for arm, runs in sorted(by_arm.items()):
        started = [s for s in (_started(r) for r in runs) if s is not None]
        if started and min(started) < when:
            out.append(f"{heading}: results/{arm}/ has runs that started before its prediction was committed "
                       f"(first run {min(started).isoformat()}, prediction {committed}). Run that arm again under "
                       "a new name, now that the prediction is committed, and link the new folder (a before arm "
                       "reused from an earlier pass too)")
    return out


def _short(commit) -> str:
    return str(commit)[:12] if commit else "no commit"


def _differing(root: Path, commits: list) -> str:
    """The code files that differ between the first commit and each of the others."""
    files = sorted({f for c in commits[1:] for f in records.code_diff(root, commits[0], c)})
    if not files:
        return "the files cannot be listed"
    return ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else "")


def _code_versions(root: Path, commits) -> list[list]:
    """The commits grouped by their code: commits that differ only in results/, traces/,
    experiments/, docs/, decisions/, ops/, eval/labels/, eval/thresholds/, README.md,
    ARCHITECTURE.md or CHANGES.md are one version of the code."""
    groups: list[list] = []
    for commit in sorted(commits, key=str):
        for group in groups:
            if commit == group[0] or not records.code_changed(root, group[0], commit):
                group.append(commit)
                break
        else:
            groups.append([commit])
    return groups


def _arm_runs(root: Path, heading: str, arms_text: str, by_arm: dict) -> list[str]:
    """A complete entry's runs: every arm has at least three sweeps of each of its tasks,
    every arm ran the same tasks, each arm's runs come from one version of the code and
    none is dirty, and all the arms ran the same code, unless Arms says "branch". The
    same code, not necessarily the same commit: commits that differ only in results/,
    traces/, experiments/, docs/, decisions/, ops/, eval/labels/, eval/thresholds/, README.md,
    ARCHITECTURE.md or CHANGES.md hold the same code."""
    out = []
    runs = {arm: [r for r in found if records.finished(r)] for arm, found in sorted(by_arm.items())}
    tasks_of = {arm: sorted({str(r.get("task")) for r in found}) for arm, found in runs.items()}
    every = sorted({t for found in tasks_of.values() for t in found})
    versions = {}
    for arm, found in runs.items():
        for task in tasks_of[arm]:
            sweeps = {r.get("sweep") for r in found if str(r.get("task")) == task}
            if len(sweeps) < MIN_SWEEPS:
                out.append(f"{heading}: results/{arm}/ has {len(sweeps)} sweep{'s' if len(sweeps) != 1 else ''} "
                           f"of {task}; run every task at least {MIN_SWEEPS} times: run the arm's command again "
                           f"with --sweeps {MIN_SWEEPS}, which adds only the missing runs")
        absent = [t for t in every if t not in tasks_of[arm]]
        if absent:
            out.append(f"{heading}: results/{arm}/ has no runs of {', '.join(absent)}, which another arm ran; "
                       "run every arm on the same tasks")
        dirty = [r for r in found if r.get("dirty") is True]
        if dirty:
            out.append(f"{heading}: results/{arm}/ has {len(dirty)} run{'s' if len(dirty) != 1 else ''} made with "
                       "uncommitted code changes (dirty); commit your code, then start the arm over: "
                       f"python run.py --discard --arm {arm}, and run it again")
        versions[arm] = _code_versions(root, {r.get("commit") for r in found})
        if len(versions[arm]) > 1:
            heads = [g[0] for g in versions[arm]]
            out.append(f"{heading}: results/{arm}/ holds runs of different code (commits "
                       f"{', '.join(_short(c) for c in heads)} differ in {_differing(root, heads)}); an arm's runs "
                       f"come from one version of the code: start it over with python run.py --discard --arm {arm}, "
                       "and run it again")
    single = {arm: v[0][0] for arm, v in versions.items() if len(v) == 1}
    if len(single) == len(versions) > 1 and not _BRANCH.search(arms_text):
        groups = _code_versions(root, set(single.values()))
        if len(groups) > 1:
            out.append(f"{heading}: its arms ran different code "
                       f"({'; '.join(f'{arm} at {_short(c)}' for arm, c in single.items())}; they differ in "
                       f"{_differing(root, [g[0] for g in groups])}). Run every arm on the same code: run the "
                       "earlier arm again under a new name, and link it. If an arm is built on a branch of its own, "
                       "say \"branch\" in Arms")
    return out


# -- all four ---------------------------------------------------------------------------------

CHECKS = (("One path", check_one_path), ("Switches recorded", check_switches),
          ("Tasks valid", check_tasks), ("Ledger", check_ledger))


def main(argv=None) -> int:
    print("make check: the project's structure, no tokens\n")
    failed = 0
    for name, fn in CHECKS:
        try:
            problems, note = fn(ROOT)
        except Exception as exc:  # a check that cannot run is a failure, said plainly
            problems, note = [f"the check itself raised {type(exc).__name__}: {exc}"], ""
        label = f"{name} ".ljust(20, ".")
        if problems:
            failed += 1
            print(f"{label} FAIL")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"{label} ok: {note}")
    print("\n" + ("All four checks pass." if not failed else f"{failed} of 4 checks fail."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
