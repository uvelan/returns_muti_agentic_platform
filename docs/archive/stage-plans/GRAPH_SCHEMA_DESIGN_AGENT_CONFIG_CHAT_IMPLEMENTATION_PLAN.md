# Graph Schema Design Agent: Configuration Chat Implementation Plan

**Status:** Authoritative component plan  
**Date:** 2026-08-02  
**Parent architecture:** `ORDER_DISCOVERY_CONTEXT_DRIVEN_CONFIGURABLE_GRAPH_IMPLEMENTATION_PLAN.md`  

This component plan supersedes the parent plan's Graph Schema Design Agent description wherever the details differ.

## 1. Final decision

`GraphSchemaDesignAgent` is an independent, context-only agent presented as an interactive chat inside the Data Console Configuration page.

It helps an authorized configuration user move from configured data sources to a complete, validated, reviewable graph schema. The user must not need to manually create Python/Pydantic data models, YAML, JSON, Cypher, tables, or collections for each source variation.

The agent must not use a fixed questionnaire. It derives each question from the selected sources, their actual structures, existing mappings and schemas, the requested graph capability, validation failures, and unresolved configuration decisions.

It asks only questions required to finalize the configuration.

The agent can draft, explain, validate, simulate, and revise a schema proposal. It cannot approve, activate, migrate, backfill, or run production graph synchronization.

## 2. Independence and context contract

The Configuration page is a client of the agent contract. It is not the agent's memory.

The agent:

- accepts one immutable, versioned `SchemaDesignContext`;
- produces a new `SchemaDesignContext` and `SchemaProposalContext` version;
- does not call another agent directly;
- does not read UI process memory, browser state, another agent's storage, or hidden transcript history;
- is idempotent for the same context version and idempotency key;
- can resume on a different runtime instance from serialized context alone;
- invokes only approved metadata-introspection, schema-validation, simulation, and proposal capabilities;
- cannot access unrestricted source values, credentials, raw Cypher execution, or production migration commands.

```text
Configuration page
    -> SchemaDesignContext
    -> GraphSchemaDesignAgent
    -> next SchemaDesignContext + SchemaProposalContext
    -> Configuration page
```

Chat messages are a display and input projection. The authoritative conversation state is structured context.

## 3. Input context

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
selectedSources:
  - sourceId: sales-invoice
    metadataSnapshotRef: metadata-snapshot-id
requestedCapability:
  capabilityId: ORDER_DISCOVERY
  businessQuestion: "Find the correct order and lines for a return"
existingConfigurationReleaseId: optional-release
existingGraphSchemaReleaseId: optional-release
answers: []
unresolvedGaps: []
proposalRef: optional-proposal
idempotencyKey: stable-key
createdAt: timestamp
```

Raw source credentials and unrestricted sampled records are excluded.

## 4. Source metadata used by the agent

For selected and authorized sources, the agent receives approved metadata such as:

- connector type: MongoDB, SQL Server, PostgreSQL, API, or another registered connector;
- database, schema, table, collection, or resource name;
- structural profiles and schema fingerprints;
- field paths, declared/inferred types, nullability, and array/object nesting;
- primary keys, unique indexes, and candidate composite identities;
- foreign keys and reference-like fields;
- observed cardinality summaries;
- approved masked example shapes when necessary;
- volume, freshness, watermark, and index metadata;
- sensitivity classification and retention policy;
- existing canonical mappings and graph-schema usage;
- compatibility differences from the active release.

Metadata introspection is deterministic and tool-driven. The model does not fabricate source fields or relationships.

## 5. Dynamic gap analysis

Before asking a question, the agent runs a deterministic gap analysis.

### 5.1 Gap categories

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

### 5.2 Gap detection

The engine compares:

```text
configured source metadata
+ existing canonical vocabulary
+ existing mapping release
+ existing graph schema
+ requested business capability/query patterns
+ privacy/security policies
+ synchronization requirements
```

It produces structured gaps containing:

```yaml
gapId: stable-id
category: ENTITY_IDENTITY
blockingStage: GRAPH_SCHEMA_VALIDATION
affectedSources: [sales-invoice]
affectedFields: [eventMeta.accountLogon, eventMeta.orderNumber]
evidenceRefs: [metadata-reference]
inferences:
  - value: "accountLogon*orderNumber"
    confidence: 0.93
    reasonCode: COMPOSITE_UNIQUE_IN_SAMPLE_AND_INDEX
