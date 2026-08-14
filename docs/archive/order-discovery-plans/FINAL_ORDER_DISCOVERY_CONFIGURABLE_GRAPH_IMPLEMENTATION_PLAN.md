# Final Order Discovery, Independent Agents, and Configurable Graph Implementation Plan

**Status:** Final authoritative implementation plan  
**Date:** 2026-08-02  
**Scope:** Operational-user Order Discovery, canonical order synchronization, independent context-only agents, metadata-driven source and graph models, Configuration-page schema design, existing-schema redesign, schema import/export, configurable system persistence, Order Analysis evidence, migration, testing, and rollout  

This file consolidates and supersedes all earlier Order Discovery, field-correction, manual-mapping, graph-first, canonical-sync, configurable-graph, and Graph Schema Design Agent plans where they differ.

## 1. Final architecture decisions

### 1.1 Operational users

The platform is operated by:

```text
ASSOCIATE | SALES_REPRESENTATIVE | CUSTOMER_CARE
```

It is not described as a customer-facing application. A customer may provide evidence to an operational user, but the customer is not assumed to use the workflow directly.

Administrators, data stewards, source owners, security owners, and architects participate in configuration, schema approval, release activation, migration, and exception handling.

Role, branch, account/logon, job, source, and channel authorization are carried in context and revalidated at every agent, query, synchronization, import, approval, and activation boundary.

### 1.2 Canonical order and line identity

Every durable order graph write uses:

```text
fullOrderId = ACCOUNT_OR_LOGON + "*" + ORDERNUMBER
```

Every confirmable order-line graph write uses:

```text
fullOrderLineId = fullOrderId + "*" + IMMUTABLE_LINE_NUMBER
```

`salesInv` may contain physical records shaped as:

```text
ACCOUNT_OR_LOGON*ORDERNUMBER
ACCOUNT_OR_LOGON*ORDERNUMBER*LINENUMBER
```

The active source mapping declares whether a record represents an order/header with embedded lines or an individual line. Runtime code must not guess record type only by counting delimiters.

Keep these values separate:

```text
fullOrderId
rawOrderNumber
fullOrderLineId
sourceOrderDocumentId
sourceLineDocumentId
webOrderNumber
trilogieOrderNumber
sourceTransactionId
```

Order number alone is not globally unique. Web, Trilogie, source transaction, and physical document IDs are evidence or aliases, not replacements for `fullOrderId`.

### 1.3 Full-order synchronization

`FULL_ORDER_SYNC` requires exactly one validated `fullOrderId`.

Exactly one order identity does not mean one line. A full sync loads zero, one, or many authoritative lines belonging to that order:

```text
SalesOrder: ACCOUNT1*ORDER100
    |- OrderLine: ACCOUNT1*ORDER100*1
    |- OrderLine: ACCOUNT1*ORDER100*2
    `- OrderLine: ACCOUNT1*ORDER100*3
```

The sync hydrates the complete, minimal graph projection required to finish discovery, order analysis, return request preparation, fulfillment, staging, and handoff.

If the submitted reference is a full line ID, the platform extracts its parent `fullOrderId`, full-syncs the entire order, and treats the line component as an initial line focus.

### 1.4 Partial-order synchronization

`PARTIAL_ORDER_SYNC` accepts an approved strong anchor, resolves zero, one, or more `fullOrderId` values, and synchronizes a bounded candidate projection for only those orders.

Partial describes projection depth, not identity quality. Every candidate graph write is keyed by a validated `fullOrderId`.

After an operational user selects an order, the orchestrator requests `FULL_ORDER_SYNC` for the selected `fullOrderId`. The user then selects one or more confirmable order lines.

### 1.5 Independent, context-only agents

Every business agent is independently deployable, testable, retryable, and replayable.

Each agent:

1. receives one immutable, versioned input context;
2. validates context type, schema version, configuration release, and authorization;
3. uses only capabilities granted to that context;
4. produces a new immutable output context plus bounded events and evidence references;
5. does not call another business agent directly;
6. does not read another agent's process memory, private storage, hidden transcript, or database tables;
7. does not mutate another agent's context;
8. is idempotent for the same context version and idempotency key.

The Return Session Orchestrator routes context references. Context is the only business handoff.

### 1.6 Metadata-driven models

Adding or changing a source table, collection, API, field, node, relationship, constraint, index, or projection profile must not require a new hard-coded application model for each physical variation.

The stable coded kernel contains generic contracts, identity and security invariants, connector interfaces, mapping validation, context/version rules, schema compilation, and synchronization execution.

Source and graph structure are versioned configuration interpreted by that kernel.

Custom transformation code is allowed only when the approved mapping language cannot safely express a required deterministic transformation. Such plugins are exceptional, explicitly registered, versioned, reviewed, and tested. LLM-generated executable code is prohibited.

### 1.7 Configurable graph schema

Graph nodes, keys, properties, relationships, joins, directions, cardinality, constraints, indexes, projection profiles, provenance, retention, masking, and synchronization behavior are configuration-driven.

Graph sync runs only against active, signed, compatible graph-schema and mapping releases.

### 1.8 Minimal graph

The graph is a return-process projection, not a source replica. Store only fields needed to:

1. identify the authorized customer/account, order, and line;
2. disambiguate order candidates;
3. determine return evidence and remaining returnable quantity;
4. complete support, fulfillment, staging, and handoff;
5. prove provenance, freshness, authorization, confirmation, and audit state.

## 2. Agent and context architecture

### 2.1 Independent agents

| Agent | Input context | Output context | Primary responsibility |
|---|---|---|---|
| Return Session Orchestrator | `SessionCommandContext` | `ReturnSessionContext` and routing events | Create session and route context references |
| Order Discovery Agent | `ReturnSessionContext` and `DiscoveryInputContext` | `DiscoveryContext` | Resolve and confirm order and lines |
| Order Analysis Agent | Sealed `DiscoveryContext` | `OrderAnalysisContext` | Validate quantities and return evidence |
| Return Workflow Agent | Sealed discovery and approved analysis contexts | `ReturnRequestContext` | Prepare return request |
| Return Fulfillment Agent | Return request context and support events | `FulfillmentTrackingContext` | Track return setup, label, and pickup |
| Bay Allocation Agent | Fulfillment context | `BayStagingContext` | Guide staging and handoff |
| Learning Agent | Final context references and bounded events | `LearningFeedbackContext` | Produce governed improvement signals |
| Graph Schema Design Agent | `SchemaDesignContext` | `SchemaProposalContext` | Help users design or redesign graph configuration |

### 2.2 Standard context envelope

```yaml
contextId: immutable-id
contextType: DiscoveryContext
schemaVersion: "2"
contextVersion: 7
sessionId: session-id
correlationId: correlation-id
actor:
  actorId: opaque-id
  type: ASSOCIATE
