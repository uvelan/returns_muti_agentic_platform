# Return Multi-Agent Platform

Production-oriented return orchestration with an associate-facing Order Discovery Copilot, graph-configured runtime behavior, Vault-managed credentials, durable workflow execution, and operational evidence tooling.

> **Consolidation in progress.** This repository is being restructured onto a single four-domain architecture
> (`/returns`, `/config`, `/graph-schema`, `/ai`), removing the V1/V2/Data Console split. Full detail:
> [`docs/UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md`](docs/UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md)
> and [`docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`](docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md).
>
> - **Consolidation source branch:** `feat/v2-order-discovery-integration`
> - **Source commit:** `c3cdd354fdef93583c2b67da219701e76489a221`
> - **Consolidation branch:** `refactor/unified-return-platform`
> - **Target application architecture:** `backend/src/return_platform/{bootstrap,platform,configuration,agents,business,graph,graph_schema_analyzer,ai}` — see the target design doc §2 for the full tree.
>
> This section is removed in Phase 28 once the root README describes only the finished architecture. Baseline
> inventory: [`docs/consolidation/baseline-inventory.md`](docs/consolidation/baseline-inventory.md).

## Current architecture

The application runs Python and React as host processes by default. Infrastructure runs in Docker Compose.

```text
Associate Copilot / Customer / Support / Data Console
                         |
                         v
                   FastAPI backend
                         |
          +--------------+---------------+
          |              |               |
          v              v               v
    Platform MongoDB   Neo4j         Temporal
    sessions/audit     config +      workflows/timers
    outbox/state       discovery
          |
          +------------------+
          |                  |
          v                  v
   Source MongoDB        SQL Server
   customer/order/SKU    return/RMA/tracking facts
   read-only             read-only
```

### Ownership boundaries

| System | Responsibility | Platform access |
|---|---|---|
| Platform MongoDB | Conversation state, discovery locks, audits, configuration receipts, outbox, operational state | Read/write |
| Source MongoDB | Customer, order, shipment, and product discovery records | Read-only |
| SQL Server | Authoritative return, RMA, and tracking business facts | Read-only |
| Neo4j | Authoritative runtime configuration graph and customer/order/product discovery graph | Controlled read/write |
| Temporal | Workflow execution, retries, and timers | Execution only |
| Valkey | Event streams and non-secret runtime coordination | Read/write |
| Vault | Database credentials, AI keys, tokens, certificates, and validation fingerprint material | Path-scoped read/write |

Secrets must never be stored in Neo4j, MongoDB documents, Valkey, Temporal payloads, frontend storage, logs, evidence files, or AI traces.

## Production Order Discovery Copilot

The associate Copilot is the real order-discovery entry point for the return flow.

1. The associate provides an order number, customer ID, tracking number, SKU, phone, email, customer name, or product description.
2. Strong identifiers use deterministic exact matching first.
3. Customer names and product descriptions may use bounded Neo4j full-text fuzzy retrieval.
4. Ambiguous candidates move through a durable, multi-turn conversation state machine.
5. The server chooses the required disambiguation slot. AI may phrase the approved question but cannot select a customer, order, or line.
6. Candidate cards or natural-language answers resolve the requested slot.
7. Explicit confirmation creates an immutable Discovery Lock.
8. The confirmed customer, order, and line continue into the return workflow.
9. AI failure returns a deterministic response and does not break the business flow.

The existing console is canonical under `/v1` (including `/v1/associate/returns`);
legacy unversioned browser routes redirect to their `/v1` equivalents. The
responsive Order Discovery Copilot v2 workspace is available at
`/v2/copilot` and calls only the `/api/v2/copilot` API family. The internal
Copilot Operations Console uses the same persistent conversation service and
is restricted by backend administrative authorization.

### Versioned UI routes

| Experience | Canonical route | Notes |
|---|---|---|
| Existing Returns Assistant | `/v1/associate/returns` | All existing console routes are canonical below `/v1` |
| Order Discovery Copilot v2 | `/v2/copilot` | Responsive desktop, tablet, and mobile workspace |
| Legacy unversioned routes | `/associate/returns`, `/overview`, and others | Redirect to the matching `/v1/...` route |

