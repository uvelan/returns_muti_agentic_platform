Return Platform — Data Console and Customer Graph Foundation

The Return Platform is a backend-first Sales Order Return platform with a separate operational Data Console.

The customer return process is the primary end-to-end product experience. The Data Console is a developer and operator control plane used to create and inspect supporting data, validate infrastructure, observe synchronization, inspect evidence, and diagnose failures. It is not the primary customer demo.

The current repository includes:

FastAPI and React application foundations.

Live dependency health visibility.

Immutable data-governance and asset-catalog contracts.

SQL Server and MongoDB metadata inventory.

Declared-versus-observed drift analysis.

Bounded sampling contracts.

The complete canonical domain-model layer.

Versioned physical-to-canonical mapping contracts.

The first Customer and CustomerAccount mapping profile.

A code-owned mapping-handler registry.

A bounded multi-file mapping loader.

An immutable Customer-profile compiler.

Deterministic in-memory Customer normalization.

A read-only exact-ID MongoDB Customer source adapter.

Deterministic Customer graph-projection materialization.

Fixed parameterized Customer Neo4j command contracts.

An explicit no-hidden-retry Customer Neo4j writer implementation.

No production source asset, live Customer source lookup, or live Neo4j graph write is claimed as validated unless the corresponding evidence is explicitly recorded below.

1. Current Status

Area

Status

Notes

Stage 1 — Frontend/API foundation

Complete

FastAPI application shell, React/Vite shell, typed API client, routing, lifecycle resources, and baseline quality gates

Stage 2 — Infrastructure visibility

Basic acceptance passed

Five dependency probes, concurrent aggregation, partial responses, correlation IDs, safe errors, and live healthy/degraded evidence

Stage 3 — Governance and catalog

Complete

Immutable governance contracts, strict YAML catalog loading, startup registration, and empty production catalog

Stage 3 — SQL Server inventory

SANDBOX_VALIDATED

Metadata-only live inventory completed against the configured sandbox database

Stage 3 — MongoDB inventory

SANDBOX_VALIDATED

Metadata-only live inventory completed against the configured sandbox database

Stage 3 — Drift

Implemented; live output observed

Empty declared and observed state produced zero confirmed drift; process exit code was not captured

Stage 3 — Bounded sampling

CONTRACT_TESTED

Live sampling deliberately deferred

Stage 3 — Inventory API/UI

Not implemented

Data Ownership inventory pages remain pending

Canonical domain model

Complete

Customer, order, product, warehouse, shipment, return, bay, session, audit, decision, and graph-evidence contracts

Mapping configuration language

Complete

Source, canonical, graph, relationship-direction, physical-scope, and pipeline contracts

Customer mapping profile

Complete

Customer and CustomerAccount profile across four versioned YAML files

Mapping handler registry

Complete

Code-owned field and identity handlers with purpose, arity, output, version, and determinism metadata

Multi-file mapping loader

Complete

Bounded UTF-8 YAML loading, duplicate-key and alias rejection, schema agreement, and digest evidence

Customer mapping compiler

Complete

Governance, canonical-model, handler, graph, and pipeline validation into an immutable execution plan

Customer in-memory normalization

Complete

Deterministic document and nested-account normalization with safe record rejection evidence

Customer MongoDB source adapter

Complete

Exact governed _id lookup contract; live lookup remains blocked

Customer graph materialization

Complete

Immutable Customer, CustomerAccount, and HAS_ACCOUNT parameter materialization

Customer Neo4j command builder

Complete

Fixed constraints and parameterized node/relationship commands

Customer Neo4j writer

Implemented; repository revalidation pending

Latest Ruff, strict mypy, and Neo4j 6.2 test corrections are supplied; complete repository gates must be rerun

Live Customer source lookup

BLOCKED_EXTERNAL_DEPENDENCY

The production catalog intentionally has no approved Customer CDM source asset

Live Neo4j Customer write

Not validated

No constraint, node, relationship, or read-back evidence has been captured

Temporal return workflow

Not implemented

Workflow orchestration begins after the basic data and graph foundation is reliable

Scenario runner

Not implemented

Positive and negative end-to-end return scenarios remain pending

