"""The test task files in tasks/: read them, and say what is wrong with one.

A test task is one realistic piece of work your system receives, written as a
file in tasks/. tasks/example.yaml shows every field. A test task needs:

    id          unique across tasks/
    input       what arrives before the run; sandbox/seed.py's seed(task) puts it in place.
                Or inputs: a list of several, for a session where more than one arrives
    operator    the operator's scripted turns, in order: one or more messages
    outside     the strangers, one per thread: their replies in order, what they say when
                asked again after those run out, and what they send if never asked (null
                for silence). An empty list, outside: [], if no stranger takes part
    check       the function that decides pass or fail from the sandbox:
                checks/<file>.py (its function `check`) or checks/<file>.py:<function>

Optional: title, and approvals (the operator's scripted answers, yes or no, in
order, to what the agent puts up for approval).
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harness import ROOT

REQUIRED = ("id", "operator", "outside", "check")      # and input, or inputs
PERSON_FIELDS = ("thread", "replies", "asked_again", "never_asked")
ANSWERS = {"yes": True, "no": False}


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class RunInfo:
    """What a check receives: the run to check.

    A check reads the sandbox for the truth: the records under state_dir for
    run_id, the channel's threads. It reads `replies`, the agent's replies to the
    operator in order (each a dict: function, text, actions, waiting_for), only to
    score an answer to a question the task's operator asked, or a claim of work,
    against that truth.
    `trace_path` is the run's trace file."""
    run_id: str
    task: dict
    arm: str
    sweep: int
    state_dir: Path
    replies: list = field(default_factory=list)
    trace_path: Path | None = None

    @property
    def task_id(self) -> str:
        return str(self.task["id"])


def task_files(target: Path) -> list[Path]:
    """A file, or the .yaml files directly inside a folder (not its subfolders: keep
    tasks/dev/ and tasks/sessions/, the long sessions, apart, and name them when
    you want them)."""
    target = Path(target)
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())
    raise TaskError(f"{target}: no such file or folder")


def all_task_files(root: Path = ROOT) -> list[Path]:
    folder = root / "tasks"
    return sorted(p for p in folder.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file()) if folder.exists() else []


def read(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:
        raise TaskError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskError(f"{path}: a task file is a mapping of fields")
    return data


def check_target(spec: str, root: Path = ROOT) -> tuple[Path, str]:
    """('checks/x.py', 'fn') from 'checks/x.py' or 'checks/x.py:fn'."""
    text = str(spec).strip()
    file, _, fn = text.partition(":")
    return root / file, (fn or "check")


def problems(task: dict, path: Path | None = None, root: Path = ROOT) -> list[str]:
    """Everything wrong with one task; [] when it is valid."""
    out = []
    for name in REQUIRED:
        if name not in task:
            out.append(f"no {name}")
    if "id" in task and not (isinstance(task["id"], (str, int)) and str(task["id"]).strip()):
        out.append("id is empty")
    out += _input_problems(task)
    operator = task.get("operator")
    if "operator" in task and not (isinstance(operator, list) and operator
                                   and all(isinstance(t, str) and t.strip() for t in operator)):
        out.append("operator is a list of one or more messages")
    outside = task.get("outside")
    if "outside" in task:
        if not isinstance(outside, list):
            out.append("outside is a list of people (an empty list if no one outside takes part)")
        else:
            for i, person in enumerate(outside):
                if not isinstance(person, dict):
                    out.append(f"outside[{i}] is a mapping with {', '.join(PERSON_FIELDS)}")
                    continue
                missing = [f for f in PERSON_FIELDS if f not in person]
                if missing:
                    out.append(f"outside[{i}] has no {', '.join(missing)}"
                               + (" (write null for silence)" if {"asked_again", "never_asked"} & set(missing) else ""))
                replies = person.get("replies", [])
                if not isinstance(replies, list):
                    out.append(f"outside[{i}].replies is a list (empty if they never reply)")
                elif not all(isinstance(r, str) for r in replies):
                    out.append(f"outside[{i}].replies: every reply is text; quote one YAML reads as a "
                               "number or as yes, no, on or off")
                for key in ("asked_again", "never_asked"):
                    if person.get(key) is not None and not isinstance(person.get(key), str):
                        out.append(f"outside[{i}].{key} is text or null; quote it")
                if "thread" in person and not (isinstance(person["thread"], (str, int)) and str(person["thread"]).strip()):
                    out.append(f"outside[{i}].thread names the thread they write on")
            threads = [str(p["thread"]) for p in outside if isinstance(p, dict) and "thread" in p]
            shared = sorted({t for t in threads if threads.count(t) > 1})
            if shared:
                out.append(f"outside: one stranger per thread, and {', '.join(shared)} has more than one")
    if "check" in task:
        out += check_problems(task["check"], root)
    approvals = task.get("approvals")
    if approvals is not None and not (isinstance(approvals, list)
                                      and all(str(a).lower() in ANSWERS or a in (True, False) for a in approvals)):
        out.append("approvals is a list of yes or no")
    return out


def _input_problems(task: dict) -> list[str]:
    empty = (None, "", {}, [])
    if "input" in task and "inputs" in task:
        return ["give input or inputs, not both"]
    if "inputs" in task:
        items = task["inputs"]
        if not (isinstance(items, list) and items and all(i not in empty for i in items)):
            return ["inputs is a list of one or more inputs, none of them empty"]
        return []
    if "input" not in task:
        return ["no input (or inputs, a list): say what arrives before the run"]
    if task["input"] in empty:
        return ["input is empty: say what arrives before the run"]
    return []


def check_problems(spec, root: Path = ROOT) -> list[str]:
    if not isinstance(spec, str) or not spec.strip():
        return ["check names a function in checks/: checks/<file>.py or checks/<file>.py:<function>"]
    file, fn = check_target(spec, root)
    try:
        rel = file.resolve().relative_to((root / "checks").resolve())
    except ValueError:
        return [f"check {spec}: the file must be in checks/"]
    if file.suffix != ".py" or not file.exists():
        return [f"check {spec}: checks/{rel} does not exist"]
    try:
        tree = ast.parse(file.read_text())
    except SyntaxError as exc:
        return [f"check {spec}: checks/{rel} does not parse: {exc}"]
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn for n in tree.body):
        return [f"check {spec}: checks/{rel} has no function {fn}"]
    return []


def load(path: Path, root: Path = ROOT) -> dict:
    """A valid task, with `file` added; raises TaskError naming every problem."""
    task = read(path)
    found = problems(task, path, root)
    if found:
        raise TaskError(f"{path}: " + "; ".join(found))
    task["id"] = str(task["id"])
    task["file"] = str(Path(path).resolve().relative_to(root.resolve())) if Path(path).resolve().is_relative_to(root.resolve()) else str(path)
    return task


def check_function(spec: str, root: Path = ROOT):
    """Import the check function a task names."""
    file, fn = check_target(spec, root)
    rel = file.resolve().relative_to(root.resolve()).with_suffix("")
    module = importlib.import_module(".".join(rel.parts))
    return getattr(module, fn)


def scripted_answers(task: dict) -> list[bool]:
    return [a if isinstance(a, bool) else ANSWERS[str(a).lower()] for a in task.get("approvals") or []]