authorizationScopeRef: opaque-reference
configurationReleaseId: configuration-release-id
graphSchemaReleaseId: graph-schema-release-id
createdAt: timestamp
createdBy: ORDER_DISCOVERY_AGENT
previousContextRef: immutable-reference
idempotencyKey: stable-key
evidenceRefs: []
payload: {}
```

Rules:

- updates create new context versions;
- optimistic concurrency prevents lost updates;
- context consumers revalidate authorization;
- payload validation uses the referenced context schema;
- large artifacts and raw source documents remain outside context;
- each agent sees only fields required for its capability;
- retention and expiry are configuration-driven.

### 2.3 Communication boundary

Allowed:

```text
Agent A -> Context A -> Orchestrator -> context reference -> Agent B
```

Prohibited:

```text
Agent A -> direct business call -> Agent B
Agent B -> Agent A private database
Agent B -> Agent A in-memory conversation
Agent B -> unrestricted transcript
```

## 3. Generic source, mapping, and graph configuration

### 3.1 Stable generic runtime contracts

```text
ContextEnvelope
SourceDescriptor
SourceMetadataSnapshot
SourceRecordEnvelope
CanonicalEntityEnvelope
RelationshipEnvelope
MappingDefinition
GraphSchemaDefinition
ProjectionProfile
SyncRequest
SyncResult
SchemaDesignContext
SchemaProposalContext
ConfigurationRelease
ImportPackageManifest
```

### 3.2 Source descriptor

MongoDB example:

```yaml
sourceId: sales-invoice-v3
kind: MONGODB
connectionRef: tds-source
database: configured-database
collection: salesInv
schemaVersionSelector: eventMeta.writerSchemaVersion
recordProfiles:
  - profileId: embedded-order
    match:
      field: eventMeta.recordType
      equals: ORDER
    identity:
      entity: SalesOrder
      expression: "join('*', accountLogon, orderNumber)"
  - profileId: line-document
    match:
      field: eventMeta.recordType
      equals: ORDER_LINE
    identity:
      entity: OrderLine
      expression: "join('*', accountLogon, orderNumber, lineNumber)"
