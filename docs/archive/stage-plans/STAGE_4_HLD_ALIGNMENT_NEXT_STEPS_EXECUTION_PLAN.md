# Stage 4 HLD Alignment — Next Steps Execution Plan

## 1. Purpose

This document is the execution handoff for the delivered Return Multi Agents / Return Platform source archive.

The current repository classification is **`SOURCE_VALIDATED`**. The next work must move the project through these classifications without skipping evidence gates:

```text
SOURCE_VALIDATED
  -> CONTRACT_TESTED
  -> SANDBOX_VALIDATED
  -> PRODUCTION_READY
  -> PRODUCTION_VALIDATED
```

`PRODUCTION_VALIDATED` requires a protected production deployment, production smoke evidence, SLO evidence, and tested rollback. It cannot be inferred from local, source-only, or sandbox validation.

## 2. Current verified state

The source package already contains:

- Associate-first Returns Assistant with minimal-anchor discovery.
- Graph-first lookup with targeted Source MongoDB fallback.
- Digest-bound, expiring, active-only discovery locks.
- Typed return reason, quantity, package count, shipping path, product type, and warehouse context.
- Return Support provider boundary with sandbox and external adapters.
- Authoritative SQL return, item, fulfillment, tracking, support-ticket, bay, and feedback models.
- AI Gateway providers isolated into separate files:
  - Google
  - NVIDIA
  - OpenAI
  - Anthropic
  - Ollama
  - Simulator
- Root `.env` integration for credentials; the archive intentionally excludes `.env`.
- Host-native Python 3.13 and Node 24 run scripts; application Docker is optional.
- Data Console screens for schema, AI Studio, graph sync, feedback learning, sources, browser, workspaces, jobs, scenarios, evidence, governance, and dependencies.
- Governed AI Studio random-data generation for modeled MongoDB and SQL assets.
- Schema registry covering 33 MongoDB collections and 8 SQL Server tables.
- Neo4j graph schema covering 13 node labels and 16 relationship types.
- Graph schema application and source-to-graph synchronization services.
- Deterministic seed matrix with 5 approval, 3 rejection, and 2 manual-review scenarios.
- Feedback Learning Agent that emits reviewable, evidence-bound recommendations without automatically mutating production rules.

Source validation evidence already passes:

```text
docs/evidence/stage4_e2e_completion/source_validation.json
docs/evidence/stage4_e2e_completion/frontend_syntax_validation.json
docs/evidence/stage4_hld_alignment/source_contract_validation.json
```

Known execution blockers on the audit host are recorded in:

```text
docs/evidence/stage4_hld_alignment/execution_blockers.json
```

The blockers were environmental:

- Node 22 was installed; the repository requires Node 24.
- Frontend dependency installation was incomplete.
- Python dependency-backed imports were unavailable.
- Docker was unavailable.

These source-level passes do **not** replace dependency-backed tests.

## 3. Non-negotiable rules

1. Use Python `3.13.x`, Node `24.x`, and npm `11.x`.
2. Keep the root `.env` untracked, unprinted, and excluded from evidence and archives.
3. Google and NVIDIA keys are expected in the existing root `.env`; do not ask for them again before running a secret-safe preflight.
4. Never overwrite an existing `.env` with `.env.example`.
5. FastAPI, React, Temporal worker, orchestrator, outbox publisher, and data-job worker must run on the host by default.
6. Docker Compose is infrastructure-only unless the explicit `containerized-app` profile is selected.
7. Source MongoDB remains read-only in production. AI Studio direct writes are development/test-only and allowlist-governed.
8. Neo4j is a derived, rebuildable projection—not a business source of truth.
9. SQL Server owns return/RMA/tracking/bay/feedback business facts.
10. Platform MongoDB owns internal sessions, events, conversations, traces, locks, jobs, audit, and evidence.
11. Temporal owns execution and timers, never authoritative business state.
12. Feedback recommendations require review; no automatic prompt, mapping, bay, or graph-rule mutation.
13. Every stage must emit evidence before the next stage starts.
14. A failed mandatory gate blocks promotion; do not relabel it as passed.

## 4. Delivery extraction and integrity check

