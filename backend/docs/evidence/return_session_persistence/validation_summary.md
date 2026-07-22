# ReturnSession persistence and Temporal activity validation

Date: 2026-07-22

## Classification

```text
ReturnSession document contract:             CONTRACT_TESTED
Mongo repository create/read/transition:     CONTRACT_TESTED
Atomic session/audit/outbox transactions:    LIVE SANDBOX VALIDATED
Idempotent create and transition replay:      LIVE SANDBOX VALIDATED
Temporal persistence activities:             CONTRACT_TESTED
Temporal workflow sandbox preparation:       CONTRACT_TESTED
Live Temporal worker/server execution:        LIVE SANDBOX VALIDATED (follow-on)
Business-source and AI Gateway activities:    NOT IMPLEMENTED
Customer-facing mutation APIs:                NOT IMPLEMENTED
```

Platform MongoDB is authoritative for `ReturnSession`, immutable audit events,
and outbox events. Temporal owns coordination state only. This slice performs no
Customer, order, return, eligibility, fulfillment, tracking, or model-provider
I/O.

## Implemented boundary

- Digest-bound, revisioned `ReturnSessionDocument` with contiguous command history.
- Canonical fixed-stage compare-and-transition behavior.
- Explicit MongoDB transactions for session, audit, and outbox writes.
- Compare-and-replace guards on document identity, revision, stage, and command ID.
- Exact create and transition replay semantics.
- Stale-stage and command-evidence conflict rejection.
- Sanitized stable persistence errors with cancellation preservation.
- Unknown write/commit outcomes remain unknown and are never blindly retried.
- Injected Temporal activities that construct canonical persistence evidence.
- Workflow readiness barrier and serialized persistence transitions.
- One-attempt activity policy; callers replay the same command identity after an
  unknown result.

## Docker validation

Focused gate:

```text
Ruff format: PASS, 7 files
Ruff lint:   PASS
Strict mypy: PASS
Tests:       PASS, 21 persistence/workflow tests
Temporal sandbox preparation: PASS
```

Complete backend gate:

```text
ruff check src tests scripts/validate_return_session_persistence.py: PASS
python -m mypy --no-incremental src tests scripts/validate_return_session_persistence.py:
  PASS, 111 source files
python -m pytest -q: PASS, 873/873
```

Live MongoDB replica-set validation from the backend Docker container:

```text
ReturnSession live MongoDB transaction validation: PASS
Documents: sessions=1 audit_events=2 outbox_events=2
Idempotent create and transition replay: PASS
```

The validator uses the dedicated `return_session_live_validation` database and
drops it in cleanup. No source-system asset is read or written.

The complete `ruff format --check src tests` command still reports the same 12
pre-existing unrelated files. All files in this slice pass the focused format
gate; unrelated formatting was preserved.

## Next bounded step

Completed in the follow-on evidence at
`backend/docs/evidence/return_workflow_live/validation_summary.md`. The next slice
defines intake and order-discovery activity-result contracts and persists their
bounded context evidence before any AI Gateway or eligibility decision work.