```

SQL example:

```yaml
sourceId: omc-v1-returns
kind: SQLSERVER
connectionRef: omc-source
database: configured-database
schema: dbo
table: returns
primaryKeyFields: [return_id]
watermarkField: updated_at
```

The mapping expression language is allowlisted. Arbitrary Python, JavaScript, SQL, shell, or Cypher is prohibited.

### 3.3 Field mapping

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

Mappings declare type, required/optional status, transform, validation, sensitivity, masking, provenance, and default rules. Missing required identity fields fail; they are never silently synthesized.

### 3.4 Graph schema definition

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

### 3.5 Generic runtime pipeline

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

Normal source onboarding changes configuration rather than creating new source-specific application classes.

## 4. Strong anchors and order resolution

### 4.1 Primary strong-anchor families

| Anchor family | Resolver path | Output |
|---|---|---|
| Order reference | Exact full order ID; raw number within authorized account/logon scope; approved web/source alias mapping | Zero, one, or bounded many full order IDs |
| Shipment | Exact tracking -> configured shipment/order join -> authoritative order source | One or more full order IDs; line remains separately confirmable |
| Invoice | Exact invoice -> configured invoice-line/order join -> authoritative order source | One or more full order IDs because an invoice may span orders |
| Customer/account | Exact authorized account/customer or policy-approved HMAC contact lookup -> bounded recent orders | Bounded full order IDs |

### 4.2 Composite extensions

Customer PO, delivery ticket, prior return/RMA, or SKU may become eligible when active configuration defines its authoritative resolver, required scope, cardinality, indexing, freshness, and maximum results.

Name, company, ZIP, address, product description, color, brand, approximate date, partial identifiers, and fuzzy text are narrowing evidence. They do not independently authorize source synchronization or order confirmation.

### 4.3 Smart operational conversation

The Order Discovery Agent receives only current context, graph candidates, and approved question policy.

Deterministic policy selects the permitted field. The model may phrase one concise question for the associate, sales representative, or customer-care user.

For multiple candidates, ask for the evidence expected to separate the candidates most effectively. Do not ask for evidence already available in context.

## 5. Partial and full synchronization

### 5.1 Partial request

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

### 5.2 Full request

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

### 5.3 Prerequisites

Every sync requires:

- active source descriptors;
- active mapping definitions;
- active graph schema release;
- active projection profile;
- valid authorization scope;
- compatible schema, mapping, and source fingerprints;
- healthy graph and control-store schemas.

Missing configuration never causes runtime schema invention. Sync fails closed and may create a schema-design request.

### 5.4 Lifecycle

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

Terminal alternatives:

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

### 5.5 Partial algorithm

1. Load the signed active release.
2. Validate anchor, role, scope, freshness, and result cap.
3. Compile configured connector and resolver plans.
4. Resolve anchor to full order IDs.
5. Validate and deduplicate by full order ID.
6. Stop for narrowing when the cap is exceeded.
7. Read only candidate-profile fields.
8. Map through generic envelopes.
9. Validate graph schema, privacy, and provenance.
10. Upsert candidate projections by full order ID.
11. Read back through the graph gateway.
12. Store candidate context and sync status.

### 5.6 Full algorithm

1. Validate the exact full order ID and authorization.
2. Load active source, mapping, graph-schema, and projection releases.
3. Compile exact order/header and line query plans.
4. Read all configured records for that order.
5. Reject any record whose normalized parent identity differs from the requested full order ID.
6. Read only configured customer, shipment, invoice, product, location, and prior-return evidence.
7. Produce generic canonical entities and relationships.
8. Enforce immutable line identity, cardinality, provenance, sensitivity, and quantity rules.
9. Commit the minimal order subgraph.
10. Remove stale relationships only under the approved replacement policy.
11. Read back and verify identity, revision, release IDs, and projection digest.
12. Publish `FullOrderSyncContext`.

### 5.7 Line handling

- All authoritative lines for the order are synchronized.
- Each line uses `fullOrderId*immutableLineNumber`.
- Embedded arrays and separate line documents are both supported through configuration profiles.
- Array position must not become a confirmable identity.
- Missing immutable line number produces `UNCONFIRMABLE`.
- Operational users may select one or more lines after full sync.

### 5.8 Agent boundary

Agents use fixed `AgentGraphGateway` capabilities. They never receive source connectors or raw graph drivers.

On graph miss or staleness, an agent produces a sync-request context. A worker reads sources and writes the graph. The agent receives the completed context and re-queries the graph.

## 6. Minimal graph model

### 6.1 Initial business vocabulary

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

Workflow/configuration vocabulary may include:

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

Only enabled workflow capabilities need corresponding graph entities.

### 6.2 Provenance

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

### 6.3 Prohibited data

Do not store in graph, contexts, candidates, locks, prompts, logs, export packages, or frontend projections:

- payment tokens, authorization codes, or card/account numbers;
- cardholder, expiration, or billing-address data;
- raw phone/email when a keyed digest is sufficient;
- credentials, secrets, or connection strings;
- raw source documents or unbounded source payloads;
- full transcripts, unrestricted prompts/responses, or hidden reasoning;
- image, invoice, label, POD, or attachment bytes;
- worker internals or unrelated telemetry.

## 7. Graph Schema Design Agent

### 7.1 Configuration-page chat

`GraphSchemaDesignAgent` is an independent context-only agent presented as a chat inside the Data Console Configuration page.

It helps authorized users:

- create a graph schema from configured sources;
- redesign an existing draft or active schema as a new version;
- inspect source and graph structures;
- resolve mapping, identity, relationship, privacy, and migration gaps;
- preview schema and mapping changes;
- validate and simulate a proposal;
- download/export a portable configuration package;
- upload/import a configuration package for validation and redesign;
- submit a final proposal for human review.

It cannot approve, activate, migrate, backfill, or run production synchronization.

The Configuration page is a client of the agent contract, not the agent's memory. The agent can resume on another runtime instance from serialized context alone.

### 7.2 Schema-design input context

```yaml
contextId: immutable-id
contextType: SchemaDesignContext
schemaVersion: "1"
contextVersion: 4
configurationSessionId: session-id
actor:
  actorId: opaque-id
  roles: [DATA_ADMIN]
authorizationScopeRef: opaque-reference
mode: REDESIGN_EXISTING
selectedSources:
  - sourceId: sales-invoice
    metadataSnapshotRef: snapshot-id
