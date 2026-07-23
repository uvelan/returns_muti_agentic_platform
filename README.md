# Return Platform — Data Console and Customer Graph Foundation

The Return Platform is a backend-first Sales Order Return platform with a separate operational Data Console.

The **customer return process** is the primary end-to-end product experience. The **Data Console** is a developer and operator control plane used to inspect supporting data, validate infrastructure, observe graph synchronization, review immutable evidence, and diagnose failures. It is not the primary customer demo.

The repository currently includes:

- FastAPI and React/Vite application foundations.
- Live dependency health visibility.
- Immutable data-governance and asset-catalog contracts.
- SQL Server and MongoDB metadata inventory.
- Declared-versus-observed drift analysis.
- Bounded sampling contracts.
- The complete canonical domain-model layer.
- Versioned physical-to-canonical mapping contracts.
- The first Customer and CustomerAccount mapping profile.
- A code-owned mapping-handler registry.
- A bounded multi-file mapping loader.
- An immutable Customer-profile compiler.
- Deterministic in-memory Customer normalization.
- A read-only exact-ID MongoDB Customer source adapter.
- Deterministic Customer graph-projection materialization.
- Fixed parameterized Customer Neo4j commands.
- An explicit no-hidden-retry Neo4j writer.
- Fixed Customer graph read-back contracts.
- A sandbox-only Customer graph validation and idempotency runner.
- Immutable Customer graph evidence persisted in Platform MongoDB.
- Read-only Graph Validation and Graph Inspection APIs.
- A contract-tested Data Console Customer Graph Evidence frontend with live Docker proxy validation.
- A contract-tested deterministic Temporal Return workflow execution core.
- A provider-neutral eligibility gateway boundary with deterministic fail-safe review.

No production source asset, production Customer lookup, production graph write, or production deployment is claimed as validated unless corresponding evidence is explicitly recorded below.

---

## 1. Current Status

| Area | Status | Notes |
|---|---|---|
| Stage 1 — Frontend/API foundation | **COMPLETE** | FastAPI application shell, React/Vite shell, typed API client, routing, lifecycle resources, and baseline quality gates |
| Stage 2 — Infrastructure visibility | **BASIC ACCEPTANCE PASSED** | Five dependency probes, concurrent aggregation, partial responses, correlation IDs, safe errors, and live healthy/degraded evidence |
| Stage 3 — Governance and catalog | **COMPLETE** | Immutable governance contracts, strict YAML catalog loading, startup registration, and intentionally empty production catalog |
| Stage 3 — SQL Server inventory | **SANDBOX_VALIDATED** | Metadata-only live inventory completed against the configured sandbox database |
| Stage 3 — MongoDB inventory | **SANDBOX_VALIDATED** | Metadata-only live inventory completed against the configured sandbox database |
| Stage 3 — Drift | **IMPLEMENTED; LIVE OUTPUT OBSERVED** | Empty declared and observed state produced zero confirmed drift; the original run did not capture a process exit code |
| Stage 3 — Bounded sampling | **CONTRACT_TESTED** | Live sampling remains deliberately deferred |
| Stage 3 — Inventory API/UI | **CONTRACT_TESTED; LIVE PARTIAL VALIDATED** | Unified SQL Server, MongoDB, and Neo4j metadata API plus Inventory page; MongoDB, Neo4j, and frontend proxy passed live while SQL Server returned a safe timeout warning |
| Stage 3A — Data Console Plan | **COMPLETE** | Explicit API gap register, wildcard-free route capability matrix, exact mock strategy, openapi-typescript code generation, and package selection |
| Stage 3B — Data Console Foundation | **COMPLETE** | Shared components, shell/navigation, Wouter routing, typed API client with openapi-fetch, QueryKey factory, MSW fixture mode bounded to development |
| Canonical domain model | **COMPLETE** | Customer, order, product, warehouse, shipment, return, bay, session, audit, decision, and graph-evidence contracts |
| Mapping configuration language | **COMPLETE** | Source, canonical, graph, relationship-direction, physical-scope, and pipeline contracts |
| Customer mapping profile | **COMPLETE** | Customer and CustomerAccount profile across four versioned YAML files |
| Mapping handler registry | **COMPLETE** | Code-owned field and identity handlers with purpose, arity, output, version, and determinism metadata |
| Multi-file mapping loader | **COMPLETE** | Bounded UTF-8 YAML loading, duplicate-key and alias rejection, schema agreement, and digest evidence |
| Customer mapping compiler | **COMPLETE** | Governance, canonical-model, handler, graph, and pipeline validation into an immutable execution plan |
| Customer in-memory normalization | **COMPLETE** | Deterministic document and nested-account normalization with safe record-rejection evidence |
| Customer MongoDB source adapter | **COMPLETE** | Exact governed `_id` lookup contract; production live lookup remains blocked |
| Customer graph materialization | **COMPLETE** | Immutable Customer, CustomerAccount, and `HAS_ACCOUNT` parameter materialization |
| Customer Neo4j command builder | **COMPLETE** | Fixed uniqueness constraints and parameterized node/relationship commands |
| Customer Neo4j writer | **CONTRACT_TESTED** | Focused and complete repository gates passed after strict typing and Neo4j Driver 6.2 corrections |
| Customer graph sandbox runner | **SANDBOX_VALIDATED** | Controlled source fixture normalized, written, read back, and replayed successfully |
| Live Neo4j Customer graph write | **SANDBOX_VALIDATED** | Constraints, nodes, relationships, bookmarks, and returned-key validation completed |
| Customer graph read-back | **SANDBOX_VALIDATED** | Customer, CustomerAccount, and `HAS_ACCOUNT` were read back through fixed parameterized queries |
| Second-run graph idempotency | **SANDBOX_VALIDATED** | Identical replay produced equivalent graph and evidence results |
| Platform MongoDB graph-evidence persistence | **SANDBOX_VALIDATED** | Immutable evidence document created and read back successfully |
| Graph Validation API | **SANDBOX_VALIDATED** | Live latest/list/exact lookup routes validated |
| Graph Inspection APIs | **SANDBOX_VALIDATED** | Document, sync-run, report-digest, and admin full-evidence routes validated |
| Data Console Customer Graph Evidence screens | **CONTRACT_TESTED; LIVE API PROXY VERIFIED** | Lint, strict TypeScript, 19 tests, production build, and six Docker proxy routes passed; screenshots are deferred to hardening |
| Live Customer source lookup | **BLOCKED_EXTERNAL_DEPENDENCY** | Production catalog intentionally has no approved Customer CDM source asset |
| Temporal return workflow | **LIVE SANDBOX VALIDATED** | Dedicated worker completed all seven ordered updates, query, replay, result, and MongoDB session/audit/outbox read-back |
| Intake and order-discovery contexts | **LIVE SANDBOX VALIDATED** | Strict stage results survived Temporal conversion and persisted digest-bound canonical snapshots in MongoDB |
| Eligibility and AI Gateway boundary | **CONTRACT_TESTED; LIVE PERSISTENCE VALIDATED** | Persisted-context-only input, one-attempt gateway port, deterministic `REVIEW_REQUIRED` fallback, and atomic decision evidence; live provider remains disabled |
| Deterministic RETURN_REQUEST context | **LIVE SANDBOX VALIDATED** | Eligibility-bound outcomes and digest consistency passed Temporal conversion and atomic MongoDB persistence; production return creation remains disabled |
| Deterministic FULFILLMENT_TRACKING context | **LIVE SANDBOX VALIDATED** | Return-request-bound tracking states and reference consistency passed Temporal conversion and atomic MongoDB persistence; production providers remain disabled |
| Deterministic BAY_ASSIGNMENT context | **LIVE SANDBOX VALIDATED** | Fulfillment-bound assignment states and warehouse/bay reference consistency passed Temporal conversion and atomic MongoDB persistence; warehouse mutation remains disabled |
| Deterministic FEEDBACK_LEARNING context | **LIVE SANDBOX VALIDATED** | Bay-bound feedback dispositions and complete learning-reference rules passed Temporal conversion and atomic MongoDB persistence; training and external sinks remain disabled |
| Scenario runner | **NOT IMPLEMENTED** | At least five positive and five negative end-to-end return scenarios remain pending |
| Customer-facing return UI | **NOT IMPLEMENTED** | Remains the primary eventual demo experience |

