# Ferguson Returns Order Discovery: Graph-Only Agent Architecture and Implementation Plan

**Date:** 2026-08-02  
**Status:** Consolidated implementation baseline  
**Scope:** Order Discovery and the agent-used state required to complete the return workflow  
**Supersedes as the working plan:** `ORDER_DISCOVERY_ORDER_ID_AND_ANCHOR_FINDINGS.md`, `ORDER_ANALYSIS_MANUAL_MAPPING_RECONCILIATION.md`, and `ORDER_DISCOVERY_FIELD_CORRECTIONS_AND_STRONG_ANCHORS.md`

## 1. Final architecture decision

The implementation will follow three non-negotiable rules.

1. **Neo4j is the only business-data and workflow-context read/write plane exposed to agents.**
2. **Neo4j stores only the fields and relationships needed to discover an order and complete the return process.**
3. **When required data is missing or stale in Neo4j, a strong anchor creates an on-demand synchronization request. A privileged non-agent worker resolves the source, writes the minimal projection to Neo4j, and the agent queries Neo4j again.**

This is a graph-only **agent boundary**, not a claim that Neo4j replaces every platform infrastructure store.

- Agents must not receive MongoDB, SQL Server, source-collection, filesystem, object-store, or arbitrary-Cypher clients.
- Agents interact through fixed, authorized graph commands and queries.
- Source adapters, synchronization workers, outbox dispatchers, audit services, attachment stores, telemetry, rate limits, and worker heartbeats may use their appropriate infrastructure stores.
- Data in those infrastructure stores is not directly queryable by an agent. The graph contains only an approved business projection or an opaque evidence reference.
- If Neo4j is unavailable, Order Discovery fails closed with a retryable platform-unavailable result. It must not bypass the graph and return source records directly to an agent.

### 1.1 Target request path

```text
Associate UI / API
        |
        v
Return Session Orchestrator
        |
        v
Agent Graph Gateway  <-------------------------------+
        |                                             |
        v                                             |
Neo4j exact lookup and workflow context               |
        |                                             |
        +-- candidate found and fresh -> rank/confirm |
        |                                             |
        +-- missing or stale + strong anchor          |
                    |                                 |
                    v                                 |
             SyncRequest in Neo4j                     |
                    |                                 |
                    v                                 |
       Privileged On-Demand Sync Worker               |
                    |                                 |
                    v                                 |
     Versioned source adapter reads authority         |
                    |                                 |
                    v                                 |
   Validate -> normalize -> minimal graph upsert -----+
```

The agent never consumes the raw source response. It consumes the synchronized graph result.

## 2. Proposal assessment

The proposal in `ferguson_returns_agentic.docx` provides a useful workflow and agent decomposition, but its data-access and graph-hydration sections require correction before implementation.

### 2.1 Proposal elements retained

- Human-in-the-loop execution for Order Discovery and return request preparation.
- Separate responsibilities for session orchestration, discovery, return workflow, fulfillment, bay staging, and learning.
- Structured context chain:
  - `ReturnSessionContext`
  - `IntakeContext`
  - `DiscoveryContext`
  - `ReturnRequestContext`
  - `FulfillmentTrackingContext`
  - `BayStagingContext`
  - `LearningFeedbackContext`
- Field-driven smart questions.
- Exact resolver paths selected by configuration.
- Associate confirmation before discovery is sealed.
- Versioned configuration with reviewed learning recommendations.
- Evidence-bound workflow transitions and package checklists.

### 2.2 Proposal elements changed

| Proposal statement | Required correction |
|---|---|
| Agents search the graph and call source resolvers | Agents search and write only through the Graph Gateway. A sync worker owns all source calls. |
| Context Store and Workflow Event Store are directly used by agents | Agent-owned business contexts, decisions, locks, and workflow events are graph nodes/relationships. Non-agent operational projections may remain elsewhere. |
| `salesInv._id` is the canonical order identity | Keep `canonicalOrderKey` and exact source `_id` separate. |
| Order is globally unique by displayed order number | Order identity is account/logon scoped. |
| `salesInv._id = ACCOUNT*ORDERNUMBER` is universal | Physical `_id` shape is writer-version-specific. Support header-document and line-document shapes through adapters. |
| `salesHdr.salesHdrData.orderId` or a parsed `_id` is always the order number | Treat those as conditional writer aliases. Do not generically split `_id`. |
| `salesDtl[]` is the order-line path | Current repository/manual evidence uses `salesLines[]` variants. Resolve through a writer adapter. |
| Customer join uses `party.customerId -> orderCust` | Current repository uses top-level customer authority fields and `salesHdr.salesHdrData.custId` as an order snapshot/reference. Join semantics remain adapter-owned. |
| Product join uses `lkpSearchProduct._id -> salesDtl.itemNumber` | Current repository uses `productId`, `sku`, and `masterProductId` candidates. Cardinality must be validated. |
| Every supporting collection is available | Invoice, location, ship-to, purchase-history, inbound-invoice, and outbound-order capabilities are absent or incomplete in the current runtime and must be capability-gated. |
| Graph hydration includes all source relationships and fields | Use a strict graph property allowlist. Store only process-required data and provenance. |
| Candidate score can establish a likely match | Scoring orders already source-backed candidates. It never converts a fuzzy or unscoped match into identity proof. |

## 3. Customer-facing Ferguson process constraints

The current Ferguson public process supports these design conclusions:

- Authorized users see online and offline orders for the master customer and accessible main/job accounts.
- Exact and partial order-number search exists, but results are constrained by account access.
- Online order numbers begin with `W`.
- Customer PO, job, status, and date are narrowing inputs.
- An order may be split for fulfillment.
- Tracking is reached through an order and can expose several items and quantities; it is shipment evidence, not automatic line identity.
- Product purchase history covers online and in-store purchases across the organization, can lag by 24 hours, and is limited to approximately 12 months.
- Product history and partial order search are discovery aids, not authoritative confirmation.
- Return policy belongs to eligibility after discovery. It must not influence which order is selected.

Official process references:

