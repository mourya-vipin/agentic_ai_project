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
- The planned agent for U6, bulk changes (`agent/planner.py`, `agent/plan_schema.json`, `agent/validator.py`, `agent/executor.py`, `agent/reviser.py`, prompts in `agent/prompts/`): a plan written before any tool runs from the operator's instruction and IDs and counts our code reads; validated (schema, tools, order, observable outcomes, the plan's step limit of 1 + 4 per person, capped at five people, and only the employees the planner was given), re-planned at most twice, then `plan_rejected` with nothing run; run in order by an executor that refuses and traces any off-plan call; one checkpoint after each membership listing (`protected_memberships_have_a_step`), the only place the plan may be revised, at most twice a run. Tests in `tests/test_plan.py`.
- The detectors (`agent/detectors.py`): the repeat check on every agent-path call, the orphan check on every plan version, and the diff of each planned run against its plan (`plan_diff`). Tests in `tests/test_detectors.py`.
- The critique on `reply_draft` (`agent/critique.py`): each draft checked against what the run did in the identity store and the pending records (groups said granted, removed, waiting or kept; groups or people left out; other requests or people named), at most two rounds, a held draft never written. `sandbox/access.py: changes_by_stamp()` reads what one run granted and removed; the `critique` trace event now records `changed`. Tests in `tests/test_critique.py`.

### Removed

### Limited

- `access_grant` reaches only the groups the role policy allows for that person without a manager; every other grant can only be written down as a proposed change for the operator.
- No tool returns a credential's secret; only the operator's reveal on the person page shows one.
- `notices_send_template` and `credentials_issue` are for our workflows only; the model never sees them.
- On an employee's request, `access_revoke` removes only the requester's own groups.

### Fixes after the baseline

### Reused from our assignments and the course code

- Shruti Tewaney's Assignment 2 (`shruti072003/agentic-ai-assignment2`): `agent/validator.py` (the four checks, now with schema, unique-ID and known-employee rules and a per-plan step limit), `agent/plan.py` (`make_plan`, `revise_plan`, `read_plan`, the strict `submit_plan` tool, repair-before-replace), `agent/plan_schema.json` (the same closed, all-required shape, with `expect` in place of `expected_result`), `agent/detectors.py` and `agent/diff.py` (the repeat and orphan checks; the diff, with `out_of_order` for `reordered` and matching by `plan_step`), and `ways/planned.py` (the executor, `MAX_REPLANS = 2`, `MAX_REVISIONS = 2`, the off-plan refusal, the checkpoint check in code), moved onto our registry, bound and end reasons.