Immediate truth boundary

The latest Customer Neo4j writer correction has focused validation evidence:

Customer command-builder + writer focused tests: 53 passed

The following must still be run in the actual repository before the writer is marked CONTRACT_TESTED:

poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv

Do not report repository-wide writer success until those commands pass after the latest correction.

2. Locked Architecture and Ownership

Canonical workflow

IntakeContext
  → DiscoveryContext
  → ReturnRequestContext
  → FulfillmentTrackingContext
  → BayStagingContext
  → LearningFeedbackContext
  → ReturnSessionContext

Canonical module flow:

Order Discovery
  → Return Workflow
  → Return Fulfillment
  → Bay Assignment
  → Feedback Learning

Data ownership

System

Ownership and responsibility

Platform MongoDB

Authoritative internal platform state: sessions, audits, configurations, outbox, decisions, evidence, and later operator state

SQL Server / OMC

Authoritative business facts for return, RMA, and fulfillment data; read-only from the platform

Source MongoDB

Read-only discovery and Customer CDM source data; no workflow-owned fields

Neo4j

Derived and rebuildable graph projection only; never authoritative business state

Temporal

Durable execution, timers, retries, and workflow coordination; not business-state ownership

Valkey

Transient coordination, caching, rate limiting, and SSE support

Temporal PostgreSQL

Internal Temporal persistence; not accessed directly by the Return Platform

Configuration rule

Configuration defines what varies:

Approved source assets.

Physical paths and aliases.

Canonical target fields.

Mapping and pipeline versions.

Graph labels, relationship types, and property names within strict allow-lists.

Environment-specific values.

Code defines how execution remains safe:

Identity algorithms.

Handler implementation.

Canonical validation.

Alias-conflict policy.

Retry ownership.

Transaction behavior.

Security boundaries.

Cypher templates.

Source and graph adapter behavior.

Configuration must never contain arbitrary SQL, MongoDB filters, Cypher, Python, import paths, or executable handler arguments.

3. Current End-to-End Customer Data Path

The implemented Customer foundation is:

Versioned YAML files
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
  │       └── raw immutable document + SourceDocumentEvidence
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

The final writer layer is not yet live validated.

4. Repository Structure