### Copilot v2 API endpoints

The v2 API surface is intentionally limited to Order Discovery Copilot
operations. It delegates to the same production conversation, evidence,
authorization, locking, and return-submission services used by v1.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v2/copilot/conversations` | List recent Copilot conversations |
| `POST` | `/api/v2/copilot/conversations` | Start discovery with a structured anchor |
| `POST` | `/api/v2/copilot/chat` | Start discovery from natural language |
| `GET` | `/api/v2/copilot/conversations/{conversation_id}` | Load one conversation |
| `POST` | `/api/v2/copilot/conversations/{conversation_id}/chat` | Continue with natural language |
| `POST` | `/api/v2/copilot/conversations/{conversation_id}/messages` | Submit a structured clarification anchor |
| `POST` | `/api/v2/copilot/conversations/{conversation_id}/confirm` | Confirm and lock the selected order line |
| `POST` | `/api/v2/copilot/conversations/{conversation_id}/details` | Submit return details to the production workflow |

Start a natural-language discovery conversation:

```bash
curl -fsS \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: readme-copilot-v2-001' \
  -d '{"message":"Find the damaged faucet order for customer ZIP 30301"}' \
  http://127.0.0.1:8000/api/v2/copilot/chat | jq
```

The response uses the standard `APIResponse` envelope. Use the returned
conversation `id`, `version`, candidate-set metadata, and requested
clarification fields for subsequent calls; do not invent or reuse stale
versions.

## Graph-first runtime configuration

Neo4j is the authoritative control-plane store for versioned configuration. Runtime processes
periodically compare the graph head revision with their last-good immutable snapshot; they do not
traverse the complete configuration graph for each request.

Startup sequence:

```text
load version-controlled baseline schema
        -> resolve bootstrap credentials from Vault
        -> connect to Neo4j
        -> load the active ConfigurationHead release
        -> verify release checksum
        -> validate the complete configuration model
        -> resolve graph-declared Vault references
        -> create immutable process snapshot
        -> initialize dependency clients
```

Every conversation pins:

- configuration release ID;
- configuration head revision;
- configuration checksum;
- configuration source.

A configuration release uses the following lifecycle:

```text
DRAFT -> VALIDATED -> RELEASED -> SUPERSEDED -> ARCHIVED
```

Published releases are immutable. Publication requires the expected head revision, preventing concurrent administrators from activating two releases.

The Configuration Studio can clone the active release and edit every runtime behavior domain:

- `RETURN_PLATFORM`: agents, discovery, clarification, workflow, return policy, integrations,
  feature flags, and source-resolution behavior.
- `AI_GATEWAY`: task system prompts, prompt versions, provider allowlists, token limits, retry,
  rate limiting, circuit breakers, and deterministic fallback selection.
- `DEPENDENCY_SIMULATION`: simulation contracts, operation sequences, narrative behavior, provider
  order, timeouts, and pricing assumptions.

A published release is activated atomically in the API process that accepted the publication.
Other API processes detect the new graph-head revision within five seconds and activate the same
validated domains without a restart. The AI route pool is rebuilt at the same activation boundary.
MongoDB retains the digest-addressed runtime snapshot as audit evidence; it is not an editable
configuration authority.

For targeted automation, draft documents also support an object merge patch:

```http
PATCH /data-console/v1/configuration/releases/{release_id}/domains/RETURN_PLATFORM
Content-Type: application/json

