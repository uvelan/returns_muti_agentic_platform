# Deterministic RETURN_REQUEST context validation

Date: 2026-07-22

## Classification

```text
ReturnRequestActivityResult:                 CONTRACT_TESTED
Eligibility-to-outcome mapping:              CONTRACT_TESTED
Eligibility digest binding:                  CONTRACT_TESTED
Contradictory outcome rejection:             CONTRACT_TESTED
Return-request ContextSnapshot persistence:  LIVE SANDBOX VALIDATED
Production return creation:                  NOT ENABLED
```

## Implemented boundary

The result is constructed from the persisted `eligibility-v1` snapshot and carries
its exact SHA-256 digest. Only these mappings are legal:

```text
APPROVE         -> CREATED        (return reference required)
REJECT          -> DECLINED       (return reference forbidden)
REVIEW_REQUIRED -> REVIEW_PENDING (return reference forbidden)
```

The repository reconstructs both snapshots and rejects a decision or digest mismatch
before changing authoritative state. A successful transition writes the
`return-request-v1` context with the session revision, audit, and outbox records in
one MongoDB transaction.

The live run used controlled fixtures. No OMC/source-system mutation or production
return creation was performed.

## Docker results

```text
Scoped Ruff format/lint: PASS
Strict mypy: PASS, 122 checked files
Tests: PASS, 907/907
docker compose config --quiet: PASS
```

```text
Live Temporal Return workflow: PASS
Ordered updates: 7/7; query: PASS; command replay: PASS
Context snapshots: intake=PASS discovery=PASS eligibility=PASS return_request=PASS
MongoDB read-back: sessions=1 audit_events=8 outbox_events=8 agent_decisions=1
```

## Next bounded step

Define deterministic `FULFILLMENT_TRACKING` result and context contracts using only
persisted return-request evidence. Do not enable production fulfillment providers or
source-system writes.