### Immediate truth boundary

The Customer graph backend path is validated through:

```text
controlled Customer fixture
→ mapping profile load
→ mapping compilation
→ in-memory normalization
→ graph materialization
→ fixed Neo4j command construction
→ uniqueness-constraint preparation
→ atomic Neo4j write
→ deterministic graph read-back
→ second-run idempotency proof
→ Platform MongoDB evidence persistence
→ read-only Graph Evidence APIs
→ six-route live API validation
```

The active bounded step is:

```text
Temporal Return workflow — end-to-end scenario matrix
```

The integrated frontend currently passes:

```text
ESLint
strict TypeScript
focused Vitest
complete frontend tests
Vite production build
live browser/backend integration
README evidence capture
```

Visual screenshot capture remains deferred to hardening.

### Stage 3A Evidence Reconstruction

To regenerate the Stage 3A OpenAPI and TypeScript contracts:

```bash
cd frontend
npm run contracts:generate
npm run contracts:check
```

The matrix files and API gaps are governed in `docs/evidence/data_console_complete_ui/stage3a/`.

---

## 2. Locked Architecture and Ownership

### Canonical workflow

```text
IntakeContext
  → DiscoveryContext
  → ReturnRequestContext
  → FulfillmentTrackingContext
  → BayStagingContext
  → LearningFeedbackContext
  → ReturnSessionContext
```

Canonical module flow:

```text
Order Discovery
  → Return Workflow
  → Return Fulfillment
  → Bay Assignment
  → Feedback Learning
```

### Data ownership

| System | Ownership and responsibility |
|---|---|
| Platform MongoDB | Authoritative internal platform state: sessions, audits, configurations, outbox, decisions, evidence, and later operator state |
| SQL Server / OMC | Authoritative business facts for returns, RMA, fulfillment, and tracking; read-only from the platform |
| Source MongoDB | Read-only discovery and Customer CDM source data; no workflow-owned fields |
| Neo4j | Derived and rebuildable graph projection only; never authoritative business state |
| Temporal | Durable execution, timers, retries, and workflow coordination; not business-state ownership |
| Valkey | Transient coordination, caching, rate limiting, and SSE support |
| Temporal PostgreSQL | Internal Temporal persistence; never accessed directly by the Return Platform |

### Configuration rule

Configuration defines values expected to vary:

- Approved source assets.
- Physical paths and aliases.
- Canonical target fields.
- Mapping and pipeline versions.
- Graph labels, relationship types, and property names within strict allow-lists.
- Environment-specific values.

Code defines stable safety and domain behavior:

- Identity algorithms.
- Handler implementations.
- Canonical validation.
- Alias-conflict policy.
- Retry ownership.
- Transaction behavior.
- Security boundaries.
- Cypher templates.
- Source and graph adapter behavior.
- Evidence validation.
- API query shapes and authorization.

Configuration must never contain arbitrary SQL, MongoDB filters, Cypher, Python, import paths, or executable handler arguments.

---

## 3. Current End-to-End Customer Data Path

```text
Versioned YAML mapping files
  │
  ▼
Bounded multi-file loader
  │
  ▼
Immutable mapping bundle
  │
  ├── approved AssetCatalog
  ├── code-owned handler registry
  └── code-owned canonical-model registry
  │
  ▼
Customer mapping compiler
  │
  ▼
MappingExecutionPlan
  │
  ├── exact-ID MongoDB source adapter
  │       └── immutable source document + SourceDocumentEvidence
  │
  └── deterministic in-memory normalizer
          ├── Customer
          ├── CustomerAccount[]
          └── safe per-record rejections
                  │
                  ▼
        graph projection materializer
          ├── Customer node parameters
          ├── CustomerAccount node parameters
          ├── HAS_ACCOUNT relationship parameters
          └── GraphProjectionEvidence
                  │
                  ▼
        fixed Neo4j command builder
          ├── uniqueness constraints
          ├── parameterized node MERGE commands
          └── parameterized relationship MERGE commands
                  │
                  ▼
        explicit no-hidden-retry Neo4j writer
          ├── schema transaction
          ├── bookmark propagation
          ├── atomic data transaction
          ├── returned-key verification
          └── immutable write evidence
                  │
                  ▼
        fixed graph read-back validator
          ├── Customer query
          ├── CustomerAccount query
          ├── HAS_ACCOUNT query
          ├── exact key comparison
          └── mandatory provenance comparison
                  │
                  ▼
        second-run idempotency validation
                  │
                  ▼
        immutable sandbox report
          ├── local JSON evidence
          └── Platform MongoDB evidence document
                  │
                  ▼
        read-only Graph Evidence APIs
          ├── newest-first bounded listing
          ├── latest validation
          ├── document lookup
          ├── admin full evidence
          ├── sync-run lookup
          └── report-digest lookup
                  │
                  ▼
        Data Console Graph Evidence screens
          └── current frontend integration step
```

---

## 4. Repository Structure

The following structure reflects the current backend and the active frontend step. Inspect the actual repository before assuming every prepared frontend file has already been integrated.

