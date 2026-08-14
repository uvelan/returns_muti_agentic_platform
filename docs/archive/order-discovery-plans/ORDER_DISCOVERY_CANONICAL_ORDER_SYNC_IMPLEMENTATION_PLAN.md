# Order Discovery: Canonical Order Sync and Graph-First Implementation Plan

**Status:** Authoritative implementation plan  
**Date:** 2026-08-02  
**Primary scope:** Order Discovery, canonical order synchronization, agent graph access, and configurable system persistence  
**Secondary scope:** The minimum Order Analysis data that must be available after an order is confirmed  

This document consolidates and supersedes the implementation direction in:

- `ORDER_DISCOVERY_ORDER_ID_AND_ANCHOR_FINDINGS.md`;
- `ORDER_DISCOVERY_FIELD_CORRECTIONS_AND_STRONG_ANCHORS.md`;
- `ORDER_ANALYSIS_MANUAL_MAPPING_RECONCILIATION.md`;
- `ORDER_DISCOVERY_GRAPH_FIRST_IMPLEMENTATION_PLAN.md`;
- the return-discovery analysis package and its repository-alignment reports; and
- the Ferguson Returns Platform proposal.

The prior reports remain evidence. Where their terminology conflicts with this document, this document controls implementation.

## 1. Final decisions

### 1.1 Canonical order identity

The full order ID is the only identity permitted for durable order synchronization and graph relationships:

```text
fullOrderId = ACCOUNT_OR_LOGON + "*" + ORDERNUMBER
```

The order number by itself is a display and lookup value. It is not globally unique.

The canonical line identity is:

```text
fullOrderLineId = fullOrderId + "*" + IMMUTABLE_LINE_NUMBER
```

Known `salesInv` physical records may therefore have either of these shapes:

```text
ACCOUNT_OR_LOGON*ORDERNUMBER
ACCOUNT_OR_LOGON*ORDERNUMBER*LINENUMBER
```

The active source adapter must determine whether a record is a header with embedded lines or a line document. The implementation must not guess record type solely by counting delimiters.

### 1.2 Full sync

`FULL_ORDER_SYNC` means:

> Given one validated `fullOrderId`, read the authoritative records for that order and hydrate the complete, minimal graph projection needed to finish the return process.

A full-order sync must never start from a raw order number, web order number, tracking number, invoice number, phone, email, PO number, SKU, or free text. Those values must first resolve to one or more full order IDs.

### 1.3 Partial sync

`PARTIAL_ORDER_SYNC` means:

> Given a permitted strong anchor, resolve zero, one, or more full order IDs; then synchronize a bounded discovery projection for only those resolved orders.

Partial describes the amount of order data written, not the quality of identity. Every partial-sync write is still keyed by a validated full order ID.

When the associate selects one candidate, the orchestrator invokes `FULL_ORDER_SYNC` for that full order ID before discovery is sealed.

### 1.4 Agent data boundary

Agents read and write business and workflow context through an `AgentGraphGateway`. They must not receive direct MongoDB, SQL Server, PostgreSQL, or arbitrary Neo4j access.

Source resolvers and synchronization workers may read authoritative sources. They write only property-allowlisted graph projections and synchronization status.

### 1.5 Minimal graph rule

Neo4j is a return-process projection, not a replica of Ferguson source systems. Store only data required to:

1. identify the authorized customer, order, and line;
2. disambiguate candidates with the associate;
3. determine the quantity and evidence required for return analysis;
4. complete support, fulfillment, staging, and handoff workflow;
5. prove provenance, freshness, confirmation, and audit state.

Raw source documents, payment credentials, full transcripts, attachments, and unrelated catalog or shipment telemetry remain outside the graph.

### 1.6 Configurable system persistence

Business order context remains a Neo4j projection. Operational control records may use a configured provider:

```text
NEO4J | MONGODB | POSTGRESQL | SQLSERVER
```

At startup or deployment, the selected provider must create or migrate every required collection, table, index, constraint, or graph label/constraint when missing. Schema creation must be versioned, idempotent, locked against concurrent bootstrap, and fail closed on incompatible versions.

## 2. Terminology

| Term | Meaning |
|---|---|
| `inputOrderReference` | The single order-reference value supplied by an associate or extracted from a document. |
| `rawOrderNumber` | The order number displayed to users. It can repeat across accounts/logons. |
| `fullOrderId` | Durable canonical order identity: `ACCOUNT_OR_LOGON*ORDERNUMBER`. |
| `sourceOrderDocumentId` | Exact MongoDB `_id` or other physical source identity. It is retained separately from `fullOrderId`. |
| `fullOrderLineId` | `fullOrderId*IMMUTABLE_LINE_NUMBER`. |
| `matchedReferenceType` | The resolver interpretation that produced the match: canonical, raw ERP, web, source-document, and so on. |
| full-order sync | Complete minimal return-process hydration for one known `fullOrderId`. |
| partial-order sync | Strong-anchor resolution followed by bounded candidate hydration for resolved `fullOrderId` values. |
| administrative backfill | Bulk or scheduled population of many orders. It is not called full-order sync. |

