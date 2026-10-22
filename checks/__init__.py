"""The task checks: one function per kind of task, each deciding pass or fail.

A task file names its check as checks/<file>.py (its function `check`) or
checks/<file>.py:<function>. run.py calls it after the run with a RunInfo
(harness.tasks.RunInfo): the run's ID, the task, the state folder where the
sandbox is, the agent's replies to the operator (`replies`) and the run's trace
file (`trace_path`). It returns True or False, or (passed, reason), or (passed,
reason, measures): `measures` is a dict of named counts, such as
{"questions_asked": 2}, kept in the run record as `measures`. Your analysis
scripts read them, and `python -m harness.summarize ... --measure <name>` sums one
per arm.

The rule: a check reads the sandbox for the truth, your records for this run's
ID and the messages on a thread. It reads the agent's replies only to score an
answer to a question the task's operator asked, such as a question about earlier
work after a compaction, or a claim of work, a reply that says something was
done, against the truth it read from the sandbox. A run never passes because the
agent said it succeeded. A check never imports agent/.

Test each check as in Assignment 1: it fails a run that reported success and left
no effect, and passes a planted right effect from a run that said nothing.
"""
