# Order Discovery: Context-Driven Agents and Configurable Graph Sync Plan

**Status:** Authoritative revised implementation plan  
**Date:** 2026-08-02  
**Scope:** Order Discovery, canonical order synchronization, independent agent contracts, metadata-driven source models, configurable graph schema, schema governance, and configurable system persistence  

This document incorporates the Ferguson Returns proposal, the manual source mappings, all Order Discovery and Order Analysis findings, the current repository assessment, and the architecture clarifications recorded after the first consolidated plan.

This document supersedes `ORDER_DISCOVERY_CANONICAL_ORDER_SYNC_IMPLEMENTATION_PLAN.md` where the two differ.

## 1. Final architecture decisions

### 1.1 Operational users

The application is not described as customer-facing. Its interactive operational users are:

```text
ASSOCIATE | SALES_REPRESENTATIVE | CUSTOMER_CARE
```

Customers may provide order evidence to one of these users, but customers are not assumed to operate this workflow directly.

Administrators and data stewards participate in source onboarding, schema approval, configuration release, exception handling, and production support. Role, branch, account/logon, job, and channel authorization are carried in context and enforced for every operational user.

### 1.2 Canonical order identity

Every durable order graph write uses:

```text
fullOrderId = ACCOUNT_OR_LOGON + "*" + ORDERNUMBER
```

Every confirmable order-line graph write uses:

```text
fullOrderLineId = fullOrderId + "*" + IMMUTABLE_LINE_NUMBER
```

`salesInv` may physically contain:

```text
ACCOUNT_OR_LOGON*ORDERNUMBER
ACCOUNT_OR_LOGON*ORDERNUMBER*LINENUMBER
```

The first form can identify an order/header record. The second can identify a line record. The active source mapping must declare the physical record type and identity composition. Runtime code must not guess the record type from delimiter count alone.

Order number, web order number, Trilogie number, source transaction ID, and physical MongoDB `_id` remain separate evidence fields. None replaces `fullOrderId`.

### 1.3 Full-order sync

`FULL_ORDER_SYNC` requires exactly one validated `fullOrderId`.

It reads authoritative data for that order and hydrates the complete, minimal graph projection required to finish discovery, order analysis, return request preparation, fulfillment tracking, staging, and handoff.

It cannot start directly from tracking, invoice, phone, email, PO, SKU, fuzzy text, or an unscoped raw order number.

### 1.4 Partial-order sync

`PARTIAL_ORDER_SYNC` accepts an approved strong anchor, resolves zero, one, or more `fullOrderId` values, and synchronizes a bounded discovery projection for those resolved orders.

Partial describes projection depth, not identity quality. Every synchronized candidate is keyed by `fullOrderId`.

After an operational user selects an order, the orchestrator requests `FULL_ORDER_SYNC` for that selected `fullOrderId` before discovery can be sealed.

### 1.5 Context-only independent agents

Every business agent is independently deployable, independently testable, and independently retryable. Agents never depend on another agent's process memory, database, private state, or transcript.

Each agent:

1. receives one versioned `ContextEnvelope`;
2. validates required context type, version, authorization scope, and configuration release;
3. invokes only the capabilities allowed by that context;
4. produces a new versioned context plus bounded events and evidence references;
5. does not call another business agent directly;
6. does not mutate another agent's context;
7. is idempotent for the same input context version and idempotency key.

The Return Session Orchestrator routes context references. Context is the only business handoff mechanism.

### 1.6 Generic metadata-driven models

Adding or changing a source table, collection, API, field, graph node, relationship, index, or projection profile must not require a new hard-coded Python/Pydantic business model for every variation.

The coded runtime kernel contains only stable generic contracts:

```text
ContextEnvelope
SourceDescriptor
SourceRecordEnvelope
CanonicalEntityEnvelope
RelationshipEnvelope
MappingDefinition
GraphSchemaDefinition
ProjectionProfile
SyncRequest
SyncResult
SchemaProposal
ConfigurationRelease
```

Source-specific and graph-specific structure is configuration:

- connection and source type;
- database/schema/table or MongoDB database/collection;
- API resource when applicable;
- record identity expression;
- field paths and data types;
- normalization and validation rules;
- joins and cardinality;
- canonical entity mappings;
- graph labels, keys, properties, relationships, constraints, and indexes;
- projection profiles and required provenance;
- anchor resolver definitions;
- authorization, retention, masking, and sensitivity rules.

The runtime compiles approved configuration into validators and safe execution plans. It never executes arbitrary code, queries, or Cypher produced by an LLM.