## 3. Correct end-to-end architecture

```text
Associate / document evidence
        |
        v
Order Discovery Agent
        |
        v
AgentGraphGateway -- exact/fixed graph queries only
        |
        +-- fresh graph result ------------------------------+
        |                                                    |
        +-- miss/stale/missing relationship                  |
             |                                               |
             v                                               |
        SyncRequest                                          |
             |                                               |
             v                                               |
        Anchor Resolver                                      |
             |                                               |
             v                                               |
        zero / one / many fullOrderIds                       |
             |                                               |
             v                                               |
        Partial Order Projector                              |
             |                                               |
             v                                               |
        Graph candidate set <--------------------------------+
             |
             v
        Smart disambiguation with associate
             |
             v
        selected fullOrderId
             |
             v
        Full Order Sync
             |
             v
        source-revalidated order + line projection
             |
             v
        associate confirms order and line
             |
             v
        sealed DiscoveryContext and DiscoveryLock
             |
             v
        Order Analysis / Return Workflow
```

The source resolver layer is deterministic. The agent decides which approved resolver strategy to request based on available evidence and configuration, but it does not invent source queries or Cypher.

## 4. Strong anchors and resolver behavior

### 4.1 Four primary strong-anchor families

| Anchor family | Examples | Resolution path | Expected result |
|---|---|---|---|
| Order reference | Full order ID, raw order number with account/logon scope, exact web order reference, exact source document ID | `salesInv` first; approved web/legacy mapping when required | Zero, one, or bounded many `fullOrderId` values |
| Shipment | Exact tracking number | `shipmentInfo` -> order reference -> validated `salesInv` identity | One or more `fullOrderId` values; line remains separately confirmable |
| Invoice | Exact invoice number or invoice key | `invoiceMemosCDM` invoice lines -> account + order -> `salesInv` | One or more `fullOrderId` values because an invoice may span orders |
| Customer/account | Exact authorized account/customer identity; HMAC-resolved phone/email only when policy permits | `customerOutboundCDM` -> authorized account/logon scopes -> recent/bounded `salesInv` orders | Bounded candidate `fullOrderId` values |

### 4.2 Composite resolver extensions

These may become strong only with required scope and an enabled exact resolver:

- customer PO plus account/job and, when needed, date or product;
- delivery ticket after its authoritative field and index are confirmed;
- prior return/RMA number through OMC return history to original order;
- SKU/item plus customer/account and bounded date range.

The following are narrowing evidence, not standalone sync keys:

- customer or company name;
- ZIP, city, or address hint;
- product description, color, finish, or brand;
- approximate purchase date;
- purchase channel hint;
- partial order, invoice, or tracking text;
- fuzzy similarity or unrestricted free text.

### 4.3 Resolver outcome contract

| Outcome | Required action |
|---|---|
| No full order IDs | Record `NO_MATCH`; ask for the next highest-value customer-answerable anchor. |
| One full order ID | Run partial candidate sync; if policy permits, immediately run full-order sync before displaying authoritative lines. |
| Multiple full order IDs | Partial-sync bounded candidates; rank them; ask one question that best separates the candidates. |
| Too many results | Do not hydrate all results; request account/job, date, PO, product, or another exact anchor. |
| Unauthorized result | Do not reveal existence; record `UNAUTHORIZED` using a safe audit code. |
| Source unavailable | Keep the request retryable and ask the associate for another permitted anchor or retry. |
| Identity conflict | Do not merge records; quarantine the sync result for review and request another anchor. |

### 4.4 Smart conversation strategy

The question planner receives only bounded candidate facts and configured field policies. It selects one question per turn.

Priority order:

1. exact full order ID or order reference;
2. exact tracking or invoice reference;
3. authorized customer/account identity;
4. account-scoped PO, delivery ticket, or prior return;
5. date, SKU, job, or location narrowing evidence.

The LLM may phrase the question. Configuration and deterministic code choose the allowed field, resolver, result cap, and authorization scope.

## 5. Synchronization contracts

### 5.1 Full-order sync request

