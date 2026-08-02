# Final Order Discovery and Modular Configuration Implementation Plan

**Status:** Final authoritative plan, revision 2  
**Date:** 2026-08-02  
**Scope:** Order Discovery, canonical order synchronization, independent context-only agents, modular configuration ownership, schema-driven Configuration UI, metadata-driven sources and graph, Graph Schema Design Agent, schema redesign/import/export, configurable persistence, Order Analysis, migration, testing, and rollout  

This is the single governing implementation plan. It consolidates and supersedes all earlier Order Discovery, graph sync, configurable graph, Graph Schema Design Agent, and configuration plans where they differ.

## 1. Confirmed current state

Configuration is not globally stored in only one file. The repository already has separate top-level files for AI gateway, dependency simulation, data assets, schema registry, source definitions, canonical mappings, graph projection, and sync pipelines.

However, the business return configuration is substantially centralized:

- `backend/config/returns/production.yaml` contains all business-agent entries plus discovery, source resolution, clarification, return policy, workflow, support, OMC, bay, integrations, extensions, feature flags, and runtime integrations.
- `ReturnPlatformConfiguration` validates that combined object, including an `agents` dictionary.
- Graph-backed configuration releases store the broad `RETURN_PLATFORM` domain as one payload.
- `ConfigurationStudioPage.tsx` displays the selected domain as a raw JSON textarea and saves the entire parsed object.

Therefore the current implementation is partially separated by broad domain, but it does not provide per-agent ownership or a proper schema-driven configuration form.

## 2. Final architecture decisions

### 2.1 Operational users

Interactive users are:

```text
ASSOCIATE | SALES_REPRESENTATIVE | CUSTOMER_CARE
```

The platform is not described as customer-facing. Administrators, data stewards, source owners, security owners, and architects manage configuration and schema governance.

### 2.2 Canonical order identity

```text
fullOrderId = ACCOUNT_OR_LOGON + "*" + ORDERNUMBER
fullOrderLineId = fullOrderId + "*" + IMMUTABLE_LINE_NUMBER
```

Order number, web order number, Trilogie number, source transaction ID, MongoDB `_id`, and source line ID are retained separately. Raw order number is never a globally unique graph identity.

### 2.3 Full and partial synchronization

`FULL_ORDER_SYNC` requires exactly one `fullOrderId` and synchronizes all authoritative lines for that order.

```text
SalesOrder: ACCOUNT1*ORDER100
    |- OrderLine: ACCOUNT1*ORDER100*1
    |- OrderLine: ACCOUNT1*ORDER100*2
    `- OrderLine: ACCOUNT1*ORDER100*3