### 1.7 Configurable graph schema and governed schema-design agent

Graph synchronization can run only against an approved, active, versioned graph schema release.

When no graph schema exists, a source changes, a mapping becomes incompatible, or an administrator requests a new projection, a new `GraphSchemaDesignAgent` creates a draft proposal from configured source metadata and the business question.

The agent does not activate schemas or run migrations. It gathers missing decisions, produces a validated proposal, and routes it for human approval.

### 1.8 Minimal graph projection

The graph is a return-process projection, not a source replica. Store only fields required to:

1. identify authorized customer/account, order, and line;
2. disambiguate order candidates;
3. determine return evidence and remaining returnable quantity;
4. complete support, fulfillment, staging, and handoff;
5. prove provenance, freshness, authorization, confirmation, and audit state.

## 2. Independent agent model

| Agent | Required input context | Output context | Prohibited dependency |
|---|---|---|---|
| Return Session Orchestrator | `SessionCommandContext` | `ReturnSessionContext` and routing events | Business decisions belonging to another agent |
| Order Discovery Agent | `ReturnSessionContext` plus `DiscoveryInputContext` | `DiscoveryContext` | Direct source reads or private state from other agents |
| Order Analysis Agent | Sealed `DiscoveryContext` | `OrderAnalysisContext` | Unconfirmed candidate or direct Discovery Agent call |
| Return Workflow Agent | Sealed `DiscoveryContext` plus approved `OrderAnalysisContext` | `ReturnRequestContext` | Discovery implementation details |
| Return Fulfillment Agent | `ReturnRequestContext` plus support events | `FulfillmentTrackingContext` | Return Workflow Agent memory |
| Bay Allocation Agent | `FulfillmentTrackingContext` | `BayStagingContext` | Fulfillment Agent memory |
| Learning Agent | Finalized context references and bounded events | `LearningFeedbackContext` | Raw transcripts or another agent's hidden reasoning |
| Graph Schema Design Agent | `SchemaDesignContext` | `SchemaProposalContext` | Direct schema activation, migration, or production sync |

### 2.1 Standard context envelope

```yaml
contextId: immutable-id
contextType: DiscoveryContext
schemaVersion: "2"
contextVersion: 7
sessionId: session-id
correlationId: correlation-id
actor:
  type: ASSOCIATE
  actorId: opaque-actor-id
authorizationScopeRef: opaque-scope-reference
configurationReleaseId: config-release-id
graphSchemaReleaseId: graph-schema-release-id
createdAt: timestamp
createdBy: ORDER_DISCOVERY_AGENT
previousContextRef: immutable-reference
idempotencyKey: stable-key
evidenceRefs: []
payload: {}
```

Rules:

- context versions are immutable;
- updates create a new version with optimistic concurrency;
- payload validation uses the schema referenced by `contextType` and `schemaVersion`;
- authorization is rechecked when context is consumed;
- large artifacts and raw source documents remain outside context;
- agents receive only fields required for their capability;
- context expiry and retention are configuration-driven.

### 2.2 Agent communication

```text
Agent A -> output Context A -> Orchestrator -> input reference -> Agent B
```

Not allowed:

```text
Agent A -> direct call -> Agent B
Agent B -> Agent A database
Agent B -> Agent A in-memory history
Agent B -> unrestricted transcript
```

## 3. Generic configuration architecture

### 3.1 Source descriptor

Example MongoDB configuration:

```yaml
sourceId: sales-invoice-v3
kind: MONGODB
connectionRef: tds-source
database: configured_database
collection: salesInv
schemaVersionSelector: eventMeta.writerSchemaVersion
recordProfiles:
  - profileId: embedded-order
    match: { field: eventMeta.recordType, equals: ORDER }
    identity:
      entity: SalesOrder
      expression: "join('*', accountLogon, orderNumber)"
  - profileId: line-document
    match: { field: eventMeta.recordType, equals: ORDER_LINE }
    identity:
      entity: OrderLine
      expression: "join('*', accountLogon, orderNumber, lineNumber)"
```

Example SQL configuration:

```yaml
sourceId: omc-v1-returns
kind: SQLSERVER
connectionRef: omc-source
database: configured_database
schema: dbo
table: returns
primaryKeyFields: [return_id]
watermarkField: updated_at
```

The configuration language must use an allowlisted expression library. No arbitrary Python, JavaScript, SQL, or shell expression is allowed.

### 3.2 Field and canonical mapping