```yaml
requestType: FULL_ORDER_SYNC
fullOrderId: "ACCOUNT_OR_LOGON*ORDERNUMBER"
reason: CONFIRMATION_REQUIRED
requestedRelationships:
  - CUSTOMER_ACCOUNT
  - ORDER_LINES
  - PRODUCTS
  - SHIPMENTS
  - INVOICES
  - LOCATION
  - PRIOR_RETURNS
freshnessRequirement: CONFIRMATION
actorScopeRef: "opaque-authorization-reference"
idempotencyKey: "stable-request-key"
```

Validation rules:

- `fullOrderId` must parse exactly and preserve letters and leading zeroes;
- both components must be authorized;
- no wildcard, prefix, regex, or fuzzy lookup is permitted;
- the active source adapter must support the writer/schema version;
- repeated requests with the same identity, target revision, mapping version, and freshness policy are idempotent.

### 5.2 Partial-order sync request

```yaml
requestType: PARTIAL_ORDER_SYNC
anchorType: TRACKING_NUMBER
anchorEvidenceRef: "short-lived-opaque-reference"
anchorDigest: "HMAC-or-approved-digest"
actorScopeRef: "opaque-authorization-reference"
candidateLimit: 5
projectionProfile: ORDER_DISCOVERY_CANDIDATE
idempotencyKey: "stable-request-key"
```

The worker must:

1. dereference evidence under service authorization;
2. invoke the configured exact resolver;
3. resolve and validate full order IDs;
4. enforce authorization before graph writes;
5. write only the candidate projection for those IDs;
6. perform graph readback;
7. store the result IDs, count, provenance, and terminal status;
8. cause the orchestrator to repeat the original graph query.

### 5.3 Sync lifecycle

```text
REQUESTED
  -> CLAIMED
  -> RESOLVING_IDENTITY
  -> ORDER_IDS_RESOLVED
  -> READING_ORDER
  -> VALIDATING
  -> GRAPH_COMMITTING
  -> VERIFYING
  -> COMPLETED
```

Terminal non-success states:

```text
NO_MATCH
MULTIPLE_MATCHES_REQUIRES_NARROWING
RESULT_LIMIT_EXCEEDED
UNAUTHORIZED
SOURCE_UNAVAILABLE
INVALID_SOURCE_SHAPE
IDENTITY_CONFLICT
GRAPH_WRITE_FAILED
VERIFICATION_FAILED
EXPIRED
```

### 5.4 Full-order worker algorithm

1. Parse and validate the exact full order ID.
2. Check actor/account/job authorization.
3. Select the versioned `salesInv` adapter.
4. Read the exact header document and/or exact line documents for that order.
5. Reject records whose parsed identity does not exactly equal the requested full order ID.
6. Normalize immutable order and line identities.
7. Read only required related evidence from customer, shipment, invoice, product, location, and prior-return sources.
8. Build the minimal property-allowlisted projection.
9. Validate cross-source joins, cardinality, revisions, quantities, and provenance.
10. Commit the order subgraph and sync metadata transactionally where supported.
11. Read the order subgraph back through fixed queries and verify identity/revision/digest.
12. Mark the sync complete and emit a bounded workflow event.

### 5.5 Partial-order worker algorithm

1. Validate anchor eligibility and authorized scope.
2. Resolve the anchor through its versioned adapter.
3. Normalize every result to a full order ID.
4. Deduplicate by full order ID, never raw order number.
5. Stop and ask for narrowing when the configured cap is exceeded.
6. For each allowed result, read only the fields needed for candidate display and disambiguation.
7. Upsert candidate order nodes by full order ID with provenance and freshness.
8. Create or update the session candidate set.
9. Return only graph-read candidates to the agent.

## 6. Source authority and required adapters

| Source | Authority | Required use |
|---|---|---|
| `salesInv` | Order header, immutable line identity, historical order-line facts | Primary full-order source; exact reads by full order ID through writer-version adapters |
| `customerOutboundCDM` | Customer, account/logon, contact resolution | Resolve customer/account anchor and project minimal authorized customer/account context |
| `shipmentInfo` | Tracking, shipment, carrier, delivery evidence | Resolve tracking to full order IDs and hydrate shipment evidence |
| `invoiceMemosCDM` | Invoice-to-order and invoiced-quantity evidence | Resolve invoice anchor and hydrate invoice/line correlation |
| `lkpSearchProduct` | Current product/SKU enrichment | Enrich selected order lines; never replace historical line facts |
| `locationsCDM` | Branch and warehouse details | Resolve location references needed by the workflow |
| `orderOutbnd` | Legacy/migration fallback and source/channel hints | Capability-gated fallback; never default identity or shipment authority |
| `shipTo` | Ship-to context | Optional only after collection and join contracts are proven |
| `purchaseHistory_v1` | Eventually consistent narrowing evidence | Candidate narrowing only; never order authority |
| OMC V1/V2 sources | Prior-return and consumed-quantity evidence | Required during Order Analysis before return quantity is authorized |