.
├── .env
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
│   │       │
│   │       ├── canonical/
│   │       │   ├── __init__.py
│   │       │   ├── base.py
│   │       │   ├── customer.py
│   │       │   ├── order.py
│   │       │   ├── product.py
│   │       │   ├── warehouse.py
│   │       │   ├── shipment.py
│   │       │   ├── return_models.py
│   │       │   ├── bay.py
│   │       │   └── operations.py
│   │       │
│   │       ├── configuration/
│   │       │   ├── __init__.py
│   │       │   └── settings.py
│   │       │
│   │       ├── data_console/
│   │       │   ├── __init__.py
│   │       │   ├── api/
│   │       │   │   ├── __init__.py
│   │       │   │   └── router.py
│   │       │   └── infrastructure/
│   │       │       ├── __init__.py
│   │       │       └── probes.py
│   │       │
│   │       ├── data_governance/
│   │       │   ├── __init__.py
│   │       │   ├── catalog_loader.py
│   │       │   ├── drift.py
│   │       │   ├── inventory/
│   │       │   │   ├── __init__.py
│   │       │   │   ├── mongodb.py
│   │       │   │   ├── sqlserver.py
│   │       │   │   └── contracts/
│   │       │   │       ├── __init__.py
│   │       │   │       ├── base_contracts.py
│   │       │   │       ├── mongodb_contracts.py
│   │       │   │       └── sqlserver_contracts.py
│   │       │   └── sampling/
│   │       │       ├── __init__.py
│   │       │       ├── authorization.py
│   │       │       ├── contracts.py
│   │       │       ├── mongodb.py
│   │       │       ├── sanitization.py
│   │       │       └── sqlserver.py
│   │       │
│   │       ├── data_platform/
│   │       │   ├── __init__.py
│   │       │   ├── mapping/
│   │       │   │   ├── __init__.py
│   │       │   │   ├── contracts.py
│   │       │   │   ├── loader.py
│   │       │   │   ├── compiler.py
│   │       │   │   ├── normalizer.py
│   │       │   │   ├── projection.py
│   │       │   │   └── handlers/
│   │       │   │       ├── __init__.py
│   │       │   │       ├── contracts.py
│   │       │   │       └── customer.py
│   │       │   ├── sources/
│   │       │   │   ├── __init__.py
│   │       │   │   └── mongodb/
│   │       │   │       ├── __init__.py
│   │       │   │       └── customer.py
│   │       │   └── graph/
│   │       │       ├── __init__.py
│   │       │       ├── commands.py
│   │       │       └── writer.py
│   │       │
│   │       ├── security/
│   │       │   ├── __init__.py
│   │       │   └── principal.py
│   │       │
│   │       └── shared/
│   │           ├── __init__.py
│   │           ├── contracts.py
│   │           └── governance.py
│   │
│   └── tests/
│       ├── conftest.py
│       ├── test_canonical_base.py
│       ├── test_canonical_customer.py
│       ├── test_canonical_order.py
│       ├── test_canonical_product.py
│       ├── test_canonical_warehouse.py
│       ├── test_canonical_shipment.py
│       ├── test_canonical_return_models.py
│       ├── test_canonical_bay.py
│       ├── test_canonical_operations.py
│       ├── test_governance.py
│       ├── test_catalog_loader.py
│       ├── test_catalog_lifespan.py
│       ├── test_health.py
│       ├── test_probes.py
│       ├── test_inventory_sqlserver.py
│       ├── test_inventory_mongodb.py
│       ├── test_drift.py
│       ├── test_sampling_authorization.py
│       ├── test_sampling_sanitization.py
│       ├── test_sampling_sqlserver.py
│       ├── test_sampling_mongodb.py
│       ├── test_mapping_contracts.py
│       ├── test_mapping_relationship_direction.py
│       ├── test_mapping_physical_path_scope.py
│       ├── test_mapping_configuration_loader.py
│       ├── test_mapping_handler_registry.py
│       ├── test_customer_mapping_handlers.py
│       ├── test_mapping_compiler.py
│       ├── test_customer_document_normalizer.py
│       ├── test_customer_mongodb_source_adapter.py
│       ├── test_customer_graph_projection_materializer.py
│       ├── test_customer_neo4j_command_builder.py
│       └── test_customer_neo4j_writer.py
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
        ├── contracts/
        ├── test/
        ├── components/
        └── features/data-console/

5. Backend Foundations

Application construction

return_platform.main.create_app() is the application factory.

return_platform.asgi is the production ASGI entry point:

from return_platform.main import create_app

app = create_app()

main.py must not expose a module-level app.

The FastAPI lifespan:

Validates settings.

Loads the immutable asset catalog.

Constructs RuntimeResources.

Initializes external clients.

Attaches resources to app.state.

Closes lifespan-owned resources in reverse order.

Functions decorated with contextlib.asynccontextmanager must use:

AsyncGenerator[YieldType, None]

Deprecation warnings are treated as failures.

Runtime resources

RuntimeResources owns references to:

Settings.

Loaded governance catalog.

PyMongo asynchronous client.

Neo4j asynchronous driver.

Valkey asynchronous client.

Temporal client.

The bounded one-worker SQL Server executor.

The Customer MongoDB adapter and Customer Neo4j writer reuse injected lifespan-owned clients. They must never close those clients.

Error and response contracts

Backend API responses use the shared strict envelope:

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

Raw credentials, driver messages, stack traces, source values, and infrastructure addresses must not be returned through public errors.

6. Data Governance and Catalog

Production catalog

backend/config/data_assets.yaml is the version-controlled declaration of approved physical assets.

Current required state:

version: "1.0"

# The production catalog is intentionally empty.
#
# Add an asset only after:
# 1. The physical object exists.
# 2. Ownership is approved.
# 3. Allowed operations are approved.
# 4. The live identity is verified.
assets: []

The production catalog must not be changed merely to unblock tests.

Governance invariants

Empty catalogs are valid.

Duplicate asset IDs are rejected.

Duplicate physical assets are rejected.

Source-system assets are read-only.

SQL Server objects require a namespace and must be tables or views.