### 4.1 Verify the archive

Run from the directory containing the ZIP and checksum file:

```bash
sha256sum -c RETURNS_MULTI_AGENTIC_PLATFORM_STAGE4_HLD_ALIGNED.sha256
unzip returns_multi_agentic_platform_stage4_hld_aligned_source_validated.zip
cd returns_multi_agentic_platform
```

PowerShell:

```powershell
Get-FileHash .\returns_multi_agentic_platform_stage4_hld_aligned_source_validated.zip -Algorithm SHA256
Expand-Archive .\returns_multi_agentic_platform_stage4_hld_aligned_source_validated.zip -DestinationPath .
Set-Location .\returns_multi_agentic_platform
```

### 4.2 Secret preflight

The package must not contain a root `.env`:

```bash
test ! -f .env
```

Restore the environment-specific `.env` from the secure local environment. Only create one from the example when none exists:

```bash
if [ ! -f .env ]; then cp .env.example .env; fi
```

Never run unconditional `cp .env.example .env`.

Validate that the file is ignored:

```bash
git check-ignore .env
```

Acceptance criteria:

- Archive checksum matches.
- No `.env`, private key, credential dump, `node_modules`, virtual environment, cache, or Git metadata exists in the archive.
- `.env` is restored locally and remains ignored.

Evidence:

```text
docs/evidence/stage4_contract_closure/archive_integrity.txt
docs/evidence/stage4_contract_closure/secret_preflight.json
```

## 5. Stage 4A — Repository baseline and review

The delivered archive excludes `.git`. Apply it to the canonical repository or initialize a new repository only for isolated validation.

### 5.1 Canonical repository path

Preferred flow:

```bash
git clone <canonical-repository-url> returns_multi_agentic_platform
cd returns_multi_agentic_platform
# copy the delivered source tree over this clone without copying .git
```

Then capture the delta:

```bash
git status --short > docs/evidence/stage4_contract_closure/git_status_before.txt
git diff --stat > docs/evidence/stage4_contract_closure/git_diff_stat_before.txt
git diff --check > docs/evidence/stage4_contract_closure/git_diff_check_before.txt
```

Review deleted provider monoliths and new provider packages explicitly. Confirm imports reference the new packages and no dead compatibility module remains.

Required review areas:

```text
backend/src/return_platform/ai_gateway/providers/
backend/src/return_platform/operations/return_support/providers/
backend/src/return_platform/operations/associate_flow.py
backend/src/return_platform/operations/orchestrator.py
backend/src/return_platform/operations/feedback_service.py
backend/src/return_platform/data_platform/ai_studio.py
backend/src/return_platform/data_platform/graph/schema.py
backend/src/return_platform/data_platform/graph/sync_service.py
backend/config/schema_registry.yaml
frontend/src/features/operations/AssociateReturnsPage.tsx
frontend/src/features/data-console/pages/DataStudioPages.tsx
compose.yaml
README.md
```

Acceptance criteria:

- No accidental deletion of canonical domain code.
- No duplicate provider implementation.
- No tracked credential or machine-local artifact.
- `git diff --check` passes.
- Dead compatibility code is removed rather than retained unused.

## 6. Stage 4B — Reproducible toolchains and dependencies

### 6.1 Required host versions

```bash
python3.13 --version
node --version
npm --version
```

Required:

```text
Python 3.13.x
Node 24.x
npm 11.x
```

### 6.2 Bootstrap

Linux/Ubuntu/WSL:

```bash
./scripts/bootstrap_host.sh
```

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/bootstrap_host.ps1
```

### 6.3 Lock verification

Backend:

```bash
cd backend
poetry lock --check
poetry install --with dev --sync
poetry run python -c "import fastapi,pymongo,neo4j,redis,temporalio,httpx,yaml"
cd ..
```

Frontend:

```bash
cd frontend
npm ci
npm ls --depth=0
cd ..
```

Do not regenerate lock files merely because the local cache differs. Regenerate only when the declared dependency graph changed, then review the complete lock diff.

Registry fallback policy:

1. Use official PyPI/npm registries.
2. Retry bounded transient `429`, `502`, `503`, and `504` failures.
3. Use only organization-approved mirrors already configured in environment or tool settings.
4. Record the registry and immutable lock digests in evidence.
5. Never disable TLS validation.

Evidence:

```text
docs/evidence/stage4_contract_closure/toolchains.json
docs/evidence/stage4_contract_closure/backend_lock_sha256.txt
docs/evidence/stage4_contract_closure/frontend_lock_sha256.txt
docs/evidence/stage4_contract_closure/dependency_install.log
```

Acceptance criteria:

- Clean dependency installation succeeds from lock files.
- No undeclared runtime import is required.
- No lock drift remains after a second clean install.

## 7. Stage 4C — Backend correctness and strict typing

Run:

```bash
cd backend
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests
poetry run pytest -q
poetry run pytest --cov=return_platform --cov-report=term-missing --cov-report=json:../docs/evidence/stage4_contract_closure/backend_coverage.json --cov-fail-under=90
cd ..
```

Mandatory missing or expanded suites:

```text
backend/tests/test_associate_flow.py
backend/tests/test_discovery_lock_lifecycle.py
backend/tests/test_return_support_providers.py
backend/tests/test_return_support_external_contract.py
backend/tests/test_ai_gateway_provider_factory.py
backend/tests/test_ai_gateway_live_contract_redaction.py
backend/tests/test_ai_studio_service.py
backend/tests/test_ai_studio_api.py
backend/tests/test_schema_catalog_api.py
backend/tests/test_graph_schema.py
backend/tests/test_graph_sync_service.py
backend/tests/test_graph_sync_api.py
backend/tests/test_feedback_service.py
backend/tests/test_feedback_learning_api.py
backend/tests/test_operational_orchestrator.py
backend/tests/test_sql_business_state.py
backend/tests/test_seed_api.py
backend/tests/test_data_browser_live_api.py
backend/tests/test_sse_replay.py
backend/tests/test_support_concurrency.py
```

Adversarial cases that must be tested:

- Two associates confirm the same order line concurrently.
- Expired discovery lock is reclaimed exactly once.
- Stale expected version cannot mutate a support case.
- Duplicate support-ticket submission reuses the idempotency key.
- External support timeout does not fabricate a ticket or RMA.
- AI provider returns malformed JSON, oversized payload, timeout, `429`, and `5xx`.
- Google failure causes ordered NVIDIA failover without duplicate workflow effects.
- No tracking record is created when no tracking reference exists.
- Bay capacity is exhausted between read and assignment.
- Graph sync retries do not duplicate relationships.
- AI Studio digest mismatch blocks apply.
- Unknown generator names fail closed.
- Feedback evidence digest mismatch blocks review mutation.
- Cancellation during partial import does not drift record counts.
- SSE reconnect from an old sequence replays from MongoDB without gaps or duplicates.

Acceptance criteria:

- Ruff passes with zero warnings.
- Ruff format check passes.
- Strict mypy passes with zero errors.
- All backend tests pass.
- Coverage is at least 90%; exclusions require written justification.
- No real integration is mislabeled as validated by mocks.

Evidence:

```text
docs/evidence/stage4_contract_closure/backend_ruff.txt
docs/evidence/stage4_contract_closure/backend_mypy.txt
docs/evidence/stage4_contract_closure/backend_pytest.txt
docs/evidence/stage4_contract_closure/backend_coverage.json
```

## 8. Stage 4D — Frontend type safety, tests, and accessibility

Run:

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run test:coverage
npm run build
npm run test:e2e
npm run test:a11y
cd ..
```

Required screen coverage:

- Associate Returns Assistant minimal-anchor flow.
- Candidate order and order-line confirmation.
- Lock conflict and expiration handling.
- Return details and support-ticket submission.
- Live timeline and reconnect behavior.
- Support queue assignment, decision, retry, and conflict handling.
- AI request/response inspector, interception, comparison, replay, and override.
- Schema Catalog Mongo/SQL/graph tabs.
- AI Studio generation, preview, digest check, apply denial, and successful sandbox apply.
- Graph Sync schema apply, run, progress, failure, and evidence details.
- Feedback Learning review queue and no-auto-apply boundary.
- Data Browser live read and redaction.
- Dependency status and worker heartbeat degradation.

