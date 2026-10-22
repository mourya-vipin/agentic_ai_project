"""The run records: results/<arm>/<run_id>.json, one per run, and its trace beside it.

A record holds the run ID, the arm, the task and the sweep; every switch's setting;
the model and backend, the settings' digest and the price sheet's date; the commit
the code ran from (`commit`, the git SHA) and `dirty`, true if the code had
uncommitted changes (results/, traces/, experiments/, docs/, decisions/, ops/,
eval/labels/, eval/thresholds/, README.md, ARCHITECTURE.md and CHANGES.md are not code; both null outside a git repository); the
tokens (input and output, cached and uncached), the cost and the model calls; the
latency with and without the waits (`latency_without_wait_s` leaves out the wait for
an outside reply, `wait_s`, and the time the backend slept out a rate limit or an
overload, `throttled_s`), and the agent's own time for each turn (`turn_latency_s`:
one number per operator_turn and on_outside_reply call, in order, without the
time the turn spent in wait_for_reply or sleeping out a rate limit); how the run ended
(`outcome`, the runner's view); every end reason your code wrote with trace.end, in
order (`end_reasons`), and the last of them (`end_reason`); what the strangers did;
the check's result, and the counts it returned (`measures`); and, in full, every
tool call (`tool_calls`: id, name, input, output) and every reply the agent gave the
operator (`replies`). run.py writes them; nothing else should. Commit them: your
ledger links to them.

A run on a scripted model (--backend never-stops, harness.scripted:..., or a
module:Class of your own that says free = True) is a test, not evidence: its
record goes to results/_free/<arm>/, which make check and the summarizer leave out.

`python run.py --discard --arm <name>` moves an arm's folder to
results/discarded/<name>-<time>/, out of every count.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harness.formats import UNFINISHED
from harness.paths import results_dir

ARM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DISCARDED = "discarded"      # results/discarded/ holds the arms you discarded
FREE = "_free"               # results/_free/ holds the runs on a scripted or fake model: tests, never counted


def free_dir(root: Path | None = None) -> Path:
    return Path(root or results_dir()) / FREE


def arm_dir(arm: str, root: Path | None = None, free: bool = False) -> Path:
    """results/<arm>/, or results/_free/<arm>/ for the runs on a scripted model."""
    if not ARM_NAME.match(arm or ""):
        raise ValueError(f"arm name {arm!r}: use letters, digits, '-', '_' and '.', such as control-agent")
    if arm == DISCARDED:
        raise ValueError(f"results/{DISCARDED}/ holds the arms you discarded; give the arm another name")
    return (free_dir(root) if free else Path(root or results_dir())) / arm


# What is not code: the records, the traces, the ledger and analysis, the write-ups, the
# decision records, the operations notes, and an evaluation's hand labels and thresholds.
NOT_CODE = (":(exclude)results", ":(exclude)traces", ":(exclude)experiments", ":(exclude)docs",
            ":(exclude)decisions", ":(exclude)ops", ":(exclude)eval/labels", ":(exclude)eval/thresholds",
            ":(exclude)README.md", ":(exclude)ARCHITECTURE.md", ":(exclude)CHANGES.md")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)


def source(root: Path) -> dict:
    """{"commit": the git SHA the code runs from, "dirty": whether the code has
    uncommitted changes (NOT_CODE aside)}; both None outside a git repository."""
    try:
        head = _git(root, "rev-parse", "HEAD")
        if head.returncode != 0:
            return {"commit": None, "dirty": None}
        status = _git(root, "status", "--porcelain", "--", ".", *NOT_CODE)
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None}


def code_changed(root: Path, old: str | None, new: str | None) -> bool:
    """Whether the code differs between two commits (NOT_CODE aside). True when it cannot tell."""
    if not old or not new:
        return True
    if old == new:
        return False
    try:
        return _git(root, "diff", "--quiet", old, new, "--", ".", *NOT_CODE).returncode != 0
    except (OSError, subprocess.SubprocessError):
        return True


def code_diff(root: Path, old: str | None, new: str | None) -> list[str]:
    """The code files that differ between two commits (NOT_CODE aside); [] when none do."""
    if not old or not new or old == new:
        return []
    try:
        done = _git(root, "diff", "--name-only", old, new, "--", ".", *NOT_CODE)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in done.stdout.splitlines() if line.strip()]


def write(record: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record['run_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2, default=str) + "\n")
    tmp.replace(path)
    return path


def read(folder: Path) -> list[dict]:
    """Every record in one arm's folder, oldest first."""
    out = []
    for path in sorted(Path(folder).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "run_id" in record:
            record["_path"] = str(path)
            out.append(record)
    return sorted(out, key=lambda r: (str(r.get("started", "")), r["run_id"]))


def arms(root: Path | None = None) -> list[Path]:
    """Every arm folder that holds run records (results/discarded/ and results/_free/ are none of them)."""
    root = Path(root) if root else results_dir()
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and p.name not in (DISCARDED, FREE) and any(p.glob("*.json")))


def is_free_folder(folder: Path) -> bool:
    """Whether a folder is results/_free/ or one of its arms."""
    folder = Path(folder).resolve()
    return FREE in (folder.name, folder.parent.name)


def finished(record: dict) -> bool:
    """A run that counts: it was not stopped by a quota or a rate limit, and the agent was built."""
    return record.get("outcome") not in UNFINISHED