Minimum adapter set:

```text
SalesInvEmbeddedOrderAdapter
SalesInvLineDocumentAdapter
CustomerCdmVersionedAdapter
ShipmentInfoVersionedAdapter
InvoiceMemosCdmAdapter
ProductSearchVersionedAdapter
LocationCdmVersionedAdapter
OrderOutbndLegacyFallbackAdapter
OmcV1ReturnHistoryAdapter
OmcV2ReturnHistoryAdapter
```

Every adapter declares supported schema versions, exact anchor types, identity extraction, authorized scope requirements, maximum result count, freshness, field allowlist, and failure behavior.

## 7. Minimal graph model

### 7.1 Business nodes

| Node | Identity | Minimum properties |
|---|---|---|
| `Customer` | canonical customer key | name/display reference only when required; source provenance |
| `CustomerAccount` | account/logon-qualified customer key | account/logon, customer reference, authorization attributes needed for discovery |
| `SalesOrder` | `fullOrderId` | raw order number, date, status, customer/account reference, PO/job hints, source/channel, warehouse references, freshness/provenance |
| `OrderLine` | `fullOrderLineId` | immutable line number, historical product IDs/description, ordered/shipped/invoiced quantities as available, UOM, line status, provenance |
| `Product` | validated product/master-product key | SKU/MPID, minimal current description, governed logistics facts needed later |
| `Shipment` | source shipment identity | tracking reference(s), carrier, status/dates, order/line correlation quality |
| `Invoice` | source invoice identity | invoice number/date/status and order/line correlation evidence |
| `Location` | canonical location key | branch/warehouse name and region fields needed by workflow |
| `PriorReturn` | version-qualified return identity | line, consumed quantity/status class, policy and provenance |

### 7.2 Workflow and control nodes when graph is the configured system store

```text
ReturnSession
EvidenceAnchor
CandidateSet
DiscoveryContext
DiscoveryLock
ReturnRequestContext
FulfillmentTrackingContext
BayStagingContext
WorkflowEvent
AgentDecision
LearningFeedback
SyncRequest
SyncAttempt
SchemaVersion
```

If another system-store provider is configured, equivalent records are stored there while the agent-facing business/context view remains available through the gateway.

### 7.3 Core relationships

```text
(Customer)-[:HAS_ACCOUNT]->(CustomerAccount)
(CustomerAccount)-[:PLACED]->(SalesOrder)
(SalesOrder)-[:CONTAINS]->(OrderLine)
(OrderLine)-[:REFERENCES_PRODUCT]->(Product)
(SalesOrder)-[:SHIPPED_AS]->(Shipment)
(SalesOrder)-[:BILLED_AS]->(Invoice)
(SalesOrder)-[:SOLD_AT]->(Location)
(PriorReturn)-[:RETURNED_FROM]->(OrderLine)

(ReturnSession)-[:CAPTURED]->(EvidenceAnchor)
(ReturnSession)-[:HAS_CANDIDATE_SET]->(CandidateSet)
(CandidateSet)-[:CANDIDATE]->(SalesOrder)
(DiscoveryContext)-[:CONFIRMS_ORDER]->(SalesOrder)
(DiscoveryContext)-[:CONFIRMS_LINE]->(OrderLine)
(DiscoveryLock)-[:LOCKS]->(OrderLine)
(ReturnSession)-[:REQUESTED_SYNC]->(SyncRequest)
```

### 7.4 Required provenance

Every synchronized business node includes, when applicable:

```text
sourceSystem
sourceAsset
sourceRecordId
sourceWriterSchemaVersion
sourceRevision
sourceUpdatedAt
graphSyncedAt
syncRequestId or backfillRunId
mappingVersion
identityQuality
evidenceDigest or opaque evidence reference
```

### 7.5 Prohibited data

Do not store in graph nodes, discovery locks, prompts, logs, traces, or frontend projections:

- payment tokens or payment authorization codes;
- card or account numbers, including masked values;
- cardholder, expiry, or billing-address data;
- raw phone/email when a keyed digest is sufficient;
- raw source payloads;
- full chat transcripts;
- invoice/document images or attachment bytes;
- shipping labels, POD files, or unrelated delivery telemetry;
- unrestricted AI prompts, responses, or chain-of-thought;
- secrets, credentials, worker heartbeats, or rate-limit internals.

## 8. Candidate, confirmation, and lock contracts

An `OrderCandidate` must retain:

```text
inputOrderReference
matchedReferenceType
fullOrderId
accountLogon
rawOrderNumber
sourceOrderDocumentId
sourceWriterSchemaVersion
sourceRevision
sourceTransactionId
webOrderNumber
trilogieOrderNumber
orderSource
identityQuality
identityEvidenceReferences
graphSyncedAt
```

