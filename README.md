# Return Multi Agents / Return Platform

Production-oriented Sales Order return orchestration with an associate-first AI assistant, graph-first order discovery, Return Support ticket integration, authoritative SQL return tracking, warehouse bay assignment, governed feedback learning, and a separate Data Console.

> **Validation status:** `SOURCE_VALIDATED`
> Full dependency-backed, Compose, live-provider, and real-time E2E gates must still run in the target environment before claiming `SANDBOX_VALIDATED` or `PRODUCTION_VALIDATED`.

The cross-platform quality review is tracked in
`docs/code_quality/FULL_CODEBASE_REVIEW_FINDINGS.md`. Linux operators must use
`docs/code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md` and the single master
command `./scripts/linux/run_full_linux_validation.sh --from-start`. Windows
results are not proof of Linux execution.

## 1. Product flow

The operational entry point is the **Returns Assistant**:

```text
Associate supplies one strong anchor
  -> AI-guided graph-first discovery
  -> targeted Source MongoDB fallback when graph context is incomplete
  -> associate confirms customer, Sales Order, and exact order line
  -> expiring optimistic discovery lock
  -> collect reason, quantity, packages, shipping path, and notes
  -> AI eligibility gateway with interception and provider failover
  -> Return Support ticket submission and bounded follow-up
  -> authoritative SQL return/RMA/tracking persistence
  -> compatible warehouse bay assignment
  -> resumable SSE timeline
  -> governed feedback-learning review record
```

The Data Console is a separate developer/operator control plane. It is not the main return demo.

## 2. Data ownership

| Store | Ownership |
|---|---|
| Platform MongoDB | Authoritative internal sessions, events, AI traces, support cases, conversations, locks, jobs, audit, graph-sync evidence, AI Studio proposals, feedback records |
| Source MongoDB | Read-only discovery facts in production: `salesInv`, `customerOutboundCDM`, `shipmentInfo`, `lkpSearchProduct` |
| SQL Server | Authoritative return request, item, support ticket, fulfillment, tracking, bay assignment, and feedback-review facts |
| Neo4j | Derived and rebuildable graph projection only |
| Temporal | Workflow execution and ordering; never business-state ownership |
| Valkey | Transient event transport, rate limits, and live delivery; MongoDB remains the replay source |

## 3. Technology baseline

- Python `3.13.x`
- FastAPI, Pydantic v2, PyMongo Async, Neo4j Python driver, Temporal Python SDK
- React 19, TypeScript, Vite, TanStack Query, Wouter
- Node.js `24.x`, npm `11.x`
- SQL Server `2025-CU4-ubuntu-22.04`
- MongoDB `8.0.26-noble`
- Neo4j Community `5.26.28-community`
- Valkey `8.0.9-alpine`
- PostgreSQL `17.10-alpine`
- Temporal auto-setup `1.29.7` and Temporal UI `2.52.1`

FastAPI, workers, and the frontend run directly on the host. Docker is optional and used only to provision infrastructure unless the explicit `containerized-app` profile is selected.

## 4. Repository layout

```text
backend/
  config/
    schema_registry.yaml              # Mongo, SQL, graph model registry
  src/return_platform/
    ai_gateway/providers/             # one provider per file
    api/                              # operational APIs
    data_console/                     # Data Console APIs
    data_platform/
      ai_studio.py                    # governed synthetic-data proposals
      graph/schema.py                 # fixed graph constraints
      graph/sync_service.py           # source-to-graph synchronization
    operations/
      associate_flow.py               # minimal-anchor chat/discovery/lock
      return_support/providers/       # sandbox and external ticket adapters
      feedback_service.py             # review-only learning output
      sql_business_state.py           # authoritative SQL writes
      orchestrator.py                 # Temporal stage driver
frontend/
  src/features/operations/            # Associate, Customer, Support, AI, System
  src/features/data-console/          # dedicated Data Console screens
infra/sqlserver/init/                  # idempotent SQL schema migrations
scripts/                              # host bootstrap/run/infra/validation
compose.yaml                           # infra by default; app profile optional
```

## 5. Provider isolation

Each AI provider is isolated behind the provider contract:

```text
backend/src/return_platform/ai_gateway/providers/
  contracts.py
  http.py
  google.py
  nvidia.py
  openai.py
  anthropic.py
  ollama.py
  simulator.py
  factory.py
```

Return Support integration is also isolated:

