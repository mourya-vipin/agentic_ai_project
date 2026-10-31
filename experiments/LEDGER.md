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

- **Problem:** In the baseline, t06 had the slowest turns in the baseline, 15.8 s, 24.3 s, and 18.6 s, compared with a median run time of 10.5 s across all 21 runs (the median turn is 8.23 s). Planning and revising took 10.1 s, 19.2 s, and 13.4 s, respectively. They also accounted for roughly 5,600 of the 10,600 tokens used per run. Each run had one revision.

  The main issue is that the planner does not know which groups are protected. As a result, its first plan never includes the proposal needed for the legal hold.

- **Evidence:** In the baseline, `results/baseline/baseline-t06_bulk_ended_contractors-s01-70317d.trace.jsonl:17` shows the failed checkpoint, followed by the revision at `:18`–`:19`. In `results/baseline/baseline-t06_bulk_ended_contractors-s02-d3eb0b.trace.jsonl`, the plan appears at `:7` and the revision at `:18` (14.8 s).

  I ran three pilot tests with the switch set to `off` before making this prediction. The first two attempts did not work:
  - `results/dev-bulk-loop/` failed because the model used `access_revoke` on the legal hold and then incorrectly reported that it had finished (`:24`–`:27`).
  - `results/dev-bulk-loop-2/` failed because the model wrote a separate note for each contractor, and the critique failed both of the drafts to Hannah, and held the second (`:18`, `:22`, `:23`).
  - `results/dev-bulk-loop-3/` (`8a66ccc`) passed. The model proposed the change at `:14`, and both notes passed in round 1 (`:17`, `:20`). The run used 23,993 total tokens, including 18,560 cache-read tokens, cost $0.0048, and took 14.8 s.

- **Why this fix:** Slide 54 asks whether a plan is worth the cost of making it. In t06, the plan's main benefit is that it adds the legal-hold proposal. The loop can now handle this case too: `access_revoke_all` returns `kept_protected` and a `next_step` pointing to `access_propose_change`.

  The question is whether keeping the planning step is worth the extra time and tokens. My Assignment 2 (`shruti072003/agentic-ai-assignment2`, `reports/LAB_02.md`, lines 79–90) gives a mixed result. The planned arm used fewer tokens per run than the loop (9,064 vs. 12,538), and the loop had six runs with an exact repeat compared with none for the planned arm. However, the planned arm did not pass more runs: 8 of 18 passed, compared with 10 of 18 for the loop.

- **Prediction:** I will compare cost and uncached tokens (input plus output) on t06, with three sweeps per arm.

  - **`pass1-before` (`on`):** All 3 of 3 runs pass. Each run has one revision, costs $0.0055–$0.0062, and uses 9,000–10,600 uncached tokens. The slowest turn takes 15–25 s.
  - **`pass1-after` (`off`):** At least 2 of 3 runs pass. Cost per run drops by at least 15%, to about $0.0048, and uncached tokens drop by at least 30%, to about 5,500. The median t06 turn gets at least 2 s faster. Total tokens, including cache reads, rise to about 24,000 per run, more than twice the total for a planned run.

  Both arms are expected to have zero false finishes. Every passing run must include a proposal for `SYN-GRP-LEGAL-HOLD`. The other six tasks do not use this switch, so I expect at least 17 of their 18 runs to pass in each arm, with t05 now passing after the critique fix. Any difference between the arms on those tasks is expected to be run-to-run variation.

  **Safety takes priority over cost:** if an `off` run removes the legal hold, fails to propose a change for it, or reports a false finish, the verdict will be to keep `on`, even if the loop is cheaper.

- **Arms:** `results/pass1-before` with `plan.bulk_change=on`; `results/pass1-after` with `plan.bulk_change=off`.

- **Held constant:** Both arms use the same model (`gpt-4.1-mini`, effort `null`), prompts, tool definitions, seven test tasks, scripted turns, and all other settings in `switches.yaml`. They also use the same commit and machine, with a clean working tree.

- **Tasks and sweeps:** All seven tasks in `tasks/` are tested, with three sweeps per arm. t06 is the main focus because it showed the original problem.