baseConfigurationReleaseId: existing-release
baseGraphSchemaReleaseId: existing-schema-release
requestedCapability:
  capabilityId: ORDER_DISCOVERY
  businessQuestion: "Find the correct order and lines for a return"
answers: []
unresolvedGaps: []
proposalRef: proposal-id
idempotencyKey: stable-key
```

### 7.3 Dynamic gap analysis

The agent must not use a fixed questionnaire.

It compares:

```text
configured source metadata
+ selected tables/collections/APIs and structures
+ existing mappings and graph schema
+ requested graph capability and query patterns
+ privacy, authorization, retention, and freshness policy
+ synchronization and migration requirements
```

Gap categories include:

```text
SOURCE_SELECTION
RECORD_PROFILE
ENTITY_IDENTITY
FIELD_SEMANTICS
CANONICAL_MAPPING
RELATIONSHIP_JOIN
CARDINALITY
NULLABILITY
DATA_TYPE
PROVENANCE
PRIVACY
AUTHORIZATION
RETENTION
FRESHNESS
INDEXING
PROJECTION_SCOPE
MIGRATION
BACKFILL
ROLLBACK
```

These are classification categories, not hard-coded questions.

### 7.4 Smart configuration questions

For each turn, the agent:

1. reruns gap analysis from the latest context;
2. removes answered, inferable, duplicate, irrelevant, and unnecessary gaps;
3. ranks remaining blockers by identity, privacy, relationships, sync correctness, migration safety, and performance;
4. asks one smallest configuration-related question for the highest-priority gap;
5. explains why it is required;
6. shows relevant source fields, structures, indexes, cardinality, or schema diff;
7. offers evidence-backed options when possible;
8. accepts correction from an authorized user;
9. creates a new proposal-context version;
10. revalidates immediately;
11. stops when the proposal is review-ready.

It must not ask:

- generic unrelated return-workflow questions;
- conversational filler;
- questions already answered by approved metadata or context;
- questions about fields excluded by minimal-data policy;
- requests for credentials or unrestricted production values;
- the same static questions for every source.

### 7.5 Owner routing

When the current user lacks authority or knowledge, the proposal records an `UnresolvedConfigurationDecision` with the exact question, evidence, impact, and required role:

```text
DATA_ADMIN
DATA_STEWARD
SOURCE_OWNER
SECURITY_OWNER
ARCHITECT
OPERATIONAL_SUBJECT_MATTER_EXPERT
```

Associates, sales representatives, or customer-care SMEs may clarify configuration-relevant operational semantics. Technical identity, source authority, cardinality, privacy, migration, approval, and activation decisions require configured owner roles.

The agent does not call another agent. Workflow routing delivers the context to the appropriate human and later resumes the schema chat.

## 8. Create and redesign schema workflows

### 8.1 Create new schema

```text
Select sources and requested capability
  -> introspect authorized metadata
  -> detect entities, candidate keys, joins, and gaps
  -> chat resolves only blocking configuration gaps
  -> draft graph schema and mappings
  -> validate
  -> sandbox simulate
  -> review
  -> approve
  -> release
  -> migrate/activate