Required new or expanded tests should be colocated with the affected feature or placed under `frontend/tests/`.

Production bundle adversarial checks:

- No MSW bootstrap in production.
- No fixture adapter imported by production routes.
- No seeded default customer/order values submitted without user action.
- No hidden hard-coded `RMA-*`, `TRK-*`, warehouse, or bay identifiers.
- No secret or raw AI prompt payload in browser logs.
- Every mutation exposes loading, success, partial, conflict, and failure states.

Acceptance criteria:

- ESLint passes with zero warnings.
- TypeScript project build passes.
- Unit and integration tests pass.
- Vite production build passes.
- Bundle guard passes.
- Playwright E2E passes against the real API path.
- Accessibility tests pass with no serious or critical violations.

Evidence:

```text
docs/evidence/stage4_contract_closure/frontend_lint.txt
docs/evidence/stage4_contract_closure/frontend_typecheck.txt
docs/evidence/stage4_contract_closure/frontend_tests.txt
docs/evidence/stage4_contract_closure/frontend_build.txt
docs/evidence/stage4_contract_closure/frontend_e2e.txt
docs/evidence/stage4_contract_closure/frontend_a11y.txt
```

## 9. Stage 4E — OpenAPI and generated contract convergence

Run:

```bash
cd frontend
npm run contracts:generate
npm run contracts:check
cd ..
```

Inspect all new operational and Data Console routes:

```text
Associate discovery and confirmation
Return detail submission
Return Support ticket lifecycle
AI Gateway inspection and interception
Schema Catalog
AI Studio
Graph Sync
Feedback Learning
Seed operations
Data Browser
SSE timeline
```

Requirements:

- Backend OpenAPI export succeeds using the installed backend environment.
- Generated TypeScript declarations compile without local handwritten shadow models.
- Frontend request bodies exactly match backend schemas.
- Response envelopes and error contracts are typed.
- Contract generation is deterministic.
- `git diff --exit-code` passes after a second generation.

Evidence:

```text
docs/evidence/stage4_contract_closure/openapi_sha256.txt
docs/evidence/stage4_contract_closure/contracts_generate.txt
docs/evidence/stage4_contract_closure/contracts_diff.txt
```

Promotion after Stages 4B–4E: **`CONTRACT_TESTED`**.

## 10. Stage 4F — Infrastructure startup and readiness

Docker is used for infrastructure only by default.

```bash
./scripts/infra.sh config
./scripts/infra.sh start
./scripts/infra.sh status
```

Equivalent:

```bash
docker compose config --quiet
docker compose up -d --wait
docker compose ps
```

Required infrastructure:

- SQL Server
- SQL migration runner
- MongoDB replica set
- Neo4j
- Valkey
- Temporal PostgreSQL
- Temporal server
- Temporal UI

Do not start the optional application profile for the primary host-run validation.

Readiness requirements:

- SQL database exists and both migrations apply idempotently.
- MongoDB replica set is primary and transactions are available.
- Neo4j authentication succeeds.
- Valkey ping succeeds.
- Temporal namespace is available.
- No dependency is exposed beyond intended localhost/VPN boundaries.
- Container logs contain no restart loop, authentication failure, or repeated migration failure.

Evidence:

```text
docs/evidence/stage4_sandbox/infrastructure/compose_config.txt
docs/evidence/stage4_sandbox/infrastructure/compose_ps.json
docs/evidence/stage4_sandbox/infrastructure/readiness.json
docs/evidence/stage4_sandbox/infrastructure/log_summary.md
```

## 11. Stage 4G — Host services

Start infrastructure first, then use separate terminals.

API:

```bash
./scripts/run_backend_host.sh
```

Temporal worker:

```bash
./scripts/run_worker_host.sh temporal
```

Orchestrator:

```bash
./scripts/run_worker_host.sh orchestrator
```

Outbox publisher:

```bash
./scripts/run_worker_host.sh outbox
```

Data job worker:

```bash
./scripts/run_worker_host.sh jobs
```

Frontend:

```bash
./scripts/run_frontend_host.sh
```