```yaml
mappingId: sales-order-v3
sourceId: sales-invoice-v3
sourceProfile: embedded-order
targetEntity: SalesOrder
identityField: fullOrderId
fields:
  - target: accountLogon
    source: eventMeta.accountLogon
    required: true
  - target: rawOrderNumber
    source: eventMeta.orderNumber
    required: true
    normalization: PRESERVE_IDENTIFIER
  - target: customerReference
    source: salesHdr.salesHdrData.orderCust
  - target: sourceOrderDocumentId
    source: _id
    required: true
```

Mappings declare field type, required/optional status, default prohibition, sensitivity, masking, transform, validation, and provenance. Missing required fields fail the mapping; they are not silently synthesized.

### 3.3 Graph schema definition

```yaml
graphSchemaId: order-discovery-v2
nodes:
  - entity: SalesOrder
    label: SalesOrder
    key: fullOrderId
    properties:
      - fullOrderId
      - rawOrderNumber
      - orderDate
      - orderStatus
      - customerReference
      - sourceRevision
      - graphSyncedAt
  - entity: OrderLine
    label: OrderLine
    key: fullOrderLineId
relationships:
  - type: CONTAINS
    from: SalesOrder.fullOrderId
    to: OrderLine.fullOrderId
constraints:
  - unique: SalesOrder.fullOrderId
  - unique: OrderLine.fullOrderLineId
```

The schema compiler validates referenced entities, property types, join compatibility, cardinality, uniqueness, cycles, required provenance, privacy policy, and migration compatibility.

### 3.4 Generic runtime model

The execution pipeline is:

```text
Active source descriptors
  -> connector query plan
  -> SourceRecordEnvelope
  -> active mapping definitions
  -> CanonicalEntityEnvelope / RelationshipEnvelope
  -> active GraphSchemaDefinition
  -> property-allowlisted graph commands
  -> graph readback and verification
```

Source onboarding normally adds configuration, not new application classes.

Custom plugins are allowed only when the mapping language cannot safely express a required deterministic transformation. Plugins require code review, security review, tests, explicit registration, and a versioned capability declaration.

## 4. Graph Schema Design Agent

### 4.1 Responsibility

The `GraphSchemaDesignAgent` helps define or revise a graph schema using:

- approved source descriptors and discovered source metadata;
- existing canonical vocabulary and identity rules;
- the business question or workflow capability requested;
- current graph schema and usage evidence when present;
- privacy, authorization, retention, and minimal-data policies;
- synchronization and query performance requirements.

It produces a `SchemaProposalContext`. It does not write production schema, activate configuration, or initiate backfill.

### 4.2 Trigger conditions

Start schema-design workflow when:

- no graph schema release exists for a requested capability;
- a configured source/table/collection is added or removed;
- source introspection detects a breaking shape change;
- a required field or join is missing from active mappings;
- a graph query cannot be supported by the current schema;
- an administrator requests a new node, relationship, or projection profile;
- sync verification detects schema/configuration incompatibility.

Graph sync fails closed with `SCHEMA_NOT_ACTIVE` or `SCHEMA_INCOMPATIBLE` until an approved release exists.

### 4.3 Question routing

Questions are routed by decision type.

Ask an associate, sales representative, or customer-care subject-matter expert about:

- which business question must be answered;
- what information they actually have during a real interaction;
- which fields are useful for candidate confirmation;
- what ambiguity they can resolve conversationally;
- which workflow step consumes the data.

Ask an administrator, data steward, source owner, security owner, or architect about:

- source-of-truth and field semantics;
- identity, join, and cardinality;
- schema/table/collection ownership and versions;
- authorization and retention;
- sensitivity and masking;
- freshness, volume, indexing, and performance;
- migration, backfill, and rollback approval.

Operational users must not be asked to approve technical schema details outside their role. Administrators must not invent business meaning without the appropriate subject-matter owner.

### 4.4 Schema proposal lifecycle

```text
DRAFT_REQUESTED
  -> SOURCE_METADATA_COLLECTED
  -> BUSINESS_QUESTIONS_PENDING
  -> TECHNICAL_QUESTIONS_PENDING
  -> DRAFT_GENERATED
  -> STATIC_VALIDATION
  -> SANDBOX_SIMULATION
  -> HUMAN_REVIEW
  -> APPROVED
  -> RELEASE_CREATED
  -> MIGRATION_PLANNED
  -> ACTIVE
```

Alternative terminal states:

```text
REJECTED
NEEDS_SOURCE_CONTRACT
PRIVACY_REJECTED
MIGRATION_UNSAFE
SUPERSEDED
```

### 4.5 Required proposal contents

The proposal includes:

- business questions and supported query patterns;
- source inventory and source-of-truth decisions;
- canonical identities and aliases;
- node, relationship, property, constraint, and index definitions;
- source-to-canonical and canonical-to-graph mappings;
- projection profiles for partial and full sync;
- privacy classification and prohibited fields;
- freshness and retention rules;
- expected cardinality and bounded-result rules;
- migration and backfill plan;
- compatibility and rollback analysis;
- unresolved questions and named approvers;
- generated validation and scenario tests.

### 4.6 Governance boundary

The agent may recommend. Only an authorized human can approve. A deterministic release service validates signatures, checksums, schemas, policies, and tests before activation.

No LLM-produced configuration is used by production graph sync until it belongs to an `ACTIVE` signed configuration release.

## 5. Strong-anchor resolution

### 5.1 Primary anchor families

| Anchor family | Resolution | Result |
|---|---|---|
| Order reference | Exact full order ID; exact raw order number within account/logon scope; approved web/source-reference mapping | Zero, one, or bounded many full order IDs |
| Shipment | Exact tracking -> configured shipment/order join -> validated order source | One or more full order IDs; line remains separately confirmable |
| Invoice | Exact invoice -> configured invoice-line/order join -> validated order source | One or more full order IDs because an invoice may span orders |
| Customer/account | Exact authorized customer/account or policy-approved HMAC contact lookup -> bounded recent orders | Bounded full order IDs |

### 5.2 Composite extensions

Customer PO, delivery ticket, return/RMA, and SKU may become eligible only when their configuration defines authoritative resolution, required scope, cardinality, indexes, and freshness.

Name, company, ZIP, address, description, color, brand, approximate date, partial identifier, and fuzzy text are narrowing evidence. They never independently authorize source synchronization or order confirmation.

### 5.3 Operational conversation

The Order Discovery Agent receives current `DiscoveryInputContext`, graph candidates, and approved question policy. Deterministic policy chooses the next allowed field. The model may phrase one concise question for the associate, sales representative, or customer-care user.

For multiple candidates, select the question with the highest expected separation using only configured, customer-answerable evidence available to the operational user.

## 6. Synchronization architecture

### 6.1 Full-order request

```yaml
requestType: FULL_ORDER_SYNC
fullOrderId: "ACCOUNT_OR_LOGON*ORDERNUMBER"
projectionProfile: RETURN_PROCESS_FULL
graphSchemaReleaseId: active-release
mappingReleaseId: active-release
freshnessRequirement: CONFIRMATION
authorizationScopeRef: opaque-reference
idempotencyKey: stable-key
```

### 6.2 Partial-order request

```yaml
requestType: PARTIAL_ORDER_SYNC
anchorType: INVOICE_NUMBER
anchorEvidenceRef: short-lived-reference
anchorDigest: approved-digest
projectionProfile: ORDER_DISCOVERY_CANDIDATE
candidateLimit: 5
graphSchemaReleaseId: active-release
mappingReleaseId: active-release
authorizationScopeRef: opaque-reference
idempotencyKey: stable-key
```

### 6.3 Sync prerequisites

Every request requires:

- active source descriptors;
- active field/canonical mappings;
- active graph schema release;
- active projection profile;
- valid authorization scope;
- compatible schema and mapping versions;
- successful provider/schema health validation.

Missing prerequisites do not trigger improvised runtime modeling. The request stops with a safe status and may create a `SchemaDesignContext` for the schema-design workflow.

### 6.4 Generic sync lifecycle

```text
REQUESTED
  -> CLAIMED
  -> CONFIGURATION_RESOLVED
  -> IDENTITY_RESOLVED
  -> SOURCE_READ
  -> MAPPED
  -> VALIDATED
  -> GRAPH_COMMITTED
  -> VERIFIED
  -> COMPLETED
```

Terminal states include:

```text
NO_MATCH
REQUIRES_NARROWING
RESULT_LIMIT_EXCEEDED
UNAUTHORIZED
SOURCE_UNAVAILABLE
SOURCE_SCHEMA_CHANGED
SCHEMA_NOT_ACTIVE
SCHEMA_INCOMPATIBLE
MAPPING_FAILED
IDENTITY_CONFLICT
GRAPH_WRITE_FAILED
VERIFICATION_FAILED
EXPIRED
```

### 6.5 Partial sync algorithm