MongoDB objects must be collections and must not use SQL-style namespaces.

Derived projections cannot be authoritative.

Ownership must match the asset_id prefix.

Sampling is bounded and requires explicit READ permission.

Catalog loader protections

.yaml and .yml extension allow-list.

Resolved regular-file checks.

File-size bound.

UTF-8 and UTF-8 BOM handling.

Safe YAML parsing.

Duplicate-key rejection at every mapping depth.

Empty-document rejection.

Root-mapping enforcement.

Strict governance validation.

SHA-256, byte-size, path, and asset-count evidence.

Startup failure for missing or invalid catalogs.

The catalog is loaded once per application lifespan. Request handlers must use RuntimeResources.catalog.

7. Canonical Domain Model

All canonical models inherit from CanonicalBaseModel:

strict scalar validation
frozen instances
unknown-field rejection
validated defaults
hidden raw input values in validation errors
UTC timestamp normalization

Shared contracts

IdentityQuality
CanonicalIdentifier
NonBlankText
VersionReference
Sha256Digest
UtcDateTime
CanonicalBaseModel
SourceProvenance

Customer foundation

Customer
CustomerAccount
ContactPoint
Address

Identity:

Customer.customer_key =
"CUSTOMER_CDM:" + party_id

CustomerAccount.account_key =
"CUSTOMER_CDM:" + account_number

ContactPoint and Address are canonical value objects, not graph nodes in graph model v1.

Order and Product

SalesOrder
OrderLine
Product

Identity:

SalesOrder.source_document_id =
account_id + "*" + order_id

SalesOrder.sales_order_key =
"TDS:" + account_id + ":" + order_id + ":" + order_instance_key

OrderLine.order_line_key =
sales_order_key + ":LINE:" + source_line_number

Product.product_key =
"STEP:" + master_product_id

Order-line identity remains conditional until line-number immutability is confirmed.

Warehouse and Shipment

Warehouse
WarehouseProduct
Shipment
ShipmentItem
TrackingEvent
CarrierTrackingReference

bin_location remains inventory location and is never interpreted as a Return Bay.

Actual carrier tracking remains optional legacy evidence.

Package and PPLTracking remain excluded from graph v1.

Return, Bay, session, and evidence

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

Platform-owned Bay identity remains separate from warehouse inventory location.

The model layer does not perform database I/O, graph I/O, workflow transitions, or cross-record uniqueness enforcement.

8. Versioned Mapping Configuration

Configuration files

backend/config/data_platform/sources.yaml
backend/config/data_platform/canonical_mappings.yaml
backend/config/data_platform/graph_projection.yaml
backend/config/data_platform/sync_pipelines.yaml

Mapping contracts

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

Relationship direction

The mapping language distinguishes:

reference-holder match direction
emitted graph edge direction

For the Customer profile:

CustomerAccount.customer_key holds the reference
Customer → CustomerAccount is the emitted edge

Resolved graph direction:

(Customer)-[:HAS_ACCOUNT]->(CustomerAccount)

Physical path scope

RECORD   → resolve from the current nested selected record
DOCUMENT → resolve from the original source document

No implicit fallback exists between scopes.

Handler registry

Handlers are code-owned and registered with:

purpose
input arity
output type
contract version
deterministic flag

Customer handlers include separate roles:

customer_key_v1
  purpose = IDENTITY

customer_reference_key_v1
  purpose = FIELD

The compiler rejects identity handlers used in ordinary field positions.

Multi-file loader

The loader enforces:

fixed filenames and load order
regular-file and symlink protections
1 MiB per file
4 MiB total
UTF-8 with BOM support
duplicate YAML-key rejection
YAML alias rejection
exact root keys
exact schema-version agreement
immutable bundle validation
per-file SHA-256
domain-separated multi-file digest

Customer compiler

The compiler resolves the loaded configuration against:

AssetCatalog
CanonicalModelRegistry
HandlerRegistry

It validates:

Approved catalog asset existence.

Source ownership, authority, store, object kind, and allowed operations.

Canonical entity registration.

Configured canonical fields.

Required canonical field coverage.

Handler existence, purpose, arity, output type, version, and determinism.

Graph property sources.

Relationship direction.