{
  "patch": {
    "agents": {
      "order_discovery": {
        "version": "2.1",
        "human_confirmation_required": true
      }
    }
  }
}
```

The merged document must pass complete `ReturnPlatformConfiguration` validation before it is
written to the draft. Equivalent typed validation applies to the AI gateway and dependency
simulation domains. Secrets remain in Vault; graph configuration stores only validated secret
references and receipts. Deployment wiring, database schema definitions, and graph migrations
remain version controlled because they are infrastructure contracts rather than agent behavior.

### Migrating an existing environment

Releases created before the graph-owned behavior migration may contain only `RETURN_PLATFORM`.
Publish a complete three-domain release before starting the upgraded API or workers:

```bash
./scripts/prepare_runtime_configuration.sh
```

Production and staging fail closed when `AI_GATEWAY` or `DEPENDENCY_SIMULATION` is absent. Use this
rollout order:

1. Make the upgraded configuration bootstrap command available.
2. Run `prepare_runtime_configuration.sh` while the existing application remains available.
3. Confirm that the active release contains `RETURN_PLATFORM`, `AI_GATEWAY`, and
   `DEPENDENCY_SIMULATION`.
4. Roll the API and worker processes.
5. Confirm `/data-console/v1/configuration/active-snapshot` reports the new release and checksum.

Graph migrations are checksum-tracked in `ConfigurationMigration` nodes. Modified migration files are rejected after application.

## AI provider, key, and model configuration

AI providers, keys, models, task bindings, priorities, rate limits, and failover order are configurable. Raw keys are accepted only by the backend validation control plane.

Validation sequence:

1. Validate the provider adapter and endpoint allowlist.
2. Authenticate with the transient key.
3. Discover accessible models.
4. Verify the exact model ID.
5. Run a minimal synthetic inference.
6. Verify required structured output and task capability.
7. Store the key in Vault only after all checks pass.
8. Create a receipt bound to the provider, model, task, secret fingerprint, Vault version, and configuration checksum.
9. Allow publication only when every active key/model/task route has a valid receipt.

Multiple keys and models are routed through bounded lists. Authentication failure, throttling, timeout, or model failure rotates to the next validated route. Provider routes have per-key concurrency controls, rate limits, and circuit state.

The following task is implemented for Order Discovery:

```text
RETURN_DISCOVERY_INTENT_V1
RETURN_PROGRESSIVE_DISAMBIGUATION_V1
```

`RETURN_DISCOVERY_INTENT_V1` may classify an untrusted utterance and extract one
validated lookup anchor. Explicit identifiers always win over conflicting AI
output, and invalid, unsafe, low-confidence, intercepted, rate-limited, or
unavailable AI routes fall back to bounded deterministic extraction. AI
authority is restricted to approved wording and structured interpretation.
Database access, state transitions, and candidate selection remain
deterministic.

## AI Studio and Operational Generation

The AI Studio provides a deterministic Operational Generation engine to seed synthetic, high-fidelity business data without executing manual frontend workflows.

- **Deterministic Proposals**: AI generates natural language attributes (names, reasons) but core relational constraints (IDs, keys, foreign keys) and structural constraints (quantities, dates) are purely deterministic.
- **Saga Execution**: Generated proposals are broken into transactional write plans and executed through a durable saga. 
- **Rollback**: If a write transaction fails, or if requested by the administrator, the entire generated proposal is safely rolled back using inverse compensation transactions.
- **Graph Synchronization**: Records written to source systems (MongoDB/SQL Server) are securely synchronized to the Neo4j graph, matching the exact path used by production integration events. Generated data is fully discoverable by the production Copilot.
- **Data Policies**: AI Studio evaluates strict read/write policies. Data is not generated for assets marked with `DENIED` write policies. Generation gracefully falls back to deterministic values if AI is unavailable.

### Deterministic E2E seed

The cross-store E2E seed is configured in
`backend/config/seed/e2e_seed_manifest.json`. Counts, customer/product catalogs,
fixed products, and scenario rows can be changed or extended in JSON without
editing the Python materializer. The default manifest expands deterministically
to 10,000 customers, 20,000 products, 1,000,000 orders, and 1,000,000
shipments, including
multi-line orders and the positive, negative, and review-required scenarios.
Return and support-case collections remain empty before a demo.
Million-order definitions are lazy and source writes use bounded bulk batches,
so configuration loading and validation do not materialize the entire dataset
in memory.

Seed evidence uses the Vault-aware validation fingerprint key to produce a
keyed digest. Graph synchronization selects every record matching the active
seed version and digest, rejects digest drift, and does not use an arbitrary
record limit for an active seed projection.

## Data-source configuration

Data-source metadata, dataset requirements, access mode, routing, Vault references, and validation receipts are graph-configured.

A data source cannot be activated until the backend verifies:

- connector type;
- endpoint allowlist and DNS resolution;
- cloud metadata endpoint blocking;
- transport connectivity;
- authentication;
- safe health query;
- required database, collections, tables, or indexes;
- requested access mode against the code-owned connector capability;
- configuration checksum and exact Vault secret version.

Source MongoDB and SQL Server are permanently constrained to read-only access by code. Graph configuration may narrow access but cannot broaden it.

## Vault behavior

Vault KV v2 is the exclusive runtime source for credential values.

Neo4j stores only references such as:

```text
vault://secret/production/data-sources/sqlserver#password?version=3
vault://secret/production/ai/google/key-01#api_key?version=2
```

The validation service uses compare-and-swap writes. Existing secret documents are merged so sibling fields are preserved. If receipt persistence fails, the staged write is rolled back without exposing the secret.

Runtime processes fetch credentials when creating or refreshing clients. Credentials are not fetched for every business query.

The local Vault bootstrap stores separate MongoDB connection references for host and
container execution. Host processes resolve loopback DSNs; container processes resolve
Docker service DNS names. Published graph entries marked `bootstrap_managed` cannot
override these deployment-specific endpoints.

Phone and email lookup evidence is generated with a Vault-managed, domain-separated
HMAC key. Rotating that key requires a complete customer graph reprojection before
phone/email lookup is enabled again because existing evidence cannot be recomputed in
place.

## Repository layout

```text
backend/
  config/                         version-controlled policy schemas and baseline values
  scripts/                        container entry points
  src/return_platform/
    ai_gateway/                   bounded provider/model/key routing
    api/                          production APIs
    configuration/                graph releases, snapshots, and runtime resolver
    conversation/                 reusable conversation contracts and state engine
    data_console/                 internal administration and evidence APIs
    data_platform/                graph, schema, and source integration
    operations/                   Copilot and return business flow
    secrets/                      Vault resolver and redaction
    validation/                   validation gates and receipts
    workers/                      asynchronous operational workers