```text
.
├── .env
├── .env.example
├── .gitignore
├── docker-compose.yml
├── README.md
│
├── backend/
│   ├── config/
│   │   ├── data_assets.yaml
│   │   ├── live_validation/
│   │   │   └── data_assets.sampling.yaml
│   │   └── data_platform/
│   │       ├── sources.yaml
│   │       ├── canonical_mappings.yaml
│   │       ├── graph_projection.yaml
│   │       └── sync_pipelines.yaml
│   │
│   ├── docs/
│   │   └── evidence/
│   │       ├── customer_graph_sandbox_validation.json
│   │       └── graph_evidence_api/
│   │           ├── validation_summary.json
│   │           ├── list.json
│   │           ├── latest.json
│   │           ├── document_summary.json
│   │           ├── document_full.json
│   │           ├── sync_run.json
│   │           └── report_digest.json
│   │
│   ├── scripts/
│   │   └── validate_graph_evidence_api.sh
│   ├── pyproject.toml
│   ├── poetry.lock
│   ├── run_live_drift.py
│   ├── run_live_mongo_inventory.py
│   ├── run_live_sql_inventory.py
│   │
│   ├── src/
│   │   └── return_platform/
│   │       ├── __init__.py
│   │       ├── py.typed
│   │       ├── asgi.py
│   │       ├── main.py
│   │       ├── resources.py
│   │       ├── canonical/
│   │       ├── configuration/
│   │       │   └── settings.py
│   │       ├── data_console/
│   │       │   ├── api/
│   │       │   │   ├── router.py
│   │       │   │   └── graph_evidence.py
│   │       │   └── infrastructure/
│   │       │       └── probes.py
│   │       ├── data_governance/
│   │       ├── data_platform/
│   │       │   ├── mapping/
│   │       │   ├── sources/
│   │       │   └── graph/
│   │       │       ├── __init__.py
│   │       │       ├── commands.py
│   │       │       ├── writer.py
│   │       │       ├── readback.py
│   │       │       ├── sandbox.py
│   │       │       ├── sandbox_runner.py
│   │       │       ├── evidence_repository.py
│   │       │       └── evidence_query.py
│   │       ├── security/
│   │       └── shared/
│   │
│   └── tests/
│       ├── test_customer_neo4j_command_builder.py
│       ├── test_customer_neo4j_writer.py
│       ├── test_customer_graph_readback.py
│       ├── test_customer_graph_sandbox.py
│       ├── test_graph_evidence_settings.py
│       ├── test_graph_evidence_api.py
│       └── ...
│
└── frontend/
    ├── .nvmrc
    ├── package.json
    ├── package-lock.json
    ├── index.html
    ├── tsconfig.json
    ├── tsconfig.app.json
    ├── tsconfig.node.json
    ├── vite.config.ts
    ├── vitest.config.ts
    ├── eslint.config.js
    ├── tailwind.config.js
    ├── postcss.config.js
    └── src/
        ├── env.ts
        ├── main.tsx
        ├── App.tsx
        ├── index.css
        ├── api/
        │   ├── graphEvidence.ts
        │   ├── graphEvidenceQueries.ts
        │   └── graphEvidence.test.ts
        ├── contracts/
        │   └── graphEvidence.ts
        ├── components/
        │   └── Shell.tsx
        ├── test/
        └── features/
            └── data-console/
                ├── components/
                │   └── graph-evidence/
                │       ├── GraphEvidenceStatusCard.tsx
                │       ├── GraphEvidenceTable.tsx
                │       └── GraphEvidenceInspector.tsx
                └── pages/
                    ├── GraphEvidencePage.tsx
                    └── GraphEvidencePage.test.tsx
```

---

## 5. Backend Foundations

### Application construction

`return_platform.main.create_app()` is the application factory.

`return_platform.asgi` is the production ASGI entry point:

```python
from return_platform.main import create_app

app = create_app()
```

`main.py` must not expose a module-level `app`.

The FastAPI lifespan:

1. Validates settings.
2. Loads the immutable asset catalog.
3. Constructs `RuntimeResources`.
4. Initializes external clients.
5. Attaches resources to `app.state`.
6. Closes lifespan-owned resources in reverse order.

Functions decorated with `contextlib.asynccontextmanager` must return:

```python
AsyncGenerator[YieldType, None]
```

Deprecation warnings are treated as build failures.

### Runtime resources

`RuntimeResources` owns references to:

- `Settings`.
- Loaded governance catalog.
- PyMongo asynchronous client.
- Neo4j asynchronous driver.
- Valkey asynchronous client.
- Temporal client.
- Bounded one-worker SQL Server executor.

Source adapters, graph writers, evidence repositories, and API query repositories reuse injected lifespan-owned clients and never close them.

### Error and response contracts

Backend API responses use the shared strict envelope:

```json
{
  "data": {},
  "page": null,
  "meta": {
    "schema_version": "1.0",
    "request_id": "uuid",
    "generated_at": "UTC timestamp",
    "freshness": "LIVE",
    "partial": false,
    "warnings": []
  }
}
```

Raw credentials, driver messages, stack traces, source values, and infrastructure addresses must not be returned through public errors.

---

## 6. Data Governance and Catalog

### Production catalog

`backend/config/data_assets.yaml` is the version-controlled declaration of approved physical assets.

Current required state:

```yaml
version: "1.0"

# The production catalog is intentionally empty.
#
# Add an asset only after:
# 1. The physical object exists.
# 2. Ownership is approved.
# 3. Allowed operations are approved.
# 4. The live identity is verified.
assets: []
```

The production catalog must not be changed merely to unblock tests.

### Governance invariants

- Empty catalogs are valid.
- Duplicate asset IDs are rejected.
- Duplicate physical assets are rejected.
- Source-system assets are read-only.
- SQL Server objects require a namespace and must be tables or views.
- MongoDB objects must be collections and must not use SQL-style namespaces.
- Derived projections cannot be authoritative.
- Ownership must match the `asset_id` prefix.
- Sampling is bounded and requires explicit `READ` permission.

### Catalog loader protections

- `.yaml` and `.yml` extension allow-list.
- Resolved regular-file checks.
- File-size bound.
- UTF-8 and UTF-8 BOM handling.
- Safe YAML parsing.
- Duplicate-key rejection at every mapping depth.
- Empty-document rejection.
- Root-mapping enforcement.
- Strict governance validation.
- SHA-256, byte-size, path, and asset-count evidence.
- Startup failure for missing or invalid catalogs.

The catalog is loaded once per application lifespan. Request handlers use `RuntimeResources.catalog`.

---

## 7. Canonical Domain Model

All canonical models inherit from `CanonicalBaseModel`:

- Strict scalar validation.
- Frozen instances.
- Unknown-field rejection.
- Validated defaults.
- Hidden raw input values in validation errors.
- UTC timestamp normalization.

### Shared contracts

```text
IdentityQuality
CanonicalIdentifier
NonBlankText
VersionReference
Sha256Digest
UtcDateTime
CanonicalBaseModel
SourceProvenance
```

### Customer foundation

```text
Customer
CustomerAccount
ContactPoint
Address
```

Identity:

```text
Customer.customer_key =
"CUSTOMER_CDM:" + party_id

CustomerAccount.account_key =
"CUSTOMER_CDM:" + account_number
```

`ContactPoint` and `Address` are canonical value objects, not graph nodes in graph model v1.

### Order and Product

```text
SalesOrder
OrderLine
Product
```

Identity:

```text
SalesOrder.source_document_id =
account_id + "*" + order_id

SalesOrder.sales_order_key =
"TDS:" + account_id + ":" + order_id + ":" + order_instance_key

OrderLine.order_line_key =
sales_order_key + ":LINE:" + source_line_number

Product.product_key =
"STEP:" + master_product_id
```

Order-line identity remains conditional until line-number immutability is confirmed.

### Warehouse and Shipment

```text
Warehouse
WarehouseProduct
Shipment
ShipmentItem
TrackingEvent
CarrierTrackingReference
```

`bin_location` remains an inventory location and is never interpreted as a Return Bay.

Actual carrier tracking remains optional legacy evidence.

`Package` and `PPLTracking` remain excluded from graph v1.

### Return, Bay, session, and evidence

