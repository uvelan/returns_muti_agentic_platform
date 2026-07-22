# Return eligibility and AI Gateway boundary validation

Date: 2026-07-22

## Classification

```text
EligibilityEvaluationInput:                  CONTRACT_TESTED
EligibilityActivityResult:                  CONTRACT_TESTED
Provider-neutral AI Gateway port:            CONTRACT_TESTED
Single-attempt timeout boundary:              CONTRACT_TESTED
Deterministic REVIEW_REQUIRED fallback:       CONTRACT_TESTED
Eligibility ContextSnapshot persistence:      LIVE SANDBOX VALIDATED
AgentDecision atomic persistence:             LIVE SANDBOX VALIDATED
Live model-provider adapter/call:              NOT ENABLED
Production eligibility policy:                NOT ENABLED
```

## Implemented boundary

- Input is built only from persisted `intake-v1` and `order-discovery-v1` snapshots.
- Request and customer references must agree across both snapshots.
- Exactly one gateway attempt is allowed, and cancellation is preserved.
- Timeout, sanitized gateway failure, or invalid output deterministically returns
  `REVIEW_REQUIRED` with zero confidence and a stable safe error reference.
- Results are canonicalized, digest-bound, reconstructed, and strictly revalidated.
- Eligibility context, `AgentDecision`, session revision, audit, and outbox are
  written in the same MongoDB transaction.

No live AI/model provider was configured or called. The live workflow used a
controlled fixture solely to validate Temporal conversion and atomic persistence.

## Docker results

```text
Scoped Ruff format: PASS, 14 files
Ruff lint: PASS
Strict mypy: PASS, 120 source files
Tests: PASS, 900/900
docker compose config --quiet: PASS
ReturnSession live MongoDB transaction validation: PASS
```

```text
Live Temporal Return workflow: PASS
Ordered updates: 7/7; query: PASS; command replay: PASS
Context snapshots: intake=PASS discovery=PASS eligibility=PASS
MongoDB read-back: sessions=1 audit_events=8 outbox_events=8 agent_decisions=1
```

The repository-wide Ruff formatting check retains the documented legacy formatting
baseline outside this slice. All workflow, test, and validator files changed for
this slice pass the scoped formatting gate.

## Next bounded step

Define deterministic `RETURN_REQUEST` result and context contracts, bind them to
persisted eligibility evidence, and persist the snapshot atomically. Keep live
provider integration disabled until an approved adapter and policy are selected.