- [How to Use My Orders](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-use-my-orders/)
- [How to Use Order Tracking](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-use-order-tracking/)
- [How to Find Product Purchase History](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-find-product-purchase-history/)
- [Returns and Cancellations](https://www.ferguson.com/content/customer-support/returns-cancellations/)

## 4. Current repository assessment

### 4.1 Implemented foundation

- Neo4j driver, schema migrations, graph write/readback utilities, evidence queries, and graph-backed configuration releases exist.
- The associate discovery flow already queries Neo4j first.
- Current seeded graph synchronization projects customer, customer account, order, line, product, shipment, and warehouse relationships.
- Phone and email graph lookup use HMAC digests instead of raw contact values.
- Human confirmation, candidate TTL, confirmation locks, context snapshots, workflow contexts, agent decisions, and audit concepts exist.
- Production configuration defines order, customer, shipment, and product source collections.

### 4.2 Critical current-state gaps

| Current implementation | Gap against target |
|---|---|
| `AssociateConversationService` receives both `source_client` and `graph` | Agent-facing orchestration can directly query MongoDB source collections. |
| `_source_documents` reads `salesInv`, customer, and shipment collections | Violates graph-only agent access. |
| `_targeted_graph_upsert` writes Cypher directly from discovery candidates | Bypasses canonical mapping, source validation, provenance requirements, and a privileged sync boundary. |
| Graph lookup uses `STARTS WITH` for order, tracking, customer, and SKU paths | Exact identifiers can be treated as partial matches. |
| Graph failure activates direct source fallback | New architecture requires fail-closed graph behavior. |
| Missing line identity creates `<order>:LINE:<array-position>` | Synthetic array position can become confirmable identity. |
| `OrderCandidate` and `DiscoveryLock` retain overloaded order fields | Canonical key, raw order number, source IDs, writer version, revision, and matched-reference type are missing. |
| `SalesOrder` canonical model uses `TDS:account:order:instance` while discovery uses raw order number | Canonical and discovery identity contracts do not align. |
| `SalesOrder.source_document_id` is forced to `account*order` | Cannot represent multiple physical writer shapes safely. |
| Canonical `SalesOrder` includes `payment_authorization_code` | Payment fields must not enter Order Discovery or the graph. |
| Constraint `uq_sales_order_number` makes raw order number globally unique | Same displayed number can collide across logons/accounts. |
| `graph_projection.yaml` contains only Customer and CustomerAccount | Order Discovery graph mappings are not configuration-owned. |
| `sync_service.py` contains hard-coded seed-oriented projections | It is not a production on-demand, adapter-driven synchronization service. |
| `CandidateRetriever` and `SourceOperations` return stubs | No production graph-candidate or source-integrity contract exists. |
| `SynchronizationManager` only forwards a generated-data run | No anchor-triggered sync request lifecycle exists. |
| Agent-used contexts, snapshots, locks, and decisions are stored directly in MongoDB | Agents do not yet use graph-only business memory. |

### 4.3 Current internal collections and their target disposition

| Current collection / store | Target disposition |
|---|---|
| `associate_conversations` | May remain an API/UI transport projection. Agents do not query it. Authoritative agent stage and business context move to graph. |
| `associate_messages` | Keep full transcript outside graph when required for UI/audit. Agents receive the current turn from orchestration; graph stores only extracted evidence, summaries, and references. |
| `discovery_snapshots` | Replace agent authority with versioned `DiscoveryContext` graph nodes. Optional non-agent archive projection may remain. |
| `return_request_snapshots` | Replace agent authority with versioned `ReturnRequestContext` graph nodes. |
| `discovery_locks` | Move the active line lease and confirmation binding to a transactional graph model. |
| `agent_decisions` | Store minimal decision nodes/events in graph; telemetry projection may remain outside graph. |
| `returns`, `return_items`, fulfillment and bay records | Graph becomes the agent-facing business state. Existing collections may remain integration/read-model projections owned by non-agent services. |
| `events` | Agent-visible workflow events move to graph. Durable audit/event export may remain outside graph. |
| `document_artifacts` and attachments | Keep blobs outside graph; store content digest, type, authorization, and opaque evidence reference only. |
| `integration_outbox`, OMC command records, worker heartbeats, rate limits, seed metadata, AI attempts/traces | Remain infrastructure-only and unavailable to agents. |
| Graph-backed configuration release | Retain. Agents receive only the active, approved policy through the Graph Gateway/orchestrator. |

## 5. Correct identity contract

### 5.1 One user-facing reference

The UI and conversation use one input:

```text
inputOrderReference
```

The resolver classifies the value without collapsing the resulting identities.

| Submitted form | Resolver interpretation |
|---|---|
| `LOGON*ORDERNUMBER` | Exact canonical order-key lookup |
| Exact `W...` value | Exact web-order lookup and evidence-bearing canonical mapping |
| Exact source document identifier | Writer-version-aware source-ID lookup |
| Raw order number | Exact lookup inside authorized account/logon scopes |
| Partial value | Candidate search only; never direct binding |

Persist after resolution:

- `inputOrderReference`
- `matchedReferenceType`
- `canonicalOrderKey`
- `accountLogon`
- `rawOrderNumber`
- `sourceOrderDocumentId`
- `sourceWriterSchemaVersion`
- `sourceRevision`
- `sourceTransactionId` when available
- `webOrderNumber` when available
- `trilogieOrderNumber` when available
- `identityEvidenceReferences`

### 5.2 Canonical order and line identity

```text
canonicalOrderKey =
    accountLogon + '*' + rawOrderNumber

canonicalOrderLineKey =
    'ORDERLINE:' + canonicalOrderKey + '*' + immutableLineNumber
```

The namespace on `canonicalOrderLineKey` prevents it from being confused with a physical `salesInv._id`.

Known physical source shapes must be supported by versioned adapters:

```text
Header-document writer:
    sourceOrderDocumentId = LOGON*ORDERNUMBER
    lines may be embedded

Line-document writer:
    sourceLineDocumentId = LOGON*ORDERNUMBER*LINENUMBER
    order is grouped by the validated LOGON and ORDERNUMBER components

Other writer/version:
    exact shape defined by that adapter contract
```

Do not infer the shape by counting delimiters without first selecting the source writer/version.

If an immutable source line number cannot be established, mark the line `UNCONFIRMABLE`. Never generate a confirmable identity from array position.

## 6. Strong-anchor model

Anchor strength is the combination of:

```text
exact value
+ authorized scope
+ authoritative resolver
+ evidence-bearing mapping
+ graph freshness
```

String shape alone does not make an anchor strong.

| Anchor | Sync eligibility | Scope and confirmation rule |
|---|---|---|
| Exact canonical order key | **Yes** | Exact authorized key; refresh order and lines before sealing |
| Exact internal source order/line document ID | **Yes** | Internal-only; writer-version adapter must validate and map it |
| Exact web order number | **Yes** | Map to canonical order with retained web/ERP evidence |
| Exact tracking number | **Yes** | Resolve shipment to one or more scoped orders; associate selects the order/line |
| Exact invoice number | **Yes when adapter enabled** | Resolve invoice lines, then canonical orders and lines; invoices may span orders |
| Exact return/RMA number | **Yes when adapter enabled** | Resolve original canonical order/line and prior-return evidence |
| Raw order number | **Only with account/logon scope** | Never treat as globally unique |
| Phone/email | **Yes for customer-account discovery** | Use HMAC lookup in graph; source sync is bounded by authorization and may yield multiple accounts |
| Customer PO | **Composite only** | Require account/job and usually date or product |
| Delivery ticket | **Only after source contract/index validation** | Exact resolver required |
| SKU/item/product | **Composite only** | Require customer/account plus date or another strong order anchor |
| Customer name/company/ZIP/address hint | **No standalone sync** | Narrow graph candidates only |
| Partial order/tracking/invoice text | **No direct binding** | Graph candidate search only; require exact confirmation |
| Free text or fuzzy similarity | **No** | Ask a smart question |

### 6.1 Resolver outcomes

| Outcome | Agent behavior |
|---|---|
| Fresh exact graph candidate | Show candidate and authoritative line projection |
| Graph miss with eligible strong anchor | Create bounded sync request and wait/retry graph query |
| Graph data stale for confirmation | Request source revalidation, then re-query graph |
| Source returns zero exact candidates | Ask for another anchor; do not fuzzy-correct an ID |
| Source returns one exact scoped candidate | Upsert minimal projection, re-query graph, show candidate |
| Source returns multiple scoped candidates | Upsert only authorized minimal candidates and ask for account/job/PO/date/product |
| Weak anchor only | Search graph; if insufficient, ask for a stronger anchor |
| Graph unavailable | Return retryable unavailable status; no source bypass |
| Source unavailable during sync | Preserve request/error state and ask the associate to retry or use another permitted anchor |

## 7. Source authority and capability status

| Source | Authority role | Current repository status | Target use |
|---|---|---|---|
| `salesInv` | Order header, immutable line, historical item and quantity snapshot | Configured and directly queried today | Primary sync authority through versioned adapters |
| `customerOutboundCDM` | Customer/account/contact resolution | Configured; current schema uses top-level fields and `custAccts` | Sync minimal customer/account nodes and contact digests |
| `shipmentInfo` | Tracking, shipment, carrier, shipping evidence | Configured and graph-projected | Resolve tracking to scoped canonical orders; add line correlation when contracted |
| `lkpSearchProduct` | Product/SKU enrichment | Configured and graph-projected | Minimal product projection; validate `productId`/`masterProductId` joins |
| `invoiceMemosCDM` | Invoice and invoiced quantity | Not implemented | Capability-gated adapter; invoice-line-to-order-line evidence |
| `locationsCDM` | Branch/warehouse details | Not implemented | Capability-gated location enrichment |
| `shipTo` | Ship-to master/context | Not implemented | Do not add until collection and join contract are proven |
| `purchaseHistory_v1` | Eventually consistent product/customer order search | Not implemented | Narrowing sync only; never authority and never used for purchases under its lag window |
| `orderOutbnd` | Legacy/migration fallback and source hints | Not active in discovery | Capability-gated worker adapter; never default order or shipment authority |
| `inbndSalesInv` | Historical/inbound enrichment | Not implemented | No identity role; add only with an approved business need |
| OMC V1/V2 return sources | Prior-return quantity/status evidence | Existing broader platform concepts, incomplete in this discovery path | Sync minimal prior-return facts needed for eligibility |

Every optional adapter must advertise:

- enabled/disabled capability;
- supported writer/schema versions;
- exact anchor types;
- authorized scope requirements;
- maximum candidate count;
- freshness policy;
- field allowlist;
- identity-quality rules;
- failure and discrepancy behavior.

## 8. Minimal graph projection

Neo4j is not a source-system replica. Each property must answer one of four questions:

1. Is this the correct authorized customer/order/line?
2. Is the item and quantity eligible to proceed?
3. What is the current return/fulfillment/staging state?
4. What source evidence and freshness prove the answer?

### 8.1 Business nodes

| Node | Minimum properties |
|---|---|
| `CustomerAccount` | `customerAccountKey`, `customerId`, `accountLogon`, limited display name/type, authorization-scope reference, phone/email HMAC digests when required, provenance/freshness |
| `SalesOrder` | `canonicalOrderKey`, `accountLogon`, `rawOrderNumber`, `sourceOrderDocumentId`, writer version, revision, source transaction ID when available, customer account key, PO/job, order status/date, source channel, web/Trilogie references when present, selling/ship-from warehouse references, provenance/freshness |
| `OrderLine` | `canonicalOrderLineKey`, immutable line number, source line document ID when applicable, source/master product IDs, SKU/alternate code, historical description, ordered/shipped/invoiced/previously-returned quantities required for the process, UOM, identity quality, provenance/freshness |
| `Product` | Stable product key, source/master product ID, SKU/UPC when used, current short description, UOM, serial-required indicator, minimal substitution/obsolescence facts needed by the return process, provenance/freshness |
| `Shipment` | Tracking number, canonical order link, carrier/method, shipment/delivery status and dates needed for discovery/fulfillment, bounded item correlation when available, provenance/freshness |
| `Invoice` | Invoice number/key, canonical order and line relationships, invoice date, invoiced quantities needed for return validation, provenance/freshness |
| `Location` | Warehouse/branch key, limited display name/type/city/state needed for routing, provenance/freshness |
| `PriorReturn` | Return/RMA number, canonical order/line links, quantity, status, version/source, provenance/freshness |
| `WebOrderReference` | Exact web order number and evidence-bearing `RESOLVES_TO` relationship to canonical order |

Separate nodes are optional when a property on the owning business node provides the same bounded query and lifecycle. Do not create a node merely because a source collection exists.

### 8.2 Agent workflow nodes

| Node | Purpose and minimum content |
|---|---|
| `ReturnSession` | Session ID, actor/branch scope, current stage, status, active configuration release, timestamps |
| `EvidenceAnchor` | Type, normalized digest, strength, source/evidence reference, capture time, authorization scope; no unnecessary raw PII |
| `CandidateSet` | Version, expiry, bounded candidate relationships, matched/conflicting evidence references |
| `DiscoveryContext` | Versioned draft/sealed status and relationships to confirmed customer account, order, and line |
| `DiscoveryLock` | Transactional line lease, session, expiry, canonical identities, source revision, confirmation actor/time |
| `ReturnRequestContext` | Reason, condition, requested quantity, package facts, support-request status, confirmed evidence relationships |
| `FulfillmentTrackingContext` | Return number, support/label/pickup status, next action |
| `BayStagingContext` | Bay/staging reference, package count, checklist state, carrier handoff state |
| `WorkflowEvent` | Event type, stage transition, actor, time, idempotency/digest reference |
| `AgentDecision` | Decision type, bounded explanation/code, configuration version, confidence, evidence relationships, human-confirmation requirement |
| `LearningFeedback` | Aggregated outcome/rework signals and reviewed recommendation references; no raw transcript |
| `SyncRequest` | Strong-anchor digest/ref, scope, resolver, status, timestamps, result count, error code, freshness request |

### 8.3 Core relationships

```text
(CustomerAccount)-[:PLACED]->(SalesOrder)
(SalesOrder)-[:CONTAINS]->(OrderLine)
(OrderLine)-[:REFERENCES_PRODUCT]->(Product)
(SalesOrder)-[:SHIPPED_AS]->(Shipment)
(SalesOrder)-[:BILLED_AS]->(Invoice)
(SalesOrder)-[:SOLD_AT]->(Location)
(WebOrderReference)-[:RESOLVES_TO]->(SalesOrder)
(PriorReturn)-[:RETURNED_FROM]->(OrderLine)

(ReturnSession)-[:CAPTURED]->(EvidenceAnchor)
(ReturnSession)-[:HAS_CANDIDATE_SET]->(CandidateSet)
(CandidateSet)-[:CANDIDATE]->(SalesOrder)
(DiscoveryContext)-[:CONFIRMS_ORDER]->(SalesOrder)
(DiscoveryContext)-[:CONFIRMS_LINE]->(OrderLine)
(DiscoveryLock)-[:LOCKS]->(OrderLine)
(ReturnSession)-[:HAS_CONTEXT]->(DiscoveryContext)
(ReturnSession)-[:HAS_CONTEXT]->(ReturnRequestContext)
(ReturnSession)-[:HAS_CONTEXT]->(FulfillmentTrackingContext)
(ReturnSession)-[:HAS_CONTEXT]->(BayStagingContext)
(ReturnSession)-[:EMITTED]->(WorkflowEvent)
(ReturnSession)-[:HAS_DECISION]->(AgentDecision)
(ReturnSession)-[:REQUESTED_SYNC]->(SyncRequest)
```

### 8.4 Required provenance on synchronized business nodes

- `sourceSystem`
- `sourceAsset`
- `sourceRecordId`
- `sourceWriterSchemaVersion`
- `sourceRevision`
- `sourceUpdatedAt`
- `graphSyncedAt`
- `syncRequestId` or scheduled sync run ID
- `mappingVersion`
- `identityQuality`
- evidence digest/reference where required

### 8.5 Data prohibited from the graph

- payment authorization codes;
- card/account numbers, including masked values;
- payment tokens;
- cardholder name, expiry, and billing address;
- raw source documents or unbounded source payloads;
- raw phone/email when an HMAC lookup is sufficient;
- complete addresses unless an approved process step requires a minimal subset;
- full chat transcripts;
- attachment/image bytes;
- shipping labels or proof-of-delivery files;
- broad product catalog or purchase-history replication;
- driver photos or unrelated delivery telemetry;
- AI chain-of-thought or unrestricted prompt/response bodies;
- infrastructure secrets, credentials, rate-limit state, or worker internals.

The graph stores digests, bounded summaries, and opaque references when the process requires evidence without the underlying payload.

## 9. Graph-only agent interface

Agents receive a single `AgentGraphGateway` capability with fixed methods. They do not receive a raw Neo4j driver.

Minimum commands and queries:

- `get_session(session_id, actor_scope)`
- `append_evidence(session_id, evidence_envelope)`
- `resolve_exact_order_reference(reference, actor_scope)`
- `search_scoped_candidates(anchor_set, actor_scope, limit)`
- `get_order_lines(canonical_order_key, actor_scope)`
- `request_on_demand_sync(anchor_ref, actor_scope, freshness_requirement)`
- `get_sync_status(sync_request_id, actor_scope)`
- `create_candidate_set(session_id, candidates, expiry)`
- `seal_discovery_context(expected_version, confirmed_identities, source_revision)`
- `acquire_line_lock(canonical_order_line_key, session_id, expiry)`
- `release_line_lock(lock_id, reason)`
- `write_return_request_context(expected_version, payload)`
- `write_fulfillment_context(expected_version, payload)`
- `write_bay_staging_context(expected_version, payload)`
- `append_workflow_event(idempotency_key, event)`
- `append_agent_decision(decision)`

All methods enforce:

- actor and account/job authorization;
- fixed query templates;
- bounded results;
- exact-match behavior for identifiers;
- optimistic version checks;
- idempotency;
- property allowlists;
- safe audit codes;
- timeouts and cancellation;
- no arbitrary Cypher supplied by an LLM or agent.

## 10. On-demand synchronization design

### 10.1 Trigger conditions

Create a sync request only when:

- the graph has no authorized result for an eligible strong anchor;
- the graph result is older than the configured freshness threshold;
- confirmation requires a newer source revision;
- a known relationship is missing, such as web order -> canonical order or tracking -> order;
- the active adapter capability supports the anchor and scope.

Do not sync from free text, an unscoped partial identifier, or a weak/fuzzy-only signal.

### 10.2 Request lifecycle

```text
REQUESTED
  -> CLAIMED
  -> SOURCE_RESOLVED
  -> VALIDATED
  -> GRAPH_COMMITTED
  -> COMPLETED

Terminal alternatives:
  NO_MATCH
  MULTIPLE_MATCHES
  UNAUTHORIZED
  SOURCE_UNAVAILABLE
  INVALID_SOURCE_SHAPE
  IDENTITY_CONFLICT
  GRAPH_WRITE_FAILED
  EXPIRED
```

### 10.3 Secure anchor handling

- Graph `SyncRequest` stores the anchor type, normalized digest, authorized scope, and an opaque short-lived evidence reference.
- Raw phone, email, address, or document image stays in the approved evidence vault or request boundary.
- The sync worker dereferences the evidence under service authorization.
- Non-sensitive exact identifiers may be stored only when they are needed for graph lookup and allowed by policy.
- Request evidence expires independently from durable business nodes.

### 10.4 Worker algorithm

1. Claim one `SyncRequest` transactionally.
2. Resolve the active configuration release and adapter capability.
3. Validate actor/account/job scope before source lookup.
4. Read the authoritative source with an exact, bounded query.
5. Select the writer/schema adapter from source metadata.
6. Normalize canonical order and line identities.
7. Reject invalid, ambiguous, or synthetic line identities.
8. Build a property-allowlisted graph projection.
9. Validate provenance, source revision, cardinality, and cross-source mappings.
10. Commit nodes, relationships, and sync status atomically where possible.
11. Perform fixed-query readback and digest validation.
12. Mark the request terminal and emit a bounded workflow event.
13. The orchestrator re-runs the original graph query.

### 10.5 Freshness and confirmation

Candidate display can use graph data within the configured discovery freshness window. Sealing `DiscoveryContext` requires:

- one selected canonical order;
- one confirmable canonical line;
- current authorization;
- source revision/freshness within the confirmation threshold;
- no active conflicting line lock;
- associate confirmation.

If the candidate is stale, the agent requests revalidation. The sync worker refreshes the graph, and confirmation uses the new graph revision. The agent never receives a direct source re-read.

## 11. Correct end-to-end agent workflow

### 11.1 Session start

1. API authenticates the associate and resolves permitted organization/main/job scopes.
2. Orchestrator creates `ReturnSession` in Neo4j with the active configuration release.
3. Full UI messages may be persisted by the non-agent conversation service.
4. The agent receives the current user turn plus the bounded graph session context.

### 11.2 Order Discovery

1. Capture `inputOrderReference` or another approved anchor.
2. Normalize without destroying leading zeroes, letters, or meaningful delimiters.
3. Store an `EvidenceAnchor` digest/reference in graph.
4. Query Neo4j through fixed exact/scoped methods.
5. If missing or stale and eligible, create `SyncRequest`.
6. Sync worker resolves source and commits the minimal graph projection.
7. Re-query Neo4j.
8. Intersect multiple anchors by `canonicalOrderKey`, never by display number alone.
9. Present a bounded candidate set.
10. Ask one high-value question if zero or multiple candidates remain.
11. Associate confirms customer/account, order, source channel when required, and immutable line.
12. Revalidate stale data through the sync worker.
13. Acquire a graph line lock and seal the versioned `DiscoveryContext`.

### 11.3 Return request

1. Return Workflow Agent reads the sealed `DiscoveryContext` from graph.
2. It captures only reason, condition, quantity, package facts, notes summary, and evidence references required by policy.
3. Eligibility service uses graph-projected order, line, prior-return, invoice, shipment, product, and policy facts.
4. Agent writes a versioned `ReturnRequestContext`.
5. Associate confirms the prepared request.
6. Agent writes a support-command intent node/event. A non-agent dispatcher sends or copies the request through the approved integration.

### 11.4 Support and fulfillment

1. Integration worker records support acknowledgement, return number, label/pickup status, or error in graph.
2. Fulfillment Agent reads graph state only.
3. It writes `FulfillmentTrackingContext` and next-associate action.
4. Raw labels, documents, and external response bodies remain outside graph with evidence references.

### 11.5 Bay staging and carrier handoff

1. Bay Agent reads the confirmed line, return number, package count, method, and fulfillment readiness from graph.
2. It writes staging guidance and checklist state to `BayStagingContext`.
3. Associate confirmation creates the handoff workflow event.
4. Operational warehouse systems consume a non-agent projection or command; the agent does not write those tables directly.

### 11.6 Learning

1. Learning Agent reads finalized graph contexts and bounded workflow events.
2. It writes aggregate `LearningFeedback` signals, not transcripts or raw source data.
3. Recommendations require human review.
4. Approved changes publish a new graph-backed configuration release for future sessions.

## 12. Implementation plan

The target is a full replacement of direct agent/source/internal-table access. The phases below define safe delivery order, not a permanent dual architecture.

### Phase 0 - Freeze architecture and enforce dependency boundaries

Deliverables:

- `AgentGraphGateway` protocol and fixed command/query contract.
- `OnDemandSyncWorker` and versioned source-adapter protocols.
- Graph property allowlist and prohibited-field policy.
- Architectural test that fails if agent packages or agent-facing orchestration import MongoDB/SQL clients or raw Neo4j driver APIs.
- Feature flags for graph-only discovery, on-demand sync, and graph-context authority.

Primary changes:

- Add `backend/src/return_platform/operations/order_discovery/graph_gateway.py`.
- Add `backend/src/return_platform/operations/order_discovery/sync_requests.py`.
- Add `backend/src/return_platform/operations/order_discovery/source_adapters/`.
- Update dependency construction in `associate_service_factory.py`, API dependencies, and `main.py`.

Exit gate:

- Agents and agent-facing services compile and test with only the graph gateway and approved external action tools.

### Phase 1 - Replace the order identity contract

Deliverables:

- Canonical order identity `LOGON*ORDERNUMBER`.
- Namespaced canonical order-line identity based on immutable line number.
- Separate physical source order/line IDs.
- Unified `inputOrderReference` and `matchedReferenceType`.
- Writer/schema version, source revision, transaction ID, and evidence references in candidate/context contracts.
- Payment fields removed from Order Discovery canonical and graph mappings.

Primary changes:

- Rewrite identity validation in `backend/src/return_platform/canonical/order.py`.
- Update `backend/src/return_platform/agents/contracts.py`.
- Update `OrderCandidate`, `OrderLineCandidate`, and `DiscoveryLock` contracts now located in `associate_flow.py`; preferably move them to dedicated discovery contracts.
- Update `backend/config/schema_registry.yaml`.
- Update generated API/frontend contracts after backend contract stabilization.

Exit gate:

- Two identical raw order numbers under different logons do not collide.
- Header-document and line-document `salesInv` fixtures map to the same canonical order when appropriate.
- No array-position identity can be confirmed.

### Phase 2 - Define the minimal Order Discovery graph schema

Deliverables:

- Configuration-owned canonical mappings for SalesOrder, OrderLine, Product, Shipment, Invoice, Location, PriorReturn, and required agent contexts.
- Minimal graph projection mapping for each enabled entity.
- New constraints and exact indexes based on canonical keys.
- Retention/freshness metadata and property-allowlist validation.

Primary changes:

- Expand `backend/config/data_platform/canonical_mappings.yaml`.
- Expand `backend/config/data_platform/graph_projection.yaml`.
- Add new additive migrations after `0013`; do not rewrite already-applied migrations.
- Replace global raw-order uniqueness with `canonical_order_key` uniqueness.
- Add exact indexes for scoped raw order, web order, tracking, invoice, return/RMA, PO/account, line key, contact digests, sync request, context ID/version, and lock lease key.
- Update graph schema validation, mapping compiler, commands, writer, and readback.

Exit gate:

- Schema migration and mapping compiler reject non-allowlisted fields and invalid identities.
- Sensitive-field scan finds no payment/card/contact plaintext in graph commands or readback.

### Phase 3 - Implement production on-demand synchronization

Deliverables:

- Transactional `SyncRequest` repository in Neo4j.
- Worker claim/lease/idempotency behavior.
- Production adapters for currently available sources:
  - `salesInv`
  - `customerOutboundCDM`
  - `shipmentInfo`
  - `lkpSearchProduct`
- Capability-gated adapter shells for invoice, location, prior-return, ship-to, purchase-history, order-outbound, and inbound-invoice sources.
- Canonical validation, minimal projection, graph commit, and readback proof.

Primary changes:

- Replace the `SourceOperations.verify_source_integrity` stub with adapter-owned validation.
- Replace `SynchronizationManager.enqueue_sync` with graph request lifecycle operations.
- Refactor `data_platform/graph/sync_service.py` from seed/hard-coded projection to mapping-driven production synchronization.
- Reuse strict graph command/write/readback infrastructure rather than constructing Cypher in the discovery service.

Exit gate:

- An exact graph miss creates one idempotent sync request, reads the source once per policy, writes the minimal graph, validates readback, and enables the same graph query to succeed.

### Phase 4 - Replace candidate discovery with graph-only access

Deliverables:

- Production `CandidateRetriever` backed by fixed, authorized Neo4j queries.
- Exact typed resolver for `inputOrderReference`.
- Scoped raw-order, tracking, invoice, web-order, return/RMA, customer, PO, product, and date query methods.
- No direct source reads or graph writes in `AssociateConversationService`.
- No source fallback when graph is unavailable.

Primary changes:

- Remove `source_client` and `self._source` from `AssociateConversationService`.
- Delete/replace `_source_documents`, `_source_candidate`, `_direct_source_query`, and `_targeted_graph_upsert`.
- Replace `_graph_candidates` inline Cypher with `AgentGraphGateway` methods.
- Replace exact-ID `STARTS WITH` behavior with exact equality. Keep partial search only in explicitly labeled candidate-search methods.
- Implement `candidate_retriever.py` and remove its fixed stub result.
- Update production discovery configuration to declare anchor type, exact/partial mode, scope, sync eligibility, freshness, result bound, and adapter.

Exit gate:

- Static and runtime tests prove that discovery can complete with source network clients unavailable to the API/agent process.

### Phase 5 - Move agent-owned context, decisions, and locks to graph

Deliverables:

- Versioned context nodes with optimistic concurrency.
- Candidate set TTL and evidence relationships.
- Transactional discovery line lock/lease.
- Graph workflow events and agent decisions.
- Sealed context immutability and digest validation.

Primary changes:

- Replace direct `discovery_snapshots`, `return_request_snapshots`, `discovery_locks`, and agent-decision authority with graph repositories.
- Refactor `workflows/persistence.py` and `production_return_state.py` so agents consume graph context.
- Keep UI transcript and infrastructure projections behind non-agent services.
- Add a background projection/export path where MongoDB read models or audit archives remain required.

Exit gate:

- Two concurrent sessions cannot acquire the same active line lease.
- A stale candidate set or context version cannot be confirmed.
- Agent recovery after restart reconstructs required business state from graph.

### Phase 6 - Complete source authorities and eligibility evidence

Deliverables:

- Invoice line mapping and invoiced quantity.
- V1/V2 prior-return evidence and quantity consumption policy.
- Shipment line correlation for split/multiple shipments.
- Product UOM, serial requirement, and required logistics facts.
- Location enrichment.
- Capability-gated delivery-ticket, purchase-history, ship-to, and legacy outbound resolvers only after contracts are proven.

Exit gate:

- Eligibility quantity is computed from source-attributed ordered, shipped, invoiced, and previously returned quantities without relying on a single overloaded status field.

### Phase 7 - Convert downstream agents to graph-only state

Deliverables:

- Return Workflow, Fulfillment, Bay, and Learning agents read/write only through the Graph Gateway.
- External effects use intent/command nodes plus non-agent dispatchers.
- Support responses, return number, label/pickup readiness, staging, and handoff become bounded graph state.
- Learning uses aggregates and reviewed configuration updates.

Primary changes:

- Refactor `agents/return_workflow.py`, `agents/fulfillment.py`, `agents/bay_assignment.py`, and `agents/feedback.py`.
- Refactor workflow activities and return-support integration boundaries.
- Preserve outbox/idempotency infrastructure outside graph but remove direct agent access.

Exit gate:

- An end-to-end return can be reconstructed from graph contexts and evidence references without an agent querying platform MongoDB tables.

### Phase 8 - Backfill, shadow validation, and cutover

Deliverables:

- Mapping-driven backfill of the configured active order window using only the minimal projection.
- On-demand synchronization for records outside the active window.
- Shadow comparison between current discovery output and graph-only output.
- Collision, missing-line, stale-source, authorization, and sensitive-data reports.
- Final removal of temporary compatibility paths.

Cutover sequence:

1. Apply additive graph constraints/indexes.
2. Deploy sync worker and adapters disabled.
3. Enable backfill for a bounded authorized window.
4. Enable on-demand sync in shadow mode.
5. Compare identities, cardinality, and selected lines.
6. Enable graph-only reads for internal/test users.
7. Enable graph context writes and graph locks.
8. Enable graph-only production discovery.
9. Convert downstream agents.
10. Remove direct source and agent-used Mongo paths after the rollback window.

Rollback:

- Disable the new agent workflow release and stop new graph context writes.
- Do not fall back to raw source reads inside agents.
- Re-enable the previous application release only as an explicit operational rollback, not a runtime per-request bypass.

## 13. File-level change map

| Repository area | Required change |
|---|---|
| `backend/src/return_platform/operations/associate_flow.py` | Remove source client, inline Cypher, direct source fallback, synthetic line IDs, and Mongo authority for agent contexts/locks |
| `backend/src/return_platform/operations/order_discovery/candidate_retriever.py` | Implement fixed graph queries, typed results, authorization, exact/partial separation, bounds, freshness |
| `backend/src/return_platform/operations/order_discovery/source_operations.py` | Move to worker-side adapter validation; remove unconditional success |
| `backend/src/return_platform/agents/order_discovery.py` | Rank only graph/evidence-backed candidates; do not classify identity solely from string pattern |
| `backend/src/return_platform/agents/contracts.py` | Add canonical/source identities, reference type, revision/freshness, identity quality, context versions |
| `backend/src/return_platform/canonical/order.py` | Align canonical identity, physical IDs, line identity, provenance, and remove prohibited payment field |
| `backend/src/return_platform/configuration/return_configuration.py` | Model graph-only access, resolver capabilities, strong-anchor sync eligibility, freshness, projection allowlists |
| `backend/config/returns/production.yaml` | Remove unsafe path fallbacks; configure typed resolvers and capability gates |
| `backend/config/schema_registry.yaml` | Register canonical key, raw number, source identity/version/revision, immutable line number, minimal source facts |
| `backend/config/data_platform/canonical_mappings.yaml` | Add validated mappings for enabled Order Discovery entities |
| `backend/config/data_platform/graph_projection.yaml` | Add minimal business and context projections; prohibit broad source replication |
| `backend/src/return_platform/data_platform/graph/sync_service.py` | Replace seed-specific hard-coded writes with production adapter/mapping-driven sync |
| `backend/src/return_platform/data_platform/graph/writer.py` and readback | Enforce allowlists, canonical identities, transactional writes, provenance and digest validation |
| `backend/src/return_platform/data_platform/graph/evidence_query.py` | Add fixed agent queries and authorization-safe result models |
| `backend/src/return_platform/data_platform/graph/migrations/` | Add canonical-order, line, anchor, context, sync, and lock constraints/indexes in a new migration |
| `backend/src/return_platform/configuration/graph_repository.py` | Retain graph-backed configuration and expose approved active release through gateway |
| `backend/src/return_platform/workflows/persistence.py` | Move agent-owned contexts/events to graph; retain non-agent projections where needed |
| `backend/src/return_platform/operations/repository.py` | Separate infrastructure repositories from agent-facing business state |
| `backend/src/return_platform/operations/associate_service_factory.py` and API dependencies | Inject Graph Gateway only; inject source adapters into sync worker process only |
| `backend/src/return_platform/workers/` | Add on-demand sync worker and graph-request claim loop |
| `frontend/src/features/operations/order_discovery/` | Use one order-reference input, show sync/retry status, scoped candidates, evidence/freshness, and immutable line confirmation |
| Backend/frontend tests and generated contracts | Replace overloaded fields and direct-fallback expectations with graph-only scenarios |

## 14. Required real-time scenarios

| Scenario | Expected behavior |
|---|---|
| Exact `DALLAS*0672657` exists and is fresh | Exact graph result; no source sync |
| Raw `0672657` exists under one authorized logon | Scoped exact result |
| Raw `0672657` exists under two authorized logons | Multiple candidates; ask account/job |
| Raw number exists only under an unauthorized account | No disclosure and no unauthorized graph hydration |
| Exact `W...` web order is absent from graph | Strong-anchor sync maps web reference to canonical order, then graph re-query |
| Exact tracking number is absent | Shipment sync produces one or more scoped orders; no automatic line selection |
| Exact invoice maps to several orders | Show grouped scoped candidates and invoice-line evidence |
| SKU/product only | Search graph; require account/date/order evidence before on-demand order hydration |
| Customer phone/email maps to several accounts | Show authorized account choices only |
| Purchase is under 24 hours old | Do not rely solely on purchase history; exact source adapter path is used when a strong anchor exists |
| Same SKU appears on two lines | Require immutable line selection |
| Source has no immutable line number | Mark line `UNCONFIRMABLE` and route to review |
| Candidate source revision changes before confirmation | Sync refresh invalidates stale candidate set; associate reconfirms |
| Graph is unavailable | Retryable unavailable response; no direct source fallback |
| Source is unavailable | Sync request records safe error; graph remains authoritative |
| Two associates confirm the same line | One graph lease succeeds; the other receives conflict/retry |
| Payment fields exist in `salesInv` | Mapping/allowlist excludes them; sensitive-field test passes |

## 15. Test and acceptance plan

### 15.1 Architecture enforcement

- Agent and agent-facing service modules have no MongoDB/SQL client dependency.
- Agents have no raw Neo4j driver or arbitrary-Cypher interface.
- Source adapters are constructed only in the sync worker process.
- Graph outage never activates a source-data response path.

### 15.2 Identity

- Canonical order key is account/logon scoped.
- Leading zeroes, letters, and delimiters are preserved.
- Same raw order number across logons does not collide.
- Source order and line document IDs remain separate from canonical IDs.
- All supported writer fixtures select the correct adapter.
- Missing immutable line number cannot produce a confirmable line.

### 15.3 On-demand sync

- Only eligible strong anchors create sync requests.
- Duplicate request digest/scope/freshness creates one idempotent active request.
- Zero, one, and multiple source results produce the correct terminal status.
- Unauthorized results are neither written nor disclosed.
- Minimal projection and provenance readback match the source evidence digest.
- Partial graph write cannot be reported as completed.

### 15.4 Minimal-data and privacy

- Every graph label has a property allowlist.
- Payment/card fields are absent from graph, prompts, traces, logs, and agent contexts.
- Raw phone/email/address and attachment bytes are absent where digests/references suffice.
- Full source payloads and transcripts are absent.
- Retention removes expired candidate sets, evidence requests, locks, and superseded draft contexts according to policy.

### 15.5 Workflow and concurrency

- Context version conflicts are rejected.
- Sealed discovery is immutable.
- Lock acquisition is atomic and expiry/release is auditable.
- Return quantity cannot exceed verified available quantity.
- Support, fulfillment, staging, and handoff transitions require their graph context guards.
- All external actions are idempotent and dispatched by non-agent services.

### 15.6 Completion definition

Implementation is complete only when:

- Order Discovery and downstream agents can finish the supported return flow using graph reads/writes only.
- A graph miss or stale result can be repaired through strong-anchor on-demand sync.
- No agent-visible response is built from a direct source fallback.
- Canonical order and line identities are collision-safe and source-proven.
- Graph contains only approved process-required properties.
- Current enabled sources have production adapters and disabled sources are explicitly capability-gated.
- End-to-end tests cover discovery, confirmation, return request, support response, fulfillment, staging, handoff, and learning feedback.

## 16. Source-contract questions that must be closed during implementation

These do not change the architecture. Until answered, the related adapter remains disabled or marks identity conditional.

1. Which `salesInv` writer/schema versions are active?
2. Which versions use header documents, embedded lines, or separate line documents?
3. What exact field provides account/logon for every writer?
4. Is `salesHdrEventData.orderId` a raw number, composite ID, or alias in each version?
5. What is the authoritative immutable line-number path in each version?
6. What maps a `W...` web order to canonical ERP order, and can it map to multiple orders?
7. What are the exact invoice-to-order-line and shipment-to-order-line cardinalities?
8. Are delivery-ticket values indexed and unique within a defined scope?
9. Which location, ship-to, purchase-history, outbound, and inbound collections are production-approved?
10. What source revisions and timestamps support confirmation-time revalidation?
11. What OMC V1/V2 statuses and quantities consume returnable quantity?
12. What active-window and retention duration should be pre-hydrated versus on-demand?

## 17. Evidence reviewed

### Proposal and manual package

- `ferguson_returns_agentic.docx`
- `SalesInv.xlsx`
- `Product and Order Data Analysis.docx`
- `MongoDB_Fields_Required_For_Ferguson_Returns_AI_POC.docx`
- `mongo db collection.docx`

### Existing consolidated inputs

- `docs/ORDER_DISCOVERY_ORDER_ID_AND_ANCHOR_FINDINGS.md`
- `docs/ORDER_ANALYSIS_MANUAL_MAPPING_RECONCILIATION.md`
- `docs/ORDER_DISCOVERY_FIELD_CORRECTIONS_AND_STRONG_ANCHORS.md`

### Current repository evidence

- `backend/src/return_platform/operations/associate_flow.py`
- `backend/src/return_platform/operations/order_discovery/source_operations.py`
- `backend/src/return_platform/operations/order_discovery/candidate_retriever.py`
- `backend/src/return_platform/agents/order_discovery.py`
- `backend/src/return_platform/canonical/order.py`
- `backend/src/return_platform/data_platform/graph/sync_service.py`
- `backend/src/return_platform/data_platform/graph/synchronization.py`
- `backend/src/return_platform/data_platform/graph/writer.py`
- `backend/src/return_platform/data_platform/graph/readback.py`
- `backend/src/return_platform/data_platform/graph/evidence_query.py`
- `backend/src/return_platform/configuration/graph_repository.py`
- `backend/src/return_platform/workflows/persistence.py`
- `backend/config/returns/production.yaml`
- `backend/config/schema_registry.yaml`
- `backend/config/data_platform/canonical_mappings.yaml`
- `backend/config/data_platform/graph_projection.yaml`
- `backend/src/return_platform/data_platform/graph/migrations/0012_order_discovery_fulltext.cypher`
- `backend/src/return_platform/data_platform/graph/migrations/0013_order_discovery_fulltext_v2.cypher`

## 18. Final implementation directive

Build Order Discovery around a graph-only agent contract:

```text
Agents read graph.
Agents write bounded graph context and intent.
Agents never read source or internal tables directly.

Sync workers read authoritative sources.
Sync workers write minimal validated graph projections.
Sync workers never expose raw source payloads to agents.

Strong anchors repair graph misses.
Weak anchors ask questions.
Human confirmation seals canonical order and line identity.
```

This directive is the baseline for the repository changes.
