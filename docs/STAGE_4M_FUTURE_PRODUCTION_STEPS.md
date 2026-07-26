# Stage 4M — Future Production Steps

The simulator is a development, test and sandbox implementation of stable dependency gateway contracts. It must not be enabled in production.

## P0 — dependency-backed validation

1. Install the supported Python, Node and frontend dependency sets.
2. Run Ruff, Ruff format check, strict mypy and the complete pytest suite.
3. Run frontend lint, TypeScript checking, unit tests and production build.
4. Start MongoDB, SQL Server, Neo4j, Temporal, PostgreSQL and Valkey.
5. Execute both API-driven Stage 4M E2E scenarios.
6. Execute Playwright across all dedicated simulator pages.
7. Restart API, Temporal worker and MongoDB during a return and prove durable recovery.
8. Validate duplicate commands and idempotent readback.
9. Validate retryable, terminal, timeout and invalid-response scenarios.

## P0 — real adapter replacement

Replace one dependency at a time through the same gateway boundaries:

```text
SIMULATED -> REAL
```

Required order:

1. Approved OMC command and readback adapter.
2. Parcel label and tracking adapter.
3. Freight/TMS quote, BOL, booking and pickup adapter.
4. LSI file/API ingestion and reconciliation adapter.

Each replacement must retain:

- idempotency keys;
- request and response digests;
- authoritative readback;
- outbox retry behavior;
- dependency health probes;
- operational audit;
- no fabricated success.

## P1 — observability and governance

- OpenTelemetry traces for every dependency operation.
- Metrics dashboards for latency, failure, fallback and token usage.
- Approved current provider pricing for cost estimates.
- Retention and redaction policy for AI metrics.
- SSO and role restrictions for simulator pages.
- Simulator operation export and evidence bundles.
- Alerts when simulation is accidentally configured outside approved environments.

## P2 — later capabilities

- OCR workers for uploaded documents.
- Image-quality and damage-analysis workers.
- Automatic document classification.
- Additional external dependency scenarios.
- More sophisticated AI explanations without changing deterministic authority.