1. Load the signed active configuration release.
2. Validate anchor eligibility, role, scope, and result cap.
3. Compile the configured connector and resolver plan.
4. Resolve the anchor to full order IDs.
5. Validate and deduplicate by full order ID.
6. Stop for narrowing when the cap is exceeded.
7. Read only fields declared by `ORDER_DISCOVERY_CANDIDATE`.
8. Map records through generic envelopes.
9. Validate against the active graph schema and privacy policy.
10. Upsert candidate projections by full order ID.
11. Read back candidates through the graph gateway.
12. Store result context and terminal status.

### 6.6 Full sync algorithm

1. Validate the exact full order ID and authorization.
2. Load the active source, mapping, graph schema, and projection releases.
3. Compile an exact order query plan; wildcard and fuzzy identity reads are prohibited.
4. Read configured order/header and line records.
5. Reject every record whose normalized identity does not equal the requested full order ID.
6. Read only configured related customer, shipment, invoice, product, location, and prior-return evidence.
7. Produce generic canonical entities and relationships.
8. Enforce immutable line identity, cardinality, provenance, sensitivity, and quantity rules.
9. Commit the minimal order subgraph.
10. Remove stale relationships only according to the approved replacement policy.
11. Read back and verify full order ID, source revision, mapping release, schema release, and projection digest.
12. Publish `FullOrderSyncContext` for the orchestrator.

### 6.7 Agent boundary

Agents use `AgentGraphGateway` fixed capabilities. They never receive a raw source connector or raw Neo4j driver.

On graph miss or staleness, an agent creates a sync-request context. A worker reads sources and writes the graph. The agent then receives the completed sync context and re-queries the graph.

## 7. Minimal graph schema for the return process

### 7.1 Initial business entities

The following are the initial approved vocabulary, expressed through configuration rather than one class per source shape:

```text
Customer
CustomerAccount
SalesOrder
OrderLine
Product
Shipment
Invoice
Location
PriorReturn
```

Initial relationships:

```text
(Customer)-[:HAS_ACCOUNT]->(CustomerAccount)
(CustomerAccount)-[:PLACED]->(SalesOrder)
(SalesOrder)-[:CONTAINS]->(OrderLine)
(OrderLine)-[:REFERENCES_PRODUCT]->(Product)
(SalesOrder)-[:SHIPPED_AS]->(Shipment)
(SalesOrder)-[:BILLED_AS]->(Invoice)
(SalesOrder)-[:SOLD_AT]->(Location)
(PriorReturn)-[:RETURNED_FROM]->(OrderLine)
```

Workflow/context entities may include:

```text
ReturnSession
EvidenceAnchor
CandidateSet
DiscoveryContext
DiscoveryLock
OrderAnalysisContext
ReturnRequestContext
FulfillmentTrackingContext
BayStagingContext
WorkflowEvent
AgentDecision
LearningFeedback
SyncRequest
SyncAttempt
SchemaProposal
ConfigurationRelease
```

The active schema may omit entities not required by the enabled workflow. The schema-design and approval process governs additions or changes.

### 7.2 Required provenance

Synchronized graph entities include when applicable:

```text
sourceSystem
sourceAsset
sourceRecordId
sourceSchemaVersion
sourceRevision
sourceUpdatedAt
graphSyncedAt
syncRequestId or backfillRunId
mappingReleaseId
graphSchemaReleaseId
identityQuality
evidenceDigest or opaque evidence reference
```

### 7.3 Prohibited data

Do not store in graph, contexts, candidates, locks, prompts, logs, or frontend projections:

- payment tokens, authorization codes, or card/account numbers;
- cardholder, expiry, or billing-address data;
- raw phone/email when a keyed digest is sufficient;
- raw source documents or unbounded payloads;
- full transcripts, unrestricted prompts/responses, or hidden reasoning;
- image, invoice, label, POD, or attachment bytes;
- secrets, credentials, worker internals, or unrelated telemetry.

## 8. Configurable system persistence

### 8.1 Providers

```text
NEO4J | MONGODB | POSTGRESQL | SQLSERVER
```

Business graph projection remains Neo4j. The control/context store provider is configurable unless a deployment explicitly chooses graph-backed control/context records.

### 8.2 Generic repositories

```text
ContextRepository
SyncRequestRepository
SyncAttemptRepository
DiscoveryLockRepository
ConfigurationReleaseRepository
SchemaProposalRepository
IdempotencyRepository
OutboxRepository
SchemaVersionRepository
```

Business logic depends on these interfaces, not provider names.

### 8.3 Automatic bootstrap

For the selected provider, startup/deployment must:

1. validate configuration and credentials;
2. acquire a provider-specific migration lock;
3. read schema version and checksums;
4. create missing collections/tables/labels, constraints, and indexes;
5. apply forward-only migrations;
6. introspect and verify the final schema;
7. record migration evidence;
8. fail closed on drift, missing objects, or unsupported newer versions.