requiresHumanDecision: true
requiredOwnerRole: SOURCE_OWNER
```

### 5.3 No-question conditions

The agent does not ask a question when:

- an approved source contract already provides the answer;
- the active configuration release already contains a compatible decision;
- deterministic metadata and policy yield exactly one safe result;
- the question was answered in the current context lineage;
- the answer is unrelated to the requested graph capability;
- the property or relationship is unnecessary under minimal-data policy.

## 6. Smart question generation

### 6.1 One focused question per turn

For every chat turn, the agent:

1. loads the latest context and metadata snapshots;
2. reruns gap analysis;
3. removes resolved, inferable, irrelevant, and duplicate gaps;
4. ranks blockers in this order:
   - unsafe or missing entity identity;
   - privacy or authorization uncertainty;
   - incompatible relationship join/cardinality;
   - required field semantics/type;
   - sync correctness and provenance;
   - migration/backfill safety;
   - indexing/performance;
   - optional display enrichment;
5. selects the smallest question that resolves the highest-priority gap;
6. shows the source structures and evidence that caused the question;
7. provides evidence-backed choices when appropriate;
8. accepts correction or a new value from the authorized user;
9. stores the structured answer in a new context version;
10. immediately recompiles and validates the proposal;
11. stops when no blocking gap remains.

### 6.2 Question scope

Allowed questions are directly related to configuration, for example:

- Which of two plausible source fields is the durable order identity?
- Does this collection contain order headers, line records, or both?
- Is the relationship from invoice to order one-to-one or one-to-many?
- Which source owns shipment status when two sources disagree?
- May this sensitive field be projected, hashed, referenced, or excluded?
- Should a newly required relationship trigger historical backfill?
- Which field is the incremental-sync watermark?

Disallowed questions include:

- generic return-discovery questions unrelated to schema design;
- conversational filler;
- questions already answered by approved metadata;
- questions about fields excluded by minimal-data policy;
- unrestricted requests for source credentials or raw production values;
- open-ended requests such as "describe your whole database" when specific gaps are known;
- repeated fixed questions for every source.

### 6.3 Question response contract

```yaml
questionId: stable-id
gapId: gap-id
questionType: SELECT_ONE
prompt: "Which field is the immutable line number for salesInv line records?"
whyRequired: "OrderLine needs a stable key; array position cannot be confirmed."
sourceEvidence:
  - field: salesLines.salesLnsEventData.lineNumber
    type: STRING
    uniquenessSummary: "unique within order in inspected metadata"
choices:
  - value: salesLines.salesLnsEventData.lineNumber
    label: lineNumber
    evidenceCode: BEST_SUPPORTED_CANDIDATE
  - value: NO_AUTHORITATIVE_FIELD
    label: No authoritative field exists