```

`PARTIAL_ORDER_SYNC` resolves an approved strong anchor to zero, one, or more `fullOrderId` values and writes a bounded discovery projection for those orders.

After the operational user selects an order, full sync hydrates the entire selected order before one or more lines can be confirmed.

### 2.4 Independent context-only agents

Every agent:

1. receives an immutable versioned context;
2. validates context schema, configuration snapshot, and authorization;
3. uses only capabilities referenced by that context;
4. creates a new versioned output context;
5. does not call another business agent directly;
6. does not access another agent's memory, transcript, database, or private state;
7. is independently deployable, testable, retryable, and replayable;
8. is idempotent for the same context version and idempotency key.

The orchestrator routes context references. Context is the only business handoff.

### 2.5 Modular configuration ownership

Each agent owns an independent configuration module. Shared concerns are separate modules owned by the appropriate platform capability.

One file/module must not combine all agents and unrelated policies.

An atomic runtime release is a manifest of immutable module versions and checksums. Separation must not allow incompatible module versions to activate independently.

### 2.6 Schema-driven Configuration UI

Raw JSON is not the primary editor.

The Configuration page renders typed fields from configuration schema and UI metadata. It supports nested objects, lists, maps, references, conditional fields, and inline validation.

JSON and YAML are available only for preview, download/export, and governed upload/import.

### 2.7 Metadata-driven source and graph models

New tables, collections, APIs, fields, graph nodes, relationships, constraints, indexes, and projection profiles normally require configuration, not a new source-specific application class.

The stable runtime kernel contains generic envelopes, identity/security invariants, connectors, mapping validation, schema compilation, context/version rules, and synchronization execution.

### 2.8 Governed Graph Schema Design Agent

The independent `GraphSchemaDesignAgent` operates through versioned context and appears as a chat inside the Configuration page.

It creates or redesigns graph configuration based on selected sources, actual structures, existing configuration, the requested graph capability, and unresolved validation gaps.

It does not use a fixed questionnaire and cannot approve, activate, migrate, backfill, or run production sync.

## 3. Independent agent contracts

| Agent | Owned configuration module | Input context | Output context |
|---|---|---|---|
| Return Session Orchestrator | `agent.return_session_orchestrator` | `SessionCommandContext` | `ReturnSessionContext` |
| Order Discovery Agent | `agent.order_discovery` | Session and discovery input contexts | `DiscoveryContext` |
| Order Analysis Agent | `agent.order_analysis` | Sealed discovery context | `OrderAnalysisContext` |
| Return Workflow Agent | `agent.return_workflow` | Discovery and analysis contexts | `ReturnRequestContext` |
| Return Fulfillment Agent | `agent.return_fulfillment` | Return request and support-event contexts | `FulfillmentTrackingContext` |
| Bay Allocation Agent | `agent.bay_allocation` | Fulfillment context | `BayStagingContext` |
| Learning Agent | `agent.learning` | Final context references and bounded events | `LearningFeedbackContext` |
| Graph Schema Design Agent | `agent.graph_schema_design` | `SchemaDesignContext` | `SchemaProposalContext` |

An agent module may reference shared modules but may not embed or silently override them.

## 4. Modular configuration architecture

### 4.1 Target repository structure

```text
backend/config/
  manifest.yaml

  agents/
    return_session_orchestrator.yaml
    order_discovery.yaml
    order_analysis.yaml
    return_workflow.yaml
    return_fulfillment.yaml
    bay_allocation.yaml
    learning.yaml
    graph_schema_design.yaml

  contexts/
    return_session.schema.json
    discovery_input.schema.json
    discovery.schema.json
    order_analysis.schema.json
    return_request.schema.json
    fulfillment_tracking.schema.json
    bay_staging.schema.json
    schema_design.schema.json
    schema_proposal.schema.json

  workflows/
    return_session.yaml
    transitions.yaml
    approvals.yaml

  policies/
    clarification.yaml
    candidate_scoring.yaml
    return_eligibility.yaml
    privacy.yaml
    authorization.yaml
    retention.yaml
    freshness.yaml

  sources/
    sales_inv.yaml
    customer_outbound_cdm.yaml
    shipment_info.yaml
    invoice_memos_cdm.yaml
    product_search.yaml
    locations_cdm.yaml
    order_outbound.yaml
    omc_v1_returns.yaml
    omc_v2_returns.yaml

  mappings/
    canonical/
    graph/

  graph/
    schemas/
    projection_profiles/
    query_capabilities/

  sync/
    order_partial.yaml
    order_full.yaml
    administrative_backfill.yaml

  integrations/
    ai_gateway.yaml
    return_support.yaml
    omc.yaml
    messaging.yaml

  platform/
    runtime.yaml
    feature_flags.yaml
    dependency_simulation.yaml
    system_store.yaml
```

Physical files are the version-controlled baseline. Graph/system-store configuration releases use the same logical module boundaries.

### 4.2 Module envelope

Every module uses a common envelope:

```yaml
moduleId: agent.order_discovery
moduleType: AGENT
schemaVersion: "1"
configurationVersion: "2.1.0"
owner: ORDER_DISCOVERY_TEAM
status: DRAFT
dependencies:
  - moduleId: policy.clarification
    versionConstraint: "^2.0"
  - moduleId: sync.order_partial
    versionConstraint: "^1.0"