`autoCreate=false` means validation-only. It never means continue with missing structures.

## 9. Current repository differences

| Current repository | Required target |
|---|---|
| Concrete canonical Pydantic classes encode a specific TDS order shape | Stable generic envelopes plus configuration-defined entity schemas; keep coded identity/security invariants |
| `GraphSyncScope.FULL` copies bounded top-N records from Mongo and SQL | Rename to administrative backfill; add exact `FULL_ORDER_SYNC` and anchor-based `PARTIAL_ORDER_SYNC` |
| API returns `202` while awaiting sync inline | Durable enqueue, worker claim, status context, and asynchronous completion |
| `SalesOrder` is keyed by raw sales-order number | Key and join by `fullOrderId` |
| Source sync hard-codes collection names, fields, and Cypher | Compile connector, mapping, projection, and graph commands from signed active configuration |
| Graph projection YAML defines only Customer and CustomerAccount | Make complete source, canonical, graph, relationship, index, and projection definitions configurable |
| Sync pipeline YAML covers only customer foundation | Add generic identity-resolution, partial projection, full hydration, verification, and cleanup stages |
| Associate flow reads Mongo directly and performs inline Neo4j upsert | Agent creates sync context, worker performs sync, agent re-queries graph |
| Candidate/source integrity services contain unconditional stubs | Implement real generic services and verification receipts |
| Synthetic array-position line IDs can be created | Require configured immutable line number; otherwise `UNCONFIRMABLE` |
| Invoice/location/prior-return sources are missing from current order sync | Add through source descriptors and mappings, not new sync branches |
| Control persistence is hard-coded to Mongo collections and assumed SQL tables | Provider-neutral repositories and automatic bootstrap |
| No governed missing-schema workflow exists | Add independent Graph Schema Design Agent and signed schema-release lifecycle |
| Agents are organized in one broader runtime flow | Enforce context-only agent boundaries and independent contract tests |

## 10. Implementation plan

### Phase 0 - Freeze invariants and generic contracts

Deliver:

- full order and full line ID contracts;
- operational-user roles and authorization scope;
- generic envelope contracts;
- immutable context/version/idempotency rules;
- configuration and schema-release signatures;
- rename of legacy broad `FULL` mode.

Primary repository areas:

- `backend/src/return_platform/canonical/`
- `backend/src/return_platform/agents/contracts.py`
- configuration models and JSON schemas
- Graph Sync API contracts

### Phase 1 - Build source metadata and mapping runtime

Deliver:

- source descriptor schema;
- MongoDB, SQL Server, PostgreSQL, and API connector interfaces;
- allowlisted mapping expression language;
- source introspection and schema fingerprints;
- generic source/canonical envelopes;
- mapping compiler, validator, and safe query-plan compiler;
- initial configuration profiles for `salesInv`, customer, shipment, invoice, product, location, and OMC sources.

Hard-coded adapter classes are replaced by configuration profiles. Custom plugins remain an exception path.

Primary repository areas:

- new `backend/src/return_platform/data_platform/metadata_runtime/`
- `backend/config/data_platform/source_assets.yaml`
- mapping compiler and schema registry

### Phase 2 - Build Graph Schema Design Agent and release workflow

Deliver:

- `SchemaDesignContext` and `SchemaProposalContext`;
- source metadata and business-question intake;
- role-aware question routing;
- proposal generator using existing vocabulary first;
- graph schema static validator;
- sandbox schema/mapping simulation;
- human review and approval workflow;
- signed immutable configuration releases;
- fail-closed integration with sync.

Primary repository areas:

- new `backend/src/return_platform/agents/graph_schema_design.py`
- new schema-governance services and APIs
- Data Console schema review UI
- configuration release repository

### Phase 3 - Implement graph schema compiler and migrations

Deliver:

- generic node/relationship/constraint/index definitions;
- projection-profile compiler;
- schema compatibility and diff engine;
- forward migration, backfill requirement, and rollback classification;
- migration after current order-discovery graph migrations;
- removal of raw order-number uniqueness after compatibility rollout.

Primary repository areas:

- `backend/config/data_platform/graph_projection.yaml`
- `backend/config/data_platform/sync_pipelines.yaml`
- graph schema manager and mapping compiler
- graph migrations and tests

### Phase 4 - Implement configurable context/control persistence

Deliver:

- provider-neutral repositories;
- Neo4j, MongoDB, PostgreSQL, and SQL Server implementations;
- automatic bootstrap, migration lock, checksums, and drift detection;
- common provider contract tests.