frontend/
  src/
    api/                          typed HTTP clients
    features/operations/          associate-facing production pages
    features/data-console/        internal administration pages
infra/                            Docker infrastructure configuration
scripts/                          Linux bootstrap, execution, validation, and evidence scripts
```

## Linux requirements

Host execution requires:

- Linux;
- Python 3.13;
- Node.js 24;
- npm 11;
- Docker Engine with the Compose plugin;
- `flock` from `util-linux`;
- Git;
- sufficient RAM for SQL Server, Neo4j, MongoDB, Temporal, PostgreSQL, Valkey, and Vault.

Verify versions:

```bash
python3.13 --version
node --version
npm --version
docker --version
docker compose version
flock --version
```

## First-time Linux setup

From the repository root:

```bash
./scripts/bootstrap_host.sh
```

This command:

- checks Python 3.13, Node 24, and npm 11;
- creates `.env` from `.env.example` when absent;
- upgrades an existing `.env` by appending missing non-secret Vault references without
  changing existing values;
- generates missing or placeholder local infrastructure credentials without printing
  them;
- generates the local MongoDB replica-set key when required;
- installs backend dependencies;
- installs frontend dependencies.

The generated infrastructure credentials initialize the local services and are copied
into Vault. Runtime processes use Vault references rather than direct `.env`
credentials.

Never commit `.env`, `.vault-local/`, generated tokens, unseal material, or credentials.

## Host-process startup

### 1. Start infrastructure

```bash
./scripts/infra.sh start
```

This starts:

- Vault;
- SQL Server;
- MongoDB replica set;
- Neo4j;
- Valkey;
- Temporal PostgreSQL;
- Temporal;
- Temporal UI.

The command also initializes/unseals Vault and stores local infrastructure credentials under the approved production Vault paths. Existing `.env` files are upgraded before validation so older Linux installations receive the required Vault references safely.

Seed volume is controlled by `backend/config/seed/e2e_seed_manifest.json`. Linux and
other full-scale environments use those JSON counts when
`PLATFORM_SEED_RECORD_LIMIT` is empty. Set the optional environment variable to a
positive integer of at least `10` to cap each generated customer, product, order,
sales, and shipment collection for a lower-resource validation run; for example,
`PLATFORM_SEED_RECORD_LIMIT=1000`.

The Seed Data page also accepts a per-run limit from `10` through `1,000,000`.
The JSON manifest and `PLATFORM_SEED_RECORD_LIMIT` remain hard upper bounds, so
the UI cannot exceed the configured environment capacity. The page polls the
active operation, shows progress, and can request a cooperative stop at the next
safe persistence boundary.

Seed administration endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/seed-data` | Read seed readiness and the last applied record limit |
| `POST` | `/api/v1/seed-data/apply` | Apply seed data with `{"recordLimit": 1000}` |
| `POST` | `/api/v1/seed-data/reset` | Delete the active seed version and apply the requested limit |
| `GET` | `/api/v1/seed-data/operation` | Read progress and cancellation state |
| `POST` | `/api/v1/seed-data/cancel` | Stop the active operation at a safe boundary |
| `POST` | `/api/v1/seed-data/delete` | Delete only active seed-owned data; requires `{"confirmation": "DELETE SEED DATA"}` |