payload:
  enabled: true
  executionMode: HUMAN_APPROVAL_REQUIRED
```

Required envelope fields:

```text
moduleId
moduleType
schemaVersion
configurationVersion
owner
status
dependencies
payload
checksum
createdAt
createdBy
```

### 4.3 Agent-owned configuration

Each agent module contains only settings owned by that agent:

- enabled state and execution mode;
- accepted input context types/versions;
- emitted output context type/version;
- allowed capability references;
- AI route/model policy reference when applicable;
- deterministic decision thresholds owned by the agent;
- prompt/template references owned by the agent;
- timeout, retry, and idempotency behavior;
- human-approval and escalation behavior;
- agent-specific observability settings.

An agent module does not own:

- database credentials;
- source table/collection definitions;
- graph node/relationship definitions;
- global privacy or authorization rules;
- shared workflow transitions;
- another agent's fields or thresholds.

Those are references to shared modules.

### 4.4 Shared configuration modules

| Module group | Owner | Examples |
|---|---|---|
| Context schemas | Platform contract owner | Discovery, analysis, return request contexts |
| Workflow | Orchestration owner | Transitions, approvals, preconditions |
| Sources | Data/source owner | Connections by reference, structures, identities, watermarks |
| Mappings | Data platform owner | Source-to-canonical and canonical-to-graph mappings |
| Graph | Graph platform owner | Nodes, relationships, constraints, indexes, query capabilities |
| Sync | Data platform owner | Partial, full, and backfill projection profiles |
| Policies | Named business/security owner | Clarification, scoring, eligibility, privacy, authorization |
| Integrations | Integration owner | AI gateway, OMC, support, messaging |
| Platform | Platform owner | Runtime, feature flags, system store |

### 4.5 Atomic release manifest

```yaml
releaseId: returns-platform-2026.08.02-1
status: ACTIVE
modules:
  agent.order_discovery:
    version: "2.1.0"
    checksum: sha256-value
  policy.clarification:
    version: "2.0.0"
    checksum: sha256-value
  source.sales_inv:
    version: "3.0.0"
    checksum: sha256-value
  graph.order_discovery:
    version: "2.0.0"
    checksum: sha256-value
dependencyLockDigest: sha256-value
```

Activation validates all dependency constraints and creates one immutable runtime snapshot. Agents pin the release ID carried by their input context.

### 4.6 Dependency rules

- No cyclic module dependencies.
- Exact released versions are locked in an active manifest.
- Draft modules may use version constraints; activation resolves and locks them.
- Breaking context or graph changes require a new major version.
- A module cannot activate if a dependency is missing, incompatible, unapproved, or archived.
- Shared-module changes identify every affected agent and workflow before approval.

## 5. Schema-driven Configuration UI

### 5.1 Navigation

The Configuration page navigation is grouped by concern:

```text
Agents
Contexts
Workflows
Policies
Sources
Mappings
Graph
Synchronization
Integrations
Platform
Releases
```

Selecting an agent opens only that agent's owned configuration and dependency references.

### 5.2 Typed field renderer

The UI renderer uses JSON Schema or an equivalent governed configuration schema plus optional UI hints.

| Schema type | UI control |
|---|---|
| string | Text input |
| long string/template | Multiline input with length/placeholder guidance |
| enum | Select, radio group, or segmented control |
| boolean | Switch or checkbox |
| integer/number | Numeric input with min/max/step |
| duration | Value plus time-unit control |
| secret/reference | Searchable reference selector; never raw secret input |
| module reference | Searchable compatible-module selector |
| object | Nested collapsible section/card |
| array of scalar | Repeatable chips/list |
| array of object | Repeatable nested cards with add/remove/reorder |
| map | Key/value table with duplicate-key validation |
| one-of/conditional | Type selector followed by matching nested fields |
| identity expression | Guided expression builder using allowlisted operations |
| field path | Source-aware path picker |
| relationship | From/to entity selectors plus join/cardinality controls |

### 5.3 Nested values

Nested configuration must be editable without JSON.

Example:

```yaml
candidateScoring:
  thresholds:
    highConfidence: 0.85
    ambiguityGap: 0.10
  weights:
    orderReference: 100
    invoiceNumber: 95
    trackingNumber: 95
