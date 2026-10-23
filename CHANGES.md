# Changes

## Milestone 1

### Added

- The model (`gpt-4.1-mini`) and its price line; our switch groups (`control`, `plan`, `critique`, `examples`, `strict`) in place of the starter's flat `control`, `plan` and `critique`.
- The sandbox (`sandbox/`): Synthetic Corp's directory of 250 people, systems, groups and role policy, generated deterministically; a mock identity API that accepts each key once; fake credentials whose secrets no read returns; requests, pending records, template notices that check their key before sending, a fixed clock, and the demo day for `make app`. Tests in `tests/test_sandbox.py`.
- The product's own pages (`app/product.py`): today's requests, one request with its thread and changes, the directory, one person's groups and credentials with a grant and remove form, and the pending list where the operator applies, sends or dismisses what the system prepared. Tests in `tests/test_product.py`.
- The first-draft tool definitions (`agent/tools/definitions_v1.py`), the tools (`agent/tools/impl.py`) and the plain registry (`agent/registry.py`): name resolution, caller checks, a key derived for every write, and every call traced in full. Tests in `tests/test_tools.py`.

### Removed

### Limited

- `access_grant` reaches only the groups the role policy allows for that person without a manager; every other grant can only be written down as a proposed change for the operator.
- No tool returns a credential's secret; only the operator's reveal on the person page shows one.
- `notices_send_template` and `credentials_issue` are for our workflows only; the model never sees them.
- On an employee's request, `access_revoke` removes only the requester's own groups.

### Fixes after the baseline

### Reused from our assignments and the course code