Apply, reset, and delete are restricted to development and test environments.
Only one seed mutation can run in an API process at a time; concurrent requests
receive HTTP `409`.

### 2. Start backend, workers, and frontend

```bash
./scripts/run_all_host.sh
```

Before starting processes, this command:

1. stops previously managed application processes;
2. closes repository-owned listeners on ports `8000` and `5173`, while refusing
   to terminate unrelated processes;
3. serializes initialization with `flock`;
4. verifies Vault access;
5. applies checksum-tracked Neo4j migrations;
6. publishes and validates the initial graph configuration only when no active
   release exists;
7. starts the API, all workers, and the frontend.

Normal restarts reuse the active graph release and its Vault references. They do
not rerun live AI provider/model validation.

Host URLs:

| Service | URL |
|---|---|
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8000` |
| Backend readiness | `http://localhost:8000/health/ready` |
| Neo4j Browser | `http://localhost:7474` |
| Temporal UI | `http://localhost:8080` |
| Vault API | `http://127.0.0.1:8200` |

### Start one process

```bash
./scripts/run_backend_host.sh
./scripts/run_worker_host.sh temporal
./scripts/run_worker_host.sh orchestrator
./scripts/run_worker_host.sh outbox
./scripts/run_worker_host.sh jobs
./scripts/run_worker_host.sh integration-outbox
./scripts/run_frontend_host.sh
```

### Redeploy after source changes

To rebuild and restart the backend, workers, and frontend without rerunning
infrastructure bootstrap, graph publication, seed data, or AI validation:

```bash
./scripts/linux/redeploy_app.sh
```

If either lockfile changed, synchronize dependencies during the redeploy:

```bash
./scripts/linux/redeploy_app.sh --install-dependencies
```

Use `--skip-frontend-build` for a restart-only deployment.

Each backend or worker launcher prepares runtime configuration unless `PLATFORM_SKIP_RUNTIME_PREPARE=true` is explicitly supplied by the aggregate launcher.

## Fully containerized application mode

### Build and run with the repository script

```bash
./scripts/infra.sh full-containerized
```

The Compose profile performs this order:

```text
infrastructure health
  -> Vault initialization
  -> Neo4j migrations
  -> graph configuration publication
  -> seed initialization
  -> backend and workers
  -> frontend
```

### Build and run with Docker Compose directly

The backend API, workers, initialization jobs, seed runner, and frontend are
declared in the `containerized-app` profile. Always enable that profile when
building or starting the application:

```bash
docker compose --profile containerized-app build
docker compose --profile containerized-app up -d
```

The backend image is shared by the API, workers, and initialization jobs.
Compose therefore reports two built application images:

```text
return-platform-backend:local
return-platform-frontend:local
```

Containerized URLs:

| Service | URL |
|---|---|
| Copilot v2 UI | `http://localhost:3000/v2/copilot` |
| Existing v1 Returns Assistant | `http://localhost:3000/v1/associate/returns` |
| Backend API | `http://localhost:8000` |
| OpenAPI document | `http://localhost:8000/openapi.json` |
| API documentation | `http://localhost:8000/docs` |
| Liveness | `http://localhost:8000/health/live` |
| Readiness | `http://localhost:8000/health/ready` |

