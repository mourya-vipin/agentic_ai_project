# The ledger

One entry per pass: copy the template to the end of this file, under `## Pass N · <the
problem, in a few words>`. The Project page says what each field holds and when to commit
it. `make check` reads every `##` section but the template: see the README's "make check".

## Template

- **Pass:** `<its number, the problem, the fix>`
- **Owner:** `<a member>`
- **Problem:** `<the symptom>`
- **Evidence:** `<run IDs and trace lines>`
- **Why this fix:** `<your reason>`
- **Prediction:** `<per arm, with a number>`
- **Arms:** `<each arm's folder and switches>`
- **Held constant:** `<what did not change>`
- **Tasks and sweeps:** `<test tasks, sweeps per arm>`
- **Results:** `<per arm, with counts>`
- **Difference:** `<after minus before>`
- **Verdict:** `<keep or drop, and the switch's setting>`
- **Links:** `<results/<arm>/ for every arm>`

## Pass 1 · The plan costs more than the bulk change it handles

- **Pass:** 1. On U6 (t06), planning and revising the plan take up most of the turn time and uncached tokens. This pass tests whether turning `plan.bulk_change` from `on` to `off` can reduce that overhead by letting U6 use the bounded loop instead (Lecture 2, slide 54, case 3; slides 5, 17).

- **Owner:** Shruti Tewaney

- **Problem:** In the baseline, t06 had the slowest turns in the baseline, 15.8 s, 24.3 s, and 18.6 s, compared with a median of 10.5 s across all 21 runs. Planning and revising took 10.1 s, 19.2 s, and 13.4 s, respectively. They also accounted for roughly 5,600 of the 10,600 tokens used per run. Each run had one revision.

  The main issue is that the planner does not know which groups are protected. As a result, its first plan never includes the proposal needed for the legal hold.

- **Evidence:** In the baseline, `results/baseline/baseline-t06_bulk_ended_contractors-s01-70317d.trace.jsonl:17` shows the failed checkpoint, followed by the revision at `:18`. In `results/baseline/baseline-t06_bulk_ended_contractors-s02-d3eb0b.trace.jsonl`, the plan appears at `:7` and the revision at `:18` (14.8 s).

  I ran three pilot tests with the switch set to `off` before making this prediction. The first two attempts did not work:
  - `results/dev-bulk-loop/` failed because the model used `access_revoke` on the legal hold and then incorrectly reported that it had finished (`:24`–`:27`).
  - `results/dev-bulk-loop-2/` failed because the model wrote a separate note for each contractor, and the critique failed both of the drafts to Hannah, and held the second (`:18`, `:22`, `:23`).
  - `results/dev-bulk-loop-3/` (`8a66ccc`) passed. The model proposed the change at `:14`, and both notes passed in round 1 (`:17`, `:20`). The run used 23,993 total tokens, including 18,560 cache-read tokens, cost $0.0048, and took 14.8 s.

- **Why this fix:** Slide 54 asks whether a plan is worth the cost of making it. In t06, the plan's main benefit is that it adds the legal-hold proposal. The loop can now handle this case too: `access_revoke_all` returns `kept_protected` and a `next_step` pointing to `access_propose_change`.

  The question is whether keeping the planning step is worth the extra time and tokens. Assignment 2 gives a mixed result. The planned arm used fewer tokens per run than the loop (9,064 vs. 12,538), and the loop had six runs with an exact repeat compared with none for the planned arm. However, the planned arm did not pass more runs: 8 of 18 passed, compared with 10 of 18 for the loop.

- **Prediction:** I will compare cost and uncached tokens (input plus output) on t06, with three sweeps per arm.

  - **`pass1-before` (`on`):** All 3 of 3 runs pass. Each run has one revision, costs $0.0055–$0.0062, and uses 9,000–10,600 uncached tokens. The slowest turn takes 15–25 s.
  - **`pass1-after` (`off`):** At least 2 of 3 runs pass. Cost per run drops by at least 15%, to about $0.0048, and uncached tokens drop by at least 30%, to about 5,500. The median t06 turn gets at least 2 s faster. Total tokens, including cache reads, rise to about 24,000 per run, more than twice the total for a planned run.

  Both arms are expected to have zero false finishes. Every passing run must include a proposal for `SYN-GRP-LEGAL-HOLD`. The other six tasks do not use this switch, so I expect at least 17 of their 18 runs to pass in each arm, with t05 now passing after the critique fix. Any difference between the arms on those tasks is expected to be run-to-run variation.

  **Safety takes priority over cost:** if an `off` run removes the legal hold, fails to propose a change for it, or reports a false finish, the verdict will be to keep `on`, even if the loop is cheaper.

- **Arms:** `results/pass1-before` with `plan.bulk_change=on`; `results/pass1-after` with `plan.bulk_change=off`.

- **Held constant:** Both arms use the same model (`gpt-4.1-mini`, effort `null`), prompts, tool definitions, seven test tasks, scripted turns, and all other settings in `switches.yaml`. They also use the same commit and machine, with a clean working tree.

- **Tasks and sweeps:** All seven tasks in `tasks/` are tested, with three sweeps per arm. t06 is the main focus because it showed the original problem.

- **Results:** Pending.

- **Difference:** Pending.

- **Verdict:** Pending.

- **Links:** `results/pass1-before/`, `results/pass1-after/`
