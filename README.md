# Return Multi-Agent Platform

Production return orchestration: an associate-facing Order Discovery copilot,
runtime-configured behaviour held in a graph, environment-held credentials, durable
workflow execution, and operational evidence tooling.

**Current as of 2026-08-16, commit `2878be0`, branch
`refactor/unified-return-platform`.**

## Start here

| Question | Document |
|---|---|
| **I have never seen this repo. How do I get it running?** | [Running it](#running-it), then `./scripts/bootstrap_host.sh` and `./scripts/linux/reset_all.sh` |
| **How does a return actually run, end to end?** | [`docs/architecture/canonical-runtime-flow.md`](docs/architecture/canonical-runtime-flow.md) |
| How do I bring it up, in detail? | [`docs/operations/startup.md`](docs/operations/startup.md) |
| Something is wrong | [When something is wrong](#when-something-is-wrong), then [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) |
| What does each screen do? | [`docs/screens/`](docs/screens/README.md) |
| What does each endpoint guarantee? | [`docs/api/README.md`](docs/api/README.md) |
| What can I configure, and when does it take effect? | [`docs/configuration/families.md`](docs/configuration/families.md) |
| Where are the security boundaries? | [`docs/architecture/security-boundaries.md`](docs/architecture/security-boundaries.md) |
| Everything | [`docs/README.md`](docs/README.md) |

This README is the map. The depth is in `docs/`.

Where the two disagree, check the date stamps rather than assuming: `docs/` was
generally revised after this file, but the operational sections below —
[Running it](#running-it) and [When something is wrong](#when-something-is-wrong) —
are newer than `docs/operations/`, which is still stamped at commit `dcbb7dc` and
does not yet carry the container-recreate or graph-truncation notes.

## The canonical flow, in nine lines

```text
associate utterance
  → Order Discovery over the complete graph corpus
  → confirmation, which commits ONE case and starts ONE ReturnCaseWorkflow
  → Bay Assignment (concurrent, best-effort)
  → Support conversation, on business-calendar time
  → Case → N RMAs → N items, persisted to SQL
  → targeted graph sync of the affected records
  → shipment create/update, RMA-scoped
  → fulfilment reads shipment truth through the graph
```

Confirmation is the seam that matters. **If the workflow cannot be started, the
confirmation fails** — a case that exists without its workflow is unreachable by
every downstream agent — and a durable recovery sweep repairs the window where the
two systems disagree.

## Architecture

Python and React run as **host processes** by default. Infrastructure runs in Docker
Compose.

```text
nine domain screens + landing
        │
        ▼
   FastAPI backend
        │
   ┌────┼──────────┬─────────────┬────────┬────────┐
   ▼    ▼          ▼             ▼        ▼        ▼
Platform   Neo4j     Temporal    Valkey
MongoDB    config +  workflows/  streams
cases/     knowledge timers
audit/     graph
outbox        ▲
   │          │  projection (sources are read-only)
   │     ┌────┴──────────┐
   │     ▼               ▼
   │  Source MongoDB   SQL Server
   │  customer/order   source objects: READ-ONLY
   │  read-only        platform return tables: READ-WRITE
   ▼
platform-owned SQL return tables
```

### Ownership boundaries

| System | Responsibility | Platform access |
|---|---|---|
| Platform MongoDB | Conversation state, cases, discovery locks, audits, configuration receipts, process adoption, outbox, operational state | Read/write |
| **Source** MongoDB | Customer, order, shipment, product source records | **Read-only** |
| SQL Server — **source** objects | Source order/customer/product tables | **Read-only** |
| SQL Server — **platform-owned** objects | `dbo.return_requests`, `dbo.return_items`, `dbo.return_case`, `dbo.return_record`, `dbo.return_record_item`, `dbo.return_fulfillment`, `dbo.return_tracking`, `platform.bay_assignment`, `platform.bay_reservation`, `integration.return_support_ticket` | **Read/write** |
| Neo4j | Runtime configuration control plane, and the customer/order/product knowledge graph | Controlled read/write |
| Temporal | Workflow execution, retries, timers | Execution only |
| Valkey | Event streams and non-secret runtime coordination | Read/write |

> **"Read-only" applies to source-system objects only.** The platform owns and writes
> its own return tables in the same SQL Server. The two must not be conflated: the
> read-only guarantee is a **security boundary against source systems**, not a claim
> that the platform never writes to SQL Server.
>
> This table said "SQL Server … Read-only" unqualified while the platform inserted
> into eleven of its own tables there. The code was right; the sentence was wrong.

Source connectors are read-only **by code**. Graph configuration may narrow access;
it cannot broaden it.

**Secrets must never be stored in** Neo4j, MongoDB documents, Valkey, Temporal
payloads, frontend storage, logs, evidence files or AI traces.

### Backend packages

```text
backend/src/return_platform/
  bootstrap/              epoch-keyed reconfiguration, four-pass activation, adapters
  platform/               dependency-free kernel: contracts, capabilities, modules,
                          system store, reasoning, redaction, audit, governance
  configuration/          graph releases, snapshots, runtime resolver, sources,
                          process adoption, SQL migrations
  agents/                 the agent plugin contract and registry
  ai/                     provider routing, redaction, interception, ONE dispatch
                          boundary; `ai/gateway/` is the gateway surface
  api/                    HTTP surface, canonical and legacy
  canonical/              shared canonical models
  conversation/           reusable conversation contracts and state engine
  operations/             return business flow, SQL business state, connection pool,
                          warehouse placement
  policy/                 shared policy evaluation
  workflows/              Temporal workflows, activities, workers, recovery
  dynamic_knowledge/      order agent, graph generations, lifecycle, integration
  data_platform/          graph sync, schema, source integration
  data_governance/        inventory and sampling
  dependency_simulation/  dev/test simulators (forbidden in production)
  graph_schema_analyzer/  host-composable schema proposal and approval
  housekeeping/           scheduled maintenance work
  secrets/                optional Vault resolution
  security/               capabilities, roles, FastAPI dependencies
  source_connectors/      one read path per source technology
  validation/             runtime validation
  workers/                integration outbox, interception resume
  shared/                 cross-cutting helpers
```

There is no top-level `ai_gateway/` package; this list claimed one. The gateway
is `ai/gateway/`, inside the package that owns the single dispatch boundary,
which is the point. `policy/` and `housekeeping/` were missing entirely.

Frontend:

```text
frontend/src/
  api/         typed HTTP clients, one per canonical domain
  domains/     the nine domain screens and the shell
  mocks/       MSW handlers for `npm run dev:mock`
```

`bootstrap/adapters/` is the only package allowed to import two modules at once, to
bind a provider's contract to a consumer's port.
`tests/platform/test_no_module_cross_imports.py` enforces that statically.

## The nine domains

| Domain | Route | Capability |
|---|---|---|
| Return Business Copilot | `/returns` | `returns.session.read` |
| Returns Support | `/support` | `returns.session.read` |
| Configuration | `/config` | `config.runtime.read` |
| Approvals | `/approvals` | `governance.proposal.read` |
| Data Sources | `/data-sources` | `config.source.read` |
| Graph Schema Analyzer | `/graph-schema` | `graph_schema.draft.read` |
| AI Control Center | `/ai` | `ai.request.read` |
| Source Sync | `/sync` | `config.source.read` |
| Operations | `/operations` | `config.runtime.read` |

Plus the landing page at `/`. Anything unrecognised redirects to `/returns`.

Nine, not four. This README described "four canonical domains" long after Approvals,
Data Sources, Support, Sync and Operations were registered in
`frontend/src/domains/registry.ts` — and named a target package tree containing
`business` and a top-level `graph`, neither of which has ever existed.

**What was deleted has no replacement.** Wave F4 removed the `/v1` console
(76 routes), the `/v2/copilot` workspace and the `/v2/config` datasource app. The Data
Console — data browser, graph explorer, inventory, workspaces, scenarios, jobs,
imports and exports, AI studio, graph sync UI — and the system tooling had no
canonical equivalent and are **absent rather than superseded**. A deliberate decision,
not an oversight.

The shell reads only the canonical versionless surface. `/api/runtime-config` is the
last exception it cannot boot without, and it lives in `bootstrap/` where the target
design places it. `frontend/src/api/noVersionedPaths.test.ts` asserts the absence,
because describing a leftover in a README is exactly what let it survive three
deletion waves.

## Order Discovery

1. The associate supplies **whatever this deployment configures** — order number,
   customer id, tracking number, SKU, phone, email, customer name, product
   description, colour, ZIP, or anything an operator adds.
2. Strong identifiers match exactly first. Which fields are "strong" is
   configuration.
3. Misspelled names resolve through the Neo4j full-text index
   `customer_name_search_v2`, which searches the **complete** customer set
   server-side.
4. Ambiguous candidates go through a durable multi-turn conversation.
5. **The server chooses the disambiguation slot.** AI may phrase the approved
   question; it cannot select a customer, order or line.
6. Explicit confirmation commits the case and starts its workflow.
7. AI failure returns a deterministic response and does not break the flow.

**Adding a tenth identification field requires zero Python, zero TypeScript and zero
prompt edits.** The catalogue is `discovery.identification_fields`, a runtime
configuration release. Colour and ZIP are ordinary configured fields. Seven separate
sites used to hardcode the list, in three languages.

> Candidate limits may bound returned results. **They must never bound the searchable
> corpus.** An earlier implementation scored a bounded batch with `difflib` on the
> stated assumption that Neo4j had no server-side approximate match and APOC was not
> installed; both halves were false, and at production scale the correct customer
> could silently fall outside the window.

Details: [`docs/architecture/order-discovery.md`](docs/architecture/order-discovery.md),
[`docs/architecture/identification-fields.md`](docs/architecture/identification-fields.md),
[`docs/optimization/order-discovery-search.md`](docs/optimization/order-discovery-search.md).

### Endpoints

| Method | Endpoint |
|---|---|
| `GET`/`POST` | `/api/v1/associate-returns/conversations` |
| `POST` | `/api/v1/associate-returns/chat` |
| `GET` | `/api/v1/associate-returns/conversations/{id}` |
| `POST` | `/api/v1/associate-returns/conversations/{id}/chat` |
| `POST` | `/api/v1/associate-returns/conversations/{id}/messages` |
| `POST` | `/api/v1/associate-returns/conversations/{id}/confirm` |
| `POST` | `/api/v1/associate-returns/conversations/{id}/details` |
| `POST` | `/api/v2/order-agent/conversations` |
| `POST` | `/api/v2/order-agent/conversations/{id}/turns` |
| `GET` | `/api/v2/order-agent/conversations/{id}/transcript` |

`/api/v2/order-agent` is the only surviving `/api/v2` prefix; it is unrelated to the
deleted V2 shell and merely shared it.

```bash
curl -fsS -H 'Content-Type: application/json' -H 'X-Correlation-ID: readme-001' \
  -d '{"message":"Find the damaged faucet order for customer ZIP 30301"}' \
  http://127.0.0.1:8000/api/v1/associate-returns/chat | jq
```

## Runtime configuration

**The database is the source of truth.** Packaged YAML under `backend/config/` is
bootstrap/default input only and is **never rewritten at runtime**.

Neo4j is the authoritative control-plane store. Runtime processes compare the graph
head revision against their last-good immutable snapshot; they do not traverse the
configuration graph per request.

```text
DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED
```

Published releases are immutable and checksum-verified. Publication requires the
expected head revision, so two administrators cannot activate two releases from the
same starting point. A checksum mismatch **refuses** startup or activation.

Three editable domains: `RETURN_PLATFORM`, `AI_GATEWAY`, `DEPENDENCY_SIMULATION`.
Production and staging **fail closed** without the last two.

### Hot adoption — and how to verify it

**Every long-running API and worker process reconciles.** When the active release
changes, each validates it, builds a complete immutable snapshot, atomically swaps,
and reports its adopted release and head revision.

That was not always true, and this README asserted it anyway. Workers were
**startup-bound**: publishing a release changed API behaviour and left every worker on
the old one indefinitely, with no error and no way to detect it.

So do not trust the claim — check it:

```bash
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq
```

`LIVE` only when every required process class has a live instance and **all** of them
report the activated release id **and** head revision. Otherwise `ACTIVATING`, naming
the classes that have not adopted. **`ACTIVATED` is not `LIVE`.**

`/health/ready` does not report adoption. A process can be ready and serving the
previous release.

New work uses the new release; **existing cases continue on their pinned release**. A
workflow reads its timings once at start, so an in-flight return does not have its
deadline moved underneath it. Infrastructure endpoint changes are restart-required and
fail closed.

Per-family classification, editability and rollback implications:
[`docs/configuration/families.md`](docs/configuration/families.md).

### Secrets

**Credentials come from the process environment.** Every DSN, password and API key is
read from `.env` (or whatever the deployment injects) straight into `Settings`. There
is no resolver between the two, no secret store to start, and nothing to unseal.

Graph configuration holds no credential values. A `credential` block on a data source
names a `profile_key` — an identity AI route bindings address — and nothing else.

Secrets are still never serialized by application APIs, never logged, and never
returned to a browser: `SecretStr` at the boundary, redaction before every provider
call, and no request or response model with a field a credential could travel in.

**Vault is optional and off.** To put it back in front of these values, set
`PLATFORM_VAULT_ENABLED=true` and give each credential a `*_SECRET_REFERENCE`
holding a `vault://secret/production/<path>#<key>` URI; `secrets/` resolves them into
memory at startup, before any client is created. Setting a reference *without*
enabling Vault does nothing — the plain value beside it stays in force.

## AI

**One dispatch boundary.** All AI requests — completion, structured reasoning, Graph
Analyzer, Feedback, replay, simulation — terminate at one `FinalDispatcher`:

```text
interception decision → precondition → route selection → acquire
  → recursive redaction → ONE provider.generate call → output safety
  → caller validation → failover bookkeeping → priced telemetry
```

Exactly one verdict per request, before a route is even selected:
`ALLOW_PROVIDER | HUMAN_RESPONSE | REJECT`. **Redaction runs before the verdict.**

Three loops used to call `AIProvider.generate`. They shared the route pool and the
safety functions, so the platform could describe itself as having one path — but a
control attached to one loop was absent from the other two. Interception did not exist
on the path the Order Agent and Graph Analyzer use; the simulator's loop never got
recursive redaction; and one loop priced an unpriced model at `0`.

**No business agent holds a provider object or raw HTTP client.** A human answer is
reported as `MANUAL`, never as the replaced provider. Cost for an unpriced model is
`UNKNOWN`, never zero.

A key/model/task route is usable only after live validation produced a receipt bound
to provider, model, task, secret fingerprint and configuration checksum. Publication
is refused while an active route lacks one.

Details: [`docs/architecture/ai-dispatch.md`](docs/architecture/ai-dispatch.md),
[`docs/optimization/model-routing.md`](docs/optimization/model-routing.md).

### Which models are listed, and why

The route lists in `.env` are ordered newest-first, but a model earns its place by
measurement against **this** task — a ~19,400-token reasoning prompt that must come
back as schema-valid JSON. Measured 2026-08-16 against prompt release
`order-agent-prompt-v15`, over five discovery scenarios:

| Model | Scenarios | Median latency | Notes |
| --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 4/5 | 3.0s | Best measured, and it is in the *lightweight* tier |
| `gemini-3.7-flash` | 3/5 | 15.4s | **Not listed** — 503 on 15 of 21 attempts |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | — | — | First standard rung; NVIDIA has the headroom Google lacks |

Two findings worth carrying forward.

**Availability is a listing criterion, not just quality.** `gemini-3.7-flash` is the
newest model and is deliberately absent. It returned HTTP 503 "high demand" on 15 of
21 non-429 attempts and spent its entire 20/day allowance in about fifteen minutes,
because the 503s consume quota too. Demoting it would not have contained the damage:
a 503 raises `PROVIDER_UNAVAILABLE`, and route selection opens the circuit keyed on
the **provider**, not the model, so at `failureThreshold: 3` an unreliable rung
anywhere in the Google list suspends every other Google model with it. Re-measure
before restoring it.

**Most "model failures" here were ours.** Under v14, `gemini-3.5-flash-lite` failed
the ambiguous-candidate scenario 0/2; under v15 it passes 3/3, with a clean
clarification naming all five candidates at the configured cap. The only change was
v15 disclosing the response contract the platform had always enforced silently. Suspect
the prompt before the model.

One open defect: `ObservedFact.value` is typed `Any`, which reaches Gemini as an
unconstrained `"value": {}`. Every Gemini model tested degenerates at exactly that
position, emitting a repeating string until the output cap truncates the JSON.

## Graph generations

Generations are **load-bearing**, not a label on one mutable graph.

```text
allocate N+1 → sync into it → catch up → validate
  → compare-and-swap the ActiveRuntimeSnapshot → drain readers on N → retire N
```

**The active generation is never dropped before its replacement validates.** A failed
candidate is marked `FAILED`, the swap never runs, and N keeps serving — so a failed
sync degrades freshness, never availability.

The fencing token is a **durable monotonic counter**. It used to be constant, which
fenced nothing: neither the Neo4j marker's exact-match check nor the checkpoint
store's `$lte` refusal could tell an owner from a stale writer when every writer
presented `1`.

Schema changes are classified before activation, with reasons:

| Class | Strategy |
|---|---|
| `ADDITIVE` | `BACKFILL` the affected sources |
| `COMPATIBLE` | `AFFECTED_SCOPE_RESYNC` of those sources |
| `DESTRUCTIVE` | `FULL_REBUILD` via a generation cutover |

Read `GET /api/schema-releases/{id}/migration-plan` before activating. Activation used
to be a pointer flip in the dark.

Details: [`docs/architecture/graph-generations.md`](docs/architecture/graph-generations.md).

## Running it

Full detail in [`docs/operations/startup.md`](docs/operations/startup.md).

### From nothing to a working platform

Two commands. The second is the whole kit — it resets infrastructure,
loads the reference dataset, starts every host process, **builds the knowledge
graph**, and then verifies the result rather than assuming it.

```bash
./scripts/bootstrap_host.sh          # first time only: toolchain, .env, deps
./scripts/linux/reset_all.sh         # everything else, in the one order that works
```

The graph build is the step that is easy to miss and impossible to notice missing.
Loading the source collections leaves Neo4j empty, so the copilot searches a graph
with no nodes and truthfully reports finding nothing — which reads as a broken agent
rather than a missing build.

For an incremental start against infrastructure that is already up:

```bash
./scripts/infra.sh start             # datastores only; no image build
./scripts/run_all_host.sh            # API, workers, frontend (blocks; Ctrl-C stops all)
```

`run_all_host.sh` **supervises**: it does not return, and its exit trap stops
everything it started. Pass `--no-supervise` to start the processes and get the
shell back.

Then verify **both** of these — they answer different questions:

```bash
curl -fsS http://127.0.0.1:8000/health/ready        | jq
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq
```

### The fast edit loop

Infrastructure in Docker, backend/frontend/workers on the **host**, no image build in
the loop. This is the default way to work on the code, and `.claude/launch.json`
declares each process:

| Process | Command | Port |
|---|---|---|
| backend | `backend/.venv/bin/python -m uvicorn return_platform.main:create_app --factory` | 8000 |
| order-discovery-worker | `backend/scripts/run_order_discovery_worker.py` | — |
| frontend | `npm --prefix frontend run dev -- --port 5273` | **5273** |
| frontend-mock | `npm --prefix frontend run dev:mock -- --port 5174` | 5174 |

**The frontend port differs between the two loops.** `scripts/run_frontend_host.sh`
runs plain `npm run dev`, which Vite serves on **5173** — that is the port
`10_start_frontend.sh`, `11_validate_host_processes.sh` and `stop_application_ports.sh`
all check. `.claude/launch.json` asks for **5273**. Neither is wrong; they are
different loops, and a health check against the wrong one reports a frontend that is
down while it is serving.

A configuration or prompt change **no longer needs a rebuild**. The release write
routes are mounted, so the loop is draft → patch → promote:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/config/releases \
  -H 'Content-Type: application/json' -d '{"release_id":"local-001"}'
curl -fsS -X PATCH http://127.0.0.1:8000/api/config/releases/local-001/domains/RETURN_PLATFORM \
  -H 'Content-Type: application/json' -d '{"patch":{...}}'
# expected_head_revision comes from GET /api/config/adoption
curl -fsS -X POST http://127.0.0.1:8000/api/config/releases/local-001/promote \
  -H 'Content-Type: application/json' \
  -d '{"status":"RELEASED","expected_head_revision":<n>}'
```

Promotion requires `expected_head_revision` precisely so two administrators cannot
activate two releases from the same starting point.

### Published ports

Host processes must dial the **published** port, not the container's.

| Service | In-container | Published on the host |
|---|---|---|
| Backend API | 8000 | 8000 (`BACKEND_PORT`) |
| Frontend (host dev) | — | 5173, or 5273 under `.claude/launch.json` |
| Frontend (containerized) | 8080 | 3200 |
| MongoDB | 27017 | 27017 |
| SQL Server | 1433 | **14330** |
| Neo4j Bolt | 7687 | `NEO4J_BOLT_PORT`, **17687** on this dev host |
| Neo4j HTTP | 7474 | `NEO4J_HTTP_PORT` |
| Temporal | 7233 | 7233 |
| Temporal UI | 8080 | 8080 (`--profile dev-tools`) |
| Valkey | 6379 | 6379 |

Neo4j is the one that bites: its host port is overridable because WinNAT can reserve
7687, so a host process left pointing at 7687 reaches no listener and Order Discovery
simply finds nothing. `PLATFORM_NEO4J_URI` must carry the same port as
`NEO4J_BOLT_PORT`; `scripts/linux/validate_env.py` now refuses a mismatch.

Individual workers, containerized mode, redeploy and the reference dataset:
[`docs/operations/startup.md`](docs/operations/startup.md),
[`docs/operations/reset.md`](docs/operations/reset.md).

Requirements: Python 3.13, Node 24, npm 11, Docker with Compose, `flock`, one of
`ss`/`fuser`/`lsof`, and enough RAM for SQL Server, Neo4j, MongoDB, Temporal,
PostgreSQL and Valkey at once. `scripts/linux/00_validate_prerequisites.sh`
checks all of it.

Never commit `.env`, generated tokens, or credentials. `.env` **is** the credential
store now, so treat it as one: `chmod 600`, and never paste it anywhere.

## When something is wrong

[`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) is the
long form. These three cost real hours and each looks like something else.

### `docker compose up -d` does not recreate on a new image

`up -d` compares the **service definition**, not the image id. Rebuild under the same
`:local` tag and the running container is considered current — the build succeeds and
the old code keeps serving. `--force-recreate` is required.
`scripts/infra.sh full-containerized` now passes it.

### The frontend caches the backend's address

The frontend image's nginx resolves `backend` once, at startup, and holds the address
for the life of the process. Recreate the backend and every `/api/*` call 502s until
the frontend is restarted too. **Container order matters: frontend restarts last.**

### A graph that builds successfully and holds almost nothing

Two independent causes, same symptom — a copilot that finds nothing.

**Truncation.** There are two ceilings and the lower one wins:
`GraphSyncRequest.maxRecordsPerAsset` defaults to 1,000, and the effective limit is
`min(maxRecordsPerAsset, PLATFORM_GRAPH_SYNC_MAX_RECORDS)`, which defaults to 10,000.
Raising only the argument still clamps at 10,000. That ceiling is exactly the
`customers` count in `backend/config/seed/e2e_seed_manifest.json` — no headroom at
all, so a synthetic-seeded graph silently truncates the customer set the copilot
searches. Raise both:

```bash
PLATFORM_GRAPH_SYNC_MAX_RECORDS=30000 \
  python backend/scripts/build_knowledge_graph.py 30000
```

`reset_all.sh` does this for you and takes `--graph-records N`.

**Total silent data loss.** The source scan bounds on `{cursor: {"$lte": <Date>}}`,
and MongoDB compares only *within* BSON type brackets — so a timestamp written as a
**string** matches no date bound at all. Zero records scanned, the run reports
`COMPLETED`, and a graph holding nothing gets activated.
`build_knowledge_graph.py` refuses to report success when a `COMPLETED` run wrote no
nodes or relationships, which is the only thing standing between this and a
convincingly empty platform.

## Quality gates

Backend:

```bash
cd backend
poetry run ruff check src tests scripts
poetry run ruff format --check src tests scripts
poetry run mypy src tests          # `strict = true` is in pyproject.toml
poetry run pytest -q
```

Without Poetry, substitute `.venv/bin/python -m …` (`.venv/Scripts/python.exe` on
Windows). `scripts/bootstrap_host.sh` installs Poetry into `.tmp/poetry` rather than
onto `PATH`, so `command -v poetry` failing is normal and every phase that needs it
falls back.

Frontend:

```bash
cd frontend
npm ci && npm run lint && npm run typecheck && npm run test && npm run build
npm run contracts:check
```

Contracts:

```bash
python scripts/check_openapi_drift.py            # verify
python scripts/check_openapi_drift.py --write    # regenerate the five artifacts
```

**Wired into pytest**, so a contract change that is not regenerated fails the suite
rather than shipping silently.

Full validation — the numbered pipeline in `scripts/linux/`, phase 00 through the
manual screen attestation:

```bash
./scripts/linux/run_full_linux_validation.sh --from-start [--keep-running]
./scripts/linux/run_full_linux_validation.sh --resume        # skip passed phases
```

The phase list is `scripts/linux/validation_phases.txt`, and **two phases are
currently commented out of it**. Wave F4 deleted `frontend/tests/a11y.spec.ts` and
`frontend/playwright.real.config.ts` along with the npm scripts that ran them, and
nothing replaced either; left enabled they made the whole pipeline unrunnable.
`14_run_accessibility.sh` and `14_run_real_e2e.sh` each state exactly what to write
to restore their gate, and the pipeline's summary reports
`accessibility_status=SKIPPED_NO_SUITE` rather than claiming a pass.

**This pipeline is a release gate, not the way to bring the platform up.** For that,
see [Running it](#running-it).

Static checks (these do not replace dependency-backed tests):

```bash
python3 -m compileall -q backend/src backend/tests scripts
node scripts/validate_frontend_syntax.mjs
find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
git diff --check
```

Running the suite from a worktree has three non-obvious requirements, and getting the
first one wrong silently tests the wrong tree — see
[`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md).

## Required failure behaviour

| Failure | Required result |
|---|---|
| All AI providers unavailable | Deterministic task response; main flow continues |
| One AI key rejected | Open that key's circuit; try another validated key |
| Model removed or inaccessible | Next validated model/provider route |
| Neo4j discovery unavailable | Approved source fallback where policy and evidence permit |
| Weak fuzzy result | Never triggers graph synchronization unless explicitly enabled |
| Configuration checksum mismatch | Refuse startup or activation |
| Stale configuration head revision | Configuration revision conflict |
| Stale candidate card | Reject on candidate-set id, expiry, and conversation version |
| Duplicate message | Return the prior idempotent result |
| Case workflow cannot start | **Fail the confirmation.** The recovery sweep repairs the window |
| Shipment graph projection fails | 502 naming the committed row; resubmission answers `DUPLICATE` |
| RMA graph sync fails | Park the case |

## Security rules

- Strong identifiers are deterministic and exact-first.
- Fuzzy indexes contain approved natural-language fields only.
- Phone and email evidence uses normalized, domain-separated HMAC-SHA256 values in
  Neo4j. Rotating the key requires a full customer reprojection before contact lookup
  is re-enabled, because existing evidence cannot be recomputed in place.
- AI receives redacted, bounded facts only, and redaction is recursive.
- AI cannot choose candidates, change workflow state, generate database queries, or
  bypass confirmation.
- Source MongoDB and source SQL Server objects remain read-only.
- User-configured endpoints are allowlisted; metadata IPs are blocked.
- Published graph releases are immutable and checksum-verified.
- All administrative actions are audited.
- The frontend never receives a secret value.

## Delivery classification

```text
SOURCE_VALIDATED → CONTRACT_TESTED → PRODUCTION_READY → PRODUCTION_VALIDATED
```

`PRODUCTION_VALIDATED` requires execution against the intended production environment
and cannot be inferred from local checks.

## Contributing

`AGENTS.md` governs automated agents. `docs/implementation/` and
`docs/execution-context/` hold the multi-agent execution process.

`docs/archive/` holds superseded plans and status documents. Nothing in it describes
how the platform works today; [`docs/archive/README.md`](docs/archive/README.md) names
what superseded each group.