Verify the deployment:

```bash
docker compose --profile containerized-app ps
curl -fsS http://127.0.0.1:8000/health/live | jq
curl -fsS http://127.0.0.1:8000/health/ready | jq
curl -fsS http://127.0.0.1:8000/openapi.json |
  jq '.paths | keys | map(select(startswith("/api/v2/copilot")))'
```

Inspect status:

```bash
./scripts/infra.sh status
./scripts/infra.sh logs backend
./scripts/infra.sh logs runtime-configuration-init
```

Stop services:

```bash
./scripts/infra.sh stop
```

Delete local infrastructure volumes only with explicit confirmation:

```bash
CONFIRM_RESET=YES ./scripts/infra.sh reset
```

## Configuration administration

Frontend routes:

| Capability | Route |
|---|---|
| Configuration Studio | `/v1/data-console/configuration` |
| Runtime credential validation | `/v1/data-console/runtime-validation` |
| Copilot Operations Console | `/v1/data-console/copilot/operations` |
| General settings | `/v1/data-console/settings` |

Configuration publication requires backend write authorization. The Copilot Operations Console requires an administrative role and cannot bypass production APIs, configuration publication, Vault, or safety ceilings.

## Adding an AI key and model

For the repository-local bootstrap flow, add raw keys and the desired model lists to
the host `.env`, then run `./scripts/infra.sh full-containerized`. Bootstrap copies
non-placeholder keys into Vault, probes every configured provider/key/model/task
combination, publishes the successful bindings in a graph release, and activates that
release. Application containers receive Vault references only; they never receive the
raw keys. Re-running the bootstrap refreshes the active release when keys or models
change.

For production or manually administered credentials:

1. Open Runtime Credential Validation.
2. Select the provider.
3. Enter the key, exact model ID, model class, task, and a dedicated Vault reference.
4. Run validation.
5. Copy the returned versioned Vault reference, receipt ID, and validation checksum into a configuration draft.
6. Add the validated provider/model/task route.
7. Validate the full release.
8. Publish using the current head revision.

Do not place raw AI keys in a production application environment. Active application
routes are always resolved from published graph configuration and Vault.

## Adding a data source

1. Open Runtime Credential Validation.
2. Select MongoDB, Neo4j, or SQL Server.
3. Enter non-secret connection metadata and transient credentials.
4. Declare required datasets.
5. Run validation.
6. Add the returned versioned Vault reference and receipt to a configuration draft.
7. Validate and publish the complete release.

A source endpoint must be included in `PLATFORM_DATA_SOURCE_ALLOWED_HOSTS`. Production changes should use explicit hostnames or CIDR ranges rather than broad network ranges.

## Backend quality gates

```bash
cd backend

poetry run ruff check src tests scripts
poetry run mypy --strict src
poetry run pytest -q
```

Without Poetry:

```bash
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy --strict src
.venv/bin/python -m pytest -q
```

Focused Copilot and configuration gates:

```bash
cd backend
poetry run pytest \
  tests/conversation \
  tests/test_associate_chat_extraction.py \
  tests/test_graph_configuration.py \
  tests/test_configuration_api.py \
  tests/test_secrets_and_validation.py \
  tests/test_ai_gateway_routing.py \
  -q
```

## Frontend quality gates

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

Install Playwright browsers once:

```bash
cd frontend
npx playwright install --with-deps chromium
```

Run real infrastructure E2E:

```bash
cd frontend
npm run test:e2e:real -- tests/e2e/order-discovery-copilot-real.spec.ts
npm run test:e2e:real -- tests/e2e/happy-path-real.spec.ts
```

## Complete Linux validation

```bash
./scripts/linux/run_full_linux_validation.sh
```

The Linux validation sequence checks prerequisites, repository state, backend quality, frontend quality, contracts, infrastructure, seed readiness, host processes, worker heartbeats, APIs, accessibility, real E2E behavior, restart/replay behavior, and evidence generation.

## Static source checks

These checks do not replace dependency-backed tests:

```bash
python3 -m compileall -q backend/src backend/tests scripts
node scripts/validate_frontend_syntax.mjs
find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
python3.13 scripts/validate_stage4_source.py
python3.13 scripts/validate_stage4_contracts.py
git diff --check
```

Frontend contract verification:

```bash
cd frontend
npm run contracts:check
cd ..
```

## Required failure behavior

| Failure | Required result |
|---|---|
| All AI providers unavailable | Use deterministic task response and continue the main flow |
| One AI key rejected | Open the key circuit and try another validated key |
| Model removed or inaccessible | Try the next validated model/provider route |
| Neo4j discovery unavailable | Use approved source fallback when policy and evidence permit |
| Weak fuzzy result | Never trigger graph synchronization unless explicitly enabled |
| Vault temporarily unavailable with initialized client pools | Continue bounded use of already established clients |
| Vault unavailable before client creation | Fail the affected dependency initialization; never use `.env` credential fallback |
| Configuration checksum mismatch | Refuse startup or activation |
| Stale configuration head revision | Return a configuration revision conflict |
| Stale candidate card | Reject selection using candidate-set ID, expiry, and conversation version |
| Duplicate message | Return the prior idempotent result rather than applying it twice |

## Security rules

- Strong identifiers are deterministic and exact-first.
- Fuzzy indexes contain approved natural-language fields only.
- Phone and email evidence uses normalized, domain-separated HMAC-SHA256 values in Neo4j.
- AI receives redacted, bounded facts only.
- AI cannot choose candidates, change workflow state, generate database queries, or bypass confirmation.
- Source MongoDB and SQL Server remain read-only.
- User-configured endpoints are allowlisted and metadata IPs are blocked.
- Vault writes use compare-and-swap versioning.
- Published graph releases are immutable and checksum verified.
- All administrative actions are audited.
- The frontend never receives secret values.

## Health and diagnostics

```bash
curl -fsS http://127.0.0.1:8000/health/live | jq
curl -fsS http://127.0.0.1:8000/health/ready | jq
./scripts/infra.sh status
```

Worker heartbeat validation:

```bash
./scripts/linux/12_validate_worker_heartbeats.sh
```

API probes:

```bash
./scripts/linux/13_run_api_probes.sh
```

## Common failures

### Vault token file missing

```bash
./scripts/infra.sh start
python3.13 scripts/vault/bootstrap_local_vault.py
ls -l .vault-local/return-platform.token
```

### Graph configuration release missing

```bash
./scripts/prepare_runtime_configuration.sh
```

### Neo4j index not online

```bash
python3.13 scripts/apply_neo4j_migrations.py
```

### Phone or email lookup stopped after HMAC rotation

The lookup key is intentionally non-recoverable from graph evidence. Rebuild the
customer projection using the current Vault key, validate graph freshness, and only
then re-enable contact-based lookup.

### Frontend Node version rejected

Use Node 24 and npm 11, then rerun:

```bash
./scripts/bootstrap_host.sh
```

### Playwright browser missing

```bash
cd frontend
npx playwright install --with-deps chromium
```

## Delivery classification

Use evidence-backed classifications only:

```text
SOURCE_VALIDATED
  -> CONTRACT_TESTED
  -> PRODUCTION_READY
  -> PRODUCTION_VALIDATED
```

`PRODUCTION_VALIDATED` requires execution against the intended production environment and cannot be inferred from local checks.

## Startup AI Validation

Normal startup and redeployment do not contact live AI providers or validate
provider credentials and models. Runtime preparation reuses the active graph
configuration release.

Start all host processes without live AI validation:

```bash
./scripts/run_all_host.sh
```

Run live provider and model validation explicitly:

```bash
./scripts/run_all_host.sh --validate-ai
```

Redeploy without live AI validation:

```bash
./scripts/linux/redeploy_app.sh
```

Redeploy and run live AI validation once before startup:

```bash
./scripts/linux/redeploy_app.sh --validate-ai
```

During runtime preparation, missing keys in `.env` are copied from
`.env.example` using their version-controlled defaults. Existing `.env` values
are preserved and secrets are not printed.