```text
Return
ReturnItem
FreightShipment
ReturnVersion
Bay
BayAssignment
AssignmentEvidence
ReturnSession
WorkflowStage
ContextSnapshot
ConfigurationVersionBinding
AgentDecision
AuditEvent
GraphSyncRun
GraphValidationResult
GraphSyncSafeError
GraphProjectionEvidence
GraphProjectionStatus
```

Platform-owned Bay identity remains separate from warehouse inventory location.

The canonical model layer performs no database I/O, graph I/O, workflow transitions, or cross-record uniqueness enforcement.

---

## 8. Versioned Mapping Configuration

### Configuration files

```text
backend/config/data_platform/sources.yaml
backend/config/data_platform/canonical_mappings.yaml
backend/config/data_platform/graph_projection.yaml
backend/config/data_platform/sync_pipelines.yaml
```

### Mapping contracts

```text
SourceAssetDefinition
PhysicalFieldMapping
IdentityMapping
CanonicalEntityMapping
GraphPropertyMapping
GraphNodeMapping
GraphRelationshipMapping
SyncStageDefinition
SyncPipelineDefinition
DataPlatformMappingBundle
CanonicalEntityType
SourceLifecycle
PhysicalPathScope
RelationshipDirection
```

### Relationship direction

The mapping language distinguishes:

- Reference-holder match direction.
- Emitted graph-edge direction.

For the Customer profile:

```text
CustomerAccount.customer_key holds the reference
Customer → CustomerAccount is the emitted edge
```

Resolved graph direction:

```text
(Customer)-[:HAS_ACCOUNT]->(CustomerAccount)
```

### Physical path scope

```text
RECORD   → resolve from the current nested selected record
DOCUMENT → resolve from the original source document
```

No implicit fallback exists between scopes.

### Handler registry

Handlers are code-owned and registered with:

```text
purpose
input arity
output type
contract version
deterministic flag
```

Customer handlers include separate roles:

```text
customer_key_v1
  purpose = IDENTITY

customer_reference_key_v1
  purpose = FIELD
```

The compiler rejects identity handlers used in ordinary field positions.

### Multi-file loader

The loader enforces:

- Fixed filenames and load order.
- Regular-file and symlink protections.
- 1 MiB per file.
- 4 MiB total.
- UTF-8 with BOM support.
- Duplicate YAML-key rejection.
- YAML alias rejection.
- Exact root keys.
- Exact schema-version agreement.
- Immutable bundle validation.
- Per-file SHA-256.
- Domain-separated multi-file digest.

### Customer compiler

The compiler resolves loaded configuration against:

```text
AssetCatalog
CanonicalModelRegistry
HandlerRegistry
```

It validates:

- Approved catalog asset existence.
- Source ownership, authority, store, object kind, and allowed operations.
- Canonical entity registration.
- Configured canonical fields.
- Required canonical field coverage.
- Handler existence, purpose, arity, output type, version, and determinism.
- Graph property sources.
- Relationship direction.
- Pipeline dependency order.

It performs no file, database, handler-execution, Cypher, or graph I/O.

The production catalog intentionally does not contain the Customer CDM source asset. Production compilation remains blocked until approval. Sandbox compilation uses an explicit sandbox-only governance catalog and never mutates the production catalog.

---

## 9. Customer In-Memory Normalization

`normalize_customer_source_document()` consumes:

```text
MappingExecutionPlan
SourceDocumentEvidence
one supplied source document
```

It implements:

- `DOCUMENT` and `RECORD` scope resolution.
- Deterministic nested `custAccts[]` iteration.
- Ordered aliases.
- Null-as-absent fallback.
- Type-strict alias-conflict detection.
- Required and optional field handling.
- Code-owned field and identity handler invocation.
- Runtime handler-output verification.
- Canonical Customer and CustomerAccount validation.
- Parent Customer dependency validation.
- Duplicate CustomerAccount identity rejection.
- Safe per-record rejection evidence.
- Bounded depth and structural-node limits.
- Cycle and malformed-structure rejection.
- Immutable output detached from caller mutation.

The normalizer performs no MongoDB or Neo4j I/O.

---

## 10. Customer MongoDB Source Adapter

`CustomerMongoSourceAdapter`:

- Reuses an injected `AsyncMongoClient`.
- Never creates or closes the client.
- Resolves database and collection only from `CompiledSourceAssetPlan`.
- Revalidates governance constraints.
- Executes exactly:

```python
find_one({"_id": source_document_id})
```

- Accepts no caller-defined filter, projection, sort, aggregation, JavaScript, regex, database name, or collection name.
- Applies server-side `max_time_ms` and an outer `asyncio.timeout`.
- Preserves caller cancellation.
- Performs no internal retry.
- Requires returned `_id` to exactly match the requested string ID.
- Calculates SHA-256 over exact BSON bytes.
- Returns a detached recursively immutable document.
- Rejects cycles, invalid keys, invalid BSON, and structural overflow.

Production live lookup remains blocked because the production governance catalog has no approved Customer CDM source asset.

---

## 11. Customer Graph Materialization

`materialize_customer_graph_projection()` consumes:

```text
MappingExecutionPlan
CustomerNormalizationResult
sync_run_id
graph_synced_at
```

It emits immutable parameter contracts for:

```text
Customer node
CustomerAccount nodes
Customer → CustomerAccount HAS_ACCOUNT relationships
```

Mandatory graph evidence:

```text
source_system
source_database
source_asset
source_record_id
source_updated_at
canonical_key
identity_quality
mapping_version
configuration_digest
sync_run_id
graph_synced_at
```

Missing mandatory `source_updated_at` fails closed:

```text
Customer:         REJECTED
CustomerAccount:  UNRESOLVED
Node parameters:  0
Relationship parameters: 0
```

`PROJECTED` at this layer means graph parameters were materialized successfully. It does not independently prove persistence.

---

## 12. Customer Neo4j Command Builder and Writer

### Command builder

Supported schema and data commands:

```text
Customer.customer_key uniqueness constraint
CustomerAccount.account_key uniqueness constraint
Customer node MERGE
CustomerAccount node MERGE
Customer-[:HAS_ACCOUNT]->CustomerAccount MERGE
```

The builder:

- Accepts only `CustomerGraphProjectionMaterialization`.
- Uses code-owned labels, relationship types, properties, and full Cypher templates.
- Rejects arbitrary Cypher.
- Binds all values through parameters.
- Preserves Customer-to-CustomerAccount direction.
- Orders constraints before nodes and nodes before relationships.
- Rejects duplicate node keys and relationship endpoints.
- Produces deterministic UUIDv5 command IDs.
- Produces a deterministic SHA-256 command-batch digest.
- Returns detached driver-parameter dictionaries.
- Performs no Neo4j I/O.

### Writer

`CustomerNeo4jWriter` reuses an injected lifespan-owned `AsyncDriver`.

#### Phase 1 — schema preparation

- Explicit database selection.
- Write-access routing.
- One explicit unmanaged transaction.
- Both fixed uniqueness constraints.
- Server transaction timeout.
- Outer operation timeout.
- No hidden retries.
- Causal bookmark capture after commit.
- Immutable schema evidence.

#### Phase 2 — data transaction