```

UI representation:

```text
Candidate Scoring
  Thresholds
    High confidence     [0.85]
    Ambiguity gap      [0.10]
  Weights
    Order reference    [100]
    Invoice number     [95]
    Tracking number    [95]
```

Nested arrays render as repeatable rows/cards. Maps render as editable key/value tables. The UI shows full breadcrumb paths such as `candidateScoring.thresholds.highConfidence`.

### 5.4 Validation

Validation occurs at:

- field level while typing;
- nested section level;
- module level on save;
- dependency level on validate;
- release level before activation.

Errors identify field path, expected type/range, policy, and suggested resolution. Invalid values never become a released module.

### 5.5 Editing workflow

```text
Open module
  -> create draft version
  -> edit typed fields
  -> validate field/module
  -> inspect dependency impact and diff
  -> save draft
  -> submit review
  -> approve module
  -> include in release manifest
  -> validate atomic release
  -> activate
```

Active module versions are immutable. Editing creates a new version.

### 5.6 Advanced representation

The UI may show read-only generated JSON/YAML for transparency. It must not require users to edit raw JSON.

Download and upload follow the governed package process in Section 11.

## 6. Generic source and graph configuration

### 6.1 Generic runtime contracts

```text
ConfigurationModule
ReleaseManifest
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
ImportPackageManifest
```

### 6.2 Source configuration

Source modules declare:

- connector type;
- connection reference;
- database/schema/table or database/collection;
- resource/API identifiers;
- structural profiles and schema fingerprints;
- record identity expressions;
- field types, nullability, and nesting;
- watermarks and freshness;
- indexes and candidate joins;
- sensitivity, authorization, and retention;
- permitted resolver capabilities.

The expression language is allowlisted. Arbitrary Python, JavaScript, SQL, shell, or Cypher is prohibited.

### 6.3 Graph configuration

Graph modules declare:

- node labels and canonical entities;
- unique keys;
- allowed properties and types;
- relationships, direction, joins, and cardinality;
- constraints and indexes;
- provenance fields;
- partial/full/backfill projection profiles;
- supported graph query capabilities;
- migration and replacement policy.

### 6.4 Generic pipeline

```text
Active source module
  -> connector plan
  -> SourceRecordEnvelope
  -> active mapping module
  -> CanonicalEntityEnvelope / RelationshipEnvelope
  -> active graph module
  -> property-allowlisted graph commands
  -> graph readback and verification