- **Results:** I collected the numbers from the run records and `experiments/analysis/analyze.py`. The analysis output is saved in `docs/milestones/m1_pass1.json`. Both arms have 21 runs, and all runs used commit `2ca29b2`.

  - **`pass1-before` (`on`):** 20 of 21 runs passed. All three t06 runs passed, and each had one failed checkpoint followed by one plan revision. All three also proposed removing `SYN-GRP-LEGAL-HOLD`.

    Each t06 run cost $0.0058–$0.0063, with a mean of $0.0061. Uncached tokens ranged from 9,090 to 10,750 (mean 10,136), while total tokens ranged from 10,569 to 10,754 (mean 10,691). Turn times were 13.4–14.4 s, with a median of 13.4 s.

    The other six tasks passed 17 of 18 runs. The only failure was t07 s03, where the answer missed SYN-REQ-0002, 0004, and 0006. The script counted this as a false finish. But the answer was right in substance: it named the three unusual requests by person and employee ID (Rosa Lindqvist, Maya Chen, Felix Wagner) and called the other three routine. It gave no request IDs, which the U5 prompt asks for and the check reads. Counted by substance, this arm passed 21 of 21. Across all 21 runs, the arm cost $0.0728, or about $0.0035 per run, and had a median turn time of 6.10 s.

  - **`pass1-after` (`off`):** 18 of 21 runs passed. t06 passed only 1 of 3 runs, and the failures show what can go wrong without the planning step.

    - `pass1-after-t06_bulk_ended_contractors-s01-22e631` made up a date and called `directory_list_contractors_ended` with `before: "2024-06-01"`. The tool returned no contractors (`:8`), so the model told the operator that no removals were needed and ended with `done` (`:10`). It never removed any access. This was a false finish: the model used the right tool but passed the wrong arguments (Lecture 3, slide 8).

    - `pass1-after-t06_bulk_ended_contractors-s02-9c1d9f` removed the correct groups and proposed removing the legal hold (`:10`–`:14`). However, it put Victor Lam in the note to Hannah Lee, even though Victor reports to Grace Okafor. The critique caught the mistake in both rounds (`:17`, `:22`) and held the note (`:24`). The check worked as intended, but no manager received a note.

    - s03 passed and included the proposal to remove the legal hold.

    Each t06 run cost $0.0009–$0.0046, with a mean of $0.0032. Uncached tokens ranged from 630 to 2,467 (mean 1,768), while total tokens ranged from 6,262 to 29,215 (mean 19,987). Turn times were 2.6–11.1 s, with a median of 10.5 s.

    The other six tasks again passed 17 of 18 runs. This time, t07 s01 failed for the same reason as in `pass1-before`: the answer missed the required IDs. Again, the answer named the right three requests by person and employee ID, but gave no request IDs. Counted by substance, this arm passed 19 of 21. The t06 false finish is real. Across all 21 runs, the arm cost $0.0560, or about $0.0027 per run, and had a median turn time of 3.01 s.

- **Difference:** The following compares `pass1-after` against `pass1-before` on t06, with three runs in each arm.

  - **Runs passed:** Dropped from 3 of 3 to 1 of 3 (−2). I predicted at least 2 of 3, so this prediction was **missed**.
  - **False finishes:** Increased by 1, due to s01. I predicted zero, so this prediction was **missed**.
  - **Cost per run:** Fell by $0.0028 (47%), meeting my prediction of at least a 15% decrease. However, the cheapest run cost just $0.0009 because it finished without doing the required work. The one run that passed cost $0.0046, still 23% less than the planned arm's t06 mean ($0.0061).
  - **Uncached tokens per run:** Fell by 8,368 (83%), exceeding my predicted decrease of at least 30%. This was a much larger drop than expected, with the mean falling to 1,768 instead of the predicted 5,500.
  - **Total tokens per run:** Increased by 9,296 (87%): the direction I predicted, but short of the predicted 24,000, more than twice. The successful run used 29,215 total tokens.
  - **Median t06 turn time:** Dropped from 13.4 s to 10.5 s, a decrease of 2.8 s. This met my prediction of at least 2 s. However, the shortest turn, at 2.6 s, was the false finish.
  - **Revisions:** Decreased by 3 because the loop does not generate or revise a plan.
  - **Runs with a repeated call:** 0 in both arms (no `repeat_call` event in either), so neither path repeated a call.
  - **The plan, in the `on` arm:** 0 plans rejected by the validator, 3 revisions (one per t06 run, each after the legal-hold checkpoint failed), 0 runs ended at `plan_rejected` or `revision_bound`, 0 refused off-plan calls and 0 orphan flags. The `off` arm wrote no plan, so it has none of these.
  - **Legal-hold proposal:** Every passing run in both arms included the proposal, as predicted.

  The other six tasks showed no difference in pass rate: both arms passed 17 of 18 runs, and each had one t07 failure (18 of 18 by substance, as the Results say). Their median turn time dropped from 3.8 s to 2.9 s, even though these tasks do not use the switch. This suggests that API speed differences between the sweeps contributed to the drop in the full arm's median turn time, from 6.10 s to 3.01 s. I therefore cannot attribute that overall speedup to turning planning off alone.

  My prediction for the slowest t06 turn in `pass1-before` was also off. The observed range was 13.4–14.4 s, rather than the predicted 15–25 s.

- **Verdict:** Keep `plan.bulk_change` set to `on` in `switches.yaml`. I made no change to the setting.

  The results did not meet my expectations for the loop. Only one of the three t06 runs passed, and one run falsely reported that it had finished without removing any access. The loop was cheaper and used fewer uncached tokens, but those savings are not worth the risk of an incorrect result.

  The planned path avoids both mistakes by giving the planner the current date and the contractor and manager IDs. It does not need to guess a date or read the managers from a tool result. With only three runs per arm, I cannot draw a firm conclusion about the long-term pass rate, but the false finish is enough to trigger the safety rule I set beforehand.

  This pass did not test planning alone. Our code gives the planner today's date and each contractor's manager, and the loop gets no date. The loop's false finish came from a date it made up. Also, the arms ran one after the other (`pass1-before` 23:31–23:34 UTC, then `pass1-after` 23:34–23:36 UTC), so the API speed was not the same for both. The verdict still stands, by the safety rule I set before the runs.

  Slide 54 asks whether a plan is worth its cost. For t06, I would keep the plan. It costs more, but it also gives the model the information it needs to carry out the change correctly.

  Slide numbers follow the brief: for Lecture 2 that is the deck's own number plus 2, and for Lecture 3 the deck's own number.

- **Links:** `results/pass1-before/`, `results/pass1-after/`
