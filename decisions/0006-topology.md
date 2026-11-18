# 0006 · Topology: one agent

Owner: Shruti Tewaney. Status: decided, 2026-11-17. Part 3 of Milestone 2 (Lecture 6).

## Decision

**One agent, for every part of the work.** `main` keeps one agent: `topology: {setting: single, values: [single]}` in `switches.yaml`. The split is built and tested on the branch `m2-split`, tagged `m2-split-final`, where `topology: single | split` lets a later pass measure it.

## The gates, per component

They run in order (slide 46) and stop at the first no. The evidence is task 1's inventory ([M2.md, Part 3](../docs/milestones/M2.md#part-3--the-topology)).

| Component | Gate 1: little talk | Gate 2: checked alone | Gate 3: buys what one agent cannot | Verdict |
|---|---|---|---|---|
| The operator router | Yes: one handoff | Yes: the checks' `misrouted` count | **No**: one model call buys nothing | One agent |
| A worker per request, for the day's batch | Yes: only the identity store, behind `requests.claim` and keyed writes | Yes: each request's check, and the end-state check | **No**: `process_requests` already gives each request its own loop and run, with a fresh context, the room a split would buy. Speed buys only the batch's clock, which nobody waits on | One agent; closest |
| U6's planner and executor | **No**: the executor needs the plan, and the reviser needs each checkpoint's results | | | One agent |
| Access and comms, U4 | **No**: the grant waits on the manager's YES, and the reply on the grant | | | One agent |
| Access and comms, U6, by tool surface | Yes: one handoff, after the removals | Yes: t06's check and the critique on each note | **No**: seven tools and one model; the hook and each use case's tool list already keep each tool to its circumstance | One agent; ties for closest, and built |
| The critique as a reviewer of another model family | Yes | Yes | **No**: a code check against the records is stronger and cheaper than a second model | One agent |

Gate 4 is reached by none.

## Assignment 6

Shruti Tewaney's Assignment 6 (`shruti072003/agentic-ai-assignment6`, `reports/LAB_06.md`, 72 runs).

- **The multiplier** of the split over one agent was **0.88× on single tasks and 0.53× on batches** (L306), against the lecture's 15× (slide 15).
- **At a matched budget, one agent did as well.**
  - One issue: S-plus, one agent at the split's tokens, passed 13 of 18, against the split's 14 of 18. That is one run apart, in the same 4-to-5 sweep range (L268–273, L349).
  - A batch: 18 of 30 issues against 17 of 30, and 0 of 6 batches each (L295–296).
- **The split won only the batch's wall-clock time:** 17.4 s, against 43.8 s for S-plus (L271–273, L425).
- Its decision was one agent (L414–429). Ours agrees.

## The split that was built

On `m2-split`: U6 as a coordinator and two roles, split by tool surface (`agent/split/`).

- **The coordinator** is our code. It records the end state the sandbox must reach, calls each role in turn, takes only returns that keep the contract, puts every note up as one approval at the plan, sends them once approved, and reads the end state back.
- **The access role** runs once per contractor, on the bounded loop with the identity tools alone.
- **The comms role** runs once per manager. It is offered `reply_send` alone, and given nothing but the access roles' returns.
- **Every call goes through the one registry and hook.** Each role has its own run key, recorded with its parent. One budget, Milestone 1's 90,000 tokens, is shared by them all.
- **The return contract** (`agent/split/return_schema.json`) holds the result, a status (found, candidate or out of budget), what was searched and not, what was spent, and what was not determined. Each return is checked against the schema and against the trace, since what was searched and spent comes from the role's own calls, never its model's words.
- **What a return cannot say is lost:** the comms role never sees the access role's reasoning, only its result.

Its benefit was to be **specialisation by tool surface**: the comms role cannot change access, and the access role cannot send. That needs truly different requirements: two tool lists that do not overlap.

## The split's tests

`tests/test_split.py` on `m2-split`, 14 tests, no tokens:

- A return that breaks the contract is rejected and traced. That covers nine kinds: a missing field, an unknown status, the spend as the model says it, a search never made, "found" with something undetermined, a candidate that names nothing undetermined, an empty result, the parent's run key, and an extra field.
- The end-state check fails planted divergent end states: a contractor still holding groups, a missing proposal, a note naming another manager's contractor, a missing note, a note without the disclosure line, and a stranger touched. It also fails a live one: every access role returned, Victor Lam's as a candidate, and the combined end state still lacked his legal hold's proposal (`SplitRolesNoProposal`).
- Every test task on the branch (t01 to t09, and the long session s01; t10 was added on `main` after the tag) runs to the end with the split on, within the one budget, each check read from the sandbox. Only U6 changes; t06 passes its check as a split.
- The shared budget ends the split (`split_budget`) when it runs out.

## The pilot

Two arms of t06 on gpt-4.1-mini, at the same commit (`126c09f` on `m2-split`), 3 sweeps each, 2026-11-17. This is a pilot, not a pass: there is no ledger prediction for it, so it can inform this decision, not credit the split.

| Arm | Passed | Tokens, mean | Of them, cache reads | Model calls | Cost, mean | Time without waits, mean |
|---|---|---|---|---|---|---|
| `results/pilot-split-t06-split/` | 3 of 3 | 18,012 | 7,936 | 13 | $0.0055 | 12.9 s |
| `results/pilot-split-t06-single/` | 3 of 3 | 10,796 | 1,109 | 5 | $0.0059 | 17.9 s |

- **The split's multiplier here is 1.67× in tokens,** for the same 3 of 3 and the same 9 tool calls. Every one of its 15 returns kept the contract, and its end-state check passed each time.
- **It cost about the same** because its many small role prompts read from the cache.
- **It finished 5 s sooner** because one agent's planner and reviser calls are long. But the operator approves the notes before anything is sent, so they wait on the approval, not on those 5 seconds.

## What would reopen it

Either of these, testable from the trace by someone else:

1. **A batch someone waits on.** In a console session's trace (`traces/`, a person at the console, not a run.py run), an operator turn that routes two or more separate requests (two `route` lines with `scope: request`) takes longer than 60 s (its `reply` line's `latency_s`). The per-request worker then passes gate 3 for speed. A split that beats one agent given the same budget, in a pass with its prediction on `main` first, would earn the change (slide 49).
2. **A call that cannot fit.** On any test task, one U6 or U4 call is over `context_budget` even after compaction (`budget_unfit`). Then a separate context per role would buy room that compaction no longer gives.

## The rejected alternative

**The split by tool surface on U6**, the one built, lost:

- It spent 1.67× the tokens and 2.6× the model calls for the same result.
- What it was to buy, keeping each tool to its role, our hook and each use case's tool list already enforce for one agent: `reply_send` is gated by the hook whoever calls it.
- The room it gives, one agent already gets from compaction and a run per request.

**What splitting would cost us:**
- a coordinator to maintain, five model surfaces where there was one, and the design to check again when the model changes;
- and, at every handoff, whatever a return leaves out, such as an access role's view of a group it read but did not record.

**What not splitting costs us:**
- U6's one agent holds both the identity tools and `reply_send`, so a mistake in its plan could reach either. The validator, the sends-last rule and the plan approval catch that, where the split would rule it out;
- and about 5 s of wall-clock time on a bulk change that waits on the operator's approval anyway.

## The spend check

(slide 18) **We relied on no published multi-agent claim.** The lecture's 15× (slide 15) entered only as an average cost, never as a reason to expect a better score, and Shruti's Assignment 6 replaced it with a multiplier measured on a system she built (0.88× and 0.53×, L306).

The three questions test a claimed gain, so none applies here: our own split showed no gain to explain. In the pilot it spent 1.67× the tokens and passed 3 of 3, as one agent did. With no better score, there is nothing to credit to the design or to its spend.