```text
backend/src/return_platform/operations/return_support/providers/
  contracts.py
  sandbox.py
  external.py
  factory.py
```

`EXTERNAL` Return Support mode uses idempotent submission, bearer-token authentication when configured, bounded status polling, strict response parsing, and SQL persistence. Secrets are read only from the root `.env`.

## 6. Root `.env`

Create `.env` once. Never track, print, attach, or package it.

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Google and NVIDIA keys belong in the existing root `.env`:

```dotenv
GOOGLE_API_KEY=...
NVIDIA_API_KEY=...
PLATFORM_GOOGLE_API_KEY=${GOOGLE_API_KEY}
PLATFORM_NVIDIA_API_KEY=${NVIDIA_API_KEY}
```

For an enterprise Return Support endpoint:

```dotenv
PLATFORM_SUPPORT_TICKET_MODE=EXTERNAL
PLATFORM_SUPPORT_TICKET_BASE_URL=https://return-support.internal.example/api/v1
PLATFORM_SUPPORT_TICKET_API_KEY=...
PLATFORM_SUPPORT_TICKET_TIMEOUT_SECONDS=15
PLATFORM_SUPPORT_TICKET_POLL_SECONDS=5
PLATFORM_SUPPORT_TICKET_MAX_POLLS=12
```

Do not run `cp .env.example .env` when `.env` already exists; that would overwrite credentials. The bootstrap scripts create it only when absent.

## 7. Host bootstrap — no application Docker required

### Linux / Ubuntu / WSL

```bash
./scripts/bootstrap_host.sh
```

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/bootstrap_host.ps1
```

The bootstrap gate requires exact Python 3.13, Node 24, and npm 11. It installs backend dependencies through Poetry when available, otherwise through a project virtual environment, then runs `npm ci` for the frontend.

## 8. Infrastructure

### Option A — Docker infrastructure only

Docker is not used for FastAPI or React in the standard host workflow.

```bash
./scripts/infra.sh config
./scripts/infra.sh start
./scripts/infra.sh status
```

Equivalent commands:

```bash
docker compose config --quiet
docker compose up -d --wait
docker compose ps
```

Infrastructure endpoints exposed only on localhost:

| Service | Host endpoint |
|---|---|
| SQL Server | `localhost:14330` |
| MongoDB | `localhost:27017` |
| Neo4j Browser | `http://localhost:7474` |
| Neo4j Bolt | `bolt://localhost:7687` |
| Valkey | `localhost:6379` |
| Temporal | `localhost:7233` |
| Temporal UI | `http://localhost:8080` |

Logs and shutdown:

```bash
./scripts/infra.sh logs
./scripts/infra.sh logs neo4j
./scripts/infra.sh stop
```

Destructive reset requires an explicit guard:

```bash
CONFIRM_RESET=YES ./scripts/infra.sh reset
```

### Option B — external or manually installed infrastructure

Docker is optional. Point the root `.env` to reachable MongoDB, SQL Server, Neo4j, Valkey, and Temporal endpoints. FastAPI and the frontend require only network connectivity and valid credentials.

Required SQL migrations:

```text
infra/sqlserver/init/001_return_business_state.sql
infra/sqlserver/init/002_domain_models.sql
```

Apply them from a host that has Microsoft `sqlcmd` 18 installed. This avoids putting the
password on the command line or in process listings:

```bash
set -a
source .env
set +a

export SQLCMDPASSWORD="${PLATFORM_SQLSERVER_PASSWORD}"
sqlcmd -C \
  -S "${PLATFORM_SQLSERVER_HOST},${PLATFORM_SQLSERVER_PORT}" \
  -U "${PLATFORM_SQLSERVER_USER}" \
  -d master \
  -b \
  -Q "IF DB_ID(N'${PLATFORM_SQLSERVER_DATABASE}') IS NULL CREATE DATABASE [${PLATFORM_SQLSERVER_DATABASE}]"
sqlcmd -C \
  -S "${PLATFORM_SQLSERVER_HOST},${PLATFORM_SQLSERVER_PORT}" \
  -U "${PLATFORM_SQLSERVER_USER}" \
  -d "${PLATFORM_SQLSERVER_DATABASE}" \
  -b \
  -i infra/sqlserver/init/001_return_business_state.sql
sqlcmd -C \
  -S "${PLATFORM_SQLSERVER_HOST},${PLATFORM_SQLSERVER_PORT}" \
  -U "${PLATFORM_SQLSERVER_USER}" \
  -d "${PLATFORM_SQLSERVER_DATABASE}" \
  -b \
  -i infra/sqlserver/init/002_domain_models.sql
unset SQLCMDPASSWORD
```