Pipeline dependency order.

It performs no file, database, handler-execution, Cypher, or graph I/O.

The production catalog intentionally does not yet contain the referenced Customer CDM source asset. Production compilation therefore remains blocked until approval.

9. Customer In-Memory Normalization

normalize_customer_source_document() consumes:

MappingExecutionPlan
SourceDocumentEvidence
one supplied source document

It implements:

DOCUMENT and RECORD scope resolution.

Deterministic nested custAccts[] iteration.

Ordered aliases.

Null-as-absent fallback.

Type-strict alias conflict detection.

Required and optional field handling.

Code-owned field and identity handler invocation.

Runtime handler-output verification.

Canonical Customer and CustomerAccount validation.

Parent Customer dependency validation.

Duplicate CustomerAccount identity rejection.

Safe per-record rejection evidence.

Bounded depth and structural-node limits.

Cycle and malformed-structure rejection.

Immutable output detached from caller mutation.

The normalizer performs no MongoDB or Neo4j I/O.

10. Customer MongoDB Source Adapter

CustomerMongoSourceAdapter:

Reuses an injected AsyncMongoClient.

Never creates or closes the client.

Resolves database and collection only from CompiledSourceAssetPlan.

Revalidates governance constraints.

Executes exactly:

find_one({"_id": source_document_id})

Accepts no caller-defined filters, projection, sort, aggregation, JavaScript, regex, database name, or collection name.

Applies both server-side max_time_ms and an outer asyncio.timeout.

Preserves caller cancellation.

Performs no internal retry.

Requires the returned _id to exactly match the requested string ID.

Calculates SHA-256 over exact BSON bytes.

Returns a detached recursively immutable document.

Rejects cycles, invalid keys, invalid BSON, and structural overflow.

Live lookup remains blocked because the production governance catalog has no approved Customer CDM source asset.

11. Customer Graph Materialization

materialize_customer_graph_projection() consumes:

MappingExecutionPlan
CustomerNormalizationResult
sync_run_id
graph_synced_at

It emits immutable parameter contracts for:

Customer node
CustomerAccount nodes
Customer → CustomerAccount HAS_ACCOUNT relationships

Mandatory graph evidence:

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

Missing mandatory source_updated_at fails closed:

Customer:        REJECTED
CustomerAccount: UNRESOLVED
Node parameters: 0
Relationship parameters: 0

PROJECTED at this layer means graph parameters were successfully materialized. It does not prove persistence.

12. Customer Neo4j Command Builder

The graph package owns fixed Neo4j-specific command construction.

Supported schema and data commands:

Customer.customer_key uniqueness constraint
CustomerAccount.account_key uniqueness constraint
Customer node MERGE
CustomerAccount node MERGE
Customer-[:HAS_ACCOUNT]->CustomerAccount MERGE

The builder:

Accepts only CustomerGraphProjectionMaterialization.

Uses code-owned labels, relationship types, properties, and full Cypher templates.

Rejects arbitrary Cypher.

Binds all values through parameters.

Preserves Customer-to-CustomerAccount direction.

Orders constraints before nodes and nodes before relationships.

Rejects duplicate node keys and relationship endpoints.

Produces deterministic UUIDv5 command IDs.

Produces a deterministic SHA-256 command-batch digest.

Returns detached driver-parameter dictionaries.

It performs no Neo4j I/O.

13. Customer Neo4j Writer

CustomerNeo4jWriter reuses an injected lifespan-owned AsyncDriver.

Phase 1 — schema preparation

Explicit database selection.

Write access routing.

One explicit unmanaged transaction.

Execute both fixed uniqueness constraints.

Server transaction timeout.

Outer operation timeout.

No hidden retries.

Capture causal bookmarks after commit.

Return immutable schema evidence.

Phase 2 — data transaction

Require matching committed schema evidence.

Pass schema bookmarks into the data session.

Open one explicit unmanaged transaction.

Execute Customer node first.

Execute CustomerAccount nodes second.

Execute HAS_ACCOUNT relationships last.

Verify exactly one returned key for every node.

Verify exactly one returned endpoint pair for every relationship.

Roll back known failures.

Preserve caller cancellation.