```

## 7. Strong anchors and synchronization

### 7.1 Anchor families

| Family | Resolution |
|---|---|
| Order reference | Exact full order ID, or raw/web/source reference mapped within authorized scope |
| Shipment | Exact tracking -> shipment/order mapping -> full order IDs |
| Invoice | Exact invoice -> invoice lines/order mapping -> full order IDs |
| Customer/account | Exact authorized identity or approved contact digest -> bounded full order IDs |

PO, delivery ticket, RMA, or SKU require configured composite scope. Names, addresses, descriptions, dates, partial IDs, and fuzzy text only narrow candidates.

### 7.2 Partial sync

1. Load pinned release manifest.
2. Validate anchor capability, role, scope, and cap.
3. Compile configured resolver plan.
4. Resolve to full order IDs.
5. Deduplicate by full order ID.
6. Stop for narrowing when capped.
7. Map candidate-profile fields.
8. Validate privacy/provenance/graph schema.
9. Upsert candidate graph projection.
10. Read back through graph gateway.
11. publish candidate context.

### 7.3 Full sync

1. Validate exact full order ID and authorization.
2. Load pinned modules for source, mapping, graph, and full projection.
3. Read exact order/header and all authoritative lines.
4. Reject records with another normalized parent order ID.
5. Read only configured related evidence.
6. Create generic entities and relationships.
7. enforce immutable line identity, cardinality, provenance, privacy, and quantity rules.
8. Commit minimal order subgraph.
9. perform approved stale-edge cleanup.
10. read back identity, revision, module versions, and digest.
11. publish `FullOrderSyncContext`.

### 7.4 Sync prerequisites

No sync without:

- active release manifest;
- compatible agent/source/mapping/graph/sync modules;
- valid authorization scope;
- matching source fingerprints;
- healthy graph and system-store schemas.

Missing/incompatible graph configuration returns `SCHEMA_NOT_ACTIVE`, `SCHEMA_INCOMPATIBLE`, `SOURCE_SCHEMA_CHANGED`, or `MAPPING_INCOMPATIBLE` and can initiate schema design.

## 8. Graph Schema Design Agent and Configuration chat

### 8.1 Independence

The Configuration page sends `SchemaDesignContext` and renders `SchemaProposalContext`. The agent's state is not stored in the UI or hidden chat memory.

It can resume from serialized context on another instance and cannot approve, activate, migrate, backfill, or sync.

### 8.2 Dynamic gap analysis

The agent compares selected module configurations, actual source structures, existing mappings/schema, requested graph capability, validation failures, security/privacy rules, and migration requirements.

It does not use a fixed questionnaire.

For each turn it:

1. recomputes unresolved gaps;
2. removes answered, inferable, duplicate, irrelevant, and unnecessary gaps;
3. ranks blockers by identity, privacy, relationships, synchronization, migration, and performance;
4. asks one smallest configuration-related question;
5. shows why it is required and the source/configuration evidence;
6. offers evidence-backed options;
7. creates a new proposal-context version;
8. revalidates;
9. stops when review-ready.

### 8.3 Owner routing

Unresolved decisions identify a required owner:

```text
DATA_ADMIN
DATA_STEWARD
SOURCE_OWNER
SECURITY_OWNER
ARCHITECT
OPERATIONAL_SUBJECT_MATTER_EXPERT
```

The workflow routes context to a human. The schema agent does not call another agent.

### 8.4 Module-aware schema chat

The chat edits specific configuration modules rather than one giant payload. Every proposed change states:

- affected module and nested field path;
- current value and proposed value;
- evidence and reason;
- affected dependencies and agents;
- compatibility/migration impact;
- required owner/approval.

Accepted changes become typed proposal commands validated against the module schema. They do not become arbitrary JSON patches authored by the model.

## 9. Schema creation and redesign

### 9.1 Create

```text
Select sources/modules and graph capability
  -> introspect metadata
  -> detect identities/joins/gaps
  -> resolve blocking gaps in chat/forms
  -> generate modular mappings/graph/projection drafts
  -> validate
  -> simulate
  -> review/approve
  -> include modules in release manifest
  -> migrate/activate
```

### 9.2 Redesign existing

Active module and release versions are immutable. Redesign creates new draft module versions and a new draft release manifest based on the selected active release.

The workspace shows:

- current and proposed module versions;
- nested key/value changes;
- graph node/relationship/property changes;
- source and mapping drift;
- affected agents, contexts, queries, and sync profiles;
- compatibility classification;
- migration, backfill, dual-read, and rollback requirements.

### 9.3 Change classification

```text
NON_BREAKING
REQUIRES_INDEX_MIGRATION
REQUIRES_DATA_BACKFILL
REQUIRES_RELATIONSHIP_REBUILD
REQUIRES_DUAL_READ_WINDOW
BREAKING_REQUIRES_MAJOR_VERSION
UNSAFE_REJECTED
```

## 10. Configuration release lifecycle

Module states:

```text
DRAFT -> VALIDATED -> APPROVED -> RELEASED -> SUPERSEDED -> ARCHIVED
```

Release states:

```text
DRAFT
  -> DEPENDENCIES_RESOLVED
  -> VALIDATED
  -> APPROVED
  -> MIGRATION_READY
  -> ACTIVE