requiredOwnerRole: SOURCE_OWNER
blocking: true
```

The question text is generated for the current gap. The contract and safety rules are stable; the question content is not hard-coded.

## 7. User and owner routing

The chat is available only to authorized Configuration-page users.

The current user may answer a question when their role is permitted for that decision. Otherwise, the agent records the exact unresolved gap and required owner role.

Possible owner roles include:

```text
DATA_ADMIN
DATA_STEWARD
SOURCE_OWNER
SECURITY_OWNER
ARCHITECT
OPERATIONAL_SUBJECT_MATTER_EXPERT
```

An associate, sales representative, or customer-care subject-matter expert may clarify configuration-relevant business semantics, such as which facts are available during an order-discovery interaction or which fields distinguish candidates.

Technical identity, source authority, cardinality, privacy, migration, and activation decisions require the configured administrative or owner role.

The agent does not call another agent to obtain an answer. It emits an `UnresolvedConfigurationDecision` in context. The workflow routes that context to an authorized human, and the schema chat resumes from the returned context.

## 8. Configuration-page experience

The Configuration page includes:

### 8.1 Source workspace

- connection and introspection health;
- selected databases, schemas, tables, collections, or APIs;
- structure browser with nested fields and types;
- keys, indexes, cardinality, freshness, and masked sample-shape evidence;
- schema fingerprint and drift status.

### 8.2 Schema chat

- current focused configuration question;
- why the question blocks completion;
- relevant tables, collections, structures, fields, and evidence;
- evidence-backed answer choices when available;
- free-form correction for authorized users;
- owner-routing action when the user cannot decide;
- answer history reconstructed from structured context;
- resume support after reload, logout, failover, or agent-instance change.

### 8.3 Live proposal

- graph nodes, identities, and allowed properties;
- relationships, joins, direction, and cardinality;
- constraints and indexes;
- source-to-canonical and canonical-to-graph mappings;
- partial- and full-sync projection profiles;
- provenance, privacy, retention, and authorization rules;
- unresolved gaps and validation errors;
- version diff from the active release;
- migration, backfill, and rollback impact.

### 8.4 Actions

```text
SAVE_DRAFT
RESUME
ANSWER
ROUTE_DECISION
RUN_VALIDATION
RUN_SANDBOX_SIMULATION
SUBMIT_FOR_REVIEW
REJECT
APPROVE  # authorized human only
ACTIVATE # separate release permission
```

Approval, activation, migration, backfill, and graph sync are visibly separate operations.

## 9. Proposal lifecycle

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

Alternative states:

```text
NEEDS_SOURCE_CONTRACT
NEEDS_AUTHORIZED_OWNER
PRIVACY_REJECTED
MIGRATION_UNSAFE
REJECTED
SUPERSEDED
```

Graph sync requires an `ACTIVE`, signed, compatible schema and mapping release. A draft or approved-but-not-active proposal is insufficient.

## 10. Proposal output

`SchemaProposalContext` contains:

- requested graph capability and supported query patterns;
- selected source metadata snapshot IDs;
- canonical entity vocabulary and identities;
- node, relationship, property, constraint, and index definitions;
- source-to-canonical and canonical-to-graph mappings;
- partial- and full-sync projection profiles;
- source authority, cardinality, and conflict policies;
- provenance, privacy, authorization, retention, and freshness rules;
- migration, backfill, compatibility, and rollback plan;
- questions, structured answers, evidence, and owner approvals;
- static-validation and sandbox-simulation results;
- generated scenario and contract tests;
- proposal digest and version lineage.

## 11. Graph sync integration

Graph sync loads configuration by immutable release ID.

```text
SyncRequest
  -> resolve ACTIVE schema release
  -> resolve ACTIVE mapping release
  -> verify source metadata fingerprints
  -> compile connector/mapping/projection plan
  -> synchronize
  -> graph readback
  -> receipt includes release IDs and source fingerprints