`./scripts/infra.sh start` runs the same versioned migrations automatically when Compose
is used for infrastructure.

### Option C — entire application in containers

This is optional and not the default developer path:

```bash
./scripts/infra.sh full-containerized
# equivalent
docker compose --profile containerized-app up -d --build --wait
```

## 9. Run the application on the host

Open separate terminals after infrastructure is ready.

### API

```bash
./scripts/run_backend_host.sh
```

PowerShell:

```powershell
./scripts/run_backend_host.ps1
```

### Temporal workflow worker

```bash
./scripts/run_worker_host.sh temporal
```

### Return orchestrator

```bash
./scripts/run_worker_host.sh orchestrator
```

### Outbox publisher

```bash
./scripts/run_worker_host.sh outbox
```

### Data job worker

```bash
./scripts/run_worker_host.sh jobs
```

### Frontend

```bash
./scripts/run_frontend_host.sh
```

PowerShell worker examples:

```powershell
./scripts/run_worker_host.ps1 temporal
./scripts/run_worker_host.ps1 orchestrator
./scripts/run_worker_host.ps1 outbox
./scripts/run_worker_host.ps1 jobs
./scripts/run_frontend_host.ps1
```

Linux development convenience command:

```bash
./scripts/run_all_host.sh
```

Endpoints:

- Frontend: `http://localhost:5173`
- FastAPI: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`
- Readiness: `http://localhost:8000/health/ready`

## 10. Initialize schemas and deterministic seed data

The SQL init scripts create:

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

The deterministic seed runner creates coherent records across:

```text
Source MongoDB:
  salesInv
  customerOutboundCDM
  shipmentInfo
  lkpSearchProduct

Platform MongoDB:
  seed metadata and operational evidence

SQL Server:
  ten scenario records and configured bay master data

Neo4j:
  graph projection through the graph-sync service
```

Apply deterministic seed data from the host:

```bash
cd backend
poetry run python scripts/seed_e2e_data.py
# without Poetry
.venv/bin/python scripts/seed_e2e_data.py
```

Use the Data Console **Seed Data** screen for apply/status/reset operations after the API starts.

## 11. Associate return flow

Open:

```text
http://localhost:5173/associate/returns
```

Supported initial anchors:

- Sales Order number
- Customer ID
- Phone
- Email
- Tracking number
- SKU/product

The assistant first queries Neo4j. When graph context is absent or incomplete, it performs a targeted Source MongoDB read, updates the graph projection, then returns candidate orders and lines. The associate must confirm one exact order line before return details can be submitted.

Locks are:

- unique only while active
- digest-bound
- optimistic-version checked
- automatically expiring when abandoned
- released on completion, rejection, cancellation, or failure

## 12. Data Console screens

Each concern has a separate route and screen:

| Screen | Route | Purpose |
|---|---|---|
| Model & Schema | `/data-console/schema` | Mongo collections, SQL tables, graph labels/relationships, fields, keys, ownership |
| AI Studio | `/data-console/ai-studio` | Deterministic or AI-assisted schema-bound proposal generation, record preview, digest review, governed apply |
| Graph Sync | `/data-console/graph-sync` | Apply graph constraints and synchronize Source MongoDB/SQL into Neo4j |
| Feedback Learning | `/data-console/feedback-learning` | Review evidence-based recommendations; no automatic rule mutation |
| Inventory | `/data-console/inventory` | Observed data assets and metadata |
| Graph Evidence | `/data-console/graph-evidence` | Immutable graph validation evidence |
| Sources | `/data-console/sources` | Source connectivity and capability |
| Workspaces | `/data-console/workspaces` | Sandbox records with optimistic concurrency |
| Imports/Exports | `/data-console/imports`, `/data-console/exports` | Durable data jobs and artifacts |
| Scenarios | `/data-console/scenarios` | Digest-bound scenario lifecycle |
| Audit/Governance/Hardening | corresponding routes | Operational evidence and controls |

## 13. AI Studio safety model

AI Studio can generate proposals for every registry asset, including all modeled MongoDB collections and SQL tables. It does **not** blindly write every proposal.

Apply rules:

- development/test only
- expected SHA-256 digest required
- source sandbox collections and explicit sandbox tables are allowlisted
- service-owned collections with invariants remain proposal-only
- SQL writes use transactional upsert behavior
- no credentials or provider secrets are generated
- production apply is denied