```

Activation is atomic. A failure leaves the previous active release unchanged.

## 11. Download/export and upload/import

### 11.1 Download

Users may download:

```text
one configuration module as YAML or JSON
one agent plus dependency manifest
an entire release as a portable ZIP package
```

The ZIP contains modular declarative configuration, schemas, mappings, graph definitions, projection profiles, policy references, migration plan, tests, validation receipts, and a manifest.

Exports exclude credentials, secrets, raw source samples, and prohibited fields. Draft exports are marked non-activatable. Downloads are audited.

### 11.2 Upload

Users may upload one module, multiple modules, or a portable release package.

Every upload is quarantined and creates draft versions. It never modifies active configuration, activates, migrates, backfills, or synchronizes automatically.

Validate:

- file/archive safety and size;
- package/module schema versions;
- manifest, checksum, and signature;
- module ID, type, owner, dependencies, and version;
- field types and nested structures;
- allowed expressions and references;
- privacy, authorization, retention, and secrets policy;
- source fingerprints and environment bindings;
- graph/mapping compatibility;
- migration and rollback safety;
- absence of executable code, scripts, arbitrary SQL/Cypher, binaries, and macros.

Invalid or unresolved uploads open as quarantined drafts with field-level errors. The schema/configuration chat asks only upload-related unresolved questions.

### 11.3 Round trip

Supported module/package versions must preserve normalized configuration through:

```text
download -> upload -> validate -> download
```

Environment bindings, audit metadata, and regenerated signatures may differ.

## 12. Configurable persistence and bootstrap

Supported control/context providers:

```text
NEO4J | MONGODB | POSTGRESQL | SQLSERVER
```

Logical repositories include contexts, sync requests/attempts, locks, modules, releases, schema proposals, import/export audits, idempotency, outbox, and schema versions.

Startup/deployment:

1. validates provider configuration;
2. acquires migration lock;
3. reads schema version/checksum;
4. creates missing collections/tables/labels, constraints, and indexes;
5. applies forward-only migrations;
6. introspects and verifies;
7. records evidence;
8. fails closed on drift or incompatible version.

## 13. Minimal graph and data boundaries

Initial configurable vocabulary:

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

Relationships include account-to-order, order-to-line, line-to-product, order-to-shipment, order-to-invoice, order-to-location, and prior-return-to-line.

Required provenance includes source asset/record/version/revision, mapping module version, graph module version, sync request, timestamps, identity quality, and evidence references.

Prohibited everywhere: payment credentials, raw secrets, unnecessary contact data, raw source payloads, full transcripts, hidden reasoning, document bytes, labels/POD files, and unrelated telemetry.

## 14. Current-to-target differences

| Current | Target |
|---|---|
| Combined `returns/production.yaml` | Separate agent, workflow, policy, source, mapping, graph, sync, integration, and platform modules |
| `agents` dictionary inside one return-platform model | One owned module/schema per agent |
| Broad `RETURN_PLATFORM` release payload | Manifest of immutable modular payload versions |
| Raw JSON textarea | Schema-driven typed and nested form controls |
| Whole-domain JSON save | Field/module commands with optimistic versioning and validation |
| Hard-coded source shapes and graph sync | Generic runtime compiled from active modules |
| Raw order-number graph identity | Full order ID identity |
| Top-N broad `FULL` mode | Administrative backfill plus exact full-order/partial sync |
| Direct Mongo fallback in associate flow | Sync context -> worker -> graph readback -> agent graph query |
| Stubs in candidate/source verification | Real services and receipts |
| No modular redesign/import/export | Immutable module redesign plus safe module/release packages |

## 15. Implementation phases

### Phase 0 - Contracts

- identity, context, module envelope, manifest, dependency, version, and permission contracts;
- rename legacy broad sync.

### Phase 1 - Split configuration baseline

- extract combined return YAML into modules;
- create schemas and owners;
- create manifest that reproduces the current validated runtime snapshot;
- prove no behavior change.

### Phase 2 - Modular backend configuration service

- module CRUD/version APIs;
- dependency resolution;
- field/module validation;
- immutable releases and atomic activation;
- backward-compatible snapshot composition.

### Phase 3 - Schema-driven Configuration UI

- module navigation;
- typed field renderer;
- nested object/list/map editors;
- references and conditionals;
- inline validation, diff, impact, and approvals;
- remove raw JSON textarea as primary editor.

### Phase 4 - Metadata/mapping runtime

- source descriptors, introspection, fingerprints, generic envelopes, allowlisted transformations, compilers.

### Phase 5 - Graph Schema Design Agent

- independent contexts;
- dynamic gap analysis;
- module-aware chat questions and proposal commands;
- owner routing and simulation;
- no approval/activation capability.

### Phase 6 - Redesign and import/export

- new-version redesign;
- module/release downloads;
- quarantined upload/import;
- compatibility, security, migration, and round-trip tests.

### Phase 7 - Graph compiler and persistence bootstrap

- configurable graph schema/projections;
- provider-neutral stores and automatic bootstrap;
- migrations and drift detection.

### Phase 8 - Durable generic synchronization

- request lifecycle, release pinning, workers, leases, retries, idempotency, graph verification.

### Phase 9 - Partial and full order sync

- configured strong-anchor resolution;
- exact full order ID hydration with all lines;
- source revision and provenance.

### Phase 10 - Context-only agent cutover

- independent contexts and graph gateway;
- remove agent-to-agent/private-store dependencies;
- remove direct source fallback.

### Phase 11 - Confirmation and Order Analysis

- multi-line confirmation, leases, revision checks, prior-return evidence, UOM-aware quantity analysis.

### Phase 12 - Backfill and rollout

- release-bound canonical backfill, shadow comparison, cohorts, retirement of legacy config/sync/stubs.

## 16. APIs

```text
GET    /configuration/module-schemas
GET    /configuration/modules
POST   /configuration/modules
GET    /configuration/modules/{moduleId}/versions/{version}
POST   /configuration/modules/{moduleId}/drafts
PATCH  /configuration/modules/{moduleId}/drafts/{version}/fields
POST   /configuration/modules/{moduleId}/drafts/{version}/validate
POST   /configuration/modules/{moduleId}/drafts/{version}/submit
POST   /configuration/modules/{moduleId}/drafts/{version}/approve