Linux convenience command:

```bash
./scripts/run_all_host.sh
```

Validate:

```bash
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:8000/openapi.json >/dev/null
curl -fsS http://localhost:5173 >/dev/null
```

Acceptance criteria:

- API and frontend run without application containers.
- Every worker publishes a fresh heartbeat.
- Readiness fails when a required dependency or worker is unavailable.
- Graceful shutdown does not lose claimed jobs or events.

## 12. Stage 4H — Schema, seed, AI Studio, and graph synchronization

### 12.1 SQL migrations

Compose applies migrations automatically. For external SQL Server, apply:

```text
infra/sqlserver/init/001_return_business_state.sql
infra/sqlserver/init/002_domain_models.sql
```

### 12.2 Deterministic seed

```bash
cd backend
poetry run python scripts/seed_e2e_data.py
cd ..
```

Verify all required source collections:

```text
salesInv
customerOutboundCDM
shipmentInfo
lkpSearchProduct
```

Verify SQL models:

```text
dbo.return_requests
dbo.return_items
dbo.return_fulfillment
dbo.return_tracking
dbo.e2e_seed_scenarios
integration.return_support_ticket
platform.bay_configuration
platform.bay_assignment
platform.feedback_recommendation
```

### 12.3 Schema Catalog

Use `/data-console/schema` and APIs to prove:

- Every physical MongoDB collection is cataloged.
- Every SQL table is created by a versioned migration.
- Every graph node and relationship is displayed.
- Ownership, read/write capability, keys, types, and sensitive fields are accurate.

### 12.4 AI Studio

Use `/data-console/ai-studio` to generate random records for each writable sandbox asset.

Required checks:

- Proposal generation is schema-bound.
- Preview shows deterministic seed/digest.
- Digest mismatch blocks apply.
- Unknown assets and generators fail closed.
- Production environment blocks apply.
- Service-owned collections remain proposal-only.
- MongoDB and SQL writes are idempotent or conflict-safe.
- No secrets, credentials, or raw personal data are generated.

### 12.5 Graph schema and sync

Apply graph schema, then run `FULL` sync:

```text
POST /data-console/v1/graph-sync/schema/apply
POST /data-console/v1/graph-sync/runs
```

Validate:

- 13 node constraints/indexes.
- 16 relationship types.
- Source MongoDB and SQL records project into Neo4j.
- Re-running sync does not duplicate nodes or relationships.
- Deleted or corrected authoritative records reconcile according to the documented projection policy.
- Evidence contains counts, source watermarks, duration, failures, and digest.

Evidence:

```text
docs/evidence/stage4_sandbox/seed/seed_apply.json
docs/evidence/stage4_sandbox/seed/seed_idempotency.json
docs/evidence/stage4_sandbox/ai_studio/generator_matrix.json
docs/evidence/stage4_sandbox/ai_studio/apply_matrix.json
docs/evidence/stage4_sandbox/graph/schema_apply.json
docs/evidence/stage4_sandbox/graph/full_sync.json
docs/evidence/stage4_sandbox/graph/readback.json
```

## 13. Stage 4I — Ten-scenario real-time E2E validation

Run all ten deterministic scenarios through the real host application and infrastructure.

Required matrix:

- 5 approval scenarios.
- 3 hard-rejection scenarios.
- 2 manual-review scenarios.

Each scenario must prove:

1. Associate starts with one minimal anchor.
2. Graph-first discovery runs.
3. Targeted source sync occurs only when graph context is insufficient.
4. Candidate customer/order/order line is shown.
5. Associate confirms and locks one exact order line.
6. Return details are collected and validated.
7. AI eligibility request is persisted with redacted trace evidence.
8. Interception/manual review works when configured.
9. Return Support ticket is submitted idempotently.
10. Clarification or final support result is followed to completion.
11. SQL Server contains authoritative return/item/fulfillment/tracking records.
12. Bay selection respects warehouse, shipping path, product compatibility, active status, priority, and package capacity.
13. SSE timeline is ordered and resumable.
14. Discovery lock is released on terminal state.
15. Feedback Learning records evidence-derived insights and `REVIEW_PENDING` recommendations.

