# Intake and order-discovery context validation

Date: 2026-07-22

## Classification

```text
IntakeActivityResult contract:                 CONTRACT_TESTED
OrderDiscoveryActivityResult contract:         CONTRACT_TESTED
Canonical StageContextBinding:                 CONTRACT_TESTED
Temporal default-converter compatibility:      LIVE SANDBOX VALIDATED
Intake ContextSnapshot persistence:            LIVE SANDBOX VALIDATED
Discovery ContextSnapshot persistence:         LIVE SANDBOX VALIDATED
Context digest replay/conflict behavior:        CONTRACT_TESTED
Production source activity execution:          NOT ENABLED
Eligibility and AI Gateway decisions:          NOT IMPLEMENTED
```

## Ownership boundary

Temporal commands carry one concrete `StageContextBinding`: stage, schema version,
canonical JSON, and SHA-256 digest. Temporal execution state retains only the
context digest in applied-command evidence. Platform MongoDB stores the complete
canonical `ContextSnapshot` inside the authoritative `ReturnSession`.

The result contracts accept only bounded identifiers, unique evidence references,
timezone-aware observations, fixed schema versions, and code-owned intake channels.
No arbitrary source filter, query, model prompt, or provider configuration is
accepted.

## Implemented behavior

- `intake-v1` result binding and canonical payload generation.
- `order-discovery-v1` result binding and canonical payload generation.
- UTC normalization before digest construction.
- Explicit empty context binding for later unimplemented stages.
- Cross-stage, missing, duplicate, malformed, noncanonical, and tampered binding
  rejection.
- Command replay conflict when the same command ID carries different context evidence.
- Atomic intake/discovery snapshot persistence with session, audit, and outbox writes.
- Audit evidence references include the bound context SHA-256 digest.
- Live-validator failure cleanup and scoped cleanup of stale validation workflows.

Only controlled fixture identifiers were used. No production Customer or order
source was queried.

## Docker results

Focused gate:

```text
Ruff format/lint: PASS
Strict mypy: PASS
Workflow/persistence/stage-result/worker tests: PASS, 41/41
```

Complete backend gate:

```text
docker compose config --quiet: PASS
Focused Ruff format: PASS, 14 files
Ruff lint: PASS
Strict mypy: PASS, 118 source files
Tests: PASS, 893/893
```

Live Temporal and MongoDB validation:

```text
Live Temporal Return workflow: PASS
Ordered updates: 7/7; query: PASS; command replay: PASS
Context snapshots: intake=PASS discovery=PASS
MongoDB read-back: sessions=1 audit_events=8 outbox_events=8
```

The first optional/union result wire shapes were rejected by the pinned Temporal
1.25 live workflow decoder. The final command uses one concrete, unambiguous
`StageContextBinding`, and the clean live rerun passed without warnings.

## Next bounded step

Define the eligibility-evaluation input, decision, and evidence contracts. Add a
provider-neutral AI Gateway port with strict timeout, retry, redaction, model, and
configuration-version ownership. Persist `AgentDecision` atomically with the
eligibility context and stage transition. Do not call a live model provider until
the gateway contract, deterministic fallback policy, and controlled fixtures pass.