POST   /configuration/releases
POST   /configuration/releases/{releaseId}/resolve
POST   /configuration/releases/{releaseId}/validate
POST   /configuration/releases/{releaseId}/activate

GET    /configuration/modules/{moduleId}/versions/{version}/download
GET    /configuration/releases/{releaseId}/download
POST   /configuration/imports
GET    /configuration/imports/{importId}
POST   /configuration/imports/{importId}/create-drafts

POST   /schema-design/requests
POST   /schema-design/requests/{requestId}/next-question
POST   /schema-design/requests/{requestId}/answers
POST   /schema-design/requests/{requestId}/validate
POST   /schema-design/requests/{requestId}/simulate

POST   /order-sync/partial
POST   /order-sync/full
GET    /order-sync/requests/{requestId}
```

The field API carries structured field paths and typed values. The UI does not expose JSON editing even though transport serialization may use JSON.

## 17. Acceptance scenarios

| Scenario | Required behavior |
|---|---|
| User opens Order Discovery Agent | Only its owned settings and shared dependency references appear |
| User edits nested scoring threshold | Typed nested field updates; inline validation; no JSON editing |
| User adds repeatable allowed capability | Array editor adds validated reference |
| Shared policy change affects three agents | Impact view lists all three before approval |
| Incompatible module versions selected | Release resolution fails with exact dependency errors |
| Existing active agent configuration edited | New draft version; active module unchanged |
| New source added | Configuration and schema workflow, not new source model class |
| Schema chat has metadata answer | It does not ask the user to repeat it |
| Module downloaded/uploaded | Quarantined round-trip draft; no automatic activation |
| Full release downloaded/uploaded | Manifest/dependencies/checksums validated |
| Uploaded nested value has wrong type | Field-path error in typed editor |
| Full order has multiple lines | One order node and all authoritative line nodes |
| Same order number under two logons | Two isolated orders |
| Graph/config unavailable | No agent direct-source bypass |

## 18. Test strategy

### 18.1 Modular configuration

- one schema and owner per agent module;
- combined baseline decomposes/recomposes without semantic drift;
- dependency resolution, cycles, version constraints, checksums;
- atomic release activation and rollback;
- per-module RBAC and audit.

### 18.2 Form UI

- scalar, enum, boolean, numeric, reference, duration, nested object, list, object-list, map, and conditional controls;
- field/module/release validation;
- accessibility, keyboard navigation, error focus, unsaved changes, concurrency conflict;
- no raw JSON primary editor;
- read-only preview matches normalized payload.

### 18.3 Agent independence

- every agent runs from serialized context without another agent process;
- no cross-agent service imports or private-store reads;
- idempotent retry/replay and authorization revalidation.

### 18.4 Generic source/graph runtime

- onboard equivalent Mongo and SQL shapes by configuration;
- schema drift, invalid identity, unsafe expression, type/join/cardinality/privacy errors;
- compiled plan reproducibility.

### 18.5 Schema chat

- no fixed sequence;
- one relevant blocking question;
- suppress answered/inferable/irrelevant questions;
- module-aware proposed changes;
- owner routing;
- cannot approve or activate.

### 18.6 Import/export

- module/agent-with-dependencies/release packages;
- no secrets or prohibited data;
- archive traversal, bomb, malware, binary, scripts, SQL/Cypher rejection;
- version, checksum, signature, dependency, environment, and round-trip tests.

### 18.7 Order sync

- duplicate raw numbers, embedded/separate lines, zero/one/many lines;
- all anchor outcomes;
- partial then full sync;
- revisions, verification, concurrency, privacy, and graph-only boundary.

## 19. Rollout

1. Define module schemas, owners, and manifest contract.
2. Split the existing baseline and prove recomposed equality.
3. Deploy modular backend APIs behind compatibility adapter.
4. Deploy typed Configuration UI for read-only comparison.
5. Enable modular draft editing and validation.
6. Enable Graph Schema Design chat.
7. Enable controlled module/release import/export.
8. Activate first modular release with existing behavior.
9. Deploy generic graph compiler and sync workers.
10. Enable partial/full order sync and context-only agents by cohort.
11. Retire combined return payload and raw JSON editor after migration.
12. Retire direct source fallback, raw graph identity, and stubs.

## 20. Definition of done

- Each agent owns a separate configuration module and schema.
- Shared configuration is separated by concern and referenced, not duplicated.
- Runtime activates one atomic manifest of compatible module versions.
- Configuration UI uses proper typed key/value controls.
- Nested objects, lists, maps, references, and conditional values are editable without JSON.
- Raw JSON/YAML is limited to preview and governed import/export.
- Active configurations are immutable; edits create new versions.
- Users can redesign, download, and upload modules/releases safely.
- Graph Schema Design Agent is independent, context-only, dynamic, and configuration-focused.
- Source and graph changes normally require configuration rather than new source-specific models.
- Full sync uses one full order ID and synchronizes all authoritative lines.
- Partial sync resolves strong anchors to full order IDs.
- Agents operate through context and graph gateway only.
- Provider bootstrap, authorization, privacy, migration, compatibility, and recovery tests pass.

## 21. Governing invariants

```text
One agent
-> one owned configuration module.
```

```text
Shared concern
-> shared owned module referenced by agents, never copied into each agent.
```

```text
No valid atomic release manifest
-> no runtime activation.
```

```text
No validated fullOrderId
-> no SalesOrder graph write.
```

```text
No ACTIVE compatible graph and mapping modules
-> no graph synchronization.
```

```text
No versioned context
-> no agent execution.
```

```text
Upload or redesign
-> new quarantined/draft module versions, never active in-place mutation.
```
