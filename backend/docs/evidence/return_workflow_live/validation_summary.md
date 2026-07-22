# Live Temporal Return workflow validation

Date: 2026-07-22

## Classification

```text
Dedicated worker bootstrap:                  CONTRACT_TESTED
Exact workflow/activity registration:        CONTRACT_TESTED
Temporal schema bootstrap:                   LIVE SANDBOX VALIDATED
Temporal server and default namespace:        LIVE SANDBOX VALIDATED
Workflow start and session initialization:    LIVE SANDBOX VALIDATED
Seven ordered stage updates:                  LIVE SANDBOX VALIDATED
Execution-state query:                        LIVE SANDBOX VALIDATED
Identical command replay:                     LIVE SANDBOX VALIDATED
Completed workflow result:                    LIVE SANDBOX VALIDATED
MongoDB session/audit/outbox read-back:        LIVE SANDBOX VALIDATED
Business-source and AI Gateway activities:    NOT IMPLEMENTED
Customer-facing mutation APIs:                NOT IMPLEMENTED
```

## Implemented boundary

- Code-owned task queue `return-platform-return-v1`.
- Exact registration of `ReturnWorkflow` and the two persistence activities.
- Repository injection keeps MongoDB I/O outside deterministic workflow code.
- Standalone worker process entry point with owned Temporal and MongoDB clients.
- Scoped live validator that runs a real worker against Docker Temporal and MongoDB.
- Identical workflow command replay returns existing state without another activity
  or duplicate audit/outbox write.

No Customer, order, return, eligibility, fulfillment, tracking, model-provider,
or AI Gateway call occurs in this slice.

## Compose corrections discovered by live validation

The pinned Temporal 1.25 images require the SQL tool plugin name `postgres12`.
The local development server uses `temporalio/auto-setup` with `DB=postgres12`
and `SKIP_SCHEMA_SETUP=true` because the compose stack already owns an explicit
schema setup job. `docker compose config --quiet` passes.

## Docker results

Focused workflow gate:

```text
Strict mypy: PASS
Workflow/persistence/worker tests: PASS, 28/28
Temporal sandbox preparation: PASS
```

Complete backend gate:

```text
Focused Ruff format: PASS, 10 files
Ruff lint: PASS
Strict mypy: PASS, 115 source files
Tests: PASS, 880/880
```

Live worker execution from the backend Docker container:

```text
Live Temporal Return workflow: PASS
Ordered updates: 7/7; query: PASS; command replay: PASS
MongoDB read-back: sessions=1 audit_events=8 outbox_events=8
```

The live validator drops its dedicated `return_workflow_live_validation`
MongoDB database during cleanup. Temporal workflow history remains in the local
development namespace as durable execution evidence.

## Defect found and closed

The first live replay correctly produced unchanged workflow state but still
attempted a persistence activity. The update handler now short-circuits identical
command replay before activity scheduling. The repeated live run passed and the
MongoDB counts prove that replay produced no duplicate evidence records.

## Next bounded step

Completed in
`backend/docs/evidence/return_stage_contexts/validation_summary.md`. The next
slice defines eligibility decision evidence and the provider-neutral AI Gateway
boundary without enabling a live model call.