Primary repository areas:

- new `backend/src/return_platform/persistence/system_store/`
- resources, startup, and settings
- provider migration directories

### Phase 5 - Implement durable generic sync orchestration

Deliver:

- durable sync request/attempt lifecycle;
- configuration release resolution;
- lease, retry, cancellation, idempotency, and recovery;
- generic connector -> mapping -> graph execution;
- graph readback and digest verification;
- schema-missing/incompatible handoff to schema-design workflow.

Primary repository areas:

- refactor `data_platform/graph/sync_service.py`
- replace operational-generation graph-sync stubs
- update Data Console Graph Sync API

### Phase 6 - Implement strong-anchor partial sync

Deliver:

- configured order, shipment, invoice, and customer/account resolvers;
- authorized resolution to bounded full order IDs;
- `ORDER_DISCOVERY_CANDIDATE` projection;
- candidate-set context;
- smart narrowing questions for operational users;
- no direct source access from agent code.

Primary repository areas:

- replace `operations/order_discovery/source_operations.py`
- replace `operations/order_discovery/candidate_retriever.py`
- decompose source fallback from `associate_flow.py`

### Phase 7 - Implement full-order sync

Deliver:

- exact full-order request;
- generic configured reads for order/header and line shapes;
- minimal related evidence based on `RETURN_PROCESS_FULL`;
- immutable line identity enforcement;
- atomic graph update, approved stale-edge cleanup, and readback;
- `FullOrderSyncContext` bound to source, mapping, and graph-schema revisions.

### Phase 8 - Convert all business agents to context-only operation

Deliver:

- independent input/output context schemas for every agent;
- orchestrator routing by context preconditions;
- removal of direct agent-to-agent and cross-agent repository dependencies;
- graph gateway fixed queries/commands;
- architecture tests prohibiting source clients and raw graph drivers in agents;
- retry and replay tests per agent.

### Phase 9 - Correct discovery confirmation and concurrency

Deliver:

- selected candidate triggers full-order sync;
- sealed discovery binds full order ID, full line ID, source revision, mapping release, and graph schema release;
- source change requires refresh and reconfirmation;
- expiring line lease, optimistic concurrency, and idempotent confirmation;
- synthetic or ambiguous line identity cannot be confirmed.

### Phase 10 - Complete Order Analysis evidence

Deliver:

- ordered, shipped, invoiced, and prior-return quantity evidence;
- UOM-aware deterministic remaining-returnable calculation;
- V1/V2 return status policy and provenance;
- immutable `OrderAnalysisContext`;
- no payment/refund credentials in contexts or graph.

### Phase 11 - Backfill, shadow validation, and cutover

Deliver:

- administrative backfill separate from full and partial order sync;
- active-schema-release-bound backfill;
- shadow comparison of identities, candidates, revisions, relationships, and quantities;
- cohort rollout for graph-only/context-only agents;
- retirement of hard-coded sync branches, direct-source fallback, old raw identity, and unconditional stubs.

## 11. Required APIs or commands

```text
POST /order-sync/full
POST /order-sync/partial
GET  /order-sync/requests/{requestId}
POST /order-sync/requests/{requestId}/cancel

POST /schema-design/requests
GET  /schema-design/requests/{requestId}
POST /schema-design/requests/{requestId}/answers
POST /schema-design/proposals/{proposalId}/submit
POST /schema-design/proposals/{proposalId}/approve
POST /schema-design/proposals/{proposalId}/reject
POST /configuration-releases/{releaseId}/activate

POST /data-console/v1/graph-sync/backfills
POST /data-console/v1/system-schema/validate
POST /data-console/v1/system-schema/apply
```

Approval and activation are separate permissions. The Schema Design Agent has neither.

## 12. Real-time acceptance scenarios

