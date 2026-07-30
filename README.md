# Return Multi-Agent Platform

Production-oriented return orchestration with an associate-facing Order Discovery Copilot, graph-configured runtime behavior, Vault-managed credentials, durable workflow execution, and operational evidence tooling.

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

The production UI is available through the Associate Returns route. The internal Copilot Operations Console uses the same persistent conversation service and is restricted by backend administrative authorization.

## Graph-first runtime configuration

Neo4j is the authoritative control-plane store for versioned configuration. Runtime processes do not traverse the configuration graph for each request.

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
| Configuration Studio | `/data-console/configuration` |
| Runtime credential validation | `/data-console/runtime-validation` |
| Copilot Operations Console | `/data-console/copilot/operations` |
| General settings | `/data-console/settings` |

Configuration publication requires backend write authorization. The Copilot Operations Console requires an administrative role and cannot bypass production APIs, configuration publication, Vault, or safety ceilings.

## Adding an AI key and model

1. Open Runtime Credential Validation.
2. Select the provider.
3. Enter the key, exact model ID, model class, task, and a dedicated Vault reference.
4. Run validation.
5. Copy the returned versioned Vault reference, receipt ID, and validation checksum into a configuration draft.
6. Add the validated provider/model/task route.
7. Validate the full release.
8. Publish using the current head revision.

Do not place raw AI keys in `.env`. Environment key lists must remain empty; active routes are resolved from published graph configuration and Vault.

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
