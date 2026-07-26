# Stage 4N — Remaining Production Steps

## P0 — live-stack closure

1. Run with Python 3.13, Poetry, Node 24, npm 11, Docker, and the complete dependency set.
2. Execute Ruff and strict mypy.
3. Execute the complete backend pytest suite.
4. Regenerate and verify OpenAPI/frontend contracts.
5. Run frontend lint, typecheck, Vitest, production build, Playwright, and accessibility gates.
6. Start MongoDB, SQL Server, Neo4j, Valkey, Temporal, API, workers, and frontend.
7. Run `scripts/run_stage4n_live_stack_e2e.sh` for branch parcel and the existing heavy-pickup E2E.
8. Verify restart durability for traces, metrics, operations, outbox work, and Temporal waits.

## P0 — distributed rate limits

The current route counters are process-local. Implement Valkey Lua/transaction-backed limits for:

- application;
- task;
- tier;
- provider;
- model;
- credential;
- route;
- user/session;
- concurrent leases.

Persist durable usage metrics in MongoDB while Valkey owns fast reservations and cooldowns.

## P0 — live provider validation

For every configured provider/model:

1. test each credential independently;
2. test structured JSON output;
3. validate token accounting;
4. validate 401/403 credential isolation;
5. validate 429 and `Retry-After` handling;
6. validate model-unavailable rotation;
7. validate provider outage failover;
8. validate timeout and circuit recovery;
9. confirm no key is logged or exposed;
10. record source-commit-aligned evidence.

Do not enter real keys in source or evidence files.

## P0 — task wiring

Continue migrating business-agent AI calls to fixed task IDs and exact contracts:

- smart questions;
- order candidate analysis;
- Support summaries;
- return status summaries;
- external-state reconciliation;
- customer notifications;
- feedback recommendations.

Remove remaining free-form prompt construction from production paths.

## P1 — pricing and budgets

Enter approved per-model prices and enforce:

- request budgets;
- daily/monthly token budgets;
- cost alerts;
- task and environment budgets;
- simulator lightweight-only budget isolation.

Until pricing is approved, cost remains zero rather than using invented values.

## P1 — security hardening

- Resolve credential lists from the enterprise secrets manager rather than raw `.env` values.
- Add corporate SSO and role mappings to AI operations pages.
- Encrypt sensitive AI trace fields where required.
- Add retention and deletion policies.
- Add security alerting for repeated prompt-injection or unauthorized-action attempts.
- Penetration-test replay, compare, interception, and safety-test endpoints.

## P1 — observability

Export route health and attempt metrics through OpenTelemetry/Prometheus:

- success/failure/fallback rate;
- latency and queue time;
- RPM/TPM usage;
- circuit state;
- rate-limit wait;
- model/key/provider failover counts;
- schema and safety failures;
- estimated cost.

## P2 — future OCR and vision

OCR and image processing remain out of Stage 4N. Later additions should use the same task registry and safety boundary with dedicated multimodal tiers, exact evidence schemas, artifact hashes, asynchronous workers, and human verification. Vision output must remain advisory and cannot create authoritative return or damage facts.
