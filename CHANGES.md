# Changes

## Milestone 1

### Added

- The model (`gpt-4.1-mini`) and its price line; our switch groups (`control`, `plan`, `critique`, `examples`, `strict`) in place of the starter's flat `control`, `plan` and `critique`.
- The sandbox (`sandbox/`): Synthetic Corp's directory of 250 people, systems, groups and role policy, generated deterministically; a mock identity API that accepts each key once; fake credentials whose secrets no read returns; requests, pending records, template notices that check their key before sending, a fixed clock, and the demo day for `make app`. Tests in `tests/test_sandbox.py`.
- The product's own pages (`app/product.py`): today's requests, one request with its thread and changes, the directory, one person's groups and credentials with a grant and remove form, and the pending list where the operator applies, sends or dismisses what the system prepared. Tests in `tests/test_product.py`.
- The first-draft tool definitions (`agent/tools/definitions_v1.py`), the tools (`agent/tools/impl.py`) and the plain registry (`agent/registry.py`): name resolution, caller checks, a key derived for every write, and every call traced in full. Tests in `tests/test_tools.py`.
- The registry's checks (`agent/registry.py`, `agent/tools/rules.py`): arguments validated at the boundary against each tool's rules and its definition's schema, with a repair fact on rejection; a pre-call hook that denies the call if it raises; transient failures retried in code (3 attempts, backoff 0.05 s then 0.1 s); results checked against their declared shape; what the model reads capped at 4,000 characters, saying so when cut; attempts, rejections and denials in the call record. Tests in `tests/test_registry.py`.
- `tests/fakes.py: BadArgs`, a fake model that calls each tool once with one bad argument, and `tests/test_tools_errors.py`: every tool's bad argument comes back as an execution error that says what was wrong, what would be right and whether a retry can work. Errors without that last part now get it from the registry.
- `scripts/definition_cost.py`: the same one-message call with and without each set of tool definitions, the difference in the input tokens the API reports.
- The planned agent for U6, bulk changes (`agent/planner.py`, `agent/plan_schema.json`, `agent/validator.py`, `agent/executor.py`, `agent/reviser.py`, prompts in `agent/prompts/`): a plan written before any tool runs from the operator's instruction and IDs and counts our code reads; validated (schema, tools, order, observable outcomes, the plan's step limit of 4 per person, capped at five people, and only the employees the planner was given), re-planned at most twice, then `plan_rejected` with nothing run; no plan at all when no ended contractor holds access (`nothing_to_do`); run in order by an executor that refuses and traces any off-plan call; one checkpoint after each membership listing (`protected_memberships_have_a_step`), the only place the plan may be revised, at most twice a run. Tests in `tests/test_plan.py`.
- The detectors (`agent/detectors.py`): the repeat check on every agent-path call, the orphan check on every plan version, and the diff of each planned run against its plan (`plan_diff`). Tests in `tests/test_detectors.py`.
- The critique on `reply_draft` (`agent/critique.py`): each draft checked against what the run did in the identity store and the pending records (groups said granted, removed, waiting or kept; groups or people left out; other requests or people named), at most two rounds, a held draft never written. `sandbox/access.py: changes_by_stamp()` reads what one run granted and removed; the `critique` trace event now records `changed`. Tests in `tests/test_critique.py`.
- The test tasks (`tasks/t01_standard_jira.yaml` to `tasks/t07_unusual_review.yaml`, and `tasks/dev/dev_same_access_small.yaml` for tuning prompts) and one check each (`checks/<task>.py`), which read the sandbox and the channel for the run's ID and return the same named counts (`checks/common.py`: grants, removals, proposals, drafts, notices, duplicate effects, wrong grants, misrouted, secrets in messages, writes). Tests in `tests/test_checks.py`.
- Routing (`agent/router.py`, prompts `agent/prompts/route_operator.md` and `route_request.md`): one traced model call (`route_operator`) tells a new task from a question, with the session's last three turns, and falls back to the read-only path when its answer does not parse; each request is routed from its thread first (the HR feed's fixed fields are parsed in code), then by one traced call (`route_request`) whose answer our code checks: a standard grant only if it is a grant, for the requester (by ID, with no other person of the directory in the text), of exactly one catalog group the role policy allows without a manager. The route is recorded on the request and in the trace; `control.<use_case>` picks the path. A request the agent leaves new or in progress is set to `pending_operator` by our code, and the operator is told; so is an HR feed message that does not parse. Tests in `tests/test_routing.py`.
- The workflows (`agent/workflows.py`): U1 standard grant, U2 onboarding (birthright groups, the SSO credential and its hand-over record), U3 offboarding (all access, a proposed removal for each protected group); a failed step leaves the request `pending_operator` and ends `workflow_refused`.
- The bounded loop (`agent/loop.py`, prompts `nonstandard_request.md`, `operator_review.md`, `bulk_change.md`): the step limit, token budget and wall clock checked before every model call and written into the trace; the repeat check on every call; the second protocol error ends the loop (`protocol_error`); a progress line every three steps. Tests in `tests/test_bound.py`.