- Matching committed schema evidence required.
- Schema bookmarks passed into the data session.
- One explicit unmanaged transaction.
- Customer node first.
- CustomerAccount nodes second.
- `HAS_ACCOUNT` relationships last.
- Exactly one returned key verified for every node.
- Exactly one returned endpoint pair verified for every relationship.
- Known failures rolled back.
- Caller cancellation preserved.
- `IncompleteCommit` classified as `COMMIT_OUTCOME_UNKNOWN`.
- Immutable data-write evidence returned.

### No-hidden-retry rule

The writer does not use:

```text
AsyncDriver.execute_query
AsyncSession.execute_write
AsyncSession.execute_read
```

The writer uses:

```text
session.begin_transaction
transaction.run
transaction.commit
transaction.rollback
```

Session auto-commit retries are disabled defensively.

---

## 13. Customer Graph Read-Back and Sandbox Validation

The sandbox-only runner:

1. Loads the approved Customer mapping profile.
2. Compiles it against an explicit sandbox-only catalog.
3. Loads one controlled source fixture.
4. Normalizes it in memory.
5. Materializes graph parameters.
6. Builds fixed commands.
7. Prepares constraints.
8. Executes the atomic Customer graph transaction.
9. Reads back Customer, CustomerAccount, and `HAS_ACCOUNT`.
10. Compares exact canonical keys and mandatory provenance.
11. Runs the same input again.
12. Proves second-run idempotency.
13. Produces a validated immutable report.
14. Persists the report to Platform MongoDB.
15. Reads persisted evidence back.
16. Emits a safe JSON result and process exit code.

### Sandbox validation evidence

```json
{
  "evidence_output": "docs/evidence/customer_graph_sandbox_validation.json",
  "platform_evidence_document_digest": "6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b",
  "platform_evidence_document_id": "CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7",
  "platform_evidence_status": "CREATED",
  "process_exit_code": 0,
  "report_digest": "75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8",
  "status": "SANDBOX_VALIDATED"
}
```

Shell exit code:

```text
0
```

This evidence is sandbox evidence, not production validation.

---

## 14. Platform MongoDB Graph-Evidence Persistence

Graph evidence is stored in Platform MongoDB because it is authoritative internal platform evidence.

Persistence rules:

- One immutable aggregate per `sync_run_id`.
- Deterministic document ID:

```text
CUSTOMER_GRAPH_SANDBOX:<sync-run-uuid>
```

- `$setOnInsert` persistence.
- Exact replay is accepted.
- Conflicting replay fails closed.
- Majority write concern with journal acknowledgement.
- Majority read concern.
- Primary read preference.
- `retryReads=False`.
- `retryWrites=False`.
- No hidden transaction retry.
- Unknown write outcomes remain unknown and require deterministic reconciliation.

Indexes include:

```text
unique report_digest
unique sync_run_id
executed_at_epoch_microseconds descending
source_document_id + execution time
executed_at_epoch_microseconds + _id for seek pagination
```

---

## 15. Graph Validation and Inspection APIs

The Data Console backend exposes six read-only routes:

```text
GET /data-console/v1/graph-evidence
GET /data-console/v1/graph-evidence/validation/latest
GET /data-console/v1/graph-evidence/documents/{document_id}
GET /data-console/v1/graph-evidence/documents/{document_id}/full
GET /data-console/v1/graph-evidence/sync-runs/{sync_run_id}
GET /data-console/v1/graph-evidence/reports/{report_digest}
```

### Query behavior

Listing uses bounded seek pagination ordered by:

```text
executed_at_epoch_microseconds DESC
_id DESC
```

The API does not accept caller-provided:

```text
MongoDB filters
projections
sort definitions
collection names
aggregation pipelines
Cypher
```

### Authorization

```text
console_viewer:
  summary listing
  latest validation
  exact summary lookups

console_admin:
  all summary operations
  full embedded evidence inspection
```

No graph-evidence mutation route exists.

### Live six-route validation evidence

```json
{
  "evidence_output": "docs/evidence/graph_evidence_api/validation_summary.json",
  "process_exit_code": 0,
  "routes_validated": 6,
  "status": "SANDBOX_VALIDATED"
}
```

Shell exit code:

```text
0
```

The validator confirmed HTTP success, envelope integrity, request IDs, exact identity, cross-route digest consistency, idempotency evidence, admin full-access behavior, and absence of known secret/configuration field names.

---

## 16. Data Console Customer Graph Evidence Screens

The active frontend step is a read-only screen at:

```text
/data-console/graph-evidence
```

Implemented capabilities:

- Latest `SANDBOX_VALIDATED` Customer graph run.
- Execution timestamp.
- Expected Customer count.
- Expected CustomerAccount count.
- Expected `HAS_ACCOUNT` relationship count.
- Immutable sync-run and source-document identity.
- Manual refresh.
- Newest-first immutable evidence history.
- Bounded seek-pagination controls.
- Exact lookup by document ID.
- Exact lookup by sync-run ID.
- Exact lookup by report digest.
- Summary evidence inspection.
- Admin-only full evidence inspection.
- Safe viewer `403` state while preserving summary visibility.
- Loading, empty, backend-unavailable, malformed-contract, lookup-failure, and timeout states.
- Request/correlation ID display.
- Strict Zod network-boundary validation.
- Relative API paths through the existing Vite proxy.
- No graph or evidence mutation.

### Required files

```text
frontend/src/contracts/graphEvidence.ts
frontend/src/api/graphEvidence.ts
frontend/src/api/graphEvidenceQueries.ts
frontend/src/features/data-console/components/graph-evidence/GraphEvidenceStatusCard.tsx
frontend/src/features/data-console/components/graph-evidence/GraphEvidenceTable.tsx
frontend/src/features/data-console/components/graph-evidence/GraphEvidenceInspector.tsx
frontend/src/features/data-console/pages/GraphEvidencePage.tsx
frontend/src/api/graphEvidence.test.ts
frontend/src/features/data-console/pages/GraphEvidencePage.test.tsx
```

Integration points:

```text
frontend/src/App.tsx
frontend/src/components/Shell.tsx
README.md
```

### UX boundary

Do not add:

```text
Run synchronization
Sync now
Rebuild graph
Write to Neo4j
```

The validated backend currently exposes read-only evidence APIs only. A future mutation endpoint must be separately designed, authorized, tested, and sandbox-validated before any operator mutation control appears.

### Current frontend classification

```text
Frontend source implementation:       CONTRACT_TESTED
Frontend tests:                       CONTRACT_TESTED (19/19)
Frontend lint/typecheck/build:        PASS
Live Docker frontend/backend API:     PASS (six routes through Vite proxy)
Live visual browser integration:      DEFERRED TO HARDENING
```

Docker commands, request IDs, and the deferred screenshot work are in:

```text
frontend/docs/evidence/graph_evidence_ui/validation_summary.md
```

---

## 17. Infrastructure Visibility

The Data Console exposes:

```text
GET /health/live
GET /health/ready
GET /data-console/v1/overview
```

Dependency probes:

```text
MongoDB
Neo4j
SQL Server
Temporal
Valkey
```

Behavior:

- Concurrent execution.
- Bounded timeouts.
- Safe error mapping.
- Short-lived cache.
- Single-flight coordination.
- Healthy results preserved when another dependency fails.
- `meta.partial` and safe warnings.
- Correlation-ID propagation.

