# Stage 4 — E2E Implementation Handoff and Remaining Work

Generated: 2026-07-24T06:00:32.000858+00:00

## 1. Delivery Classification

**Current classification: `SOURCE_VALIDATED`**

This repository contains the source implementation for the Stage 3 audit remediation. It is **not yet
`PRODUCTION_VALIDATED`** because dependency-backed test suites, Docker Compose execution, and live AI
provider validation could not run in the audit host.

## 2. Final Source Package Contents

The packaged project contains:

- Customer return list, create, detail, cancellation, timeline, and resumable SSE screens.
- Customer return HTTP APIs and durable MongoDB operational projections.
- Temporal return workflow worker and workflow orchestration service.
- Support return list, review queue, case detail, assignment, decision, retry, and cancellation operations.
- AI Gateway provider registry, simulator, request/response trace inspection, replay, comparison,
  interception, redaction, and manual override controls.
- Deterministic seed data with five approval scenarios and five rejection/manual-review scenarios.
- Cross-store seed runner for Platform MongoDB, source MongoDB, SQL Server, and Neo4j.
- Dependency readiness APIs and UI, including worker heartbeat visibility.
- Data Console inventory, sources, browser, graph explorer, imports, exports, jobs, workspaces,
  scenarios, audit, governance, settings, and hardening screens.
- Valkey-backed live event transport with MongoDB replay as the durable recovery path.
- Compose services for backend, frontend, MongoDB, SQL Server, Neo4j, Valkey, Temporal,
  Temporal PostgreSQL, Temporal UI, workers, orchestrator, outbox publisher, job worker, and seed runner.
- Source validation scripts and retained JSON evidence under
  `docs/evidence/stage4_e2e_completion/`.

## 3. Validation Already Completed

| Gate | Result | Evidence |
|---|---|---|
| Python source compilation | PASS | `docs/evidence/stage4_e2e_completion/source_validation.json` |
| Required backend route inventory | PASS | Same evidence file |
| Frontend live route inventory | PASS — 48 routes | Same evidence file |
| Compose service topology | PASS — 16 services declared | Same evidence file |
| Seed scenario coverage | PASS — 5 approve, 3 reject, 2 review | Same evidence file |
| AI provider/control source inventory | PASS | Same evidence file |
| Concurrency/integrity source guards | PASS | Same evidence file |
| TypeScript parser gate | PASS — 142 files | `docs/evidence/stage4_e2e_completion/frontend_syntax_validation.json` |

These are source-level checks. They do not prove dependency compatibility, runtime correctness, or
cross-store consistency under failure.

## 4. Remaining P0 Release Blockers

### P0.1 — Restore a reproducible backend lock

`backend/poetry.lock` is deleted in the working tree. A final release cannot be reproduced without a
committed lock file.

```bash
cd backend
poetry lock
poetry install --with dev
```

Acceptance:

- `backend/poetry.lock` exists and is committed.
- `poetry install --sync --with dev` succeeds from a clean environment.
- No unpinned transitive dependency is resolved differently on a second clean install.

### P0.2 — Execute backend quality gates

```bash
cd backend
poetry run ruff format --check src tests scripts
poetry run ruff check src tests scripts
poetry run mypy --no-incremental src tests scripts
poetry run pytest -vv --cov=return_platform --cov-report=term-missing --cov-report=json
```

Acceptance:

- Every command exits `0`.
- No ignored strict-mypy errors are introduced to bypass failures.
- Operational APIs, support transactions, AI policies, job cancellation/retry, seed manifests,
  SSE replay, and workflow reclaim behavior have focused tests.

### P0.3 — Execute frontend gates using the declared runtime

Required runtime from `frontend/package.json`:

```text
Node >=24 <25
npm >=11 <12
```

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
npm run test:a11y
```

Acceptance:

- Every command exits `0`.
- No route marked `LIVE` falls back to fixture adapters.
- Customer, support, AI interception, seed, dependency, workspace, import/export, and scenario flows
  are covered by browser tests.

### P0.4 — Regenerate and enforce OpenAPI contracts

The root and frontend snapshots must be generated from the same application build.

```bash
cd frontend
npm run contracts:generate
npm run contracts:check
```

Acceptance:

- `openapi/return-platform.openapi.json` and generated TypeScript definitions match the backend.
- Contract generation leaves the repository clean.
- CI fails on any future drift.

### P0.5 — Run the full Compose topology

Create a real environment file; do not package or commit it:

```bash
cp .env.example .env
# Replace every Placeholder* value and set currently supported AI model IDs.
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps --all
```

Acceptance:

- All long-running services become healthy.
- `seed-runner`, `mongodb-rs-init`, and `sqlserver-init` exit successfully.
- No service is restart-looping.
- API is reachable on `http://localhost:8000`.
- UI is reachable on `http://localhost:3000`.

### P0.6 — Execute the ten real-time return scenarios

