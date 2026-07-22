# Temporal Return workflow deterministic core validation

Date: 2026-07-22

## Classification

```text
Workflow execution contracts:        CONTRACT_TESTED
Deterministic transition policy:      CONTRACT_TESTED
Temporal data conversion:             CONTRACT_TESTED
Temporal sandbox preparation:         CONTRACT_TESTED
Live Temporal server execution:       NOT VALIDATED
ReturnSession MongoDB persistence:     NOT IMPLEMENTED
Workflow activities and worker:       NOT IMPLEMENTED
```

This slice owns execution coordination only. It contains no Customer, order,
return, eligibility, fulfillment, or tracking business facts. Platform MongoDB
remains authoritative for `ReturnSession`, audit, decision, and outbox state.

## Implemented boundary

- Fixed code-owned stage sequence from `INTAKE` through `COMPLETED`.
- Immutable Temporal-serializable dataclass input, command, and state contracts.
- Canonical UUID validation for session, correlation, and command identities.
- Required unique configuration-version bindings.
- Ordered stage completion with no skipping.
- Idempotent identical command replay.
- Conflict rejection when a command ID is reused with different evidence.
- Stable safe transition error codes and messages.
- Temporal workflow query `execution_state`.
- Temporal workflow update `complete_stage`.
- Temporal workflow name `return-platform-return-v1`.

## Docker commands and results

Focused gate in `python:3.13-slim`:

```text
ruff format --check src/return_platform/workflows tests/test_return_workflow.py
ruff check src/return_platform/workflows tests/test_return_workflow.py
python -m mypy --no-incremental src/return_platform/workflows tests/test_return_workflow.py
python -m pytest -vv tests/test_return_workflow.py
```

Result:

```text
Ruff format: PASS
Ruff lint:   PASS
Strict mypy: PASS, 3 source files
Tests:       PASS, 10/10
Exit code:   0
```

Complete repository gates:

```text
ruff check src tests:                    PASS, 107 source files
python -m mypy --no-incremental src tests: PASS, 107 source files
python -m pytest -q:                     PASS, 862/862
Exit code:                               0
```

The complete `ruff format --check src tests` command remains blocked by 12
pre-existing files outside this slice that Ruff 0.15.21 would reformat. Those
unrelated files were preserved. All files added by this workflow slice pass the
focused format gate.

## Next bounded step

Implement the Platform MongoDB `ReturnSession` repository and Temporal activity
boundary that atomically persists stage transitions and audit/outbox evidence.
Do not add external business-source activities, retry policy, return decisions,
or customer-facing APIs until that ownership boundary is contract-tested.