```

### 8.2 Redesign existing schema

An authorized user can open any draft or active schema in redesign mode.

Active releases are immutable. Redesign creates a new draft version with the selected release as its base.

The redesign workspace must:

- load the existing nodes, relationships, properties, constraints, indexes, mappings, projection profiles, and policies;
- refresh source metadata and show source-schema drift;
- allow chat-driven changes;
- allow governed form or visual-editor changes using the same proposal context;
- show added, removed, renamed, and changed elements;
- classify compatibility and migration impact;
- identify affected sync profiles and graph queries;
- determine whether backfill or re-projection is required;
- preserve previous releases for audit and rollback;
- prohibit in-place mutation of an active release.

### 8.3 Redesign change classification

```text
NON_BREAKING
REQUIRES_INDEX_MIGRATION
REQUIRES_DATA_BACKFILL
REQUIRES_RELATIONSHIP_REBUILD
REQUIRES_DUAL_READ_WINDOW
BREAKING_REQUIRES_NEW_MAJOR_VERSION
UNSAFE_REJECTED
```

Examples:

- adding an optional display property may be non-breaking;
- changing `SalesOrder` identity is breaking and requires migration/dual-read planning;
- removing a property used by active queries requires compatibility remediation;
- adding a relationship may require historical re-projection;
- exposing a prohibited field is rejected.

## 9. Schema download/export

### 9.1 Export formats

The Configuration page supports:

```text
YAML preview/download
JSON preview/download
portable schema package (.zip)
```

The portable package contains declarative configuration only:

```text
manifest.yaml
source-descriptors/*.yaml
canonical-schemas/*.json
mappings/*.yaml
graph-schema/*.yaml
projection-profiles/*.yaml
policies/*.yaml
migrations/plan.yaml
tests/scenarios.yaml
validation/receipt.json
README.md
```

### 9.2 Export rules

- Connection references may be exported; credentials and secrets may not.
- Raw source samples and sensitive values may not be exported.
- Environment-specific database names may be parameterized or redacted by policy.
- Every file has a schema version and checksum.
- The manifest records package version, proposal/release lineage, required capabilities, minimum runtime version, and content digests.
- Draft exports are visibly marked `DRAFT_NOT_ACTIVATABLE`.
- Active-release exports include their signature and immutable release ID.
- Download is audited with actor, package digest, scope, and time.

### 9.3 Export uses

- offline review;
- source-control review;
- promotion between authorized environments;
- backup of declarative configuration;
- controlled editing before re-upload;
- architecture and security review.

Export never grants activation permission in another environment.

## 10. Schema upload/import

### 10.1 Upload behavior

An authorized user may upload YAML, JSON, or a portable schema package from the Configuration page.

Every upload creates a quarantined import draft. It never updates an active schema in place and never automatically activates, migrates, backfills, or synchronizes.

```text
UPLOAD_RECEIVED
  -> QUARANTINED
  -> PACKAGE_PARSED
  -> MANIFEST_VALIDATED
  -> STATIC_POLICY_VALIDATED
  -> COMPATIBILITY_ANALYZED
  -> SANDBOX_SIMULATED
  -> IMPORT_DRAFT_CREATED
  -> CHAT_REVIEW
  -> HUMAN_REVIEW
```

### 10.2 Upload validation

Validate:

- archive type, decompressed size, file count, path traversal, and malware policy;
- manifest and supported package version;
- declared schemas for every file;
- checksums and signatures when present;
- runtime and capability compatibility;
- source references and allowed connector types;
- canonical identity rules;
- mapping expression allowlist;
- property types, joins, cardinality, constraints, and indexes;
- privacy, authorization, retention, and minimal-data policies;
- migration, backfill, and rollback requirements;
- prohibited executable code, arbitrary SQL, arbitrary Cypher, scripts, binaries, macros, and secrets.

Unknown fields fail validation unless an explicitly compatible extension namespace permits them.

### 10.3 Import outcomes

```text
VALID_DRAFT
VALID_WITH_CONFIGURATION_GAPS
INCOMPATIBLE_RUNTIME
MISSING_SOURCE_REFERENCE
SOURCE_FINGERPRINT_MISMATCH
INVALID_IDENTITY
INVALID_MAPPING
PRIVACY_REJECTED
UNSAFE_MIGRATION
PACKAGE_TAMPERED
MALFORMED_PACKAGE
```

When gaps remain, the uploaded draft opens in schema chat. The agent asks only questions related to the imported configuration and current source/environment differences.

### 10.4 Round-trip guarantee

For supported package versions:

```text
export -> upload -> validate -> export
```

must preserve the normalized declarative configuration and content digest, excluding permitted environment bindings, audit metadata, and regenerated signatures.

## 11. Configuration-page experience

### 11.1 Source workspace

- connection and introspection health;
- selected databases, schemas, tables, collections, or APIs;
- nested structure and type browser;
- keys, indexes, cardinality, freshness, and masked sample-shape evidence;
- source fingerprint and drift status.

### 11.2 Schema chat

- current focused configuration question;
- why the question blocks completion;
- relevant source and graph evidence;
- evidence-backed options;
- authorized correction;
- owner-routing action;
- answer history reconstructed from context;
- save, reload, failover, and resume.

### 11.3 Schema editor and preview

- graph nodes, identities, and properties;
- relationships, joins, directions, and cardinality;
- constraints and indexes;
- source-to-canonical and canonical-to-graph mappings;
- partial/full projection profiles;
- provenance, privacy, retention, and authorization rules;
- validation issues and unresolved gaps;
- version diff;
- migration, backfill, and rollback impact;
- upload and download controls.

Chat and visual/form edits produce the same versioned proposal commands. The UI cannot bypass validation or context versioning.

### 11.4 Actions

```text
CREATE_NEW
OPEN_EXISTING
REDESIGN_AS_NEW_VERSION
SAVE_DRAFT
RESUME
ANSWER
ROUTE_DECISION
UPLOAD_IMPORT
DOWNLOAD_EXPORT
RUN_VALIDATION
RUN_SANDBOX_SIMULATION
SUBMIT_FOR_REVIEW
REJECT
APPROVE
ACTIVATE
PLAN_MIGRATION
PLAN_BACKFILL
```

Approval, activation, migration, backfill, and sync are separate permissions and operations.

## 12. Schema proposal and release lifecycle

```text
DRAFT_REQUESTED
  -> SOURCE_METADATA_COLLECTED
  -> GAP_ANALYZED
  -> CONFIGURATION_QUESTIONS_PENDING
  -> DRAFT_GENERATED
  -> STATIC_VALIDATION
  -> SANDBOX_SIMULATION
  -> HUMAN_REVIEW
  -> APPROVED
  -> RELEASE_CREATED
  -> MIGRATION_PLANNED
  -> ACTIVE
```

Alternatives:

```text
NEEDS_SOURCE_CONTRACT
NEEDS_AUTHORIZED_OWNER
PRIVACY_REJECTED
MIGRATION_UNSAFE
REJECTED
SUPERSEDED
```

Only `ACTIVE` signed releases are eligible for production sync.

## 13. Configurable system persistence

### 13.1 Providers

```text
NEO4J | MONGODB | POSTGRESQL | SQLSERVER
```

Business graph projection remains Neo4j. Context and control storage are provider-configurable.

### 13.2 Generic repositories

```text
ContextRepository
SyncRequestRepository
SyncAttemptRepository
DiscoveryLockRepository
ConfigurationReleaseRepository
SchemaProposalRepository
ImportExportAuditRepository
IdempotencyRepository
OutboxRepository
SchemaVersionRepository
```

Business logic depends on interfaces, not provider names.

### 13.3 Automatic bootstrap

For the selected provider:

1. validate configuration and credentials;
2. acquire a migration lock;
3. read schema version and checksums;
4. create missing tables, collections, labels, constraints, and indexes;
5. apply forward-only migrations;
6. introspect and verify the result;
7. record migration evidence;
8. fail closed on drift, missing objects, or unsupported versions.

`autoCreate=false` means validation-only, never silent continuation.

## 14. Source authority baseline

| Source | Authority/use |
|---|---|
| `salesInv` | Order header, immutable line identity, historical order-line facts |
| `customerOutboundCDM` | Customer, account/logon, and permitted contact resolution |
| `shipmentInfo` | Tracking, shipment, carrier, and delivery evidence |
| `invoiceMemosCDM` | Invoice-to-order and invoiced-quantity evidence |
| `lkpSearchProduct` | Current product/SKU enrichment; never replaces historical order-line facts |
| `locationsCDM` | Branch and warehouse context |
| `orderOutbnd` | Capability-gated legacy/migration fallback and source/channel hints |
| `shipTo` | Optional ship-to context after contract validation |
| `purchaseHistory_v1` | Eventually consistent narrowing evidence, not order authority |
| OMC V1/V2 sources | Prior-return and consumed-quantity evidence |

These are initial configurations, not permanent hard-coded branches.

## 15. Candidate, confirmation, and lock contract

Candidate fields include:

```text
inputOrderReference
matchedReferenceType
fullOrderId
accountLogon
rawOrderNumber
sourceOrderDocumentId
sourceSchemaVersion
sourceRevision
sourceTransactionId
webOrderNumber
trilogieOrderNumber
orderSource
identityQuality
identityEvidenceReferences
graphSyncedAt
mappingReleaseId
graphSchemaReleaseId
```

Line fields include:

```text
fullOrderLineId
immutableLineNumber
sourceLineDocumentId
sourceProductId
masterProductId
alternateCode
descriptionAtOrder
orderedQuantity
shippedQuantity
invoicedQuantity
unitOfMeasure
lineIdentityQuality
sourceRevision
```

The sealed `DiscoveryContext` binds exact order and selected line IDs, revision, mapping/schema releases, evidence references, actor, and confirmation time.

Source revision changes require refresh and reconfirmation. Synthetic or ambiguous line identities cannot be confirmed.

## 16. Current repository differences

| Current implementation | Required target |
|---|---|
| Concrete models encode selected TDS shapes | Stable generic envelopes plus configuration-defined schemas; coded identity/security invariants remain |
| `GraphSyncScope.FULL` copies bounded top-N records | Rename to administrative backfill; add exact full-order and anchor-based partial sync |
| Graph Sync API returns `202` but waits inline | Durable enqueue and asynchronous worker lifecycle |
| `SalesOrder` is keyed by raw order number | Key and join by full order ID |
| Collection names, fields, and Cypher are hard-coded in sync | Compile execution from active signed source, mapping, graph-schema, and projection configuration |
| Graph YAML covers only customer foundation | Configure complete enabled entity, relationship, constraint, index, and projection definitions |
| Associate flow performs direct Mongo fallback and inline graph upsert | Context sync request -> worker -> graph readback -> agent graph query |
| Candidate/source integrity services contain stubs | Real generic services and verification receipts |
| Synthetic line IDs can be confirmable | Immutable source line number or `UNCONFIRMABLE` |
| Invoice/location/prior-return sync is absent | Add through configuration, not new hard-coded branches |
| Control persistence is hard-coded | Provider-neutral repositories and automatic bootstrap |
| No independent schema-design workflow | Configuration-page schema chat with governed releases |
| No redesign/import/export lifecycle | Immutable redesign versions plus safe download/upload packages |
| Agents share broader runtime flow | Independent context-only contracts and architecture tests |

## 17. Implementation plan

### Phase 0 - Freeze invariants and generic contracts

- full order/line identity;
- operational-user roles;
- generic envelopes;
- immutable context and idempotency;
- signed configuration release contracts;
- legacy broad-sync rename.

### Phase 1 - Metadata and mapping runtime

- source descriptor schemas;
- MongoDB, SQL Server, PostgreSQL, and API connectors;
- metadata introspection and fingerprints;
- allowlisted mapping expressions;
- generic source/canonical/relationship envelopes;
- safe query-plan and mapping compilers;
- initial source configurations.

### Phase 2 - Independent Graph Schema Design Agent

- schema-design/proposal/gap/question/answer contexts;
- dynamic gap analysis;
- one configuration-specific question per turn;
- context-only save/resume/replay;
- owner routing;
- agent prohibition on approval, activation, migration, backfill, and sync.

### Phase 3 - Configuration-page schema workspace

- source browser;
- schema chat;
- graph visual/form editor;
- live mapping/schema preview;
- unresolved gaps and validation;
- existing-schema redesign as new version;
- version diff and migration impact;
- download/export and upload/import UI.

### Phase 4 - Import/export service

- versioned portable manifest;
- YAML, JSON, and ZIP exports;
- secret and sensitive-data exclusion;
- checksums, signatures, and audit records;
- quarantined uploads;
- archive and schema security validation;
- compatibility analysis and round-trip tests.

### Phase 5 - Graph compiler and migrations

- configurable nodes, relationships, properties, constraints, and indexes;
- projection compiler;
- schema diff and compatibility classification;
- forward migration and rollback planning;
- raw order-number uniqueness migration.

### Phase 6 - Configurable context/control persistence

- provider-neutral repositories;
- Neo4j, MongoDB, PostgreSQL, and SQL Server implementations;
- automatic bootstrap, locks, checksums, and drift detection;
- provider contract tests.

### Phase 7 - Durable generic sync orchestration

- sync request/attempt lifecycle;
- release resolution;
- leases, retries, cancellation, idempotency, and recovery;
- connector -> mapping -> graph pipeline;
- graph readback and receipts;
- schema-design trigger on missing/incompatible configuration.

### Phase 8 - Strong-anchor partial sync

- configured order, shipment, invoice, and customer/account resolvers;
- bounded authorized resolution to full order IDs;
- candidate projection and candidate-set context;
- operational narrowing conversation;
- no direct source access from agents.

### Phase 9 - Full-order synchronization

- exact full order ID request;
- all configured authoritative lines;
- minimal related evidence;
- immutable line enforcement;
- atomic graph update and approved cleanup;
- readback and full-sync context.

### Phase 10 - Context-only agent cutover

- input/output context schemas for every agent;
- orchestrator routing by preconditions;
- graph gateway fixed capabilities;
- removal of agent-to-agent/private-store dependencies;
- replay and architecture tests.

### Phase 11 - Confirmation, concurrency, and Order Analysis

- full sync before sealing discovery;
- multi-line selection;
- revision-bound confirmation;
- expiring line leases and optimistic concurrency;
- ordered/shipped/invoiced/prior-return quantity evidence;
- UOM-aware remaining returnable calculation;
- immutable Order Analysis context.

### Phase 12 - Backfill, shadow validation, and cutover

- administrative backfill separate from full/partial sync;
- active-release-bound backfill;
- shadow comparison of identities, candidates, relationships, revisions, and quantities;
- cohort rollout;
- retirement of hard-coded sync, direct-source fallback, raw identity, and stubs.

## 18. Required APIs

```text
POST /order-sync/full
POST /order-sync/partial
GET  /order-sync/requests/{requestId}
POST /order-sync/requests/{requestId}/cancel

POST /schema-design/requests
GET  /schema-design/requests/{requestId}
POST /schema-design/requests/{requestId}/introspect
POST /schema-design/requests/{requestId}/next-question
POST /schema-design/requests/{requestId}/answers
POST /schema-design/requests/{requestId}/route-decision
POST /schema-design/requests/{requestId}/validate
POST /schema-design/requests/{requestId}/simulate
POST /schema-design/proposals/{proposalId}/redesign
POST /schema-design/proposals/{proposalId}/submit
POST /schema-design/proposals/{proposalId}/approve
POST /schema-design/proposals/{proposalId}/reject

GET  /schema-packages/{proposalOrReleaseId}/download
POST /schema-packages/upload
GET  /schema-packages/imports/{importId}
POST /schema-packages/imports/{importId}/create-draft

POST /configuration-releases/{releaseId}/activate
POST /data-console/v1/graph-sync/backfills
POST /data-console/v1/system-schema/validate
POST /data-console/v1/system-schema/apply
```

Permissions for draft, export, import, submit, approve, activate, migrate, backfill, and sync are separate.

## 19. Acceptance scenarios

| Scenario | Required behavior |
|---|---|
| Full order has multiple lines | One SalesOrder, all authoritative OrderLines, one or more user-selected lines |
| Same raw order number under two logons | Two isolated orders; no merge or cross-account disclosure |
| Tracking resolves multiple orders | Partial-sync bounded candidates; user narrows/selects; full-sync selected order |
| Invoice spans orders | Group by full order ID; user selects/narrows |
| Customer account has too many orders | No broad hydration; ask one configured separator question |
| New collection has no graph schema | Schema chat introspects and asks only unresolved configuration questions; sync remains blocked |
| Existing schema is redesigned | New draft version, source refresh, diff, impact, validation, approval; active version unchanged until activation |
| Metadata proves one safe key | Agent uses evidence without asking user to repeat it |
| Two plausible identities exist | Chat presents evidence and asks one focused identity question |
| User uploads valid package | Quarantined validation creates import draft; no automatic activation |
| Uploaded package contains script/Cypher/secrets | Reject safely and audit |
| Uploaded package differs from environment | Show source/compatibility gaps and ask only related questions |
| Draft is downloaded and re-uploaded | Normalized configuration round-trips correctly |
| User lacks approval role | May edit if permitted but cannot approve/activate |
| Agent instance changes | Serialized context recreates same proposal and gaps |
| Graph is unavailable | Agents do not bypass graph |
| Source line lacks immutable number | Line is unconfirmable |
| Source revision changes | Refresh and reconfirm before sealing |

## 20. Test strategy

### 20.1 Identity and synchronization

- duplicate order number across logons;
- letters and leading zeroes;
- embedded and separate line records;
- zero/one/many lines;
- order/shipment/invoice/customer anchors;
- unauthorized and over-limit results;
- partial then full sync;
- revision and readback verification;
- no prefix widening or synthetic confirmable line IDs.

### 20.2 Agent independence

- every agent runs from serialized context without another agent process;
- no agent imports another business-agent service;
- no cross-agent private repository access;
- context precondition and version failures are explicit;
- retries and replays are idempotent;
- authorization is revalidated.

### 20.3 Metadata-driven runtime

- onboard a new collection/table through configuration only;
- add optional field without application-model code;
- reject missing identity and unsafe expressions;
- detect source drift;
- validate types, joins, cardinality, privacy, and provenance.

### 20.4 Schema chat and redesign

- no fixed question sequence;
- one highest-priority configuration question per turn;
- no repeated/inferable/unrelated questions;
- source-specific evidence in questions;
- owner routing;
- redesign never mutates active release;
- compatibility and migration classification;
- context-only reload/failover/replay;
- agent cannot approve or activate.

### 20.5 Upload/download

- YAML, JSON, and ZIP exports;
- no secrets/raw samples/prohibited fields;
- manifest, checksums, and signatures;
- ZIP traversal, decompression bomb, malware, binary, script, macro, SQL, and Cypher rejection;
- unsupported version and unknown-field rejection;
- environment binding and source fingerprint differences;
- import draft isolation;
- normalized round trip;
- export/import authorization and audit.

### 20.6 Persistence bootstrap

- clean Neo4j, MongoDB, PostgreSQL, and SQL Server stores;
- idempotent repeated bootstrap;
- concurrent migration lock;
- drift and unsupported version;
- validation-only mode;
- provider parity.

### 20.7 Privacy and architecture

- prohibited fields absent from graph, contexts, logs, prompts, APIs, and packages;
- sensitive anchors use approved digests and expiring references;
- arbitrary queries/code are rejected;
- authorization applies to graph, sync, schema, upload, and download;
- graph outage cannot enable direct-source agent fallback.

## 21. Rollout

1. Freeze identity, context, package, and configuration contracts.
2. Deploy metadata introspection and mapping runtime.
3. Deploy schema chat and redesign workspace in draft-only mode.
4. Enable safe package download/upload with quarantine.
5. Deploy graph compiler and provider bootstrap in validation mode.
6. Create and approve initial schema/mapping releases.
7. Run administrative canonical-ID backfill and shadow comparison.
8. Enable partial sync for test cohorts.
9. Enable full-order sync and revision-bound confirmation.
10. Convert agents to context-only graph access.
11. Enable controlled redesign and release activation.
12. Retire direct source fallback, hard-coded sync branches, raw graph identity, and unconditional stubs.

Rollback disables new release/agent/sync feature flags and returns to the prior approved release without deleting canonical graph/configuration history. Destructive cleanup requires separate approval after the migration window.

## 22. Definition of done

- Operational language uses associate, sales representative, and customer care.
- Every business agent runs independently from versioned context only.
- Full sync requires one validated full order ID and synchronizes all authoritative lines.
- Partial sync resolves strong anchors to full order IDs before graph writes.
- Selected orders are full-synced before discovery is sealed.
- New source structures normally require configuration, not new source-specific models.
- Graph schema and synchronization are driven by active signed configuration.
- Configuration page provides independent schema chat and visual/form editing.
- Questions are source-specific, dynamically gap-driven, and configuration-only.
- Existing schemas can be redesigned only as new immutable versions.
- Users can safely download/export and upload/import schema packages.
- Upload never activates, migrates, backfills, or syncs automatically.
- Schema Design Agent cannot approve or activate.
- Missing/incompatible configuration blocks sync and starts governed schema work.
- Context/control storage bootstraps on configured providers.
- Graph contains only minimal process data and provenance.
- Immutable line identity, revision, authorization, concurrency, privacy, import security, and recovery tests pass.
- Administrative backfill remains separate from full- and partial-order synchronization.

## 23. Governing invariants

```text
No validated fullOrderId
-> no SalesOrder graph write.
```

```text
No ACTIVE signed compatible graph schema and mapping release
-> no graph synchronization.
```

```text
No verified full-order sync at the required source revision
-> no sealed DiscoveryContext.
```

```text
No versioned input context
-> no business-agent or schema-design-agent execution.
```

```text
No authorized human approval and release activation
-> no production schema change.
```

```text
Upload or redesign
-> new quarantined/draft version, never in-place active mutation.
```