| Scenario | Required behavior |
|---|---|
| Associate supplies exact full order ID | Full ID validates and full-order sync completes before line confirmation |
| Sales representative supplies raw order number | Resolver applies authorized account/logon scope and never assumes global uniqueness |
| Customer-care user supplies tracking number | Partial sync resolves full order IDs, presents bounded graph candidates, then full-syncs the selection |
| Invoice spans multiple orders | Candidates are grouped by full order ID and the operational user selects/narrows |
| Customer/account has too many orders | No broad hydration; ask one configured date/PO/job/SKU question |
| Same order number exists under two logons | Two distinct graph orders; no merge or cross-account disclosure |
| Source collection changes a required field path | Fingerprint mismatch stops sync and starts governed schema/mapping review |
| New collection is configured with no graph schema | Schema Design Agent drafts proposal and questions; sync remains `SCHEMA_NOT_ACTIVE` |
| Schema proposal needs business meaning | Route question to associate, sales representative, or customer-care SME |
| Schema proposal needs identity/cardinality/security decision | Route question to admin/data steward/source/security owner |
| Proposal is generated but not approved | No migration, activation, sync, or backfill occurs |
| Approved schema adds a relationship | Release service validates, migrates, activates, then sync uses the new release |
| Agent is replayed with the same context | Same logical result; no duplicate side effects |
| One agent is unavailable | Other agents remain independently runnable when their required contexts exist |
| Graph unavailable | Agent never bypasses graph; return retryable context status |
| Source line lacks immutable number | Evidence may display, but line is unconfirmable |
| Source revision changes before confirmation | Full sync refreshes and operational user reconfirms changed facts |

## 13. Test and validation plan

### 13.1 Context and agent independence

- every agent runs from a serialized context fixture without another agent process;
- no business agent imports another business agent service;
- no business agent reads another agent's repository/table/collection;
- context schema/version/precondition failures are explicit;
- retries and replay are idempotent;
- authorization is revalidated on context consumption.

### 13.2 Metadata-driven model runtime

- onboard a new Mongo collection using configuration only;
- onboard equivalent SQL table using configuration only;
- add optional field without code changes;
- reject missing required identity field;
- reject unsafe expression or arbitrary query content;
- detect source schema drift;
- preserve identifier letters and leading zeroes;
- validate join cardinality and type compatibility.

### 13.3 Schema Design Agent

- no-schema, changed-schema, and new-business-question triggers;
- correct role-aware question routing;
- minimal-data recommendation;
- prohibited-field rejection;
- unresolved identity/cardinality blocks approval;
- sandbox simulation receipts;
- agent cannot approve, activate, migrate, or backfill;
- signed release required for sync;
- superseded/rejected proposal cannot activate.

### 13.4 Identity, resolver, and sync

- duplicate raw number across logons;
- header and line-document source profiles;
- order, shipment, invoice, and customer/account anchors;
- zero, one, many, unauthorized, and over-limit outcomes;
- partial projection followed by selected full hydration;
- immutable line enforcement;
- stale revision and reconfirmation;
- idempotent worker retry and lease recovery;
- graph readback verifies identity and release versions.

### 13.5 Provider bootstrap

- clean Neo4j, MongoDB, PostgreSQL, and SQL Server stores;
- idempotent repeated bootstrap;
- concurrent migration lock;
- drift and unsupported-version failure;
- validation-only mode;
- repository behavior parity across providers.

### 13.6 Privacy and security

- prohibited fields never enter graph/context/log/prompt/API projections;
- sensitive anchors use approved digests and expiring evidence references;
- arbitrary SQL, Cypher, source queries, and mapping code are rejected;
- every graph query and sync respects actor authorization scope;
- schema proposal cannot weaken policy without explicit security approval.

## 14. Definition of done

Implementation is complete only when:

- all interactive language and role contracts use associate, sales representative, or customer care;
- every business agent works independently from versioned context only;
- no direct business agent-to-agent call or private-state dependency exists;
- full sync requires one validated `ACCOUNT/LOGON*ORDERNUMBER` full order ID;
- partial sync resolves strong anchors to full order IDs before graph writes;
- selected order receives verified full hydration before discovery is sealed;
- source/table/collection changes normally require configuration, not new data-model classes;
- graph nodes, relationships, properties, constraints, indexes, and projection profiles are configurable;
- graph sync compiles execution from an approved active schema/mapping release;
- missing or incompatible schema starts a governed Schema Design Agent workflow and blocks sync;
- the Schema Design Agent asks role-appropriate questions and cannot self-approve or activate;
- control/context storage bootstraps correctly on configured Neo4j, MongoDB, PostgreSQL, or SQL Server;
- graph contains only minimal process data and required provenance;
- immutable line identity, source revision, authorization, concurrency, privacy, and recovery tests pass;
- hard-coded direct-source discovery fallbacks and unconditional stubs are removed;
- administrative backfill remains distinct from full-order and partial-order synchronization.

## 15. Governing invariants

```text
No validated fullOrderId
-> no SalesOrder graph write.
```

```text
No ACTIVE signed graph schema and mapping release
-> no graph synchronization.
```

```text
No verified full-order sync at the required source revision
-> no sealed DiscoveryContext.
```

```text
No versioned input context
-> no business agent execution.
```

```text
No authorized human approval
-> no schema activation or migration.
```