A line candidate and locked line must retain:

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

The associate confirms the displayed order and line, not a source implementation detail. The sealed `DiscoveryContext` stores the exact identity, revision, mapping version, evidence references, and confirmation time shown to the associate.

Synthetic array-position line IDs are prohibited. A line without an authoritative immutable line number is `UNCONFIRMABLE`.

## 9. Configurable system-store bootstrap

### 9.1 Configuration contract

```yaml
systemStore:
  provider: NEO4J # NEO4J | MONGODB | POSTGRESQL | SQLSERVER
  schemaVersion: "1"
  autoCreate: true
  autoMigrate: true
  failOnDrift: true

businessGraph:
  provider: NEO4J
  schemaVersion: "order-discovery-v2"
  autoCreate: true
```

### 9.2 Provider-neutral repositories

Define interfaces for:

```text
SyncRequestRepository
SyncAttemptRepository
WorkflowContextRepository
DiscoveryLockRepository
IdempotencyRepository
OutboxRepository
SchemaVersionRepository
```

Provide implementations for Neo4j, MongoDB, PostgreSQL, and SQL Server. Business logic must not branch on provider names.

### 9.3 Bootstrap behavior

On startup/deployment:

1. validate provider configuration and credentials;
2. acquire a provider-specific migration lock;
3. read the current schema version;
4. create missing schema objects in dependency order;
5. apply forward-only migrations;
6. create required uniqueness, lookup, TTL, and status indexes;
7. verify the resulting schema using provider introspection;
8. record migration checksum and application time;
9. refuse startup when drift or an unsupported newer version is detected.

`autoCreate=false` changes the behavior to validation-only. It must not silently continue with missing objects.

### 9.4 Minimum system objects

Regardless of provider, logical objects include:

```text
schema_versions
sync_requests
sync_attempts
workflow_contexts
discovery_locks
idempotency_records
outbox_events
```

Names may follow provider conventions, but logical behavior and uniqueness contracts must remain identical.

## 10. Current repository assessment and required differences

| Current behavior | Required change |
|---|---|
| `GraphSyncScope.FULL` copies the latest bounded records from all Mongo and SQL assets | Rename/reclassify as administrative `BULK_PROJECTION` or backfill; introduce `FULL_ORDER_SYNC` requiring one full order ID |
| The Graph Sync API returns `202` but awaits the entire sync inline | Enqueue a durable request and return its ID; workers claim and execute asynchronously |
| Mongo sync reads top-N collection documents without an order identity boundary | Exact full-order reads for full sync; exact anchor resolution plus bounded ID set for partial sync |
| `SalesOrder` is merged by raw `sales_order_number` | Migrate uniqueness and all joins to `full_order_id` |
| Current canonical order key is `TDS:accountId:orderId:orderInstanceKey` | Reconcile the domain model with the confirmed `ACCOUNT/LOGON*ORDERNUMBER` identity; retain source instance/revision separately |
| Current source sync reads `salesHdrEventData.orderId` as order identity | Versioned adapters extract account/logon, raw number, physical document ID, writer version, and revision separately |
| Order lines can receive synthetic `<order>:LINE:<array position>` identities | Require immutable line number and generate `fullOrderLineId`; otherwise mark unconfirmable |
| Full Mongo projection omits `invoiceMemosCDM` and location/prior-return evidence | Add capability-gated invoice, location, and OMC adapters |
| Associate flow reads Mongo directly after graph miss and performs inline graph upsert | Replace with durable partial-sync request; agents re-query graph only |
| `request_graph_sync`, verification, source integrity, and candidate retrieval contain unconditional stubs | Replace with real services and receipt/readback validation |
| Graph projection YAML covers only Customer and CustomerAccount | Add declarative order, line, product, shipment, invoice, location, prior-return, and required relationship mappings |
| Sync pipeline YAML covers only the customer foundation | Add identity resolution, candidate projection, full-order hydration, verification, and cleanup stages |
| Mongo `graph_sync_runs` is hard-coded as control storage | Move control records behind the configurable system-store repositories |
| SQL source tables are assumed to exist | Add provider-specific schema migrations and startup verification for platform-owned system tables; authoritative external tables remain validation-only |

## 11. Repository implementation plan

### Phase 0 - Freeze contracts and terminology

Deliverables:

- approve `fullOrderId` and `fullOrderLineId` parsing/serialization rules;
- preserve letters, leading zeroes, and case according to source contract;
- separate raw/display aliases from canonical identity;
- publish JSON schemas for full and partial sync requests/results;
- rename the existing broad `FULL` mode to avoid ambiguity;
- add feature flags for new sync workers and graph-only discovery.