Model registry:

```text
backend/config/schema_registry.yaml
```

Current version-controlled catalog:

- 41 physical assets
- 33 MongoDB collections
- 8 SQL Server tables
- 13 Neo4j node labels
- 16 Neo4j relationship types
- 90 explicit AI Studio field generators; unknown generator names fail closed

API routes:

```text
GET  /data-console/v1/schema
GET  /data-console/v1/schema/assets
GET  /data-console/v1/schema/graph
POST /data-console/v1/ai-studio/proposals
GET  /data-console/v1/ai-studio/proposals/{id}
POST /data-console/v1/ai-studio/proposals/{id}/apply
```

## 14. Graph schema and synchronization

Graph schema and fixed constraints are defined in:

```text
backend/src/return_platform/data_platform/graph/schema.py
backend/config/schema_registry.yaml
```

Graph synchronization is implemented in:

```text
backend/src/return_platform/data_platform/graph/sync_service.py
```

Modes:

- `FULL`
- `SOURCE_MONGODB`
- `SQLSERVER`

API routes:

```text
POST /data-console/v1/graph-sync/schema/apply
POST /data-console/v1/graph-sync/runs
GET  /data-console/v1/graph-sync/runs
GET  /data-console/v1/graph-sync/runs/{id}
```

The sync service is bounded, batch-oriented, parameterized, evidence-producing, and rebuilds Neo4j only from authoritative sources.

## 15. Feedback Learning Agent

The Feedback Learning Agent consumes persisted workflow evidence after return processing and records:

- final outcome
- late or missing fields
- support clarification/rework
- graph-sync evidence
- source usage
- bay outcome
- reviewable recommendations
- canonical evidence digest

Recommendations enter `REVIEW_PENDING`. They never modify production prompts, mappings, graph rules, or bay configuration automatically.

## 16. Quality gates

### Backend

```bash
cd backend
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests
poetry run pytest -q
poetry run pytest --cov=return_platform --cov-report=term-missing --cov-fail-under=90
```

Virtual-environment equivalent:

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/pytest -q
```

### Frontend

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
npm run test:a11y
npm run contracts:check
```

### Source-only fallback gate

```bash
python3.13 scripts/validate_stage4_source.py
python3.13 scripts/validate_stage4_contracts.py
node scripts/validate_frontend_syntax.mjs
```

The source-only gate does not replace dependency-backed tests.

## 17. OpenAPI contract convergence

```bash
cd frontend
npm run contracts:generate
npm run contracts:check
```

Generated artifacts:

```text
frontend/openapi/return-platform.openapi.json
frontend/src/api/generated/return-platform.d.ts
```

Contract drift is a release blocker.

## 18. Live AI validation

Keys are read from root `.env`; never pass them on the command line.

```bash
cd backend
poetry run python scripts/validate_ai_gateway_live.py
```

Required evidence:

- provider readiness without exposing keys
- redacted request payloads
- timeout/retry behavior
- ordered failover
- response schema validation
- persisted request/response digest
- interception and manual override behavior

## 19. Release classifications

| Classification | Required proof |
|---|---|
| `SOURCE_VALIDATED` | Compilation, source validators, route/model/topology inspection |
| `CONTRACT_TESTED` | Backend/frontend dependency-backed gates and zero OpenAPI drift |
| `SANDBOX_VALIDATED` | Full infrastructure, seed, ten scenarios, real-time replay, restart/failure matrix, live sandbox providers |
| `PRODUCTION_READY` | Security, observability, performance, DR, supply-chain, deployment and rollback evidence |
| `PRODUCTION_VALIDATED` | Successful protected production deployment, smoke tests, SLO evidence, and rollback proof |

Do not promote based on source inspection alone.

## 20. Evidence

Primary evidence directory:

```text
docs/evidence/stage4_e2e_completion/
```

Primary implementation handoff:

```text
STAGE_4_HLD_ALIGNMENT_DATA_CONSOLE_IMPLEMENTATION_HANDOFF.md
```


# Stage 4L — Production Ferguson Return Application

Stage 4L adds the production return application on top of this repository. It is not a separate demo fork.

## Production capabilities

