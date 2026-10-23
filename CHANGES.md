# Changes

## Milestone 1

### Added

- The model (`gpt-4.1-mini`) and its price line; our switch groups (`control`, `plan`, `critique`, `examples`, `strict`) in place of the starter's flat `control`, `plan` and `critique`.
- The sandbox (`sandbox/`): Synthetic Corp's directory of 250 people, systems, groups and role policy, generated deterministically; a mock identity API that accepts each key once; fake credentials whose secrets no read returns; requests, pending records, template notices that check their key before sending, a fixed clock, and the demo day for `make app`. Tests in `tests/test_sandbox.py`.

### Removed

### Limited

### Fixes after the baseline

### Reused from our assignments and the course code