Primary files:

- `backend/src/return_platform/canonical/order.py`
- `backend/src/return_platform/operations/associate_flow.py`
- `backend/src/return_platform/data_platform/graph/sync_service.py`
- configuration models under `backend/src/return_platform/configuration/`

Exit criteria: the same raw order number under two account/logon values creates two distinct valid orders in every contract test.

### Phase 1 - Implement versioned source adapters

Deliverables:

- create the adapter interfaces and capability registry;
- implement embedded-order and line-document `salesInv` adapters;
- implement exact parsing without unsafe prefix widening;
- retain physical IDs, writer/schema version, revision, and evidence references;
- add customer, shipment, invoice, product, location, and OMC adapters in capability-gated order.

Primary files:

- new `backend/src/return_platform/data_platform/source_adapters/`
- `backend/config/data_platform/source_assets.yaml`
- `backend/src/return_platform/data_platform/schema_registry.py`

Exit criteria: fixture sets for every approved writer shape normalize to identical canonical identities and reject malformed or ambiguous records.

### Phase 2 - Correct and migrate the graph schema

Deliverables:

- add unique `SalesOrder.full_order_id`;
- add unique `OrderLine.full_order_line_id`;
- remove raw order-number uniqueness after dual-read migration;
- add invoice, shipment-identity, location, and prior-return constraints/indexes;
- define property allowlists and provenance requirements;
- expand declarative projection and pipeline configuration.

Primary files:

- new migration after `backend/src/return_platform/data_platform/graph/migrations/0013_order_discovery_fulltext_v2.cypher`
- `backend/config/data_platform/graph_projection.yaml`
- `backend/config/data_platform/sync_pipelines.yaml`
- graph schema registry and mapping compiler tests

Exit criteria: relationships cannot join by raw order number, and duplicate raw numbers across accounts remain isolated.

### Phase 3 - Build configurable system-store persistence and bootstrap

Deliverables:

- provider-neutral control repositories;
- Neo4j, MongoDB, PostgreSQL, and SQL Server implementations;
- provider migrations and schema introspection;
- migration lock, checksums, drift detection, and validation-only mode;
- startup health evidence that all configured logical objects exist.

Primary files:

- new `backend/src/return_platform/persistence/system_store/`
- `backend/src/return_platform/resources.py`
- `backend/src/return_platform/main.py`
- `backend/src/return_platform/configuration/settings.py`
- provider migration directories under `backend/migrations/`

Exit criteria: the same repository contract suite passes against every supported provider, and a clean database bootstraps without manual DDL.

### Phase 4 - Implement durable sync orchestration

Deliverables:

- `SyncRequest` and `SyncAttempt` contracts;
- durable enqueue/claim/lease/retry/cancel lifecycle;
- idempotency and deduplication by canonical identity and requested revision;
- per-source timeouts, bounded retries, and dead-letter/review states;
- readback verification and safe status APIs.

Primary files:

- replace `backend/src/return_platform/data_platform/operational_generation/graph_sync.py`
- replace `backend/src/return_platform/data_platform/graph/synchronization.py`
- refactor `backend/src/return_platform/data_platform/graph/sync_service.py`
- update `backend/src/return_platform/data_console/api/graph_sync.py`
- update the operational-generation graph-sync adapter

Exit criteria: the API returns a durable request immediately, a worker completes it exactly once logically, and readback proves the graph revision.

### Phase 5 - Implement partial anchor-to-order synchronization

Deliverables:

- typed resolver registry for the four primary anchor families;
- exact, authorized, bounded resolution to full order IDs;
- candidate projection profile;
- result caps and `REQUIRES_NARROWING` behavior;
- session candidate-set persistence;
- no raw sensitive anchors in durable stores.

Primary files:

- replace `backend/src/return_platform/operations/order_discovery/source_operations.py`
- replace `backend/src/return_platform/operations/order_discovery/candidate_retriever.py`
- new resolver/orchestration modules under `operations/order_discovery/`
- update discovery configuration and policy schemas

Exit criteria: order, tracking, invoice, and customer/account scenarios resolve to full order IDs and return candidates only after graph readback.

### Phase 6 - Implement full-order hydration

Deliverables:

- full sync request requiring exactly one full order ID;
- exact header/line acquisition for embedded and line-document writers;
- minimal related-source hydration;
- atomic subgraph update and stale relationship cleanup;
- provenance/revision digest verification;
- full-order freshness receipt used by confirmation.

Primary files:

- refactored graph sync service and new projectors/materializers;
- projection mappings and Cypher templates;
- source adapter integration tests

Exit criteria: selecting a candidate results in a complete, source-revalidated order subgraph containing only approved return-process fields.