Classify IncompleteCommit as COMMIT_OUTCOME_UNKNOWN.

Return immutable data-write evidence.

No-hidden-retry rule

The writer does not use:

AsyncDriver.execute_query
AsyncSession.execute_write
AsyncSession.execute_read

Those managed APIs may retry transient failures.

The writer uses:

session.begin_transaction
transaction.run
transaction.commit
transaction.rollback

Session auto-commit retries are disabled defensively.

Latest correction

The latest repository correction addresses:

Ruff import ordering.

An unused test import.

Ruff ASYNC109 in the typed fake transaction API.

mypy loop-variable type leakage between node and relationship commands.

Unsupported Neo4jError(..., code=...) test construction under Neo4j Driver 6.2.

Focused evidence after correction:

Customer command-builder + writer tests: 53 passed

Repository-wide Ruff, strict mypy, and pytest must be rerun before changing writer status to CONTRACT_TESTED.

14. Infrastructure Visibility

The Data Console exposes:

GET /health/live
GET /health/ready
GET /data-console/v1/overview

Five dependency probes:

MongoDB
Neo4j
SQL Server
Temporal
Valkey

Behavior:

Concurrent execution.

Bounded timeouts.

Safe error mapping.

Short-lived cache.

Single-flight coordination.

Healthy results preserved when another dependency fails.

meta.partial and safe warnings.

Correlation-ID propagation.

Stage 2 remains accepted for the basic implementation. Exhaustive timeout, recovery, restart-durability, and every driver-error classification remain hardening work.

15. Environment Configuration

Use one repository-root .env.

Passwords are defined exactly once and referenced by application variables.

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

PLATFORM_NEO4J_URI=bolt://localhost:7687
PLATFORM_NEO4J_USER=neo4j
PLATFORM_NEO4J_PASSWORD=${GRAPH_PASSWORD}

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

Rules:

Never commit .env.

Never place secrets in source, tests, README examples, frontend bundles, logs, or public errors.

URI-reserved characters in credentials must be URL-encoded.

PLATFORM_CATALOG_PATH is optional because the default resolves to backend/config/data_assets.yaml from the documented backend working directory.

16. Installation

Prerequisites

Ubuntu 22.04 or compatible
Docker and Docker Compose
Python >=3.13,<3.14
Poetry 2.4 or compatible
NVM
Node.js version from frontend/.nvmrc
npm version compatible with the lock file

Backend

cd backend
poetry install

Frontend

cd frontend
nvm use
npm ci

Use the lock files. Do not manually install project packages with ad hoc pip or npm install commands.

17. Start the Platform

Terminal 1 — infrastructure

From the repository root:

docker compose up -d
docker compose ps

Terminal 2 — backend

cd backend

poetry run uvicorn return_platform.asgi:app \
  --env-file ../.env \
  --host 0.0.0.0 \
  --port 8000 \
  --reload

Terminal 3 — frontend

cd frontend
nvm use
npm run dev -- --host 0.0.0.0

Endpoints:

Backend liveness:  http://localhost:8000/health/live
Backend readiness: http://localhost:8000/health/ready
Data Console API:  http://localhost:8000/data-console/v1/overview
Frontend:          http://localhost:5173

18. Quality Gates

Required backend gate

Run from backend/:

poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv

Do not weaken Ruff, mypy, Pydantic strictness, or tests to force a pass.

Focused latest writer gate

poetry run ruff format --check \
  src/return_platform/data_platform/graph/commands.py \
  src/return_platform/data_platform/graph/writer.py \
  tests/test_customer_neo4j_command_builder.py \
  tests/test_customer_neo4j_writer.py

poetry run ruff check \
  src/return_platform/data_platform/graph/commands.py \
  src/return_platform/data_platform/graph/writer.py \
  tests/test_customer_neo4j_command_builder.py \
  tests/test_customer_neo4j_writer.py

poetry run mypy --no-incremental \
  src/return_platform/data_platform/graph/commands.py \
  src/return_platform/data_platform/graph/writer.py \
  tests/test_customer_neo4j_command_builder.py \
  tests/test_customer_neo4j_writer.py