Required matrix:

- Five deterministic approval scenarios.
- Three deterministic rejection scenarios.
- Two deterministic manual-review/interception scenarios.

For each scenario record:

- Request payload and correlation ID.
- Session ID and Temporal workflow ID.
- Ordered timeline/SSE sequence.
- AI provider/simulator trace and redacted request/response.
- Support case state where applicable.
- SQL Server return/RMA/tracking facts.
- MongoDB session, audit, decision, event, and outbox records.
- Neo4j evidence where the flow reads graph data.
- Final result and process exit code.

Acceptance:

- At least five positive and five negative/review scenarios pass.
- No duplicate sequence IDs or duplicate business mutation occurs after retry/reconnect.
- Paused sessions are not reclaimed until an explicit support/AI decision resumes them.

### P0.7 — Validate live AI providers

The simulator is deterministic evidence only. It does not validate production provider behavior.
Validate Google and NVIDIA separately using model IDs confirmed as available in the target accounts at
execution time.

Acceptance:

- Provider readiness check succeeds.
- Timeout, retry, global deadline, concurrency, and rate-limit controls are exercised.
- Failover is verified from the first provider to the second and then to the simulator/review policy.
- Sensitive fields remain redacted in stored traces and UI responses.
- Interception and manual override produce immutable audit evidence.

### P0.8 — Failure and restart validation

Adversarial tests:

1. Disconnect SSE, advance the workflow, reconnect with the last event ID, and verify exact replay.
2. Restart backend, worker, orchestrator, outbox publisher, Valkey, and Temporal separately.
3. Kill the job worker during a partially applied import and verify record counts remain correct.
4. Submit two support decisions with the same version token and verify one deterministic conflict.
5. Cancel and retry the same job repeatedly and verify idempotency.
6. Pause an AI interception for longer than the worker poll interval and verify no hot loop.
7. Make SQL Server unavailable after MongoDB acceptance and verify safe recovery without false completion.
8. Exhaust AI provider deadlines and verify `REVIEW_REQUIRED`, not an unsafe automatic approval.

Acceptance:

- No lost durable events.
- No duplicate authoritative business mutation.
- No stale workspace counts.
- No session remains permanently claimed after process failure.
- Every terminal error opens an auditable recoverable support path.

### P0.9 — Repository hygiene and release evidence

The implementation working tree currently contains hundreds of modified/untracked paths. Before a
release commit:

```bash
git status --short
git diff --check
```

Required actions:

- Review every change; remove scratch scripts and dead code.
- Do not commit `.env`, `.env.local`, credentials, caches, coverage data, `node_modules`, or `dist`.
- Commit regenerated lock files and OpenAPI artifacts.
- Store final receipts under `docs/evidence/stage4_e2e_completion/live/`.
- Update the README with actual exit codes and validation levels.

## 5. P1 Hardening After P0 Closure

These do not block the first truthful sandbox E2E demonstration, but they block production readiness:

- Authentication and immutable RBAC for customer, support, admin, AI interception, and seed/reset actions.
- Multi-region design, leader election, replication-lag behavior, and regional failover.
- Load and soak tests for 10M+ user assumptions, SSE fan-out, worker queues, and hot partitions.
- Index review for MongoDB operational collections, SQL Server return lookups, and Neo4j traversal paths.
- Retention, purge, export, and legal hold behavior for AI traces and audit events.
- Secrets management and key rotation; remove direct secret injection from developer `.env` in deployed environments.
- Supply-chain scanning, SBOM, image signing, dependency vulnerability evidence, and container policy checks.
- Observability: metrics, structured logs, traces, SLOs, alert thresholds, and dead-letter dashboards.
- Backup/restore and disaster-recovery evidence for Platform MongoDB and SQL Server ownership boundaries.

## 6. Recommended Validation Order

```text
1. Restore locks and clean dependency installs
2. Backend static/test gates
3. Frontend static/test/build gates
4. OpenAPI regeneration and drift gate
5. Compose build and readiness
6. Cross-store seed verification
7. Ten E2E scenarios
8. Live Google/NVIDIA validation
9. Restart/failure matrix
10. Evidence review, README update, clean release commit
```

Do not perform expensive live E2E debugging before the static and contract gates are green; otherwise
runtime failures will be contaminated by basic type, lint, or schema defects.

## 7. Release Exit Criteria

The project may be promoted from `SOURCE_VALIDATED` to `SANDBOX_VALIDATED` only when:

- Every P0 gate above exits `0`.
- Ten scenario receipts are retained.
- Both Google and NVIDIA are validated or explicitly classified as external blockers with simulator
  fallback evidence.
- Restart and SSE replay tests pass.
- OpenAPI artifacts are synchronized.
- The repository is clean and reproducible from the committed locks.

`PRODUCTION_VALIDATED` additionally requires the P1 security, scale, resilience, and operational
hardening evidence in the target production-like environment.