- `tests/test_no_secrets.py`: `make test` fails if any tracked file holds a key-shaped string (OpenAI, AWS, GitHub, Slack, Google keys, private keys, a password in a URL) or if a `.env` file is tracked.
- `credentials_record_handover`, a workflow-only tool for the new starter's credential hand-over, so the onboarding workflow's last write goes through the registry, keyed and traced, like every other write (it used `sandbox/pending.py` directly).

- The rewritten tool definitions (`agent/tools/definitions.py`), which the registry now serves; the first draft stays in `agent/tools/definitions_v1.py`, and its names still resolve. Namespaced names; descriptions that say what each tool does, when to use another, its result and what an empty one means; `policy_check_access` takes up to 20 groups in one call; `directory_get_employee` also finds one person by name; today's requests carry each requester's name, role and status; `access_revoke_all` returns a summary sentence. Repair facts name the closest real IDs, or today's date. Examples are rendered into a description while `examples.<tool>` is on, and `strict` is set while `strict.<tool>` is on. Tests in `tests/test_definitions.py`.
- The switches `examples.policy_check_access` and `strict.directory_get_employee` (both `added: 2026-10-25`), chosen from the dev-v1 failure labels.

### Removed

- The starter's `tasks/example.yaml` and `checks/example.py`.

- `strict.access_propose_change`: planned before the runs, dropped after them, since no run made a malformed call to that tool (M1.md, Part 3).

### Limited

- `access_grant` reaches only the groups the role policy allows for that person without a manager; every other grant can only be written down as a proposed change for the operator.
- No tool returns a credential's secret; only the operator's reveal on the person page shows one.
- `notices_send_template` and `credentials_issue` are for our workflows only; the model never sees them.
- On an employee's request, `access_revoke` removes only the requester's own groups.

- On a non-standard request, `access_grant` grants only to the requester or to someone who reports to the requester; anyone else's grant is refused with a pointer to `manager_confirmation_draft`. The case's request is the one the router assigned, whatever request the model names. (Found in the console session `traces/s-20261025-175741-0501.jsonl:88`, where the agent granted Dev Rao a group on a teammate's request.)

### Fixes after the baseline

- The critique read "cannot be granted" as a claim that a group was granted, so it held a true reply (`baseline-t05_on_behalf-s02-3d3af4`, trace lines 19 and 22) and failed a first draft in `baseline-t05_on_behalf-s03-8e0479` (line 19). `agent/critique.py` now treats "cannot" as a negation, and a modal or future ("can be", "will be", "to be") as no claim. Tests in `tests/test_critique.py`. Found by Aman Panigrahi.

### Reused from our assignments and the course code

- Shruti Tewaney's Assignment 2 (`shruti072003/agentic-ai-assignment2`): `agent/validator.py` (the four checks, now with schema, unique-ID and known-employee rules and a per-plan step limit), `agent/plan.py` (`make_plan`, `revise_plan`, `read_plan`, the strict `submit_plan` tool, repair-before-replace), `agent/plan_schema.json` (the same closed, all-required shape, with `expect` in place of `expected_result`), `agent/detectors.py` and `agent/diff.py` (the repeat and orphan checks; the diff, with `out_of_order` for `reordered` and matching by `plan_step`), and `ways/planned.py` (the executor, `MAX_REPLANS = 2`, `MAX_REVISIONS = 2`, the off-plan refusal, the checkpoint check in code), moved onto our registry, bound and end reasons.
- Aman Panigrahi's Assignment 1 (`amanpanigrahi2305/agentic-ai-assignment1`): `agent/loop.py` (the bound checked in code before every model call, each assistant turn appended unchanged, a step's tool results sent back in one user turn) for `agent/loop.py`, now calling our registry, with our bound, end reasons, repeat check, protocol-error stop and progress lines; and `ways/workflow.py`'s shape (gather, choose, post) for `agent/workflows.py`, with no model call and our registry's tools.