Hard rejection must not create fabricated RMA, tracking, or bay assignment records.

Manual review must pause without hot-loop reclaim and resume exactly once after an authorized decision.

Evidence per scenario:

```text
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/request.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/timeline.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/ai_trace.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/support_ticket.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/sql_readback.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/graph_readback.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/feedback.json
docs/evidence/stage4_sandbox/scenarios/<scenario_id>/validation_summary.json
```

## 14. Stage 4J — Live Google and NVIDIA validation

Keys are expected in the root `.env`.

Secret-safe preflight must report only booleans and key fingerprints/digests—not key values:

```bash
cd backend
poetry run python scripts/validate_ai_gateway_live.py
cd ..
```

Required provider cases:

- Google direct success.
- NVIDIA direct success.
- Google timeout or forced failure followed by NVIDIA success.
- Both providers unavailable -> deterministic, typed failure or approved simulator behavior according to environment policy.
- Rate-limit response with bounded retry/backoff.
- Malformed response rejected by schema validation.
- Request/response trace redaction.
- Global timeout enforcement.
- Per-provider concurrency enforcement.
- Interception before provider dispatch.
- Manual override audit trail.

No live response may be labeled production validation unless it was executed against approved production credentials and endpoint policy.

Evidence:

```text
docs/evidence/stage4_sandbox/ai/google_live_validation.json
docs/evidence/stage4_sandbox/ai/nvidia_live_validation.json
docs/evidence/stage4_sandbox/ai/failover_validation.json
docs/evidence/stage4_sandbox/ai/redaction_validation.json
```

## 15. Stage 4K — Failure, restart, replay, and concurrency matrix

Mandatory failure injections:

1. API restart during active session.
2. Temporal worker restart during activity.
3. Orchestrator restart after claim but before stage completion.
4. Outbox publisher restart after publish but before acknowledgement.
5. Data job worker restart during import/export.
6. MongoDB primary step-down.
7. SQL Server restart during authoritative write.
8. Neo4j outage during graph-first discovery.
9. Valkey outage during SSE delivery.
10. Temporal outage during command submission.
11. Google timeout with NVIDIA failover.
12. Return Support timeout and delayed completion.
13. Duplicate browser submission.
14. Two associates locking the same line.
15. Two support agents deciding the same case.
16. Bay capacity race.
17. SSE reconnect from stale sequence.
18. Graph sync interrupted mid-batch.
19. AI Studio apply retried after network uncertainty.
20. Seed apply/reset repeated twice.

Required properties:

- No duplicate business effect.
- No fabricated source-of-truth record.
- No lost event.
- No infinite reclaim/hot loop.
- No stale lock after terminal outcome.
- No record-count drift.
- No orphan bay assignment.
- No feedback recommendation without canonical evidence.
- Retry remains bounded.
- User-facing state is partial/degraded rather than falsely successful.

Evidence:

```text
docs/evidence/stage4_sandbox/failure_matrix/summary.json
docs/evidence/stage4_sandbox/failure_matrix/<case_id>.json
```

Promotion after Stages 4F–4K: **`SANDBOX_VALIDATED`**.

## 16. Stage 4L — Sandbox release closure

Run all gates again from a clean clone or clean worktree.

```bash
python3.13 scripts/validate_stage4_source.py
python3.13 scripts/validate_stage4_contracts.py
node scripts/validate_frontend_syntax.mjs

cd backend
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests
poetry run pytest --cov=return_platform --cov-fail-under=90
cd ../frontend
npm ci
npm run check
npm run test
npm run test:e2e
npm run test:a11y
npm run contracts:check
cd ..
```

Then:

```bash
git status --short
git diff --check
```

Acceptance criteria:

- Clean working tree after committed generated contracts/evidence policy artifacts.
- No `.env`, credentials, caches, local databases, test artifacts, or build output committed.
- All mandatory gates pass from a clean clone.
- README commands reproduce the validated environment.
- Release archive checksum is generated.
- Release notes state exact validation level and external dependencies.

## 17. Stage 5 — Production readiness

Stage 5 starts only after sandbox closure.

### 17.1 Security and RBAC