### Phase 7 - Convert Order Discovery to graph-only operation

Deliverables:

- implement `AgentGraphGateway` fixed methods;
- replace direct Mongo fallback and inline Neo4j upsert in `associate_flow.py`;
- query graph, request partial sync on eligible miss/stale state, then re-query;
- invoke full sync for the selected candidate before confirmation;
- seal discovery only against the verified source revision;
- keep smart questions deterministic in field selection and conversational in wording.

Primary files:

- `backend/src/return_platform/operations/associate_flow.py`
- `backend/src/return_platform/agents/order_discovery.py`
- new graph gateway and order-discovery services
- frontend candidate and context components

Exit criteria: architecture tests fail if any agent/discovery module imports a source database client or raw Neo4j driver.

### Phase 8 - Correct confirmation, line locks, and concurrency

Deliverables:

- immutable full order/line IDs in candidate, lock, and sealed context;
- optimistic source-revision checks;
- expiring line leases and conflict detection;
- idempotent confirmation and workflow events;
- no confirmation for ambiguous or synthetic line identity.

Primary files:

- discovery contracts and repositories
- workflow orchestrator
- graph/system-store lock implementation

Exit criteria: two sessions cannot confirm the same protected line concurrently, and a source revision change forces refresh and reconfirmation.

### Phase 9 - Supply Order Analysis evidence

Deliverables:

- revalidate selected order and line;
- aggregate ordered, shipped, invoiced, and prior-return quantities with UOM;
- preserve V1/V2 return status policy and provenance;
- calculate remaining returnable quantity deterministically;
- keep payment/refund credentials outside the model;
- publish immutable Order Analysis output to downstream workflow.

Exit criteria: aggregate over-return is prevented across prior return systems, and unresolved evidence produces a safe non-authorizing outcome.

### Phase 10 - Backfill, shadow validation, and cutover

Deliverables:

- retain an explicit administrative backfill mode separate from full-order sync;
- dual-write/read old and new identities only during a bounded migration window;
- compare candidate sets, identities, revisions, and quantities in shadow mode;
- migrate old graph nodes and remove raw-number uniqueness;
- enable graph-only discovery by cohort;
- remove legacy inline source fallback and unconditional stubs after acceptance.

Exit criteria: production metrics and scenario receipts meet agreed thresholds, rollback remains available, and no active path relies on legacy raw-order identity.

## 12. Required API surface

Minimum endpoints or equivalent commands:

```text
POST /order-sync/full
POST /order-sync/partial
GET  /order-sync/requests/{requestId}
POST /order-sync/requests/{requestId}/cancel
POST /data-console/v1/graph-sync/backfills
GET  /data-console/v1/graph-sync/backfills/{runId}
POST /data-console/v1/system-schema/validate
POST /data-console/v1/system-schema/apply
```

The associate-facing API should not expose internal source queries. It returns candidate summaries, sync state, safe error codes, and suggested next evidence.

## 13. Real-time scenario acceptance matrix

| Scenario | Required behavior |
|---|---|
| Associate provides exact full order ID | Full ID validates; partial candidate projection may be skipped; full-order sync runs before line confirmation |
| Raw order number exists under two logons | Resolver asks for account/logon or another strong anchor; records never merge |
| Exact web order number | Web resolver retains web reference and evidence-bearing mapping to one or more full order IDs |
| Exact tracking number for split shipment | Resolve all correlated full order IDs/lines; do not assume tracking identifies one return line |
| Invoice spans multiple orders | Hydrate bounded candidates grouped by full order ID and ask associate to select |
| Customer/account has many recent orders | Enforce cap and ask date, PO, job, SKU, or location question |
| Phone/email resolves multiple accounts | Do not expose raw contact; ask account/job/location narrowing question |
| PO repeats within account | Require date, job, product, or another exact anchor |
| SKU only | Do not source-sync globally; request customer/account and bounded date evidence |
| Graph miss, source match | Create partial sync, verify graph write, re-query graph, then display candidate |
| Graph stale before confirmation | Run full-order sync and require reconfirmation if displayed facts changed |
| Source has no immutable line number | Show evidence if useful but prohibit line confirmation |
| Source and graph disagree | Source wins only through a verified sync; record discrepancy and refresh candidate |
| Graph unavailable | Return retryable unavailable state; never bypass graph from the agent path |
| Source unavailable | Preserve retryable request; suggest another permitted anchor without fabricating results |
| Concurrent confirmation | One lease succeeds; the other session receives a conflict and refreshed availability |

## 14. Test strategy

### 14.1 Identity tests

- same raw order number under different logons;
- alphabetic and leading-zero order numbers;
- embedded header with lines;
- separate line documents;
- malformed composite IDs;
- source document ID distinct from display order number;
- missing immutable line number;
- no prefix widening for exact IDs.