- five bounded return agents;
- validated, versioned return configuration;
- graph-first Associate discovery and explicit confirmation;
- internal Returns Support queue and shared thread;
- production Temporal workflow v2;
- branch staging, parcel, freight, offsite pickup, receipt, resolution, and vendor-recovery state;
- OMC, carrier, external-ticket, notification, and artifact adapter contracts;
- transactional integration outbox;
- warehouse bay recommendation and atomic reservation/assignment;
- Associate, Support, Logistics, Warehouse, Tracking, Agent Evidence, Dependency, and Integration Outbox screens.

OCR and image-processing workers are deliberately disabled in the initial production foundation. Artifact metadata contracts are present for later asynchronous processing.

## Configuration

```text
backend/config/returns/production.yaml
```

Environment variables are documented in `.env.example`. Important optional adapter settings include:

```text
PLATFORM_RETURN_SUPPORT_MODE
PLATFORM_SUPPORT_TICKET_BASE_URL
PLATFORM_SUPPORT_TICKET_API_KEY
PLATFORM_OMC_COMMAND_BASE_URL
PLATFORM_OMC_COMMAND_API_KEY
PLATFORM_CARRIER_BOOKING_BASE_URL
PLATFORM_CARRIER_BOOKING_API_KEY
PLATFORM_CUSTOMER_NOTIFICATION_BASE_URL
PLATFORM_CUSTOMER_NOTIFICATION_API_KEY
PLATFORM_RETURN_CONFIGURATION_PATH
```

With no external endpoint configured, commands remain visible as blocked external dependencies. The application does not fabricate success.

## Run production host services

Backend API:

```bash
cd backend
poetry install
poetry run uvicorn return_platform.asgi:app --host 127.0.0.1 --port 8000 --reload
```

Temporal worker:

```bash
cd backend
poetry run python scripts/run_return_workflow_worker.py
```

Integration outbox worker:

```bash
cd backend
PYTHONPATH=src poetry run python -m return_platform.workers.integration_outbox
```

Frontend, using Node 24 and npm 11:

```bash
cd frontend
npm ci
npm run dev
```

Core routes:

```text
/associate/returns
/operations/returns/:sessionId
/operations/return-agents
/return-support/workbench
/logistics/returns
/warehouse/returns
/tracking/returns
/system/integration-outbox
/system/dependencies
```

## Stage 4L validation

Source validation:

```bash
python3.13 scripts/validate_stage4l_production.py
python3.13 scripts/validate_stage4_source.py
python3.13 scripts/validate_stage4_contracts.py
node scripts/validate_frontend_syntax.mjs
```

Full release gates remain mandatory:

```bash
cd backend
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src tests
poetry run pytest -q

cd ../frontend
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
npm run test:a11y
npm run contracts:check
```

Current Stage 4L evidence classification: `SOURCE_VALIDATED`. See `docs/evidence/stage4l_production/`.


---

## Stage 4M — External Dependency Simulation

OMC, parcel carriers, Freight/TMS and LSI are represented through stable gateway contracts. Development and test environments can run deterministic live-service emulators while production uses real, manual or blocked adapters.

The simulator does not place mock logic inside the return workflow. It is selected through configuration:

```env
PLATFORM_OMC_DEPENDENCY_MODE=SIMULATED
PLATFORM_PARCEL_DEPENDENCY_MODE=SIMULATED
PLATFORM_FREIGHT_DEPENDENCY_MODE=SIMULATED
PLATFORM_LSI_DEPENDENCY_MODE=SIMULATED
```

Production startup fails when any of these values is `SIMULATED`.

### AI behavior

Simulator facts, identifiers and state transitions are deterministic. Optional AI is used only to improve operational wording. The simulator uses lightweight models from `backend/config/dependency_simulation.yaml` and always returns a versioned default template when a provider is missing, times out, fails or returns invalid JSON. AI failure never blocks RMA, label, BOL, receipt, RGA or status simulation.

All attempts are captured with provider, model, tokens, latency, fallback usage, error code, request/response digests and configurable cost estimate. Pricing defaults to zero until approved provider pricing is entered.

### Start the complete host application

```bash
cp .env.example .env
./scripts/bootstrap_host.sh
./scripts/start_stage4m_simulation.sh
```

### Run an API-driven E2E scenario

```bash
./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL
./scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY
```

### Dedicated pages

```text
/system/dependency-simulator
/system/dependency-simulator/omc
/system/dependency-simulator/parcel
/system/dependency-simulator/freight
/system/dependency-simulator/lsi
/system/dependency-simulator/ai-metrics
```

### Validation

```bash
./scripts/run_stage4m_gates.sh
```

Detailed documents:

