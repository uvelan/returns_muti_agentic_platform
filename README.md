# Return Multi-Agent Platform

Production return orchestration: an associate-facing Order Discovery copilot,
runtime-configured behaviour held in a graph, Vault-managed credentials, durable
workflow execution, and operational evidence tooling.

**Current as of 2026-08-14, commit `dcbb7dc`, branch
`refactor/unified-return-platform`.**

## Start here

| Question | Document |
|---|---|
| **How does a return actually run, end to end?** | [`docs/architecture/canonical-runtime-flow.md`](docs/architecture/canonical-runtime-flow.md) |
| How do I bring it up? | [`docs/operations/startup.md`](docs/operations/startup.md) |
| Something is wrong | [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) |
| What does each screen do? | [`docs/screens/`](docs/screens/README.md) |
| What does each endpoint guarantee? | [`docs/api/README.md`](docs/api/README.md) |
| What can I configure, and when does it take effect? | [`docs/configuration/families.md`](docs/configuration/families.md) |
| Where are the security boundaries? | [`docs/architecture/security-boundaries.md`](docs/architecture/security-boundaries.md) |
| Everything | [`docs/README.md`](docs/README.md) |

This README is the map. The depth is in `docs/`, and where the two disagree, `docs/`
is newer.

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
Platform   Neo4j     Temporal    Valkey   Vault
MongoDB    config +  workflows/  streams  credentials
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
| Vault | Database credentials, AI keys, tokens, certificates, validation fingerprint material | Path-scoped read/write |

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
  ai/                     provider routing, redaction, interception, ONE dispatch boundary
  ai_gateway/             gateway surface
  api/                    HTTP surface, canonical and legacy
  canonical/              shared canonical models
  conversation/           reusable conversation contracts and state engine
  operations/             return business flow, SQL business state, connection pool,
                          warehouse placement
  workflows/              Temporal workflows, activities, workers, recovery
  dynamic_knowledge/      order agent, graph generations, lifecycle, integration
  data_platform/          graph sync, schema, source integration
  data_governance/        inventory and sampling
  dependency_simulation/  dev/test simulators (forbidden in production)
  graph_schema_analyzer/  host-composable schema proposal and approval
  secrets/                Vault resolution
  security/               capabilities, roles, FastAPI dependencies
  source_connectors/      one read path per source technology
  validation/             runtime validation
  workers/                integration outbox, interception resume
  shared/                 cross-cutting helpers
```

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
| `POST` | `/api/v2/order-agent/conversations/{id}/turns` |

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

Vault KV v2 is the exclusive runtime source for credential values. Neo4j stores only
versioned references:

```text
vault://secret/production/data-sources/sqlserver#password?version=3
vault://secret/production/ai/google/key-01#api_key?version=2
```

Compare-and-swap writes; sibling fields preserved on merge; staged writes rolled back
without exposing the secret if receipt persistence fails. Credentials are fetched when
creating or refreshing clients, not per business query.

The local Vault bootstrap stores separate MongoDB references for host and container
execution — host processes resolve loopback DSNs, containers resolve service DNS
names. Entries marked `bootstrap_managed` cannot override these.

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
to provider, model, task, secret fingerprint, Vault version and configuration
checksum. Publication is refused while an active route lacks one. Raw keys are
accepted only by the backend validation control plane.

Details: [`docs/architecture/ai-dispatch.md`](docs/architecture/ai-dispatch.md),
[`docs/optimization/model-routing.md`](docs/optimization/model-routing.md).

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

Full detail in [`docs/operations/startup.md`](docs/operations/startup.md). The short
version:

```bash
./scripts/bootstrap_host.sh          # first time only
./scripts/infra.sh start             # datastores only; no image build
./scripts/run_all_host.sh            # API, workers, frontend
```

Then verify **both** of these — they answer different questions:

```bash
curl -fsS http://127.0.0.1:8000/health/ready        | jq
curl -fsS http://127.0.0.1:8000/api/config/adoption | jq
```

Reset to a clean, fully-built environment:

```bash
./scripts/linux/reset_all.sh
```

It re-seeds Vault (nothing else does, and step two destroys its volume) and **builds
the knowledge graph** — loading the source collections leaves Neo4j empty, so the
copilot searches a graph with no nodes and truthfully reports finding nothing, which
reads as a broken agent rather than a missing build.

Individual workers, containerized mode, redeploy and the reference dataset:
[`docs/operations/startup.md`](docs/operations/startup.md),
[`docs/operations/reset.md`](docs/operations/reset.md).

Requirements: Python 3.13, Node 24, npm 11, Docker with Compose, `flock`, and enough
RAM for SQL Server, Neo4j, MongoDB, Temporal, PostgreSQL, Valkey and Vault at once.

Never commit `.env`, `.vault-local/`, generated tokens, unseal material, or
credentials.

## Quality gates

Backend:

```bash
cd backend
poetry run ruff check src tests scripts
poetry run mypy --strict src
poetry run pytest -q
```

Without Poetry, substitute `.venv/bin/python -m …`.

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

Full validation:

```bash
./scripts/linux/run_full_linux_validation.sh
```

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
| Vault unavailable **with** initialized pools | Continue bounded use of established clients |
| Vault unavailable **before** client creation | Fail that dependency initialization. **Never** fall back to `.env` |
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
- Vault writes use compare-and-swap versioning.
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
