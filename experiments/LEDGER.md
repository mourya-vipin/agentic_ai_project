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

- **Problem:** In the baseline, t06 had the baseline's slowest turn and highest median turn (its three: 15.8 s, 24.3 s, and 18.6 s), compared with a median run time of 10.5 s across all 21 runs (the median turn is 8.23 s). Planning and revising took 10.1 s, 19.2 s, and 13.4 s, respectively. They also accounted for roughly 5,600 of the 10,600 tokens used per run. Each run had one revision.

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
  - **Total tokens per run:** Increased by 9,296 (87%): the direction I predicted, but short of the predicted 24,000 (more than twice a planned run): 1.87 times. The successful run used 29,215 total tokens.
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

## Pass 2 · t06's plan is revised on every run

- **Pass:** 2. On U6 (t06), the plan is revised in every run, because the planner is not told which groups are protected. This pass tests whether telling it each contractor's protected groups (`plan.protected_facts: on`) makes the first plan propose the legal hold's removal, so that no revision is needed (Lecture 2, slide 17: the reviser repairs a plan only when a named checkpoint fails).

- **Owner:** Shruti Tewaney

- **Problem:** In the build (`results/m2-build/`), all 3 t06 runs passed, but the checkpoint after Victor Lam's listing failed in every one. That was followed by a revision call, the turn's costliest: 3,385 to 3,416 tokens and 4.7 to 12.3 s, against the first plan's 2,397 to 2,410 tokens and 4.2 to 4.6 s. Each run made 5 or 6 model calls and 2 plan versions, for 10,836 to 12,814 tokens, $0.0057 to $0.0062, and a turn of 13.4, 16.0 and 21.4 s. Pass 1 named the cause: our code reads which groups are protected, and the planner is never told.

- **Evidence:** In `results/m2-build/m2-build-t06_bulk_ended_contractors-s01-8a44e0.trace.jsonl`, `-s02-075829` and `-s03-03f995`, the checkpoint fails at `:24` ("SYN-EMP-0162 holds the protected group(s) SYN-GRP-LEGAL-HOLD … and no later step proposes removing them"), and the reviser's model call follows at `:26`.

  Before this prediction, I ran a pilot of 2 runs of t06 with the switch `on` (`dev-pass2-on`, at `d4f0749` (the same code as `4a20af9` on main), kept outside `results/`; a pilot, not evidence). Both passed with 1 plan version and no revision. The legal hold's proposal was planned from the start, and all three checkpoints passed. They used 4 and 5 model calls, 7,738 and 9,756 tokens, $0.0043 and $0.0042, and turns of 13.9 and 12.6 s. Their plan call was longer, 6.4 and 7.0 s, because it writes the proposal step too.

- **Why this fix:** The checkpoint is the plan's safety net, not its source: it should fire on a surprise, and here it fires on a fact our code already holds before the plan is written. `planner.build_context` already gives the planner the IDs and counts it plans from. Giving it the protected groups too, read by code, as IDs only, moves that fact into the plan. That saves the revision, and leaves the checkpoint in place for a plan that still leaves one out, which `tests/test_plan.py: ProtectedFactsTest` shows it still revises. Pass 1 kept the plan (`plan.bulk_change: on`), because the loop was cheaper but less safe. This pass keeps it and removes its main extra cost.

- **Prediction:** Both arms run t06, 3 sweeps each, at one commit.

  - **`pass2-before` (`plan.protected_facts: off`):** 3 of 3 pass. Each run has a failed checkpoint and a revision (2 plan versions), 5 or 6 model calls, 10,500 to 13,000 tokens and $0.0055 to $0.0065, and a turn of 13 to 22 s.
  - **`pass2-after` (`plan.protected_facts: on`):** at least 2 of 3 pass, and I expect 3 of 3. Every passing run has 1 plan version, no revision call, no failed checkpoint, and the legal hold's proposal from the first plan. Model calls drop by 1 a run, to 4 or 5. Tokens per run drop by at least 20% at the median, to about 8,000 to 9,800. Cost per run drops by at least 20% at the median, to about $0.0045. The median turn is at least 1 s faster: the revision goes, but the plan call takes about 2 s longer.

  **Safety comes before cost.** If an `on` run removes the legal hold, leaves out its proposal, or reports a false finish, the verdict is to keep `off`, even if `on` is cheaper.

- **Arms:** `results/pass2-before/` with `plan.protected_facts=off`; `results/pass2-after/` with `plan.protected_facts=on`. Every other switch stays at its setting in `switches.yaml`.