Stage 2 remains accepted for the basic implementation. Exhaustive timeout, recovery, restart-durability, and every driver-error classification remain hardening work.

---

## 18. Environment Configuration

Use one repository-root `.env`.

Do not create `backend/.env`.

Passwords are defined exactly once and referenced by application variables.

```dotenv
# Infrastructure credentials
MONGO_ROOT_USERNAME=<USERNAME>
MONGO_ROOT_PASSWORD=<SECRET>
GRAPH_PASSWORD=<SECRET>
VALKEY_PASSWORD=<SECRET>
MSSQL_SA_PASSWORD=<SECRET>
TEMPORAL_DB_PASSWORD=<SECRET>

# Docker image versions
MSSQL_VERSION=<VERSION>
NEO4J_VERSION=<VERSION>
MONGO_VERSION=<VERSION>
VALKEY_VERSION=<VERSION>
POSTGRES_VERSION=<VERSION>
TEMPORAL_VERSION=<VERSION>
TEMPORAL_ADMIN_TOOLS_VERSION=<VERSION>
TEMPORAL_UI_VERSION=<VERSION>

# Backend
PLATFORM_ENVIRONMENT=development
PLATFORM_FRONTEND_CORS_ORIGIN=http://localhost:5173
PLATFORM_PROBE_TIMEOUT_SECONDS=5
PLATFORM_DEPENDENCY_CONNECT_TIMEOUT_SECONDS=10

PLATFORM_MONGO_DSN=mongodb://${MONGO_ROOT_USERNAME}:${MONGO_ROOT_PASSWORD}@localhost:27017/return_platform?authSource=admin
PLATFORM_MONGO_DATABASE=return_platform
PLATFORM_GRAPH_EVIDENCE_COLLECTION=graph_evidence_runs
PLATFORM_GRAPH_EVIDENCE_QUERY_TIMEOUT_SECONDS=5.0
PLATFORM_MONGO_CONNECTIVITY_TIMEOUT_SECONDS=10
PLATFORM_MONGO_OPERATION_TIMEOUT_SECONDS=10

PLATFORM_NEO4J_URI=bolt://localhost:7687
PLATFORM_NEO4J_USER=neo4j
PLATFORM_NEO4J_PASSWORD=${GRAPH_PASSWORD}
PLATFORM_NEO4J_DATABASE=neo4j
PLATFORM_NEO4J_CONNECTIVITY_TIMEOUT_SECONDS=10
PLATFORM_NEO4J_TRANSACTION_TIMEOUT_SECONDS=5
PLATFORM_NEO4J_OPERATION_TIMEOUT_SECONDS=10

PLATFORM_VALKEY_HOST=localhost
PLATFORM_VALKEY_PORT=6379
PLATFORM_VALKEY_PASSWORD=${VALKEY_PASSWORD}

PLATFORM_TEMPORAL_TARGET=localhost:7233

PLATFORM_SQLSERVER_HOST=127.0.0.1
PLATFORM_SQLSERVER_PORT=1433
PLATFORM_SQLSERVER_USER=sa
PLATFORM_SQLSERVER_PASSWORD=${MSSQL_SA_PASSWORD}
PLATFORM_SQLSERVER_DATABASE=return_platform

# Vite development proxy
FRONTEND_BACKEND_TARGET=http://localhost:8000
```

Rules:

- Never commit `.env`.
- Keep a sanitized `.env.example`.
- Never place secrets in source, tests, README examples, frontend bundles, logs, or public errors.
- URI-reserved characters in credentials must be URL-encoded.
- Real environment variables may override root `.env`.
- Local environment vocabulary is `development`, `test`, `staging`, or `production`.
- `PLATFORM_ENVIRONMENT=dev` is invalid for the current Settings contract.

---

## 19. Installation

### Prerequisites

```text
Ubuntu 22.04 or compatible
Docker and Docker Compose
Python >=3.13,<3.14
Poetry 2.4 or compatible
NVM
Node.js version from frontend/.nvmrc
npm compatible with package-lock.json
```

### Backend

```bash
cd backend
poetry install
```

### Frontend

```bash
cd frontend
nvm use
npm ci
```

Use lock files. Do not install project dependencies through ad hoc `pip install` or unpinned `npm install` commands.

---

## 20. Start the Platform

### Terminal 1 — infrastructure

From the repository root:

```bash
docker compose up -d
docker compose ps
```

### Terminal 2 — backend

```bash
cd backend

poetry run uvicorn return_platform.asgi:app \
  --host 0.0.0.0 \
  --port 8000
```

For development-only hot reload:

```bash
poetry run uvicorn return_platform.asgi:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

Do not use `--reload` when capturing reproducible validation evidence.

### Terminal 3 — frontend

```bash
cd frontend
nvm use
npm run dev -- --host 0.0.0.0
```

Endpoints:

```text
Backend liveness:   http://localhost:8000/health/live
Backend readiness:  http://localhost:8000/health/ready
Data Console API:   http://localhost:8000/data-console/v1/overview
Graph Evidence API: http://localhost:8000/data-console/v1/graph-evidence
Frontend:           http://localhost:5173
Graph Evidence UI:  http://localhost:5173/data-console/graph-evidence
```

---

## 21. Quality Gates

### Required backend gate

Run from `backend/`:

```bash
poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv
```

Latest status:

```text
PASS
```

The exact test count and command transcript were not included in this README update context and should be appended when the repository evidence is captured.

Do not weaken Ruff, strict mypy, Pydantic strictness, or tests.

### Focused Graph Evidence backend gate

```bash
poetry run ruff format --check \
  src/return_platform/configuration/settings.py \
  src/return_platform/data_platform/graph/evidence_repository.py \
  src/return_platform/data_platform/graph/evidence_query.py \
  src/return_platform/data_console/api/graph_evidence.py \
  tests/test_customer_graph_sandbox.py \
  tests/test_graph_evidence_settings.py \
  tests/test_graph_evidence_api.py

poetry run ruff check \
  src/return_platform/configuration/settings.py \
  src/return_platform/data_platform/graph/evidence_repository.py \
  src/return_platform/data_platform/graph/evidence_query.py \
  src/return_platform/data_console/api/graph_evidence.py \
  tests/test_customer_graph_sandbox.py \
  tests/test_graph_evidence_settings.py \
  tests/test_graph_evidence_api.py

poetry run mypy --no-incremental \
  src/return_platform/configuration/settings.py \
  src/return_platform/data_platform/graph/evidence_repository.py \
  src/return_platform/data_platform/graph/evidence_query.py \
  src/return_platform/data_console/api/graph_evidence.py \
  tests/test_customer_graph_sandbox.py \
  tests/test_graph_evidence_settings.py \
  tests/test_graph_evidence_api.py

poetry run pytest -vv \
  tests/test_customer_graph_sandbox.py \
  tests/test_graph_evidence_settings.py \
  tests/test_graph_evidence_api.py