### 14.2 Resolver tests

- zero, one, many, and over-limit results for every strong anchor;
- invoice spanning orders;
- split shipment and multiple tracking references;
- customer/account authorization boundaries;
- repeated PO values;
- stale purchase-history evidence;
- capability disabled and unsupported writer version.

### 14.3 Synchronization tests

- durable request lifecycle and lease recovery;
- idempotent duplicate requests;
- retry after source and graph transient failures;
- atomic graph replacement and stale relationship cleanup;
- readback identity/revision/digest verification;
- partial projection followed by full hydration;
- cancellation and expiry;
- no graph write for unauthorized or invalid identity.

### 14.4 Provider bootstrap tests

- empty Neo4j, MongoDB, PostgreSQL, and SQL Server stores;
- repeated bootstrap is a no-op;
- concurrent bootstrap lock;
- missing index repair;
- incompatible schema version;
- checksum drift;
- validation-only configuration;
- repository contract parity across providers.

### 14.5 Privacy and architecture tests

- prohibited fields never enter projection commands, events, logs, API payloads, or prompts;
- contact anchors are HMAC-protected and evidence references expire;
- agents cannot import database clients or execute arbitrary Cypher;
- candidate limits and authorization are enforced in every path;
- graph outage cannot activate a direct-source agent fallback.

### 14.6 End-to-end workflow tests

- each scenario in Section 13;
- candidate selection triggers full-order sync;
- source revision change invalidates confirmation;
- sealed discovery binds exact order/line IDs;
- Order Analysis uses the confirmed full-order projection and authoritative prior-return evidence;
- downstream return, fulfillment, and bay workflows receive only confirmed context.

## 15. Observability and operational requirements

Track:

- sync request counts by type, anchor family, resolver, and terminal state;
- resolution cardinality and result-limit events;
- partial and full sync latency percentiles;
- graph miss, stale, refresh, and verification-failure rates;
- candidate count and clarification turns per session;
- source/graph revision discrepancies;
- identity conflicts and unconfirmable line rates;
- provider bootstrap version and drift status;
- prohibited-field scanning and access-denial counts.

Logs use request IDs, full-order ID digests where necessary, source record references allowed by policy, and safe error codes. Raw contact evidence and unrestricted source payloads are prohibited.

## 16. Rollout and rollback

1. Introduce new contracts and schema behind feature flags.
2. Deploy provider bootstrap in validation-only mode.
3. Apply graph/system migrations and enable dual-read compatibility.
4. Run administrative backfill using canonical full order IDs.
5. Shadow partial resolvers and compare with current discovery results.
6. Enable durable partial sync for internal/test cohorts.
7. Enable selected-order full sync and revision-bound confirmation.
8. Enable graph-only agent access by cohort.
9. Remove direct source fallback and legacy raw-number graph uniqueness.
10. Retire compatibility fields after the observation window.

Rollback disables the new orchestration feature flags and restores the previous read path without deleting newly written canonical nodes or system records. Destructive cleanup occurs only after the migration window and explicit approval.

## 17. Definition of done

The implementation is complete only when all of the following are true:

- full-order sync requires exactly one validated full order ID;
- partial sync resolves strong anchors to full order IDs before any order graph write;
- raw order numbers are never global graph identities;
- selected candidates receive full-order hydration before discovery confirmation;
- agents use only the graph gateway and cannot bypass it during graph misses;
- graph nodes contain only the minimal return-process projection;
- invoice, shipment, customer/account, and order-reference resolvers pass real-time scenarios;
- order lines use immutable source line numbers and synthetic lines cannot be confirmed;
- source revision and provenance are bound to the sealed discovery context;
- control storage can run on Neo4j, MongoDB, PostgreSQL, or SQL Server;
- the configured provider creates or validates every required system object automatically;
- unconditional graph/source integrity stubs have been removed;
- administrative backfill is clearly separated from full-order and partial-order sync;
- Order Analysis receives sufficient verified evidence to prevent aggregate over-return;
- privacy, authorization, concurrency, and recovery tests pass;
- rollout receipts prove graph readback and end-to-end workflow completion.

## 18. Implementation directive

Implement identity first, then persistence/bootstrap, then durable synchronization, then agent cutover. Do not patch the current direct-source fallback into a larger monolith.

The invariant for every order write is:

```text
No validated fullOrderId -> no SalesOrder graph write.
```

The invariant for every discovery confirmation is:

```text
No verified full-order sync at the required source revision
-> no sealed DiscoveryContext.
```

These two rules are the foundation for the remaining Return Workflow, Fulfillment, Bay Allocation, and Learning Agent implementation.