```

If configuration is absent or incompatible:

```text
SCHEMA_NOT_ACTIVE
SCHEMA_INCOMPATIBLE
SOURCE_SCHEMA_CHANGED
MAPPING_INCOMPATIBLE
```

The sync worker does not ask chat questions and does not invent a schema. It records the failure context, which may trigger a new schema-design request in the Configuration page.

## 12. Repository implementation plan

### Phase GS-1 - Context and contracts

- add `SchemaDesignContext`, `SchemaProposalContext`, `SchemaQuestion`, `SchemaAnswer`, `SchemaGap`, and `UnresolvedConfigurationDecision`;
- define immutable versions, idempotency, authorization, and owner-routing rules;
- add serialization and replay tests.

### Phase GS-2 - Metadata introspection

- build registered introspectors for MongoDB, SQL Server, PostgreSQL, and approved APIs;
- generate masked metadata snapshots and fingerprints;
- detect identities, types, nesting, indexes, references, and cardinality evidence;
- prohibit unrestricted source-value exposure.

### Phase GS-3 - Gap engine

- compare metadata, existing releases, requested capabilities, and policies;
- produce deterministic structured gaps;
- rank blocking gaps;
- suppress answered, inferable, duplicate, irrelevant, and unnecessary questions.

### Phase GS-4 - Independent schema-design agent

- implement the agent using only `SchemaDesignContext`;
- generate one source-specific configuration question per turn;
- include blocking reason and evidence;
- update proposal context and revalidate after every answer;
- emit owner-routing context when authorization is insufficient.

### Phase GS-5 - Configuration-page chat

- add source workspace, schema chat, live proposal, gap list, diff, and simulation results;
- reconstruct UI entirely from contexts;
- support save, reload, resume, failover, and concurrent-version conflicts;
- separate review, approval, activation, migration, and backfill permissions.

### Phase GS-6 - Validation and simulation

- validate identities, types, joins, cardinality, provenance, privacy, constraints, and indexes;
- compile safe mapping and graph plans in sandbox;
- run generated fixture/scenario tests;
- produce signed validation receipts.

### Phase GS-7 - Release and sync integration

- create immutable signed configuration releases;
- require active release IDs in graph sync requests;
- verify source fingerprints before sync;
- fail closed and create schema-design trigger context on incompatibility.

## 13. Required APIs

```text
POST /schema-design/requests
GET  /schema-design/requests/{requestId}
POST /schema-design/requests/{requestId}/introspect
POST /schema-design/requests/{requestId}/next-question
POST /schema-design/requests/{requestId}/answers
POST /schema-design/requests/{requestId}/route-decision
POST /schema-design/requests/{requestId}/validate
POST /schema-design/requests/{requestId}/simulate
POST /schema-design/proposals/{proposalId}/submit
POST /schema-design/proposals/{proposalId}/approve
POST /schema-design/proposals/{proposalId}/reject
POST /configuration-releases/{releaseId}/activate
```

The `next-question` operation is idempotent for a context version. It returns no question when the proposal has no unresolved blocking gap.

## 14. Acceptance scenarios

| Scenario | Required behavior |
|---|---|
| New MongoDB collection with no graph schema | Introspect structure, detect gaps, ask only required configuration questions |
| Equivalent SQL table replaces collection | Reuse canonical vocabulary; ask only incompatible/unresolved mapping questions |
| Metadata proves one unique identity | Use the approved evidence without asking the user to restate it |
| Two plausible identity fields | Show both with evidence and ask one focused identity question |
| Nested lines have no proven immutable key | Ask the precise line-identity question; never synthesize a confirmable key |
| Existing answer is in context | Do not repeat the question after reload or agent failover |
| Source changes a required type | Detect fingerprint drift and ask only about the incompatible mapping |
| User lacks authority | Record and route the precise decision; do not accept an unauthorized answer |
| Sensitive field appears | Apply policy; ask security owner only if an allowed projection choice remains unresolved |
| Field is not required by requested capability | Exclude it without asking a question |
| Proposal has no blocking gaps | Stop asking questions and present review-ready schema |
| Proposal is not active | Graph sync remains blocked |
| Agent runtime changes | Same serialized context yields the same structured gaps and proposal state |

## 15. Definition of done

- The Configuration page contains a working schema-design chat.
- The agent is independently executable from serialized context only.
- There is no fixed questionnaire or required static question sequence.
- Questions are derived from configured sources, actual structures, existing releases, validation gaps, policies, and the requested graph capability.
- The agent asks one focused configuration-related blocking question per turn.
- Questions already answered or deterministically inferable are not asked.
- Questions show why they are required and the source evidence involved.
- Unanswerable decisions are routed as structured context to the correct authorized owner.
- The live proposal updates and revalidates after each answer.
- UI reload, failover, or another agent instance does not lose or alter authoritative state.
- The agent cannot approve, activate, migrate, backfill, or run graph sync.
- Production sync accepts only active, signed, compatible graph-schema and mapping releases.
- New source structures normally require configuration changes, not new source-specific data-model classes.

## 16. Governing invariants

```text
No unresolved blocking configuration gap
-> proposal may be submitted for review.
```

```text
No authorized human approval and release activation
-> no production graph schema change.
```

```text
No ACTIVE compatible schema and mapping release
-> no graph synchronization.
```

```text
No versioned SchemaDesignContext
-> no GraphSchemaDesignAgent execution.
```