```

Latest status:

```text
PASS
```

### Focused Temporal Return workflow core gate

Run in the Python 3.13 backend Docker container:

```bash
ruff format --check src/return_platform/workflows tests/test_return_workflow.py
ruff check src/return_platform/workflows tests/test_return_workflow.py
python -m mypy --no-incremental src/return_platform/workflows tests/test_return_workflow.py
python -m pytest -vv tests/test_return_workflow.py
```

Observed on July 22, 2026:

```text
Focused format: PASS
Focused lint:   PASS
Focused mypy:   PASS
Focused tests:  PASS (10/10)
Complete lint:  PASS (107 source files)
Complete mypy:  PASS (107 source files)
Complete tests: PASS (862/862)
```

The full repository format check reports 12 pre-existing files outside the
workflow slice that would be reformatted. They were preserved; all new workflow
files pass the focused format check.

### Live Graph Evidence API validation

```bash
cd backend

chmod +x scripts/validate_graph_evidence_api.sh
./scripts/validate_graph_evidence_api.sh

echo $?
```

Latest result:

```json
{
  "evidence_output": "docs/evidence/graph_evidence_api/validation_summary.json",
  "process_exit_code": 0,
  "routes_validated": 6,
  "status": "SANDBOX_VALIDATED"
}
```

### Frontend

First inspect actual scripts:

```bash
cd frontend
cat package.json
```

Expected focused gate:

```bash
nvm use
npm ci

npm run lint
npm run typecheck

npm run test -- \
  src/api/graphEvidence.test.ts \
  src/features/data-console/pages/GraphEvidencePage.test.tsx

npm run build
```

Expected complete gate:

```bash
npm run check
```

If `npm run check` does not include all tests:

```bash
npm run test
```

Current frontend status:

```text
PENDING
```

Coverage is diagnostic and must not be reported without exact current output:

```bash
poetry run pytest \
  --cov=return_platform \
  --cov-report=term-missing \
  --cov-report=html \
  -vv
```

Broader coverage expansion is deferred until the planned implementation sequence is complete. Ruff, strict mypy, and existing pytest gates remain mandatory for every module.

---

## 22. Recorded Evidence

### Stage 2 infrastructure

Observed on July 18, 2026:

| Dependency | Status | Observed latency |
|---|---:|---:|
| MongoDB | Healthy | 3 ms |
| Neo4j | Healthy | 0 ms |
| SQL Server | Healthy | 68 ms |
| Temporal | Healthy | 4 ms |
| Valkey | Healthy | 0 ms |

Partial-degradation evidence:

```text
Valkey status = UNAVAILABLE
error_code = CONNECTION_REFUSED
meta.partial = true
```

Healthy dependency results remained visible.

### SQL Server inventory

Observed on July 20, 2026:

```text
Database:       return_platform
Observed at:    2026-07-20T04:31:06.907334+00:00
Visible empty:  True
Visible tables: 0
Visible views:  0
Schemas:        0
```

Classification:

```text
SANDBOX_VALIDATED
```

### MongoDB inventory

Observed on July 20, 2026:

```text
Database:            return_platform
Observed at:         2026-07-20T05:20:35.748940+00:00
Visible empty:       True
Visible collections: 0
Visible indexes:     0
```

Classification:

```text
SANDBOX_VALIDATED
```

### Drift

Observed output:

```text
Catalog version:        1.0
Analyzed at:            2026-07-20T05:50:18.209858+00:00
Complete evidence:      True
Drift free:             True
Confirmed drift count:  0
Not evaluated count:    0
Total records:          0
```

Classification:

```text
IMPLEMENTED; LIVE OUTPUT OBSERVED
```

The process exit code was not captured.

### Customer graph sandbox

```text
Status: SANDBOX_VALIDATED
Process exit code: 0
Local evidence:
docs/evidence/customer_graph_sandbox_validation.json

Report digest:
75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8

Platform evidence document:
CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7

Platform evidence digest:
6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b

Platform persistence status:
CREATED
```

### Graph Evidence APIs

```text
Status: SANDBOX_VALIDATED
Process exit code: 0
Routes validated: 6
Evidence:
docs/evidence/graph_evidence_api/validation_summary.json
```

### Data Console Customer Graph Evidence frontend

Observed on July 22, 2026 using Docker for both frontend and backend:

```text
ESLint:                   PASS
Strict TypeScript:        PASS
Focused frontend tests:   PASS (19/19)
Complete frontend tests:  PASS (19/19)
Vite production build:    PASS
Live Vite-proxied routes: PASS (6/6 HTTP 200)
Visual screenshots:       DEFERRED TO HARDENING
```

Evidence:

```text
frontend/docs/evidence/graph_evidence_ui/validation_summary.md
```

### Temporal Return workflow deterministic core

The first Temporal slice defines execution coordination only. It does not own
business return state and performs no external I/O.

Implemented contracts:

```text
ReturnWorkflowInput
ReturnWorkflowConfigurationVersion
ReturnWorkflowAdvanceCommand
AppliedStageCommand
ReturnWorkflowExecutionState
ReturnWorkflowTransitionError
```

The workflow uses the fixed stage order:

```text
INTAKE
→ ORDER_DISCOVERY
→ ELIGIBILITY_EVALUATION
→ RETURN_REQUEST
→ FULFILLMENT_TRACKING
→ BAY_ASSIGNMENT
→ FEEDBACK_LEARNING
→ COMPLETED
```

`complete_stage` is an ordered, idempotent Temporal update. Identical command
replay returns the existing execution state. Reusing a command ID with different
stage evidence, skipping a stage, or advancing a completed execution fails with
a stable safe code. `execution_state` is an execution-observability query, not a
business-state API.

Platform MongoDB remains authoritative for `ReturnSession`, audit, decision,
and outbox state. The repository and Temporal persistence activities atomically
write session, audit, and outbox evidence with idempotent command replay and no
hidden write retry. Eligibility transitions also write the canonical
`AgentDecision` in that same transaction.

Evidence:

```text
backend/docs/evidence/return_workflow_core/validation_summary.md
backend/docs/evidence/return_session_persistence/validation_summary.md
backend/docs/evidence/return_workflow_live/validation_summary.md
backend/docs/evidence/return_stage_contexts/validation_summary.md
backend/docs/evidence/return_eligibility_gateway/validation_summary.md
backend/docs/evidence/return_request_context/validation_summary.md
backend/docs/evidence/fulfillment_tracking_context/validation_summary.md
backend/docs/evidence/bay_assignment_context/validation_summary.md
backend/docs/evidence/feedback_learning_context/validation_summary.md
```

---

## 23. Security Rules

- Never commit `.env`.
- Never repeat real password values in tests or documentation.
- Never expose raw driver errors.
- Never log `SecretStr.get_secret_value()`.
- Never place production credential defaults in settings.
- Never expose infrastructure credentials or DSNs to the frontend.
- Never accept arbitrary SQL, MongoDB filters, Cypher, Python, or import paths from configuration.
- Never infer ownership from a physical database object.
- Never write to source-system assets.
- Never treat Neo4j as authoritative business state.
- Never use display names, list positions, or mutable attributes as identity.
- Never treat inventory `bin_location` as a Return Bay.
- Never add `Package` or `PPLTracking` to graph v1 without approved identity and join evidence.
- Preserve `asyncio.CancelledError`.
- Treat unknown commit or write outcomes as unknown; never retry blindly.
- Treat deprecation warnings as failures.
- Keep Graph Evidence APIs and the current frontend screen read-only.
- Do not expose full graph evidence to `console_viewer`; full evidence requires `console_admin`.

---

## 24. Reconstruction Procedure

From a clean clone:

```bash
git clone <repository-url>
cd <repository-directory>
```

Create the root `.env` from the safe blueprint.

Confirm the production catalog remains truthful:

```bash
cat backend/config/data_assets.yaml
```

Start infrastructure:

```bash
docker compose up -d
docker compose ps
```

Install and validate backend:

```bash
cd backend
poetry install

poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv
```

Run live metadata validation only against the intended sandbox:

```bash
poetry run python run_live_sql_inventory.py
poetry run python run_live_mongo_inventory.py
poetry run python run_live_drift.py
printf "drift_exit_code=%s\n" "$?"
```

Run the controlled Customer graph sandbox:

```bash
poetry run python -m return_platform.data_platform.graph.sandbox_runner \
  --source-file tests/fixtures/customer_graph_sandbox/customer_p100.json \
  --source-document-id P100 \
  --source-updated-at 2026-07-22T04:00:00Z \
  --source-version 17 \
  --source-event-id evt-100 \
  --evidence-output docs/evidence/customer_graph_sandbox_validation.json

printf "sandbox_exit_code=%s\n" "$?"
```

Start backend:

```bash
poetry run uvicorn return_platform.asgi:app \
  --host 0.0.0.0 \
  --port 8000
```

Validate all Graph Evidence API routes:

```bash
chmod +x scripts/validate_graph_evidence_api.sh
./scripts/validate_graph_evidence_api.sh
printf "graph_api_exit_code=%s\n" "$?"
```

Install and validate frontend:

```bash
cd ../frontend
nvm use
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
npm run check
```

Start frontend:

```bash
npm run dev -- --host 0.0.0.0
```

Verify:

```bash
curl --fail-with-body http://localhost:8000/health/live
curl --fail-with-body http://localhost:8000/health/ready
curl --fail-with-body http://localhost:8000/data-console/v1/overview
curl --fail-with-body http://localhost:8000/data-console/v1/graph-evidence
```

Open:

```text
http://localhost:5173/data-console/graph-evidence
```

---

## 25. Known Limitations

- The production asset catalog is intentionally empty.
- The Customer mapping profile references a future approved Customer CDM asset that is not yet in the production catalog.
- No production Customer source document has been fetched through the source adapter.
- Customer graph write/read-back evidence is sandbox-only, not production validation.
- Graph Evidence API evidence is sandbox-only, not production validation.
- The Data Console Customer Graph Evidence frontend passed lint, strict typecheck, 19 focused/complete tests, production build, and all six live Docker frontend-proxy API checks. Desktop and mobile screenshots are explicitly deferred to hardening.
- No governed graph synchronization mutation API exists.
- No Temporal activity owns source/graph timeout and retry policy.
- No cross-document Customer or CustomerAccount collision detector exists.
- Order-line immutability remains unconfirmed.
- OMC product bridging remains unresolved when source evidence is missing.
- Actual carrier tracking remains optional legacy evidence.
- `Package` and `PPLTracking` remain excluded from graph v1.
- Inventory API and Data Ownership frontend pages remain unimplemented.
- The complete customer return workflow and customer-facing return UI remain unimplemented.
- The scenario runner remains unimplemented.
- Restart durability, failover, multi-region, load, security, and broader hardening evidence remain deferred.

---

## 26. Current Phase and Immediate Next Step

### Current phase

```text
Stage 1 — Frontend/API foundation:             COMPLETE
Stage 2 — Infrastructure visibility:           BASIC ACCEPTANCE PASSED
Stage 3 — Governance/catalog:                  COMPLETE
Stage 3 — SQL inventory:                       SANDBOX_VALIDATED
Stage 3 — Mongo inventory:                     SANDBOX_VALIDATED
Stage 3 — Drift:                               IMPLEMENTED; LIVE OUTPUT OBSERVED
Stage 3 — Bounded sampling:                    CONTRACT_TESTED
Stage 3 — Inventory API/UI:                    NOT IMPLEMENTED

Canonical data-model implementation:           COMPLETE
Versioned mapping contracts:                   COMPLETE
Customer mapping configuration:               COMPLETE
Handler registry:                             COMPLETE
Multi-file mapping loader:                    COMPLETE
Customer mapping compiler:                    COMPLETE
Customer in-memory normalization:              COMPLETE
Customer MongoDB source adapter:               COMPLETE
Customer graph materialization:                COMPLETE
Customer Neo4j command builder:                COMPLETE
Customer Neo4j writer:                         CONTRACT_TESTED
Customer graph sandbox:                        SANDBOX_VALIDATED
Live sandbox Neo4j write:                      SANDBOX_VALIDATED
Graph read-back validation:                    SANDBOX_VALIDATED
Second-run idempotency:                        SANDBOX_VALIDATED
Platform graph-evidence persistence:           SANDBOX_VALIDATED
Graph Validation API:                          SANDBOX_VALIDATED
Graph Inspection APIs:                         SANDBOX_VALIDATED
Data Console Customer Graph Evidence screens: CONTRACT_TESTED; LIVE API PROXY VERIFIED; SCREENSHOTS DEFERRED TO HARDENING

Live production Customer source lookup:        BLOCKED_EXTERNAL_DEPENDENCY
Temporal return workflow deterministic core:   CONTRACT_TESTED
ReturnSession persistence activities:          LIVE SANDBOX VALIDATED
Temporal worker and live workflow execution:   LIVE SANDBOX VALIDATED
Intake and discovery context persistence:      LIVE SANDBOX VALIDATED
Eligibility and AI Gateway boundary:           CONTRACT_TESTED; LIVE PERSISTENCE VALIDATED
Deterministic RETURN_REQUEST context:          LIVE SANDBOX VALIDATED
Deterministic FULFILLMENT_TRACKING context:    LIVE SANDBOX VALIDATED
Deterministic BAY_ASSIGNMENT context:          LIVE SANDBOX VALIDATED
Deterministic FEEDBACK_LEARNING context:       LIVE SANDBOX VALIDATED
Customer return frontend:                      NOT IMPLEMENTED
Scenario runner:                               NOT IMPLEMENTED
```

### Immediate execution target

Continue in Codex from:

```text
Temporal Return workflow — end-to-end scenario matrix
```

All seven deterministic stage contexts and their atomic persistence boundaries are
validated. The next bounded slice is:

1. Define at least five positive and five negative end-to-end scenarios.
2. Exercise approval, rejection, review, replay, and tamper/conflict paths.
3. Verify final session contexts, decision evidence, audit, and outbox counts.
4. Produce a deterministic scenario report suitable for hardening evidence.
5. Keep screenshot capture deferred until the hardening page.

### The current step must not

```text
add graph mutation APIs
add a synchronization run button
mutate the production asset catalog
add automatic retries
add arbitrary MongoDB filters
add arbitrary Cypher
add SalesOrder graph execution
enable production business-source workflow activities
enable unvalidated workflow decisions or eligibility policy
add customer-facing workflow APIs
add Package
add PPLTracking
start the customer-facing return UI
begin the hardening phase
```

Do not enable a live model provider or customer-facing return UI. The gateway
boundary is validated, but an approved provider adapter and production eligibility
policy/configuration have not been selected.