- `docs/plans/STAGE_4M_DEPENDENCY_SIMULATION_IMPLEMENTATION_PLAN.md`
- `docs/STAGE_4M_DEPENDENCY_SIMULATION_ARCHITECTURE.md`
- `docs/runbooks/STAGE_4M_SIMULATED_E2E_RUNBOOK.md`
- `docs/STAGE_4M_IMPLEMENTATION_REPORT.md`

---

## Stage 4N — AI Gateway Key/Model Pools, Safety, and Simulator Validation

Stage 4N hardens the AI control plane while keeping AI non-authoritative. Credentials and models are configured as lists, tasks deterministically choose a lightweight or standard tier, unhealthy routes rotate by key/model/provider, and every failed route ultimately uses an approved fallback rather than blocking the return workflow.

### Configure credential and model lists

Keep credentials in `.env`:

```env
PLATFORM_GOOGLE_API_KEYS=["google-key-a","google-key-b"]
PLATFORM_GOOGLE_LIGHTWEIGHT_MODELS=["google-light-model-a","google-light-model-b"]
PLATFORM_GOOGLE_STANDARD_MODELS=["google-standard-model-a"]

PLATFORM_NVIDIA_API_KEYS=["nvidia-key-a"]
PLATFORM_NVIDIA_LIGHTWEIGHT_MODELS=["nvidia-light-model-a"]
PLATFORM_NVIDIA_STANDARD_MODELS=["nvidia-standard-model-a"]
```

Empty lists are valid. Legacy single-key/single-model variables remain readable for migration but should not be used for new environments.

Model names, task tiers, limits, retry policy, circuits, exact input allowlists, and fallback strategies are governed by:

```text
backend/config/ai_gateway.yaml
```

### Task-based model complexity

Lightweight models are used for low-complexity work such as simulator narratives, eligibility, smart questions, status summaries, and notification drafts. Standard models are reserved for conflicting order evidence, multi-message Support analysis, cross-system reconciliation, and feedback recommendations.

The registered `taskId` selects the tier. The AI cannot choose a more powerful tier, and the simulator task cannot escalate beyond `LIGHTWEIGHT`.

### Failure sequence

```text
healthy model + key
→ another key for the same model
→ another model in the same tier
→ another allowed provider in the same tier
→ exact deterministic fallback or manual review
```

Credential authentication/rate failures isolate that credential. Model-unavailable/context failures isolate that model. Provider failures are circuit-broken after the configured threshold. Retries and the total request deadline are bounded.

### Rate limits and metrics

The gateway enforces application, tier, provider, model, credential, and route request/token budgets plus concurrency limits. Every attempt records safe route identifiers, model tier, selection/failure reason, latency, token usage, fallback use, prompt version, safety result, and request/response digests.

Raw API keys are never returned or persisted.

### Prompt-injection and domain protection

AI inputs pass deterministic controls before dispatch. Requests to override instructions, reveal prompts or secrets, bypass human approval, issue direct SQL, fabricate authoritative return actions, or answer unrelated questions are blocked. Outputs must match the exact registered schema and remain within Ferguson return operations.

### Run dependency-light simulator AI validation

```bash
./scripts/run_stage4n_ai_simulator_e2e.sh
```

### Run focused AI tests

```bash
./scripts/run_stage4n_ai_tests.sh
```

### Run all source gates

```bash
./scripts/run_stage4n_full_gates.sh
```

### Run the live-stack simulated-return E2E

Terminal 1:

```bash
cp .env.example .env
./scripts/start_stage4m_simulation.sh
```

Terminal 2:

```bash
./scripts/run_stage4n_live_stack_e2e.sh
```

No provider key is required for the simulated return to complete. When every AI route fails or is absent, the versioned template response is used and the deterministic OMC/parcel/freight/LSI simulator operation continues.

### Dedicated AI operations pages

```text
/ai-gateway/routes       provider/model/key health and circuit state
/ai-gateway/tasks        task tier, prompt, limits, providers, and fallback
/ai-gateway/metrics      per-attempt token, latency, route, and fallback metrics
/ai-gateway/safety       development/test prompt-injection and domain test
/ai-gateway              request trace list
/system/dependency-simulator/ai-metrics
```

Detailed documentation:

```text
docs/implementation/STAGE_4N_AI_GATEWAY_HARDENING.md
docs/runbooks/STAGE_4N_AI_SIMULATOR_E2E.md
docs/evidence/stage4n_ai_gateway/validation_summary.json
```