- Real authentication; development principal disabled outside development.
- Roles for associate, support agent, support supervisor, data operator, AI operator, auditor, and platform administrator.
- Route-level and object-level authorization.
- Secret manager integration; `.env` limited to local development.
- TLS everywhere and encryption at rest.
- Audit immutability and retention.
- Input size limits, file-type validation, malware scanning, and output encoding.
- AI prompt-injection, data-exfiltration, and tool-authorization controls.

### 17.2 Observability and SLOs

- OpenTelemetry traces across API, workers, Temporal, AI Gateway, support adapter, graph sync, and data jobs.
- Correlation IDs propagated across stores and events.
- Metrics for latency, error rate, queue age, retry count, provider failover, lock conflict, SSE lag, graph-sync freshness, and ticket age.
- Structured logs with enforced redaction.
- Alerts tied to service-level objectives.

### 17.3 Performance and capacity

Test at target concurrency with realistic data volume:

- API throughput and p95/p99 latency.
- SSE connection count and replay cost.
- MongoDB indexes and transaction contention.
- SQL indexes, deadlocks, and connection pool saturation.
- Neo4j query plans and cardinality.
- Temporal task-queue latency and worker concurrency.
- AI provider rate limits and cost budgets.
- Import/export memory and artifact limits.

### 17.4 Backup, restore, and disaster recovery

- MongoDB backup/restore drill.
- SQL Server backup/point-in-time restore drill.
- Temporal persistence backup and worker replay validation.
- Neo4j rebuild from authoritative sources.
- Artifact-store recovery.
- Defined RPO/RTO and evidence that they are met.

### 17.5 Multi-region architecture

For 10M+ users, do not deploy a single shared writable stack across regions without explicit consistency design.

Define:

- Home-region ownership for a return session.
- Global routing and failover.
- MongoDB/SQL replication strategy and conflict policy.
- Temporal namespace/cluster topology.
- Valkey regional role; never authoritative.
- Neo4j regional projection and freshness guarantees.
- Support-ticket idempotency across regions.
- Data residency and privacy boundaries.

### 17.6 Supply chain and release controls

- Dependency and container vulnerability scanning.
- SBOM generation.
- Signed artifacts and provenance.
- Pinned images by digest for production.
- Branch protection, required checks, and reviewed migrations.
- Canary or blue/green deployment.
- Automated rollback and tested rollback evidence.

Promotion after Stage 5 controls and staging proof: **`PRODUCTION_READY`**.

## 18. Production validation

`PRODUCTION_VALIDATED` requires all of the following:

- Approved production deployment.
- Production configuration and secret-manager validation.
- Protected smoke transaction using authorized test data.
- Health, telemetry, audit, and alert evidence.
- SLO observation window.
- Confirmed rollback or rollback rehearsal.
- Change record and release approval.

Do not run destructive seeds, AI Studio apply, reset, or synthetic source mutation in production.

## 19. Required final evidence index

Create:

```text
docs/evidence/STAGE_4_5_FINAL_EVIDENCE_INDEX.md
```

It must list, for every gate:

- Environment.
- Exact command.
- Start/end timestamp.
- Exit code.
- Validation level.
- Evidence path.
- Known limitation.
- Reproduction steps.
- Commit SHA.
- Artifact SHA-256.

## 20. Definition of done

The remaining work is complete only when:

- Clean-clone bootstrap succeeds.
- Backend Ruff, format, strict mypy, pytest, and coverage pass.
- Frontend lint, typecheck, tests, build, Playwright, and accessibility pass.
- OpenAPI generation has zero drift.
- Infrastructure readiness passes.
- Host-run API, frontend, and four workers are healthy.
- SQL migrations and deterministic seed are idempotent.
- AI Studio and schema catalog are fully validated against physical stores.
- Graph schema and full sync are idempotent and read back correctly.
- All ten real-time scenarios pass.
- Google and NVIDIA live validation passes with ordered failover and redaction.
- Failure/restart/replay/concurrency matrix passes.
- Feedback recommendations are evidence-bound and review-only.
- Release tree is clean and secret-free.
- Classification is evidence-backed and not overstated.