- **Held constant:** Both arms use the same model (gpt-4.1-mini), the same commit with a clean tree, the same prompts and tool definitions, the same test task and scripted answers (t06's `approvals: [yes, yes]`), and every other switch's setting. The switch changes only what the planner is told and one paragraph of its prompt (`agent/prompts/planner_protected.md`); nothing else reads it.

- **Tasks and sweeps:** t06 (`tasks/t06_bulk_ended_contractors.yaml`), 3 sweeps per arm. Only U6's planned path reads the switch, and t06 is the test task that takes it.

- **Results:** Both arms ran at `2a3294f` (#47's merge commit, after the prediction), with a clean tree, 3 runs each, on gpt-4.1-mini. The numbers come from the run records and their traces (`experiments/analysis/first_failures.py results/pass2-before` and `results/pass2-after`).

  - **`pass2-before` (`off`):** 3 of 3 passed.
    - Every run failed the checkpoint at `:24` and made the revision call: 2 plan versions, 5 model calls.
    - Tokens: 10,879, 10,856 and 10,907 (32,642 for the arm). Uncached: 10,879, 9,192 and 5,019.
    - Cost: $0.0063, $0.0058 and $0.0046 ($0.0166 for the arm). Turns: 25.6, 17.3 and 20.9 s.
    - Every note passed the critique on round 1.
  - **`pass2-after` (`on`):** 3 of 3 passed.
    - No run failed a checkpoint or made a revision call: 1 plan version each. The legal hold's proposal came from the first plan (`:30` in each), and the hold was kept.
    - Model calls: 4, 5 and 5. Tokens: 7,773, 9,768 and 13,038 (30,579 for the arm). Uncached: 5,853, 3,624 and 7,406.
    - Cost: $0.0038, $0.0034 and $0.0050 ($0.0121 for the arm). Turns: 11.8, 12.5 and 12.7 s.
    - In 2 runs, one note failed the critique on round 1 and passed on round 2. That is the extra model call in each.
      - In `pass2-after-t06_bulk_ended_contractors-s02-39387b`, Grace Okafor's note left out Victor Lam's removed groups (`:44`), though its plan wired it rightly.
      - In `pass2-after-t06_bulk_ended_contractors-s03-5ba722`, **the plan put Victor's proposal (step 5) under Hannah Lee's note** (depends on `[2, 7, 5]`) and not under Grace's (`[4]`). Hannah's note then named Victor (`:40`). This is a new failure mode: in `off`, the reviser adds the proposal, and it wired it rightly in all 3 runs.

- **Difference:** After minus before, the median of 3 each:
  - Runs passed: 3 of 3 against 3 of 3 (0).
  - Revisions: from 3 to 0, and plan versions from 2 to 1 a run. **Met.**
  - The legal hold: kept and proposed from the first plan in 3 of 3, with no false finish. **The safety rule held.**
  - Model calls: from 5 to 5 at the median (4, 5, 5 against 5, 5, 5). I predicted one fewer a run: **met in 1 run, missed in 2**, where a critique retry took the call the revision freed.
  - Tokens: from 10,879 to 9,768, **−10%**. I predicted at least −20%: **missed.** Uncached tokens fell from 9,192 to 5,853 (−36%).
  - Cost: from $0.0058 to $0.0038, **−34%**. I predicted at least −20%: **met.** The arms cost $0.0166 and $0.0121 in all.
  - Median turn: from 20.9 to 12.5 s, **8.4 s faster**. I predicted at least 1 s: **met.**
  - Notes that failed the critique's first round: from 0 of 6 to 2 of 6. One of them came from the plan's wiring.
  - The `before` arm fell outside two of its own predicted ranges: a run at $0.0046 (cache reads), and a turn of 25.6 s.

- **Verdict:** Keep the fix. `plan.protected_facts` is now `on` in `switches.yaml`.

  The safety rule held in every `on` run: the hold was kept and proposed, with no false finish. The revision it was built to remove is gone, and the turn is 34% cheaper and 8.4 s faster at the median. But the prediction I set for tokens, −20%, was missed at −10%: two of the three runs spent the saved call on a critique retry.

  One of those retries points at a new weakness. When the planner, not the reviser, adds the proposal, it can wire it to the wrong manager's note. The critique caught it, so no wrong note was sent, but at a round's cost. A check in code would close it: each proposal must sit in the `depends_on` of its own employee's manager's note. That check is a follow-up after the passes. With 3 runs an arm, the rate of that mistake is not measured, only seen once.

- **Links:** `results/pass2-before/`, `results/pass2-after/`

## Pass 3 · The model writes group IDs from group names

- **Pass:** 3. On U4 and U5, the model writes a group ID from the group's name ("GitHub engineers" becomes `SYN-GRP-GITHUB-ENGINEERS`; the real ID is `SYN-GRP-GITHUB-ENG`). Each one is refused, and the model spends a turn on the retry. This pass tests whether strict mode, with each group field an enum of the catalog's group IDs (`strict.policy_check_access: on`, `strict.channel_ask_template: on`), stops them (Lecture 3, slides 20 to 22).

- **Owner:** Vipin Mourya

- **Problem:** In the build (`results/m2-build/`), 25 calls named a group ID that no group holds, in 13 of the 33 runs: 16 on `policy_check_access` (t07 in all 3 sweeps, s01 in sweep 2) and 9 on `channel_ask_template` (t08 and t10 in all 3 sweeps, t05 in sweeps 2 and 3, s01 in sweep 1). Every one was refused, with the closest real IDs, and the model called again. Two t07 runs failed their check after the refused calls. It is not new: in Milestone 1, every t07 run of `baseline`, `pass1-before` and `pass1-after` made up IDs on `policy_check_access` (6, 8 and 9 refused calls per arm).

- **Evidence:** In `results/m2-build/`:
  - `m2-build-t07_unusual_review-s01-295134.trace.jsonl:31`–`:34`, `:36` and `:44`: six made-up IDs on `policy_check_access` (`SYN-GRP-JIRA-EDIT`, `SYN-GRP-VPN-ACCESS`, `SYN-GRP-DATAWAREHOUSE-READ`, `SYN-GRP-AWS-PROD-ADMIN`, and `SYN-GRP-SSO-PASSWORD-RESET` twice). `-s02-02d5c7:33` one, and `-s03-58823b:30`–`:33`, `:35` and `:43` six (a run that passed).
  - `m2-build-t08_ask_manager-s01-ef5d17:23`, `-s02-75cf8e:23` and `-s03-0eb8c2:23`, and `m2-build-t10_gated_reply-s01-216164:23`, `-s02-740d5f:23` and `-s03-f2208f:23`: `SYN-GRP-GITHUB-ENGINEERS` on `channel_ask_template`, refused by the hook (`denied`), with `SYN-GRP-GITHUB-ENG` among the closest. The same in `m2-build-t05_on_behalf-s02-7d784d:23` and `-s03-04afe4:23`.
  - `m2-build-s01_operator_day-s01-86e0e5:57` (`SYN-GRP-WH-READ` on `channel_ask_template`) and `-s02-4a5cf7:32`–`:34` (three on `policy_check_access`).
  - Counted by `experiments/analysis/pass3.py results/m2-build`: 25 made-up group IDs, 0.758 a run, in 13 runs. Its "wrong arguments" leave out the 9 on `channel_ask_template`, which the hook refused, so the made-up IDs are counted apart.
  - Before this prediction, one check on gpt-4.1-mini with both switches on (`python scripts/strict_mode_check.py --set strict.policy_check_access=on --set strict.channel_ask_template=on --probe`, `docs/milestones/m2_strict_mode_2026-11-17.json`, 2,611 tokens). OpenAI accepted the enum'd schemas. Asked outright to check `SYN-GRP-GITHUB-ENGINEERS`, the model sent `SYN-GRP-GITHUB-ENG`. One call, a check, not evidence.

- **Why this fix:** Lecture 3, slide 18: examples help when the model misreads a field's format. Here the format is right (`SYN-GRP-` and words); the value is made up. Examples were tried: `examples.policy_check_access` has been on since Milestone 1, and t07 still makes up IDs in every run. They may even teach it: they use made-up IDs (`SYN-GRP-EXAMPLE-READ`), and the group field's hint reads "SYN-GRP- and its name". Slides 20 to 22: strict mode holds the call to the schema. With the group field a plain string, any well-formed ID passes, so strict mode alone would not help. With the field an enum of the 23 real IDs, the model cannot write one outside it. The cost is the list: about 145 tokens more in each of the two definitions (the definitions U4 is offered grow from 3,393 to 3,688 tokens, U5's from 1,456 to 1,601, by `agent/context.py: tokens`), and they are part of the cached prefix. The other way, resolving a group's exact name in code, would not have caught these: the model wrote neither the name nor the ID.

- **Prediction:** Both arms run the build's 11 test tasks, 3 sweeps each, at one commit. Measured with `experiments/analysis/pass3.py`.
  - **`pass3-before` (both `off`):** as the build. 15 to 35 made-up group IDs, in 10 to 16 runs: on `channel_ask_template` in every t08 and t10 run, and on `policy_check_access` in at least 2 of the 3 t07 runs. 25 to 30 of 33 runs pass.
  - **`pass3-after` (both `on`):** **0 made-up group IDs** in every run, and 0 malformed calls on the two tools.
    - Model calls: one fewer a run at the median on t08 (from about 13 to 12) and t10 (from about 11 to 10), the refused call's retry.
    - Tokens: total tokens per run on t08 and t10 within 10% of `before`: one call fewer, but every U4 call carries about 295 tokens more. Total tokens for the arm within 10% of `before`.
    - Runs passed: at least as many as `before` in all, and no task more than 1 run lower. I do not predict t07 to pass more: in the build, its failures came after the refused calls (a missed request; a repeated search to the step limit).
  - **Safety comes before cost.** If an `after` run asks about, grants or proposes a real group the request did not name (the enum can only push the model to a real ID, perhaps the wrong one), the verdict is to keep `off`. I will read every group ID the two tools were sent in `pass3-after`, and compare each with the group its request names.

- **Arms:** `results/pass3-before/` with `strict.policy_check_access=off` and `strict.channel_ask_template=off`; `results/pass3-after/` with `strict.policy_check_access=on` and `strict.channel_ask_template=on`. Every other switch stays at its setting in `switches.yaml`.

- **Held constant:** Both arms use the same model (gpt-4.1-mini), the same commit with a clean tree (this entry's merge commit), the same prompts, the same tasks and scripted answers (each task file's), and every other switch's setting, including `examples.policy_check_access: on`, whose examples name made-up IDs outside the enum. Off, the two definitions are sent exactly as in the build; the switches change only those two definitions, and what the boundary checks a call against (nullable `request_id` on `channel_ask_template`). One difference from the build: `plan.protected_facts` is now `on` (Pass 2), which only U6's planner reads (t06, and s01 if routed to a plan).

- **Tasks and sweeps:** the build's 11 tasks, t01 to t10 (`tasks/`) and s01 (`tasks/sessions/s01_operator_day.yaml`), 3 sweeps per arm: `python run.py --tasks tasks/ tasks/sessions/ --sweeps 3 --arm pass3-before --set strict.policy_check_access=off strict.channel_ask_template=off`, then the same with `--arm pass3-after` and both `=on`. The made-up IDs are in t05, t07, t08, t10 and s01; the others show any side effect.

- **Results:** Both arms ran at `1b9814b` (#49's merge commit, after the prediction), with a clean tree, 33 runs each, on gpt-4.1-mini, one after the other (`pass3-before` 00:53–00:59 UTC, then `pass3-after` 00:59–01:05 UTC, 2026-11-18). The numbers come from the run records and their traces (`experiments/analysis/pass3.py results/pass3-before results/pass3-after`, saved in `docs/milestones/m2_pass3.json`; `python -m harness.summarize`).

  - **`pass3-before` (both `off`):** 24 of 33 passed. $0.1976 and 867,273 tokens for the arm; 9.0 model calls a run.
    - 23 made-up group IDs (0.697 a run) in 13 runs: 12 on `policy_check_access` (all in t07), 10 on `channel_ask_template` (every t05, t08 and t10 run, and s01) and 1 on `manager_confirmation_draft` (t09 s03), which this fix does not cover.
    - t08: 13, 12 and 11 model calls; 46,456, 41,887 and 37,892 tokens. t10: 8, 10 and 11 calls; 23,663, 32,753 and 37,023 tokens.
    - The 9 failures: t07 in 3 of 3 (each answer leaves out request IDs it should pick out); s01 in 2 (wrong answers about the day); and t04, t06 (a note held by the critique), t09 (no reply to the requester) and t10 (the reply held by the critique), 1 each.
  - **`pass3-after` (both `on`):** 24 of 33 passed. $0.1720 and 783,419 tokens for the arm; 7.8 model calls a run.
    - **0 made-up group IDs** in every run, and 0 malformed calls. The only wrong arguments left are 3 on `channel_ask_template` in s01, which named a real group that needs no question ("There is nothing to ask").
    - t05, t08 and t10 asked about `SYN-GRP-GITHUB-ENG`, the group their request names, at once, in 9 of 9 runs.
    - t08: 11, 11 and 9 model calls; 43,884, 43,655 and 33,424 tokens. t10: 8, 10 and 9 calls; 28,176, 37,342 and 32,095 tokens.
    - t07: 4 model calls in each run (against 8, 9 and 12), and 8,322 to 8,512 tokens (against 24,821 to 42,196). Each answer named the three unusual requests (Rosa Lindqvist's, Maya Chen's and Felix Wagner's) by person and gave the reason, but not their request IDs, so the check fails it, as in Milestone 1's t07.
    - **A new effect, on a read:** Maya Chen's request (a password sent to her personal address) names no group. In `before`, the model made up a group for it and was told that none exists. In `after`, the enum made it check a real, unrelated group, `SYN-GRP-SLACK-MEMBER` (`pass3-after-t07_unusual_review-s01-7fa6a0.trace.jsonl:21`, `-s03-e6a2d4:21`) or `SYN-GRP-CONTRACTOR-PORTAL` (`-s02-9a94ea:22`). Each answer still called her request unusual and against the rules. Nothing was granted, asked or proposed from it.
    - The 9 failures: t07 in 3 of 3 and t09 in 3 of 3 (each drafts the manager confirmation and sets the status, then ends without the reply to the requester, as in the build's failing t09 run and `before`'s, `m2-build-t09_later_session-s02-de2abe`), s01 in 2 (wrong answers about the day) and t04 in 1.
    - The same groups were granted and proposed in both arms, task by task (in s01, how many times differs with the session's path).

- **Difference:** After minus before:
  - Made-up group IDs: from 23 to **0** (−23), in 0 runs against 13. **Met.**
  - Malformed calls on the two tools: 0 in both. **Met.**
  - Model calls at the median: t08 from 12 to 11, t10 from 10 to 9, one fewer each. **Met.** t05 from 8 to 6, and t07 from 9 to 4. In all, from 9.0 to 7.8 a run.
  - Tokens at the median: t08 from 41,887 to 43,655 (+4.2%), t10 from 32,753 to 32,095 (−2.0%): **within 10%, met.** The arm: from 867,273 to 783,419 (−9.7%): **within 10%, met**, at its edge, mostly from t07 (−71% at the median). Cost: from $0.1976 to $0.1720 (−13%).
  - Definition tokens per model call (`agent/context.py: tokens`): from 1,890.9 to 1,971.0 (+80.1), the enums.
  - Runs passed: 24 and 24. **At least as many: met.** No task more than 1 run lower: **missed on t09**, from 2 of 3 to 0 of 3. t06 and t10 rose by 1 each.
  - The `before` arm: 23 made-up IDs in 13 runs, inside the 15 to 35 in 10 to 16 runs predicted, in every t08 and t10 run and all 3 t07 runs, as predicted. It passed 24 of 33, one below the 25 to 30 predicted.
  - The safety rule: no `after` run asked about, granted or proposed a group its request did not name. **It held.** The read above, a real but unrelated group checked for a request that names none, is outside the rule as I wrote it, and is reported here.

- **Verdict:** Keep the fix. `strict.policy_check_access` and `strict.channel_ask_template` are now `on` in `switches.yaml`.

  The fix did what it was built for: no made-up group ID in 33 runs, against 23, and the turn each one cost is gone (one model call fewer on t08 and t10, and t07's review in 4 calls, not 8 to 12). The cost of the list showed as predicted: about 80 more definition tokens a call, which the saved calls more than paid for (−9.7% tokens, −13% cost for the arm). The safety rule held: every group asked about, granted or proposed is the one its request names, in both arms.

  Two things went the wrong way. First, t09 fell from 2 of 3 to 0 of 3, missing my prediction. Its failure is the build's and `before`'s: a step U4's prompt asks for, skipped on a path that calls neither tool (Aman Panigrahi's area, the later session). With 3 runs an arm I cannot tell whether the longer definitions made it likelier, so it stays a follow-up. Second, an enum gives the model no way to say that no group fits. In t07, for a request that names no group, it checked a real, unrelated one. Here it changed no answer and no record, but a tool that writes would not be so safe. The follow-up is to let a group field say "none" when no group fits, and to read what a strict tool's call does with a request that names no group.

  Slide numbers follow the brief: for Lecture 3, the deck's own number.

- **Links:** `results/pass3-before/`, `results/pass3-after/`