poetry run pytest \
  tests/test_customer_neo4j_command_builder.py \
  tests/test_customer_neo4j_writer.py \
  -vv

Focused data-platform gate

poetry run pytest \
  tests/test_mapping_contracts.py \
  tests/test_mapping_relationship_direction.py \
  tests/test_mapping_physical_path_scope.py \
  tests/test_mapping_configuration_loader.py \
  tests/test_mapping_handler_registry.py \
  tests/test_customer_mapping_handlers.py \
  tests/test_mapping_compiler.py \
  tests/test_customer_document_normalizer.py \
  tests/test_customer_mongodb_source_adapter.py \
  tests/test_customer_graph_projection_materializer.py \
  tests/test_customer_neo4j_command_builder.py \
  tests/test_customer_neo4j_writer.py \
  -vv

Frontend

cd frontend
nvm use

npm run lint
npm run typecheck
npm run build
npm run test

Full frontend gate:

npm run check

Coverage is diagnostic and must not be reported without the exact current command output:

poetry run pytest \
  --cov=return_platform \
  --cov-report=term-missing \
  --cov-report=html \
  -vv

Broader coverage expansion is deferred until the planned implementation sequence is complete. Ruff, strict mypy, and existing pytest gates remain mandatory for every module.

19. Recorded Evidence

Stage 2 infrastructure

Observed on July 18, 2026:

Dependency

Status

Observed latency

MongoDB

Healthy

3 ms

Neo4j

Healthy

0 ms

SQL Server

Healthy

68 ms

Temporal

Healthy

4 ms

Valkey

Healthy

0 ms

Partial degradation evidence:

Valkey status = UNAVAILABLE
error_code = CONNECTION_REFUSED
meta.partial = true

Healthy dependency results remained visible.

SQL Server inventory

Observed on July 20, 2026:

Database:       return_platform
Observed at:    2026-07-20T04:31:06.907334+00:00
Visible empty:  True
Visible tables: 0
Visible views:  0
Schemas:        0

Classification:

SANDBOX_VALIDATED

MongoDB inventory

Observed on July 20, 2026:

Database:            return_platform
Observed at:         2026-07-20T05:20:35.748940+00:00
Visible empty:       True
Visible collections: 0
Visible indexes:     0

Classification:

SANDBOX_VALIDATED

Drift

Observed output:

Catalog version:        1.0
Analyzed at:            2026-07-20T05:50:18.209858+00:00
Complete evidence:      True
Drift free:             True
Confirmed drift count:  0
Not evaluated count:    0
Total records:          0

Classification:

IMPLEMENTED; LIVE OUTPUT OBSERVED

The exit code was not captured.

Customer graph writer correction

Focused suite:

Customer Neo4j command-builder + writer: 53 passed

Classification:

FOCUSED_PYTEST_VALIDATED
REPOSITORY RUFF/MYPY/FULL PYTEST PENDING
LIVE NEO4J EXECUTION NOT VALIDATED

20. Security Rules

Never commit .env.

Never repeat password values in tests or documentation.

Never expose raw driver errors.

Never log SecretStr.get_secret_value().

Never place production credential defaults in settings.

Never expose infrastructure credentials or DSNs to the frontend.

Never accept arbitrary SQL, MongoDB filters, Cypher, Python, or import paths from configuration.

Never infer ownership from a physical database object.

Never write to source-system assets.

Never treat Neo4j as authoritative business state.

Never use display names, list positions, or mutable attributes as identity.

Never treat inventory bin_location as a Return Bay.

Never add Package or PPLTracking to graph v1 without approved identity and join evidence.

Preserve asyncio.CancelledError.

Treat unknown commit outcomes as unknown; do not retry blindly.

Treat deprecation warnings as failures.

21. Reconstruction Procedure

From a clean clone:

git clone <repository-url>
cd <repository-directory>

Create the root .env using the safe blueprint.

Confirm the production catalog remains truthful:

cat backend/config/data_assets.yaml

Start infrastructure:

docker compose up -d
docker compose ps

Install and validate backend:

cd backend
poetry install

poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv

Run live metadata validation only against the intended sandbox:

poetry run python run_live_sql_inventory.py
poetry run python run_live_mongo_inventory.py
poetry run python run_live_drift.py
printf "drift_exit_code=%s\n" "$?"

Start backend:

poetry run uvicorn return_platform.asgi:app \
  --env-file ../.env \
  --host 0.0.0.0 \
  --port 8000

Install and validate frontend:

cd ../frontend
nvm use
npm ci
npm run check
npm run dev -- --host 0.0.0.0

Verify:

curl -s http://localhost:8000/health/live
curl -s http://localhost:8000/health/ready
curl -s http://localhost:8000/data-console/v1/overview

22. Known Limitations

The production asset catalog is intentionally empty.

The Customer mapping profile references a future approved Customer CDM asset that is not yet in the production catalog.

No live Customer source document has been fetched through the new adapter.

No graph constraints, nodes, or relationships have been live validated through the new writer.

No graph read-back validator exists.

No second-run idempotency proof exists.

Writer evidence is returned but not yet persisted to Platform MongoDB.

No Temporal activity owns source/graph timeout and retry policy.

No cross-document Customer or CustomerAccount collision detector exists.

Order-line immutability remains unconfirmed.

OMC product bridging remains unresolved when source evidence is missing.

Actual carrier tracking remains optional legacy evidence.

Package and PPLTracking remain excluded from graph v1.

Inventory API and Data Ownership frontend pages remain unimplemented.

The customer return workflow and customer-facing return UI remain unimplemented.

Restart-durability and broader hardening evidence remain deferred.

23. Current Phase and Next Step

Current phase

Stage 1 — Frontend/API foundation:            COMPLETE
Stage 2 — Infrastructure visibility:          BASIC ACCEPTANCE PASSED
Stage 3 — Governance/catalog:                 COMPLETE
Stage 3 — SQL inventory:                      SANDBOX_VALIDATED
Stage 3 — Mongo inventory:                    SANDBOX_VALIDATED
Stage 3 — Drift:                              IMPLEMENTED; LIVE OUTPUT OBSERVED
Stage 3 — Bounded sampling:                   CONTRACT_TESTED
Stage 3 — Inventory API/UI:                   NOT IMPLEMENTED

Canonical data-model implementation:          COMPLETE
Versioned mapping contracts:                  COMPLETE
Customer mapping configuration:              COMPLETE
Handler registry:                            COMPLETE
Multi-file mapping loader:                   COMPLETE
Customer mapping compiler:                   COMPLETE
Customer in-memory normalization:             COMPLETE
Customer MongoDB source adapter:              COMPLETE
Customer graph materialization:               COMPLETE
Customer Neo4j command builder:               COMPLETE
Customer Neo4j writer implementation:         COMPLETE
Latest writer repository revalidation:        PENDING
Live Customer source lookup:                  BLOCKED_EXTERNAL_DEPENDENCY
Live Neo4j graph write:                       NOT VALIDATED
Graph read-back validation:                   NOT IMPLEMENTED
Platform graph-evidence persistence:          NOT IMPLEMENTED
Temporal return workflow:                     NOT IMPLEMENTED
Customer return frontend:                     NOT IMPLEMENTED
Scenario runner:                              NOT IMPLEMENTED

Immediate execution target

First, integrate the latest writer correction and run:

cd backend

poetry run ruff format --check src tests
poetry run ruff check src tests
poetry run mypy --no-incremental src tests
poetry run pytest -vv

Record the exact outputs and exit codes in this README.

Only after those gates pass, implement a sandbox-only Customer graph validation runner and deterministic read-back contracts.

The next step must:

Load the approved Customer mapping profile.

Compile it against an explicit sandbox-only governance catalog.

Accept one controlled Customer source document.

Normalize it in memory.

Materialize graph parameters.

Build fixed commands.

Prepare constraints.

Execute the atomic Customer graph transaction.

Read back Customer, CustomerAccount, and HAS_ACCOUNT through fixed parameterized validation queries.

Compare exact canonical keys and mandatory provenance.

Prove second-run idempotency.

Emit safe evidence and a process exit code.

The next step must not:

mutate the production asset catalog
persist evidence to Platform MongoDB
introduce Temporal orchestration
add automatic retries
add SalesOrder graph execution
add Package
add PPLTracking