# Unified Returns Platform — Complete Target Design

**Companion to** `UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md`
**Baseline:** `c3cdd354fdef93583c2b67da219701e76489a221`

This document is the full structural specification: every directory, every file, its responsibility, the core
contracts, the persisted data model, the complete API surface, every state machine, and a file-by-file
migration map from the current tree. The plan says *when*; this says *what*.

---

## Table of contents

1. [Design rules](#1-design-rules)
2. [Backend directory structure](#2-backend-directory-structure)
3. [Frontend directory structure](#3-frontend-directory-structure)
4. [Configuration directory structure](#4-configuration-directory-structure)
5. [Infrastructure, scripts, docs](#5-infrastructure-scripts-docs)
6. [Test directory structure](#6-test-directory-structure)
7. [Core contracts](#7-core-contracts)
8. [System store data model](#8-system-store-data-model)
9. [Complete API surface](#9-complete-api-surface)
10. [State machines](#10-state-machines)
11. [Dependency direction rules](#11-dependency-direction-rules)
12. [Migration map](#12-migration-map)
13. [**Distributed correctness invariants (normative)**](#13-distributed-correctness-invariants-normative)
14. [**Durable reasoning runtime — LangGraph (normative)**](#14-durable-reasoning-runtime--langgraph-normative)
15. [Definition of structurally done](#15-definition-of-structurally-done)

---

## 1. Design rules

**R1 — Layering.** Dependencies point inward and downward only:
`api → application → domain` and `module → platform`. A domain layer imports nothing but its own domain and
`platform.*` contracts.

**R2 — Ports and capability resolution.** Any dependency crossing a module boundary is a Protocol declared in
the **consuming** module's `ports/`. The consumer resolves it at runtime from the platform capability registry
(§7.2) — it never imports the providing module, and it never holds a provider-specific adapter. Adapters that
bind a concrete provider to a consumer's port are constructed in `bootstrap/adapters/`, which is the only
place in the codebase that imports two modules at once.

**R2a — No named cross-module fields in shared contexts.** `ModuleRuntimeContext` and
`AgentExecutionContext` expose only platform services and the capability registry. They must never carry a
named field for another module's service (no `.ai`, no `.knowledge`, no `.graph`). Structural typing means the
publishing module and the consuming port never need to know about each other.

**R3 — One module, one `module.py`.** Each activatable module exposes a `ModuleFactory` in `module.py`. That
file is the only thing `bootstrap/` knows about the module.

**R4 — Logical names only.** Persistence code addresses `system_store.collection("<logical>")`. Physical names
exist only in `backend/config/platform/system_store.yaml`.

**R5 — Contracts are frozen models.** All cross-boundary models are `pydantic.BaseModel` with
`model_config = ConfigDict(frozen=True, extra="forbid")`, matching the existing convention in
`dynamic_knowledge/`.

**R6 — Every directory has a README.** Every leaf package that represents an independent unit carries
`README.md`. Marked ★ below.

**R7 — No `__init__.py` re-export sprawl.** `__init__.py` exports only the module's public surface; deep
imports are the norm internally.

**R8 — Nothing sensitive is written durably without classification.** Any payload derived from a configured
source, a resolved secret, or a customer record passes through `platform/secrets/redaction.py` before a
durable write, unless the target structure is explicitly declared `encrypted` with a retention policy in
`system_store.yaml`. See §13.6.

**R9 — Every distributed state change is fenced or transactional.** No compare-and-set stands alone where two
replicas can race it. See §13 for the eight invariants this expands into and the mechanism enforcing each.

**R10 — Reasoning runtimes are implementation details.** LangGraph is the durable reasoning runtime inside
exactly two components (Order Discovery, Graph Schema Analyzer). No graph object, state dict, or checkpointer
crosses a module boundary; no consumer's type signature mentions LangGraph. Removing it from one component
must not require touching Temporal, the AI Gateway, any other agent, or any product screen. See §14.

> **§13 (Distributed correctness invariants) and §14 (Durable reasoning runtime) are normative.** §13 resolves
> eleven defects found in review of this design's first draft. §14 specifies the LangGraph integration
> boundary. Where either disagrees with an earlier section, they win.

---

## 2. Backend directory structure

```
backend/
├── Dockerfile                       uv sync --frozen (see §5.2a)
├── pyproject.toml                   PEP 621 [project]; [tool.poetry] removed after migration
├── uv.lock                          the single lockfile
│   (poetry.lock deleted once Dockerfile + host scripts + CI all consume uv.lock)
├── config/                          → §4
├── docs/
├── openapi/
│   └── return-platform.openapi.json
├── scripts/                         → §5.3
├── tests/                           → §6
└── src/
    └── return_platform/
        ├── __init__.py
        ├── py.typed
        ├── main.py                  create_app() — module activation only
        ├── asgi.py                  ASGI entrypoint
        │
        ├── bootstrap/ ★
        ├── platform/ ★
        ├── configuration/ ★
        ├── agents/ ★
        ├── business/ ★
        ├── graph/ ★
        ├── graph_schema_analyzer/ ★
        └── ai/ ★
```

### 2.1 `bootstrap/` ★

Startup composition. Knows every module's `module.py` and nothing else about them.

```
bootstrap/
├── __init__.py
├── settings.py        BootstrapSettings — deployment/env values only (no business config)
├── context.py         ModuleRuntimeContext assembly: system store, secrets, audit, config handle,
│                      capability registry, clock, correlation. No module-specific fields (R2a).
├── capabilities.py    publishes each active module's capabilities into the registry
├── adapters/          THE ONLY place that imports two modules at once (R2)
│   ├── __init__.py
│   ├── analyzer_source_adapter.py    configuration.sources.registry → analyzer SourceDiscoveryPort
│   ├── analyzer_ai_adapter.py        ai.gateway              → analyzer SchemaReasoningPort
│   ├── analyzer_graph_adapter.py     graph.lifecycle         → analyzer GraphTargetPort
│   ├── agent_ai_adapter.py           ai.gateway              → agents AgentAiPort
│   ├── agent_knowledge_adapter.py    graph.query             → agents KnowledgePort
│   ├── graph_source_adapter.py       configuration.sources   → graph SourceScanPort
│   └── README.md
├── lifespan.py        FastAPI lifespan; strict ordered startup and reverse-ordered shutdown
├── activation.py      reads configuration.modules → resolves factories → creates + initializes
├── reconciler.py      ConfigurationReconciler — adopts newly ACTIVE releases at runtime (§13.2)
├── routers.py         mounts each active module's router; the only place include_router appears
├── health.py          /health/live, /health/ready — module health + configuration adoption state
├── errors.py          startup failure classification; fail-closed in production, degrade in dev
└── README.md
```

**Startup order (enforced in `lifespan.py`):**
```
1  BootstrapSettings from environment
2  SecretResolver (Vault)                          → platform/secrets
3  SystemStore adapter + fenced bootstrap lease
   + migrations                                    → platform/system_store   (§13.7)
4  Audit sink                                      → platform/audit
5  CapabilityRegistry construction                 → platform/capabilities
6  Canonical configuration load, active-release
   resolution, ConfigurationHandle construction    (§13.2)
7  ModuleRuntimeContext assembly (no module fields)
8  Module construction — factory.create() for every enabled module
9  Native publication — each module's publish_capabilities(registry)
10 Adapter publication — bootstrap/adapters/ resolves native contracts and publishes
   consumer-shaped bindings under (capability, contract) keys
11 Resolution — each module's resolve_capabilities()
12 Module initialize() in dependency order
13 Router mounting
14 ConfigurationReconciler start
15 Health gate opens
```

Steps 8–11 are four deliberately separate passes. A module is *created* before any cross-module capability
exists, and resolves its ports only after **every** publication — including the bootstrap-constructed
adapters, which cannot exist until the modules they wrap have published. Splitting these is what makes
activation order independent of capability dependency order and removes the circular-construction problem
entirely. `initialize()` runs after resolution, so a module may use its resolved ports during startup.

### 2.2 `platform/` ★

Cross-cutting capability every module may depend on. Depends on nothing above it.

```
platform/
├── __init__.py
│
├── contracts/ ★                                          [NEW — §7.1]
│   ├── __init__.py
│   ├── runtime_configuration.py  RuntimeConfigurationView, RuntimeConfigurationHandle
│   ├── consistency.py            ConsistencyHandle, ConsistencyChanged
│   ├── epoch.py                  RuntimeEpoch
│   ├── clock.py                  Clock
│   ├── correlation.py            CorrelationContext
│   └── README.md
│   Neutral protocols that domain types structurally satisfy. This package is what lets
│   platform/ carry a configuration handle and a consistency token without naming
│   configuration.* or graph.* — the rule tests/platform/test_layering.py enforces.
│
├── modules/ ★
│   ├── __init__.py
│   ├── contracts.py     ModuleRuntime, ModuleFactory, ModuleRuntimeContext, ModuleHealth, HealthStatus
│   ├── descriptor.py    ModuleDescriptor, ModuleKind, CapabilityName, ConfigurationSchemaRef
│   ├── registry.py      ModuleRegistry — register/resolve/validate/construct/health
│   ├── builtins.py      allowlisted implementation_id → ModuleFactory table (no dynamic import)
│   ├── lifecycle.py     ordered initialize/shutdown, dependency sort, health rollup
│   ├── exceptions.py    ModuleNotRegistered, DuplicateImplementation, CapabilityUnsatisfied, ModuleInitFailed
│   └── README.md
│
├── capabilities/ ★                                                        [NEW — fixes defect #1]
│   ├── __init__.py
│   ├── contracts.py     CapabilityRegistry, CapabilityName, CapabilityPublication
│   ├── registry.py      publish / resolve / resolve_optional / list; late binding
│   ├── errors.py        CapabilityNotPublished, DuplicateCapability, CapabilityTypeMismatch
│   └── README.md
│
├── system_store/ ★
│   ├── __init__.py
│   ├── contracts.py     SystemStoreAdapter, StructureDefinition, FieldDefinition, IndexDefinition,
│   │                    StructureInspection, CompatibilityStatus, BootstrapReport
│   ├── manifest.py      SystemStoreManifest parse/validate; logical→physical resolution
│   ├── bootstrap.py     SystemStoreBootstrapper (migrated from InternalStoreBootstrapper)
│   ├── migrations.py    forward-only runner; every write fenced on the current token (§13.7)
│   ├── locking.py       FencedLease — lease_id, owner, monotonic fencing_token, heartbeat
│   │                    renewal, abort-on-heartbeat-failure. Never a bare TTL lock.
│   ├── fencing.py       monotonic token allocation + guarded-write helper
│   ├── drift.py         DriftPolicy — FAIL | WARN; never destructive
│   ├── encryption.py    envelope encryption for structures declared `encrypted` (§13.6)
│   ├── mongo.py         MongoSystemStoreAdapter — canonical provider
│   ├── neo4j.py         Neo4jSystemStoreAdapter — retained
│   ├── sqlserver.py     SqlServerSystemStoreAdapter — retained
│   ├── repository.py    SystemStore facade: .collection(logical) / .table(logical) / .health()
│   └── README.md
│
├── auth/ ★
│   ├── __init__.py
│   ├── principal.py     Principal, Role, Subject
│   ├── capabilities.py  Capability catalogue (the enumerated permission vocabulary)
│   ├── policy.py        role → capability resolution; deny by default
│   ├── middleware.py    FastAPI dependency: require_capability(...)
│   ├── errors.py        AuthorizationError
│   └── README.md
│
├── reasoning/ ★                                          [NEW — §14; D11.2]
│   ├── __init__.py
│   ├── checkpoint.py    SystemStoreCheckpointSaver — LangGraph BaseCheckpointSaver over
│   │                    SystemStore logical structures. Never lets the library pick a
│   │                    physical collection name (R4).
│   ├── thread_ids.py    ReasoningThreadIdFactory — bounded deterministic thread IDs
│   ├── receipts.py      ReasoningActionReceipts — idempotency for replayed nodes (§14.6)
│   ├── retention.py     CheckpointRetentionPolicy
│   ├── redaction.py     checkpoint-state allowlist enforcement (§14.5)
│   ├── observability.py reasoning-run trace emission into platform observability
│   ├── errors.py        typed reasoning outcomes (§14.9)
│   └── README.md
│   NOTE: contains NO business reasoning. No Order Discovery graph, no Analyzer graph.
│
├── audit/ ★
│   ├── __init__.py
│   ├── contracts.py     AuditSink protocol, AuditEvent (actor, action, subject, outcome, correlation)
│   ├── service.py       AuditService
│   ├── repository.py    system_store "audit"
│   └── README.md
│
├── secrets/ ★
│   ├── __init__.py
│   ├── contracts.py     SecretResolver protocol, SecretRef, DataClassification
│   ├── vault.py         Vault-backed resolver
│   ├── runtime.py       resolution of vault-referenced settings at startup
│   ├── redaction.py     Redactor — mandatory before any durable write of source-  [NEW, R8]
│   │                    or configuration-derived payload (§13.6)
│   ├── envelope.py      Vault-transit envelope encryption for `encrypted` structures
│   └── README.md
│
├── outbox/ ★
│   ├── __init__.py
│   ├── contracts.py     OutboxPublisher protocol
│   ├── models.py        OutboxRecord, DeliveryAttempt, OutboxStatus
│   ├── repository.py    system_store "integration_outbox"
│   ├── publisher.py     at-least-once publisher with backoff
│   └── README.md
│
└── observability/ ★
    ├── __init__.py
    ├── logging.py       structured logging config
    ├── metrics.py       counters/histograms
    ├── correlation.py   correlation-ID propagation
    └── README.md
```

### 2.3 `configuration/` ★

The configuration control plane and the canonical source connector framework.

```
configuration/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── platform.py         PlatformConfig (environment, region, feature gates)
│   ├── system_store.py     SystemStoreConfig
│   ├── modules.py          ModulesConfig — module_id → {enabled, implementation, config}
│   ├── agents.py           AgentsConfig
│   ├── workflow.py         WorkflowConfig
│   ├── sources.py          SourcesConfig
│   ├── integrations.py     IntegrationsConfig
│   ├── graph.py            GraphConfig
│   ├── ai.py               AiConfig (providers, routes, tasks, safety, interception)
│   ├── features.py         FeatureFlags
│   ├── release.py          ConfigurationRelease, ReleaseStatus, RuntimeSnapshot, checksum
│   ├── handle.py           ConfigurationHandle — current(epoch) / await pinned(release_id) [NEW, §13.2]
│   ├── adoption.py         ConfigurationAdoption — per-instance adoption state
│   └── errors.py
├── application/
│   ├── __init__.py
│   ├── loader.py           manifest-driven load of backend/config/**
│   ├── validator.py        cross-reference resolution (module/agent/task/route/structure IDs)
│   ├── precedence.py       env → baseline → active release → runtime snapshot
│   ├── release_service.py  DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED
│   ├── activation.py       transactional activation: SUPERSEDE current + ACTIVATE   [NEW, §13.8]
│   │                       target + CAS the active pointer, in one transaction
│   ├── snapshot.py         immutable snapshot construction + SHA-256
│   └── compatibility.py    legacy adapters — TEMPORARY, deleted in Phase 26
├── sources/ ★
│   ├── __init__.py
│   ├── contracts.py        SourceConnectorPlugin protocol (read-only surface only)
│   ├── registry.py         SourceConnectorRegistry
│   ├── definition.py       SourceDefinition, DiscoveryPolicy, SamplingPolicy, QueryLimits
│   ├── service.py          create → Vault secret → validate → discover → activate
│   ├── sanitization.py     sample redaction before any AI or UI exposure
│   ├── connectors/ ★
│   │   ├── __init__.py
│   │   ├── mongodb.py      from dynamic_knowledge/connectors/mongodb.py
│   │   ├── sqlserver.py    from dynamic_knowledge/connectors/sqlserver.py
│   │   └── README.md
│   └── README.md
├── integrations/ ★
│   ├── __init__.py
│   ├── contracts.py        IntegrationConnector protocol
│   ├── definition.py       IntegrationDefinition, credential_ref, retry policy
│   ├── service.py
│   └── README.md
├── persistence/
│   ├── __init__.py
│   ├── module_repository.py
│   ├── release_repository.py
│   ├── source_repository.py
│   └── integration_repository.py
├── api/
│   ├── __init__.py
│   ├── router.py           APIRouter(prefix="/api/config")
│   ├── sources.py          integrations.py   business.py   runtime.py
│   ├── modules.py          security.py       releases.py   audit.py
│   └── schemas.py          request/response models
├── module.py               ConfigurationModuleFactory
└── README.md
```

### 2.4 `agents/` ★

```
agents/
├── __init__.py
├── contracts/ ★
│   ├── __init__.py
│   ├── plugin.py       AgentPlugin protocol
│   ├── descriptor.py   AgentDescriptor
│   ├── request.py      AgentRequest (typed input payload + session/correlation)
│   ├── result.py       AgentResult (typed output + confidence + evidence + next-hints)
│   ├── context.py      AgentExecutionContext — interfaces only, never another agent
│   ├── errors.py       AgentTimeout, AgentContractViolation, AgentDisabled
│   └── README.md
├── registry/ ★
│   ├── __init__.py
│   ├── registry.py     AgentRegistry — uniqueness of agent_id/task_queue/state_namespace
│   ├── builtins.py     allowlisted implementation_id → AgentPlugin factory
│   ├── resolution.py   agent_id → configured implementation_id → plugin
│   └── README.md
├── order_discovery/ ★
│   ├── __init__.py
│   ├── plugin.py               AgentPlugin — a THIN façade that starts or resumes the
│   │                           reasoning graph and maps its outcome to AgentResult.
│   │                           No reasoning state machine lives here (§14.3).
│   ├── contracts.py            DiscoveryInput / DiscoveryOutput
│   ├── state.py                state_namespace-scoped durable agent state
│   ├── conversation.py         conversation memory (system_store backed)
│   ├── anchors.py              strong-anchor validation
│   ├── guards.py               hallucination / response safety
│   ├── prompt_policy.py
│   ├── reasoning/                                        [NEW — LangGraph, §14.3]
│   │   ├── __init__.py
│   │   ├── state.py            typed bounded OrderDiscoveryState (references, not records)
│   │   ├── graph.py            graph construction + compile with the platform checkpointer
│   │   ├── nodes.py            LOAD_CONTEXT … RESPOND
│   │   ├── routing.py          conditional edges; every cycle bounded by configuration
│   │   ├── tools.py            port-backed tools only; never a driver or provider client
│   │   ├── limits.py           budget enforcement → ReasoningLimitExceeded
│   │   └── README.md
│   └── README.md
│   (dynamic_knowledge/order_agent/coordinator.py decomposes INTO reasoning/nodes.py —
│    it does not survive as a coordinator; see §12.1)
├── order_analysis/ ★      plugin.py  analyzer.py  contracts.py  state.py  README.md
├── return_workflow/ ★     plugin.py  eligibility.py  decision.py  contracts.py  state.py  README.md
├── return_fulfillment/ ★  plugin.py  fulfillment.py  carrier_selection.py  contracts.py  state.py  README.md
├── bay_assignment/ ★      plugin.py  placement.py  contracts.py  state.py  README.md
├── feedback_learning/ ★   plugin.py  learning.py  contracts.py  state.py  README.md
└── README.md
```

Every agent README states verbatim: *This agent does not directly invoke another agent.*

### 2.5 `business/` ★

```
business/
├── __init__.py
├── orchestrator/ ★
│   ├── __init__.py
│   ├── definition.py     WorkflowDefinition, StageDefinition, HandlerSpec, HandlerKind
│   ├── conditions.py     allowlisted condition identifiers → configured rule implementations
│   ├── engine.py         ReturnSessionOrchestrator — sequencing only, no business reasoning
│   ├── state.py          session state machine + transition guards
│   ├── handlers.py       AGENT | HUMAN_WORK_QUEUE | INTEGRATION_EVENT dispatch
│   ├── retries.py        per-stage retry/timeout policy
│   ├── temporal/
│   │   ├── __init__.py
│   │   ├── workflow.py   durable workflow definition
│   │   ├── activities.py activity wrappers around agent invocation
│   │   └── worker.py     worker entrypoint
│   └── README.md
├── returns/ ★
│   ├── __init__.py
│   ├── models.py         ReturnSession aggregate, ReturnStage, ReturnDecision, ReturnLine
│   ├── repository.py     system_store "return_sessions"
│   ├── service.py        lifecycle operations
│   ├── conversation.py   the single durable conversation mechanism
│   ├── timeline.py       the single business-event timeline
│   ├── artifacts.py      RMA/RGA, label, tracking, BOL, shipping instructions
│   ├── events.py         domain events → platform.outbox
│   ├── locking.py        session-level optimistic concurrency
│   └── README.md
├── support/ ★        models.py  repository.py  service.py  queue.py  README.md
├── fulfillment/ ★    models.py  repository.py  service.py  tracking.py  carriers.py  README.md
├── warehouse/ ★      models.py  repository.py  service.py  placement.py  bays.py  README.md
├── api/
│   ├── __init__.py
│   ├── router.py     APIRouter(prefix="/api/returns")
│   ├── sessions.py   messages.py  actions.py  support.py
│   ├── artifacts.py  timeline.py  warehouse.py  feedback.py
│   └── schemas.py
├── module.py
└── README.md
```

### 2.6 `graph/` ★

```
graph/
├── __init__.py
├── schema/ ★
│   ├── __init__.py
│   ├── active_schema.py   ActiveSchema — the only source of business structure
│   ├── entities.py        EntityDefinition, IdentifierDefinition, PropertyDefinition
│   ├── relationships.py   RelationshipDefinition, Cardinality, join semantics
│   ├── mappings.py        SourceMapping, physical_path binding
│   ├── derive.py          derive ops: COALESCE, CONTACT_LOOKUP_DIGEST, …
│   ├── ownership.py       OwnershipPolicy / replace-child-set semantics
│   ├── path_resolver.py   logical field_id → physical_path  (the bug class this repo has hit twice)
│   ├── fingerprint.py     schema fingerprinting
│   └── README.md
├── query/ ★
│   ├── __init__.py
│   ├── planner.py         query planning from ActiveSchema
│   ├── compiler.py        Cypher compilation
│   ├── safety.py          query safety / injection guards
│   ├── knowledge_gateway.py  read API used by agents
│   └── README.md
├── connectors/ ★
│   ├── __init__.py
│   ├── neo4j_reader.py
│   ├── neo4j_writer.py    Neo4jDynamicGraphWriter, generation fencing, idempotent receipts
│   ├── write_compiler.py
│   ├── constraints.py     graph-side index/constraint derivation
│   └── README.md
├── projection/ ★
│   ├── __init__.py
│   ├── extractor.py       SourceRecordExtractor
│   ├── projector.py       GraphProjector (one-to-many correct)
│   ├── writer.py          ProjectionWriter — expected_generation_status is a per-call parameter
│   ├── ownership.py       ProjectionOwnership records + reconciliation
│   └── README.md
├── sync/ ★
│   ├── __init__.py
│   ├── coordinator.py     GenericSyncCoordinator
│   ├── full_sync.py       Stage A nodes → Stage B relationships
│   ├── incremental.py     per-page Stage B, checkpoint after both stages
│   ├── on_demand.py       targeted read on graph miss; takes the caller's GenerationHandle
│   │                      as a required argument and never resolves "the active generation"
│   │                      itself (§13.4)
│   ├── checkpoints.py     CheckpointStore — composite identity
│   │                      (configuration_release_id, graph_generation_id, source_asset_id,
│   │                       sync_mode, cursor_partition)
│   ├── manifest.py        SyncRunManifest + RunManifestRecorder (persisted)
│   ├── watermarks.py      watermark capture before any scan
│   └── README.md
├── lifecycle/ ★
│   ├── __init__.py
│   ├── generation.py      GraphGeneration, GraphGenerationStatus, ActiveRuntimeSnapshot,
│   │                      ConfigurationRelease, RebuildLease
│   ├── orchestrator.py    GenerationLifecycleOrchestrator.build_and_activate()
│   ├── handles.py         GenerationHandle — acquired once per operation, carries        [NEW, §13.4]
│   │                      generation_id + fencing_token, asserts currency, released in finally
│   ├── leases.py          GenerationReadLease (ephemeral, request-scoped)                [NEW, §13.3]
│   │                      GenerationWriteReservation (sync-scoped)
│   │                      GenerationSessionLease (durable, workflow-scoped)
│   │                      DrainController — retirement gate over all three
│   ├── binding.py         SessionGenerationBinding — PIN_STRICT | REBIND_ON_RESUME       [NEW, §13.3]
│   ├── validation.py      deep pre-activation validation, schema-derived queries         [NEW]
│   ├── trigger.py         RebuildTrigger — the wiring the orchestrator currently lacks   [NEW]
│   ├── stores.py          MongoActiveRuntimeSnapshotStore, MongoRebuildLeaseStore, lease stores
│   └── README.md
├── migrations/
│   └── *.cypher           0010–0014 today; graph-side only
├── module.py
└── README.md
```

### 2.7 `graph_schema_analyzer/` ★

Strict hexagonal. Imports no other business module — only its own `ports/`.

```
graph_schema_analyzer/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── analysis_session.py    AnalysisSession, SessionStatus
│   ├── source_snapshot.py     SourceSchemaSnapshot — immutable, content-addressed
│   ├── schema_draft.py        GraphSchemaDraft
│   ├── schema_revision.py     SchemaRevision, diff representation
│   ├── mutation.py            typed mutation commands (see §10.4)
│   ├── clarification.py       Clarification, ClarificationAnswer
│   ├── validation_result.py   ValidationResult, ValidationFinding, Severity
│   ├── approval.py            Approval, Approver, ApprovalStatus
│   └── errors.py
├── application/
│   ├── __init__.py
│   ├── discovery_service.py   resolve connectors → metadata → bounded samples → snapshot.
│   │                          Samples pass through the source's sampling policy and the
│   │                          platform redactor before persistence (§13.6).
│   ├── reasoning_service.py   THIN façade: start / resume / inspect the reasoning graph.
│   │                          The former linear "snapshot + requirements → draft" call is
│   │                          replaced by the reasoning/ package (§14.4).
│   ├── prompt_context.py      the six-block untrusted-input framing (see §10.5)
│   ├── mutation_service.py    apply typed mutations, produce revisions
│   ├── validation_service.py  the 13 validation checks
│   ├── approval_service.py
│   └── build_service.py       delegates to the graph module's RebuildTrigger port
├── reasoning/                                            [NEW — LangGraph, §14.4]
│   ├── __init__.py
│   ├── state.py               typed AnalyzerState — IDs and metadata, never raw samples
│   ├── graph.py               graph construction + compile with the platform checkpointer
│   ├── nodes.py               LOAD_SOURCE_SNAPSHOT … READY_FOR_APPROVAL
│   ├── routing.py             conditional edges; bounded revision/clarification loops
│   ├── tools.py               port-backed tools only
│   ├── limits.py              budgets → NEEDS_HUMAN_REVIEW, never unbounded looping
│   └── README.md
├── ports/                     the analyzer's entire outward surface; resolved from the
│   │                          capability registry, never imported from another module (R2)
│   ├── __init__.py
│   ├── source_port.py         SourceDiscoveryPort
│   ├── ai_port.py             SchemaReasoningPort
│   ├── graph_target_port.py   GraphTargetPort (compile / validate / build / sync)
│   ├── system_store_port.py   PersistencePort
│   └── audit_port.py
│
│   NOTE: there is no adapters/ package here. Binding these ports to
│   configuration.sources.registry, ai.gateway, and graph.lifecycle happens in
│   bootstrap/adapters/ (§2.1). The analyzer must not import those modules — asserted by
│   tests/graph_schema_analyzer/test_independence.py.
│
├── persistence/
│   ├── __init__.py
│   ├── session_repository.py     system_store "analysis_sessions"
│   ├── snapshot_repository.py    system_store "source_snapshots"
│   ├── draft_repository.py       system_store "graph_schema_drafts" + "schema_revisions"
│   ├── clarification_repository.py
│   └── approval_repository.py
├── api/
│   ├── __init__.py
│   ├── router.py       APIRouter(prefix="/api/graph-schema")
│   ├── analyses.py
│   ├── schemas_api.py
│   └── schemas.py
├── module.py
└── README.md
```

### 2.8 `ai/` ★

```
ai/
├── __init__.py
├── gateway/ ★
│   ├── __init__.py
│   ├── service.py       AIGateway.invoke(task_id, inputs, context)
│   ├── models.py        AIRequest, AIResponse, TokenUsage, Decision envelope
│   ├── envelope.py      immutable request envelope — the replay unit. Persisted only
│   │                    encrypted, RBAC-gated, with a retention TTL (§13.6).
│   ├── resilience.py    retry, timeout, rate limit, concurrency, circuit breaker
│   ├── fallback.py      fallback strategy execution (TEMPLATE, MANUAL_REVIEW, next provider)
│   └── README.md
├── providers/ ★
│   ├── __init__.py
│   ├── contracts.py     AIProviderPlugin — capabilities / invoke / health
│   ├── registry.py      configuration-driven availability
│   ├── anthropic.py  google.py  nvidia.py  openai.py
│   ├── openai_compatible.py  ollama.py  http.py  simulator.py
│   ├── schema_cleaner.py
│   └── README.md
│   (note: manual.py — the filesystem provider — is deleted; see ai/interception)
├── routing/ ★
│   ├── __init__.py
│   ├── tasks.py         AITaskDefinition — tier, prompt version, system prompt, limits,
│   │                    allowed providers, allowed input keys, fallback
│   ├── routes.py        AIRoute, AIRoutePool, fallback chains
│   ├── selection.py     task → route → provider/model selection
│   └── README.md
├── safety/ ★
│   ├── __init__.py
│   ├── prompt_policy.py     system/module policy composition
│   ├── injection_guard.py   untrusted-input containment
│   ├── scope_guard.py       business-scope enforcement
│   ├── response_contract.py output schema enforcement
│   ├── grounding.py         hallucination / grounding checks
│   └── README.md
├── interception/ ★
│   ├── __init__.py
│   ├── models.py           InterceptionRecord, InterceptionStatus, ResponseOrigin,
│   │                       ResumeCommand (embedded — see §13.5)
│   ├── repository.py       system_store "ai_interceptions"; every transition is one atomic
│   │                       guarded update on `version`
│   ├── service.py          intercept / claim / respond / release / cancel / expire.
│   │                       Completion writes terminal status AND the pending resume command
│   │                       in the same document update — never two writes (§13.5).
│   ├── resume_worker.py    at-least-once delivery of pending resume commands   [NEW, §13.5]
│   ├── replay.py           same-route and alternate-route replay
│   ├── manual_response.py  the human-response validation chain
│   ├── assisted.py         operator-assisted candidate generation (never auto-submit)
│   ├── policy.py           when to intercept; claim TTL; envelope retention and encryption
│   └── README.md
├── metrics/ ★
│   ├── __init__.py
│   ├── traces.py        AITrace persistence
│   ├── aggregation.py   filterable rollups
│   ├── repository.py    system_store "ai_traces"
│   └── README.md
├── api/
│   ├── __init__.py
│   ├── router.py        APIRouter(prefix="/api/ai")
│   ├── requests.py  interceptions.py  metrics.py  providers.py
│   ├── routes.py    safety.py  configuration.py  audit.py
│   └── schemas.py
├── module.py
└── README.md
```

---

## 3. Frontend directory structure

```
frontend/
├── Dockerfile  nginx.conf  index.html
├── package.json  tsconfig*.json  vite.config.ts  vitest.config.ts
├── eslint.config.js  postcss.config.js  tailwind.config.js
├── playwright.config.ts  playwright.real.config.ts
├── openapi/return-platform.openapi.json
├── tests/
│   ├── a11y.spec.ts
│   └── e2e/
│       ├── returns.spec.ts  config.spec.ts  graph-schema.spec.ts  ai.spec.ts
└── src/
    ├── main.tsx
    ├── App.tsx                 four routes; no version prefix, no legacy redirect
    ├── routes.ts               four RouteDefinitions with required capabilities
    ├── env.ts
    ├── index.css
    │
    ├── app/
    │   ├── Shell.tsx           four-domain navigation
    │   ├── Navigation.tsx      RBAC-filtered nav items
    │   ├── RequireCapability.tsx
    │   ├── ErrorBoundary.tsx
    │   ├── NotFoundPage.tsx
    │   └── providers/
    │       ├── AuthProvider.tsx        principal + capabilities
    │       ├── RuntimeConfigProvider.tsx
    │       ├── QueryProvider.tsx
    │       └── ToastProvider.tsx
    │
    ├── domains/
    │   ├── returns/
    │   │   ├── ReturnsPage.tsx
    │   │   ├── components/
    │   │   │   ├── StageBar.tsx
    │   │   │   ├── QueuePanel.tsx           My Returns / Support / Warehouse / Closed
    │   │   │   ├── ConversationPanel.tsx
    │   │   │   ├── MessageList.tsx
    │   │   │   ├── StructuredActionForm.tsx
    │   │   │   ├── ReturnContextPanel.tsx   Customer/Order/Items/Decision/RMA/Tracking/
    │   │   │   │                            Warehouse/Resolution
    │   │   │   └── drawer/
    │   │   │       ├── DetailDrawer.tsx
    │   │   │       ├── TimelineTab.tsx      AgentActivityTab.tsx  AiCallsTab.tsx
    │   │   │       └── GraphEvidenceTab.tsx IntegrationsTab.tsx   AuditTab.tsx
    │   │   ├── hooks/    useReturnSession.ts  useQueues.ts  useReturnActions.ts
    │   │   ├── api/      returnsClient.ts
    │   │   └── types.ts
    │   │
    │   ├── config/
    │   │   ├── ConfigPage.tsx
    │   │   ├── tabs/
    │   │   │   ├── OverviewTab.tsx      DataSourcesTab.tsx   IntegrationsTab.tsx
    │   │   │   ├── BusinessTab.tsx      RuntimeTab.tsx       ModulesTab.tsx
    │   │   │   └── SecurityTab.tsx      ReleasesTab.tsx      AuditTab.tsx
    │   │   ├── sources/
    │   │   │   ├── SourceDetail.tsx
    │   │   │   ├── ConnectionPanel.tsx  ValidationPanel.tsx  DatasetsPanel.tsx
    │   │   │   ├── SchemaPanel.tsx      DataPreviewPanel.tsx (bounded, read-only)
    │   │   │   └── UsagePanel.tsx       SourceAuditPanel.tsx
    │   │   ├── hooks/  api/  types.ts
    │   │
    │   ├── graph-schema/
    │   │   ├── GraphSchemaPage.tsx
    │   │   ├── components/
    │   │   │   ├── SourcesPanel.tsx      databases / collections / tables
    │   │   │   ├── GraphCanvas.tsx       entities / relationships / mappings
    │   │   │   ├── AnalyzerCopilot.tsx   analysis / clarification / modification
    │   │   │   ├── SchemaDiff.tsx
    │   │   │   └── tabs/
    │   │   │       ├── PropertiesTab.tsx  MappingTab.tsx   IndexesTab.tsx
    │   │   │       └── ValidationTab.tsx  SyncTab.tsx      VersionsTab.tsx
    │   │   ├── hooks/  api/  types.ts
    │   │
    │   └── ai/
    │       ├── AiPage.tsx
    │       ├── tabs/
    │       │   ├── OverviewTab.tsx    RequestsTab.tsx       InterceptionsTab.tsx
    │       │   ├── MetricsTab.tsx     ProvidersModelsTab.tsx RoutesTasksTab.tsx
    │       │   └── SafetyTab.tsx      ConfigurationTab.tsx  AuditTab.tsx
    │       ├── components/
    │       │   ├── RequestInspector.tsx
    │       │   ├── InterceptionQueue.tsx      Pending/Claimed/Completed/Expired
    │       │   ├── ManualResponseEditor.tsx   schema-driven, blocks invalid shapes
    │       │   ├── AssistedGenerationPanel.tsx
    │       │   ├── ReplayDialog.tsx           same route | alternate route
    │       │   ├── MetricsFilters.tsx         provider/model/agent/task/route/status/time
    │       │   └── ProvenanceBadge.tsx        HUMAN_INTERCEPTION never shown as a provider
    │       ├── hooks/  api/  types.ts
    │
    ├── shared/
    │   ├── components/
    │   │   ├── PageHeader.tsx  Breadcrumbs.tsx  StatusBadge.tsx  CapabilityBadge.tsx
    │   │   ├── EmptyState.tsx  ErrorState.tsx   LoadingState.tsx ConfirmationDialog.tsx
    │   │   └── DataTable.tsx   JsonViewer.tsx   CodeBlock.tsx    RedactedValue.tsx
    │   ├── hooks/    useCapability.ts  usePolling.ts  usePagination.ts
    │   ├── api/      client.ts  errors.ts  types.ts
    │   └── utils/    format.ts  dates.ts
    │
    ├── contracts/
    │   ├── generated.ts     from openapi — regenerated with every contract change
    │   ├── returns.ts  config.ts  graphSchema.ts  ai.ts
    │   └── common.ts
    │
    └── test/  setup.ts  fixtures/  msw/
```

**Deleted in Phase 23:** `versioning.ts`, `features/data-console/` (67 files), `features/copilot-v2/`,
`features/configuration-v2/`, `features/data-source-config/`, `features/dependency-simulator/` (9 files),
`features/operations/` (15 files — components salvaged into `domains/returns/`), and 70 of 74 route entries.

---

## 4. Configuration directory structure

```
backend/config/
├── README.md                  every key documented
├── manifest.yaml              module_id → relative path (promoted from config/v2/manifest.yaml)
│
├── platform/
│   ├── platform.yaml          environment, region, limits
│   └── system_store.yaml      provider, structures, indexes, migration/drift policy
│
├── modules/
│   └── modules.yaml           module_id → { enabled, implementation, config }
│
├── agents/
│   ├── order_discovery.yaml   order_analysis.yaml      return_workflow.yaml
│   └── return_fulfillment.yaml bay_assignment.yaml     feedback_learning.yaml
│
├── workflows/
│   └── return_session.yaml    stages + handlers + conditions
│
├── policies/
│   ├── privacy.yaml  clarification.yaml  candidate_scoring.yaml  return_eligibility.yaml
│
├── sources/
│   └── <source_id>.yaml       connector_type, connection metadata, credential_ref, policies
│
├── integrations/
│   └── <integration_id>.yaml
│
├── graph/
│   ├── active_schema.yaml     entities, relationships, mappings, ownership, anchors
│   ├── sync.yaml              full/incremental/on-demand policy, cursor fields, page sizes
│   └── generation.yaml        validation expectations, lease TTLs, drain timeout
│
├── ai/
│   ├── providers.yaml         availability + limits (from config/ai_gateway.yaml)
│   ├── routes.yaml            route pools + fallback chains
│   ├── tasks.yaml             task definitions incl. GRAPH_SCHEMA_ANALYSIS
│   ├── safety.yaml
│   └── interception.yaml      when to intercept, claim TTL, retention
│
├── business/
│   └── returns.yaml           from config/returns/production.yaml
│
├── reasoning.yaml             LangGraph runtime settings (§14.11)
└── features.yaml
```

`reasoning.yaml`:
```yaml
reasoning:
  langgraph:
    enabled: true
    checkpoint_store: SYSTEM_STORE       # SYSTEM_STORE is the only production value
    checkpoint_encryption: true
    checkpoint_retention:
      active_runs_expire: false          # not settable to true in production (§14.2)
      terminal_retention_hours: 168      # clock starts at the terminal transition, not creation
      abandon_after_hours: 720           # idle INTERRUPTED/WAITING → ABANDONED → retention starts
    execution:
      bounded: true                      # unbounded execution is not configurable in production
```
Per-component budgets live with the component: `agents/order_discovery.yaml` and
`modules/graph_schema_analyzer.yaml` each carry a `reasoning:` block (§14.7).

**Removed in Phase 26:** `config/v2/` (husk only — content promoted in Phase 2),
`config/dependency_simulation.yaml`, `config/schema_registry.yaml` or `config/data_platform/` (whichever loses
the duplicate-registry decision), `config/seed/`, `config/live_validation/`, `config/data_assets.yaml`.

---

## 5. Infrastructure, scripts, docs

### 5.1 `infra/`
```
infra/
├── sqlserver/init/
│   ├── 001_return_business_state.sql   002_domain_models.sql
│   ├── 003_production_return_platform.sql   004_production_bay_constraints.sql
│   └── README.md                       ordering + runner contract
├── vault/config/vault.hcl
└── README.md
```
The Compose `sqlserver-init` command is replaced by an ordered runner that discovers `NNN_*.sql`, records
applied versions, and never reruns. 003 and 004 are applied for the first time.

### 5.2 `compose.yaml` target services

**One profile contract.** `docker compose up` with no profile brings up infrastructure and bootstrap only —
nothing that serves traffic. The application tier is always an explicit opt-in.

| Profile | Services |
|---|---|
| *(none — default)* | `vault` `mongodb` `mongodb-rs-init` `neo4j` `valkey` `sqlserver` `sqlserver-init` `temporal-postgresql` `temporal` `runtime-configuration-init` |
| `containerized-app` | `backend` `return-workflow-worker` `return-orchestrator` `outbox-publisher` `frontend` |
| `dev-tools` | `temporal-ui` `seed-runner` `diagnostics` |

```bash
docker compose --profile containerized-app up -d          # what start.sh runs
docker compose --profile containerized-app --profile dev-tools up -d
```

`scripts/start.sh` and `start.ps1` pass `--profile containerized-app` explicitly and accept `--dev` to add
`dev-tools`. No service relies on being in the default set to get started.

Removed: `data-job-worker`. Consolidated: `integration-outbox-worker` into `outbox-publisher`.

### 5.2a Dependency locking — one manager, one lock

**Current state is three resolution paths, none of them shared:**

| Path | Resolves via | Lock consumed |
|---|---|---|
| `backend/Dockerfile:13` | `python -m pip wheel .` | **none** — resolves fresh from `pyproject.toml` |
| `scripts/bootstrap_host.sh:22-24`, `run_backend_host.sh:33` | `poetry install --sync` | `poetry.lock` |
| `scripts/bootstrap_host.ps1:36`, `run_all_host.ps1:14` | `uv sync --frozen` (poetry fallback) | `uv.lock` |

The container is the worst case: it is not reproducible against either lock.

**Decision: uv, single `uv.lock`.** `pyproject.toml` already uses PEP 621 `[project]` with `==`-pinned
dependencies, which uv consumes directly, and the PowerShell scripts already prefer uv.

| Consumer | Target |
|---|---|
| `backend/Dockerfile` | `uv sync --frozen --no-dev` against a copied `uv.lock` |
| `scripts/bootstrap_host.{sh,ps1}` | `uv sync --frozen --all-groups` |
| `scripts/run_*_host.{sh,ps1}` | `uv run …` |
| CI / phase gates | `uv run ruff`, `uv run mypy`, `uv run pytest` |

`poetry.lock` and the `[tool.poetry]` block in `pyproject.toml` are deleted **only after** all four consumers
are migrated and a container build plus a host bootstrap both succeed from `uv.lock`. Until then both files
stay and the migration is incomplete — this is the one place where keeping the duplicate is correct.

### 5.3 `scripts/` and `backend/scripts/`
```
scripts/
├── bootstrap.sh  bootstrap.ps1
├── start.sh      start.ps1
├── stop.sh       stop.ps1
├── check_openapi_drift.py
├── validate_frontend_syntax.mjs
├── vault/
└── README.md

backend/scripts/
├── export_openapi.py
├── apply_neo4j_migrations.py
├── apply_sql_migrations.py           [NEW — the ordered runner]
├── run_return_workflow_worker.py
├── run_return_orchestrator.py
├── run_outbox_publisher.py
├── manual_llm_responder.py           [CONVERTED — thin AI Interception API client]
├── diagnostics/
│   ├── live_drift.py  live_mongo_inventory.py  live_sql_inventory.py
│   └── README.md
└── README.md
```
Deleted in Phase 27: all `run_stage4*`, `validate_stage4*`, `start_stage4m_simulation.sh`,
`emit_stage_gate.py`, `generated-fixes/`, `run_data_job_worker.py`, `seed_e2e_data.py` (moves to dev tooling),
`reset_seed.py`, one-time repair and handoff scripts, plus root `fix_eslint.py` and `fix_imports.py`.

### 5.4 `docs/`
```
docs/
├── ARCHITECTURE.md
├── CONFIGURATION.md
├── BOOTSTRAP.md
├── OPERATIONS.md
├── VAULT.md
├── AI.md
├── GRAPH_SCHEMA_ANALYZER.md
├── EXTENSION_GUIDE.md
├── UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md
├── UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md      (this file)
└── consolidation/baseline-inventory.md            (removed after Phase 28)
```
Everything else under `docs/` — `evidence/`, `plans/`, `implementation/`, `execution-context/`, `review/`,
`code_quality/`, `runbooks/`, and the 22 root-level stage plans — is dispositioned in Phase 27.

---

## 6. Test directory structure

Mirrors `src/` exactly. Deleting a module deletes its mirrored test package in the same commit.

```
backend/tests/
├── conftest.py
├── fixtures/                     shared source fixtures (Ferguson-shaped)
├── platform/     test_module_registry.py  test_system_store_bootstrap.py
│                 test_system_store_locking.py  test_migrations.py  test_auth_policy.py
├── reasoning/    test_checkpoint_uses_system_store.py  test_checkpoint_survives_restart.py
│                 test_checkpoint_contains_no_secrets.py  test_bounded_reasoning.py
│                 test_active_run_checkpoints_never_expire.py
│                 test_pending_external_receipt_resolves.py
│                 test_abandonment_blocked_by_pending_external.py
│                 test_forced_abandonment_is_atomic.py
│                 test_late_completion_after_abandonment_rejected.py
│                 test_nodes_do_not_construct_ai_providers.py
│                 test_no_langchain_provider_packages.py
│                 test_langgraph_not_in_public_api.py  test_no_cross_agent_subgraphs.py
├── configuration/ test_loader.py  test_validator.py  test_release_lifecycle.py
│                 test_concurrent_activation.py  test_active_uniqueness_constraint.py
│                 test_reconfiguration_protocol.py  test_late_restart_required_aborts_all.py
│                 test_pinned_release_retention.py
│                 test_source_registry.py  test_connectors_mongodb.py  test_connectors_sqlserver.py
├── agents/       test_registry.py  test_contracts.py  test_no_cross_agent_imports.py
│                 test_context_has_no_module_fields.py
│                 test_new_turn_does_not_reuse_previous_reasoning_state.py
│                 test_clarification_resume_reuses_same_reasoning_run.py
│                 test_order_discovery.py  … one per agent  … test_extension_plugin.py
├── business/     test_orchestrator.py  test_return_session.py  test_support.py
│                 test_fulfillment.py  test_warehouse.py  test_artifacts.py  test_timeline.py
├── graph/        test_active_schema.py  test_path_resolver.py  test_projector.py
│                 test_writer.py  test_full_sync.py  test_incremental.py  test_on_demand.py
│                 test_checkpoints.py  test_generation_writer.py  test_lifecycle_orchestrator.py
│                 test_leases.py  test_generation_validation.py
├── graph_schema_analyzer/  test_discovery.py  test_reasoning.py  test_prompt_context.py
│                 test_mutations.py  test_validation.py  test_persistence.py  test_independence.py
├── ai/           test_gateway_routing.py  test_resilience.py  test_safety.py
│                 test_interception_service.py  test_interception_concurrency.py
│                 test_replay.py  test_manual_response.py  test_provenance.py
├── api/          contract tests per canonical router
└── integration/  live-stack tests, marked and opt-in
```

`test_no_cross_agent_imports.py` and `test_independence.py` are architecture tests — they fail the build if
rule R2 or §4.2 is violated. `test_extension_plugin.py` is the `TEST_AGENT` proof from Phase 30.6.

---

## 7. Core contracts

### 7.1 Neutral platform contracts — `platform/contracts/`

**`platform/*` must not name a type owned by any domain module** (§11). An earlier draft of this design put
`configuration.ConfigurationHandle` and `graph.lifecycle.GenerationHandle` directly into platform-owned
contexts, which violated exactly the rule the capability registry exists to enforce. Platform declares neutral
protocols; domain modules structurally satisfy them.

```python
# platform/contracts/runtime_configuration.py
class RuntimeConfigurationView(Protocol):
    """An IMMUTABLE window onto exactly one release. Reading is only possible here.

    There is no unscoped read anywhere in this contract: you cannot obtain configuration
    values without first naming which release you are reading. That is what makes the
    pinning promise structural rather than a convention.

    release_id/checksum are read-only properties, not plain attributes — a plain
    `x: str` Protocol member means "readable AND writable" to mypy, which the natural
    frozen implementation cannot satisfy.
    """
    @property
    def release_id(self) -> str: ...
    @property
    def checksum(self) -> str: ...
    def section(self, key: str) -> Mapping[str, object]:
        """Raw configuration for one module, from THIS release. The module validates it
        into its own typed model."""


class RuntimeConfigurationHandle(Protocol):
    """Resolves views. Deliberately has NO section() of its own."""
    def current(self, epoch: RuntimeEpoch) -> RuntimeConfigurationView:
        """The release this replica has adopted for this epoch. For startup and background work —
        never for a request or workflow that is pinned."""
    async def pinned(self, release_id: str) -> RuntimeConfigurationView:
        """Raises ReleaseNotRetained if retention has expired."""
    @property
    def adopted_release_id(self) -> str: ...
    @property
    def pending_release_id(self) -> str | None: ...
    @property
    def requires_restart(self) -> bool: ...


# platform/contracts/consistency.py
class ConsistencyHandle(Protocol):
    """A read-consistency token acquired at an operation boundary and threaded through it.

    Deliberately says nothing about graph generations — `graph.lifecycle.GenerationHandle`
    structurally satisfies this, and platform never names it. `token` is a read-only
    property, not a plain attribute, for the same reason as RuntimeConfigurationView above.
    """
    @property
    def token(self) -> str: ...
    def assert_current(self) -> None: ...
    async def release(self) -> None: ...


class ConsistencyChanged(RuntimeError):
    """The consistency token was superseded mid-operation. Restart, never continue."""
```

| Domain type | Satisfies |
|---|---|
| `configuration.domain.handle.ConfigurationHandle` | `RuntimeConfigurationHandle` |
| `configuration.domain.handle.ConfigurationView` | `RuntimeConfigurationView` |
| `graph.lifecycle.handles.GenerationHandle` | `ConsistencyHandle` |
| `graph.lifecycle.GenerationChanged` | `ConsistencyChanged` |

Neither module imports `platform.contracts` to declare conformance — structural typing means the relationship
is checked where the object is published, not where it is defined (see §7.3 on how that check is actually
enforced, which is not by `isinstance` alone).

**Who gets a handle and who gets a view.**

| Consumer | Receives | Why |
|---|---|---|
| `ModuleRuntimeContext` | `RuntimeConfigurationHandle` | modules must observe adoption; they read via `current()` at the epoch they are serving (§13.2) |
| `AgentExecutionContext` | `RuntimeConfigurationView` | an agent is always executing for a pinned session and must not be able to reach `current()` |
| request/activity boundary | `RuntimeConfigurationView` | captured once per request from the request's epoch |

A pinned session resolves its view once at the boundary — `handle.pinned(session.configuration_release_id)` —
and that view is what flows inward. Because the view is the only thing with `section()`, an agent physically
cannot read a newer release. Without this split, a session pinned to release 41 could call
`configuration.section("return_policy")` and silently get release 42's rules while still reporting itself
pinned to 41.

### 7.2 Module kernel — `platform/modules/contracts.py`

```python
class ModuleHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    module_id: str
    status: HealthStatus                 # HEALTHY | DEGRADED | UNAVAILABLE
    detail: str | None = None
    checked_at: datetime


class ModuleRuntimeContext(Protocol):
    """Platform services plus the capability registry. Nothing module-specific (R2a).

    No `.ai`, no `.knowledge`, no `.graph`, and no domain-owned type anywhere in the
    signature. A module needing another module's service declares a Protocol in its own
    ports/ and resolves it from `capabilities` during resolve_capabilities(). See §13.1.

    Every field is a read-only property, not a plain attribute: a plain `x: T` Protocol
    member means "readable AND writable" to mypy, which the natural frozen
    implementation (a frozen dataclass or pydantic frozen model) cannot satisfy.
    """
    @property
    def system_store(self) -> SystemStore: ...
    @property
    def secrets(self) -> SecretResolver: ...
    @property
    def redactor(self) -> Redactor: ...
    @property
    def audit(self) -> AuditSink: ...
    @property
    def configuration(self) -> RuntimeConfigurationHandle: ...    # platform-neutral (§7.1)
    @property
    def capabilities(self) -> CapabilityRegistry: ...
    @property
    def clock(self) -> Clock: ...
    @property
    def correlation(self) -> CorrelationContext: ...


class RuntimeEpoch(Protocol):
    """A replica-local generation of runtime state. Monotonic. Exactly one is current.

    Read-only properties, not plain attributes, for the same reason as
    RuntimeConfigurationView above.
    """
    @property
    def epoch(self) -> int: ...
    @property
    def release_id(self) -> str: ...


class ReconfigureOutcome(StrEnum):
    READY = "READY"                      # candidate resources built, safe to commit
    NO_CHANGE = "NO_CHANGE"              # nothing in this snapshot affects this module
    RESTART_REQUIRED = "RESTART_REQUIRED"


class ModuleRuntime(Protocol):
    async def initialize(self) -> None: ...

    async def publish_capabilities(self, registry: CapabilityRegistry) -> None:
        """Publish what this module provides. Called before any module resolves."""

    async def resolve_capabilities(self) -> None:
        """Resolve this module's ports. Called after every publication, including
        bootstrap-constructed adapters. Never resolve in create() or initialize()."""

    # Epoch-keyed two-phase reconfiguration (§13.2).
    # One-phase cannot be all-or-nothing; per-module swaps cannot be replica-atomic.
    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        """Do ALL fallible work here: validate, build candidate pools/clients/caches
        for `epoch`. Mutate nothing live. Must be safe to abandon."""

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        """Make the prepared candidate ADDRESSABLE under `epoch`. Does NOT make it
        current — the replica's single epoch-pointer swap does that. MUST NOT FAIL."""

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        """Destroy candidates for `epoch`. Live state untouched. MUST NOT FAIL."""

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        """Drop resources for a fully drained epoch. MUST NOT FAIL."""

    async def health(self) -> ModuleHealth: ...
    async def shutdown(self) -> None:
        """Must tolerate being called after initialize() raised partway through -- a
        module that opened a pool then failed a later validation step must still be
        able to close that pool here. The module whose own initialize() failed gets a
        shutdown() call too, not just the ones that succeeded before it."""
    @property
    def router(self) -> APIRouter | None: ...
```

**Modules resolve their resources by epoch, not from a mutable `self._current`.** A module keeps an
epoch-keyed map and looks up whichever epoch the in-flight request is carrying:

```python
def _pool(self, epoch: RuntimeEpoch) -> ConnectionPool:
    return self._by_epoch[epoch.epoch]
```


class ModuleFactory(Protocol):
    @property
    def descriptor(self) -> ModuleDescriptor: ...
    def create(self, context: ModuleRuntimeContext,
               config: Mapping[str, object]) -> ModuleRuntime: ...
```

### 7.3 Capability registry — `platform/capabilities/contracts.py`

**Registrations are keyed by `(capability, contract)`, not by capability alone.** One capability legitimately
serves several consumer shapes: `AI_INVOCATION` must satisfy both `AgentAiPort.invoke(...)` and
`SchemaReasoningPort.reason(...)`. Keying on the capability name alone makes those mutually exclusive — the
second publication hits `DuplicateCapability`, and whichever consumer loses gets a structural type mismatch at
resolve time. Keying on the pair lets one AI Gateway back many differently shaped ports.

```python
class CapabilityName(StrEnum):
    AI_INVOCATION      = "ai.invocation"
    AI_INTERCEPTION    = "ai.interception"
    GRAPH_QUERY        = "graph.query"
    GRAPH_LIFECYCLE    = "graph.lifecycle"
    GRAPH_SYNC         = "graph.sync"
    SOURCE_DISCOVERY   = "source.discovery"
    SOURCE_SCAN        = "source.scan"
    AGENT_EXECUTION    = "agent.execution"
    WORK_QUEUE         = "work.queue"


class CapabilityRegistry(Protocol):
    def publish(self, capability: CapabilityName, contract: type,
                provider_module_id: str, instance: object) -> None:
        """Register `instance` as satisfying `contract` for `capability`.

        Performs the ATTRIBUTE-LEVEL check at publication (see the three-layer note
        below — this is not full signature verification). Raises DuplicateCapability
        only when the same (capability, contract) pair is published twice.
        """

    def resolve(self, capability: CapabilityName, contract: type[T]) -> T:
        """Exact lookup on (capability, contract). Raises CapabilityNotPublished —
        never a partial or best-effort match."""

    def resolve_optional(self, capability: CapabilityName,
                         contract: type[T]) -> T | None: ...
    def list(self) -> tuple[CapabilityPublication, ...]: ...
```

**Who publishes what.** Modules publish their own native surface; `bootstrap/adapters/` publishes the
consumer-shaped bindings. This is why the startup sequence has a distinct adapter pass (§2.1 step 10) between
module publication and module resolution:

```python
# ai/module.py — the AI module publishes its own contract
registry.publish(CapabilityName.AI_INVOCATION, AiGatewayContract, "ai", self._gateway)

# bootstrap/adapters/agent_ai_adapter.py
gateway = registry.resolve(CapabilityName.AI_INVOCATION, AiGatewayContract)
registry.publish(CapabilityName.AI_INVOCATION, AgentAiPort, "bootstrap",
                 AgentAiAdapter(gateway))

# bootstrap/adapters/analyzer_ai_adapter.py
registry.publish(CapabilityName.AI_INVOCATION, SchemaReasoningPort, "bootstrap",
                 AnalyzerAiAdapter(gateway))

# graph_schema_analyzer/module.py — resolves its OWN shape, imports nothing from ai
async def resolve_capabilities(self) -> None:
    self._reasoning = self._ctx.capabilities.resolve(
        CapabilityName.AI_INVOCATION, SchemaReasoningPort
    )
```

Both adapters wrap the same gateway, so routing, failover, rate limits, circuit breakers, interception,
replay, safety, and metrics are shared — there is still exactly one AI execution path.

`graph_schema_analyzer` imports nothing from `ai`; `ai` imports nothing from `graph_schema_analyzer`; only
`bootstrap/adapters/` sees both.

**Conformance is checked in three layers, because one is not enough.** `@runtime_checkable` +
`isinstance()` verifies that the named methods *exist*. It does **not** verify parameter names, arity, types,
or return types — an adapter with `reason(self, prompt)` where the port declares
`reason(self, task_id, context)` passes `isinstance` and fails at first call. Never treat the publication
check as proof of type safety:

| Layer | Catches | Where |
|---|---|---|
| Publication check (`isinstance`) | missing or misnamed methods, wrong object entirely | `registry.publish()`, at startup |
| Static conformance | wrong parameter names, arity, types, return types | mypy, in the phase gate |
| Adapter contract test | wrong behaviour with a correct signature | `tests/platform/test_capability_keying.py` + per-adapter tests |

Static conformance is made checkable by giving every adapter a typed factory — the return annotation is what
mypy verifies:

```python
# bootstrap/adapters/analyzer_ai_adapter.py
def build_analyzer_ai_adapter(gateway: AiGatewayContract) -> SchemaReasoningPort:
    return AnalyzerAiAdapter(gateway)      # mypy proves conformance here
```

A bare `registry.publish(..., AnalyzerAiAdapter(gateway))` with no typed factory is a review defect: it
silently downgrades conformance to attribute-existence only.

### 7.4 Configuration handle — `configuration/domain/handle.py`

```python
class ConfigurationHandle(Protocol):
    def current(self, epoch: RuntimeEpoch) -> ConfigurationView:
        """The snapshot view this instance has ADOPTED for this epoch. Never the merely-ACTIVE release."""

    async def pinned(self, release_id: str) -> ConfigurationView:
        """A specific historical release, for a workflow that started under it.

        Raises ReleaseNotRetained if retention has expired.
        """

    @property
    def adopted_release_id(self) -> str: ...
    @property
    def pending_release_id(self) -> str | None: ...
    @property
    def requires_restart(self) -> bool: ...
```

### 7.5 Generation handle — `graph/lifecycle/handles.py`

```python
class GenerationHandle(Protocol):
    """Acquired ONCE at the start of an operation and threaded through every step.

    Structurally satisfies platform's ConsistencyHandle (§7.1) — that is how it reaches
    an agent without any agent or platform contract naming a graph type.
    No code below the acquisition point may resolve "the active generation" itself.
    """
    token: str                  # == generation_id; satisfies ConsistencyHandle.token
    generation_id: str
    fencing_token: int
    lease_id: str

    def assert_current(self) -> None:
        """Raises GenerationChanged if the active generation moved under us."""

    async def release(self) -> None: ...


class GenerationChanged(ConsistencyChanged):
    """The active generation advanced mid-operation. The caller RESTARTS from the top
    with a fresh handle (bounded retries) — it never continues on stale state.

    Subclasses platform's neutral ConsistencyChanged so a consumer that only knows the
    neutral contract can still catch it.
    """
```

### 7.6 `ModuleDescriptor` — `platform/modules/descriptor.py`

```python
class ModuleDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    module_id: str
    module_kind: ModuleKind              # PLATFORM | BUSINESS | ANALYZER | AI | CONFIGURATION
    implementation_id: str               # must exist in builtins allowlist
    version: str
    capabilities: frozenset[str]
    configuration_schema: str            # reference, not inline schema
    required_platform_capabilities: frozenset[str]
    initialization_dependencies: frozenset[str] = frozenset()
    # module_ids (not Python imports) that must finish initialize() before this
    # module's initialize() runs. Empty by default -- most modules have none. See
    # platform.modules.lifecycle.topological_order (§7.2, dependency-ordered init).
```

### 7.7 System store — `platform/system_store/contracts.py`

```python
class IndexDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    keys: Mapping[str, int]
    unique: bool = False
    name: str | None = None
    partial_filter: Mapping[str, object] | None = None


class StructureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    logical_name: str
    physical_name: str
    schema_version: int
    fields: tuple[FieldDefinition, ...] = ()
    indexes: tuple[IndexDefinition, ...] = ()


class SystemStoreAdapter(Protocol):
    provider: SystemStoreProvider
    async def inspect(self, d: StructureDefinition) -> StructureInspection: ...
    async def create(self, d: StructureDefinition) -> None: ...
    async def ensure_indexes(self, d: StructureDefinition) -> tuple[str, ...]: ...
    async def applied_version(self, logical_name: str) -> int | None: ...
    async def record_version(self, logical_name: str, version: int) -> None: ...


class SystemStore(Protocol):
    def collection(self, logical_name: str) -> Any: ...
    async def health(self) -> ModuleHealth: ...
```

### 7.8 Agents — `agents/contracts/`

```python
class AgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    agent_id: str
    implementation_id: str
    task_queue: str                      # unique across the registry
    state_namespace: str                 # unique across the registry
    prompt_ref: str
    policy_ref: str
    ai_route_ref: str
    input_contract: str
    output_contract: str
    capabilities: frozenset[str]
    timeout_seconds: int = Field(ge=1)
    retry_policy: RetryPolicy
    max_concurrency: int = Field(ge=1)
    requests_per_minute: int = Field(ge=1)
    circuit_breaker_failure_threshold: int = Field(ge=1)
    enabled: bool = True


class AgentExecutionContext(BaseModel):
    """Platform services + capability registry only (R2a).

    No `.ai`, no `.knowledge`, and no type owned by any domain module. An agent declares
    AgentAiPort / KnowledgePort in its own package and resolves them from `capabilities`.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    configuration: RuntimeConfigurationHandle    # platform-neutral (§7.1)
    capabilities: CapabilityRegistry
    audit: AuditSink
    redactor: Redactor
    principal: Principal
    correlation_id: str
    session_id: str
    configuration_release_id: str        # the release this session is pinned to
    consistency: ConsistencyHandle | None   # platform-neutral; threaded from the caller,
                                            # NEVER self-resolved (§13.4)
    clock: Clock
    # deliberately absent: any domain type, and any reference to another agent


class AgentPlugin(Protocol):
    @property
    def descriptor(self) -> AgentDescriptor: ...
    async def execute(self, request: AgentRequest,
                      context: AgentExecutionContext) -> AgentResult: ...
```

**`consistency`, not `generation`.** The generic agent contract says nothing about graph generations — five of
the six agents never touch graph knowledge, and `graph.lifecycle.GenerationHandle` is a domain type an agent
contract must not name. The caller acquires the handle at the operation boundary and passes it through as a
`ConsistencyHandle`; `GenerationHandle` structurally satisfies it.

Agent-owned ports, declared in `agents/contracts/ports.py` and resolved per execution:

```python
class AgentAiPort(Protocol):
    async def invoke(self, task_id: str, inputs: Mapping[str, object]) -> AiOutcome: ...

class KnowledgePort(Protocol):
    async def query(self, request: KnowledgeRequest,
                    consistency: ConsistencyHandle) -> KnowledgeResult: ...
```

Order Discovery, the only agent that needs generation-aware reads, narrows this in its own package —
generation semantics stay entirely outside the shared agent contract:

```python
# agents/order_discovery/ports.py
class KnowledgeConsistencyPort(Protocol):
    async def query(self, request: KnowledgeRequest,
                    consistency: ConsistencyHandle) -> KnowledgeResult: ...
    async def request_targeted_sync(self, spec: SyncSpec,
                                    consistency: ConsistencyHandle) -> SyncOutcome: ...
```

### 7.9 Source connectors — `configuration/sources/contracts.py`

```python
class SourceConnectorPlugin(Protocol):
    """Read-only by construction. No mutating method exists on this surface."""
    connector_type: ConnectorType
    async def validate_connection(self) -> ConnectionValidation: ...
    async def discover_namespaces(self) -> tuple[NamespaceInfo, ...]: ...
    async def discover_datasets(self, namespace: str) -> tuple[DatasetInfo, ...]: ...
    async def describe_dataset(self, ref: DatasetRef) -> DatasetSchema: ...
    async def sample_records(self, ref: DatasetRef, limit: int) -> tuple[Mapping, ...]: ...
    async def scan_records(self, ref: DatasetRef, cursor: Cursor | None,
                           page_size: int) -> ScanPage: ...
    async def current_watermark(self, ref: DatasetRef) -> Watermark: ...
```

### 7.10 AI provider — `ai/providers/contracts.py`

```python
class AIProviderPlugin(Protocol):
    provider_id: str
    @property
    def capabilities(self) -> ProviderCapabilities: ...
    async def invoke(self, request: ProviderRequest) -> ProviderResponse: ...
    async def health(self) -> ProviderHealth: ...
```

### 7.11 Workflow — `business/orchestrator/definition.py`

```python
class HandlerKind(StrEnum):
    AGENT = "AGENT"
    HUMAN_WORK_QUEUE = "HUMAN_WORK_QUEUE"
    INTEGRATION_EVENT = "INTEGRATION_EVENT"


class HandlerSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: HandlerKind
    agent: str | None = None
    queue: str | None = None
    event: str | None = None


class StageDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    handler: HandlerSpec
    optional: bool = False
    conditional: bool = False
    condition: str | None = None         # allowlisted identifier only, never an expression
    timeout_seconds: int | None = None
    retry_policy: RetryPolicy | None = None
```

---

## 8. System store data model

Logical name → physical name → key fields. Physical names live only in
`backend/config/platform/system_store.yaml`.

| Logical | Physical | Key fields | Indexes |
|---|---|---|---|
| `configuration_modules` | `platform_configuration_modules` | `module_id`, `configuration_version`, `status`, `checksum`, `revision` | `(module_id, configuration_version)` unique |
| `configuration_releases` | `platform_configuration_releases` | `release_id`, `status`, `checksum`, `activated_at`, `superseded_by` | `release_id` unique; `status`; **partial unique on `status = ACTIVE`** (§13.8) |
| `configuration_active_pointer` | `platform_configuration_active_pointer` | `_id: "active"`, `release_id`, `checksum`, `version` | singleton; CAS target (§13.8) |
| `configuration_adoption` | `platform_configuration_adoption` | `instance_id`, `adopted_release_id`, `adopted_epoch`, `adopted_at`, `pending_release_id`, `requires_restart`, `draining_epochs[]`, `heartbeat_at` | `instance_id` unique; `heartbeat_at` TTL (§13.2) |
| `source_definitions` | `platform_source_definitions` | `source_id`, `connector_type`, `credential_ref`, `enabled`, `policies` | `source_id` unique |
| `integration_definitions` | `platform_integration_definitions` | `integration_id`, `credential_ref`, `enabled` | `integration_id` unique |
| `return_sessions` | `platform_return_sessions` | `session_id`, `stage`, `status`, `customer_ref`, `order_ref`, `configuration_release_id`, `generation_binding` (`PIN_STRICT`\|`REBIND_ON_RESUME`), `generation_id_at_start` (audit), `generation_id_current`, `version` | `session_id` unique; `(status, stage)`; `customer_ref` |
| `conversations` | `platform_conversations` | `conversation_id`, `session_id`, `messages[]`, `version` | `conversation_id` unique; `session_id` |
| `return_timeline` | `platform_return_timeline` | `event_id`, `session_id`, `event_type`, `occurred_at`, `actor` | `(session_id, occurred_at)` |
| `return_artifacts` | `platform_return_artifacts` | `artifact_id`, `session_id`, `artifact_type`, `payload_ref` | `artifact_id` unique; `session_id` |
| `support_tickets` | `platform_support_tickets` | `ticket_id`, `session_id`, `queue`, `status`, `assignee` | `ticket_id` unique; `(queue, status)` |
| `agent_state` | `platform_agent_state` | `state_namespace`, `session_id`, `payload`, `version` | `(state_namespace, session_id)` unique |
| `analysis_sessions` | `platform_analysis_sessions` | `analysis_id`, `status`, `source_refs[]`, `created_by`, `version` | `analysis_id` unique; `status` |
| `source_snapshots` | `platform_source_snapshots` | `snapshot_id`, `analysis_id`, `content_hash`, `datasets[]` (metadata, always plaintext), `sample_classification` (`NONE`\|`REDACTED`\|`ENCRYPTED`), `samples_ref`, `sample_expires_at`, `captured_at` | `snapshot_id` unique; `analysis_id`; `sample_expires_at` TTL (§13.6) |
| `source_samples` *(encrypted)* | `platform_source_samples` | `samples_ref`, `ciphertext`, `key_ref`, `expires_at` | `samples_ref` unique; `expires_at` TTL |
| `clarifications` | `platform_clarifications` | `clarification_id`, `analysis_id`, `question`, `answer`, `status` | `analysis_id` |
| `graph_schema_drafts` | `platform_graph_schema_drafts` | `draft_id`, `analysis_id`, `status`, `current_revision`, `version` | `draft_id` unique; `analysis_id` |
| `schema_revisions` | `platform_schema_revisions` | `revision_id`, `draft_id`, `sequence`, `mutations[]`, `author` | `(draft_id, sequence)` unique |
| `validation_results` | `platform_validation_results` | `result_id`, `draft_id`, `revision_id`, `findings[]`, `passed` | `(draft_id, revision_id)` |
| `schema_approvals` | `platform_schema_approvals` | `approval_id`, `draft_id`, `approver`, `status`, `approved_at` | `draft_id` |
| `graph_generations` | `platform_graph_generations` | `generation_id`, `status`, `fencing_token`, `schema_fingerprint`, `configuration_release_id` | `generation_id` unique; `status` |
| `active_runtime_snapshot` | `platform_active_runtime_snapshot` | `snapshot_name`, `generation_id`, `version` | `snapshot_name` unique |
| `rebuild_leases` | `platform_rebuild_leases` | `snapshot_name`, `owner_instance_id`, `fencing_token`, `expires_at`, `heartbeat_at` | `snapshot_name` unique; `expires_at` TTL |
| `generation_read_leases` | `platform_generation_read_leases` | `lease_id`, `generation_id`, `holder`, `expires_at` | `generation_id`; `expires_at` TTL |
| `generation_session_leases` | `platform_generation_session_leases` | `lease_id`, `generation_id`, `session_id`, `acquired_at`, `released_at` — **durable, no TTL** | `generation_id`; `session_id` unique-while-open (§13.3) |
| `fencing_tokens` | `platform_fencing_tokens` | `scope`, `next_token` | `scope` unique; monotonic `$inc` (§13.7) |
| `sync_runs` | `platform_sync_runs` | `sync_run_id`, `generation_id`, `mode`, `status`, `manifest` | `sync_run_id` unique; `generation_id` |
| `sync_checkpoints` | `platform_sync_checkpoints` | `configuration_release_id`, `graph_generation_id`, `source_asset_id`, `sync_mode`, `cursor_partition`, `cursor` | composite unique |
| `projection_ownership` | *(graph-side, Neo4j)* | `canonical_key_hash`, `owner_key`, `generation_id` | — |
| `ai_traces` | `platform_ai_traces` | `trace_id`, `request_id`, `task_id`, `agent_id`, `provider`, `model`, `tokens`, `latency_ms`, `status`, `response_origin` — **metrics only, no prompt or response bodies** | `trace_id` unique; `(task_id, created_at)`; `agent_id` |
| `ai_interceptions` | `platform_ai_interceptions` | see §10.3 — no configuration snapshot, no resolved credentials | `interception_id` unique; `(status, created_at)`; `trace_id`; `resume_command.status` |
| `ai_request_envelopes` *(encrypted)* | `platform_ai_request_envelopes` | `envelope_id`, `trace_id`, `ciphertext`, `key_ref`, `classification`, `expires_at` | `envelope_id` unique; `trace_id`; `expires_at` TTL (§13.6) |
| `integration_outbox` | `platform_integration_outbox` | `record_id`, `aggregate_id`, `event_type`, `status`, `attempts` | `record_id` unique; `(status, created_at)` |
| `reasoning_checkpoints` *(encrypted)* | `platform_reasoning_checkpoints` | `thread_id`, `checkpoint_id`, `parent_checkpoint_id`, `ciphertext`, `key_ref`, `metadata`, `expires_at` — **null while the run is live** | `(thread_id, checkpoint_id)` unique; `expires_at` TTL (§14.2) |
| `reasoning_checkpoint_writes` *(encrypted)* | `platform_reasoning_checkpoint_writes` | `thread_id`, `checkpoint_id`, `task_id`, `idx`, `channel`, `ciphertext`, `key_ref`, `expires_at` — **null while live** | `(thread_id, checkpoint_id, task_id, idx)` unique; `expires_at` TTL |
| `reasoning_runs` | `platform_reasoning_runs` | `reasoning_run_id`, `thread_id`, `component_id`, `session_id`/`analysis_id`, `configuration_release_id`, `graph_generation_id`, `lifecycle_state`, `current_node`, `step_count`, `budgets_used`, `outcome`, `started_at`, `last_activity_at`, `terminal_at` | `reasoning_run_id` unique; `thread_id`; `(lifecycle_state, last_activity_at)` for the abandonment sweeper (§14.2) |
| `reasoning_resume_commands` | `platform_reasoning_resume_commands` | `command_id`, `reasoning_run_id`, `workflow_id`, `run_id`, `signal`, `status` (`PENDING`\|`DELIVERED`), `attempts`, `last_attempt_at`, `last_error`, `expires_at` | `command_id` unique; `(status, last_attempt_at)`; `expires_at` TTL (§14.2) |
| `reasoning_action_receipts` | `platform_reasoning_action_receipts` | `reasoning_run_id`, `node_name`, `logical_action_id`, `state`, `external_ref`, `result_ref`, `attempt`, `recorded_at`, `expires_at` — **null while live** | composite unique on `(reasoning_run_id, node_name, logical_action_id)`; `expires_at` TTL (§14.6) |
| `audit` | `platform_audit` | `event_id`, `actor`, `action`, `subject`, `outcome`, `correlation_id`, `occurred_at` | `(subject, occurred_at)`; `actor` |
| `schema_versions` | `platform_schema_versions` | `logical_name`, `applied_version`, `applied_at` | `logical_name` unique |
| `bootstrap_locks` | `platform_bootstrap_locks` | `lock_name`, `lease_id`, `owner_instance_id`, `fencing_token`, `acquired_at`, `heartbeat_at`, `expires_at` | `lock_name` unique; `expires_at` TTL. Heartbeat, release, and every protected write CAS on `(lock_name, lease_id, fencing_token)` — `owner` alone cannot fence a reused process identity (§13.7) |

---

## 9. Complete API surface

Four canonical, versionless domains. Every route requires a capability from `platform/auth/capabilities.py`.

### 9.1 `/api/returns` — Return Business Copilot

```
GET    /api/returns/queues                              list RBAC-filtered queues
GET    /api/returns/queues/{queue}/sessions             queue contents
POST   /api/returns/sessions                            start a return session
GET    /api/returns/sessions/{id}                       session aggregate
GET    /api/returns/sessions/{id}/messages
POST   /api/returns/sessions/{id}/messages              user/associate turn
GET    /api/returns/sessions/{id}/actions               available structured actions
POST   /api/returns/sessions/{id}/actions/{action}      execute structured action
POST   /api/returns/sessions/{id}/cancel
GET    /api/returns/sessions/{id}/timeline
GET    /api/returns/sessions/{id}/artifacts
GET    /api/returns/sessions/{id}/artifacts/{type}
POST   /api/returns/sessions/{id}/artifacts/{type}      generate RMA / label / BOL
GET    /api/returns/sessions/{id}/support
POST   /api/returns/sessions/{id}/support               raise to support queue
POST   /api/returns/sessions/{id}/support/{tid}/resolve
GET    /api/returns/sessions/{id}/warehouse
POST   /api/returns/sessions/{id}/warehouse/assign      bay assignment
POST   /api/returns/sessions/{id}/feedback
GET    /api/returns/sessions/{id}/evidence/graph        graph evidence for the detail drawer
GET    /api/returns/sessions/{id}/evidence/ai           AI calls for the detail drawer
GET    /api/returns/sessions/{id}/audit
```

**There is deliberately no `POST /sessions/{id}/advance`.** A generic client-visible advance would let a
caller skip an agent, a human queue, or an integration wait — directly contradicting "the orchestrator owns
every transition". Return progress is **event- and action-driven only**. The four things that can move a
session forward:

| Trigger | Endpoint / source |
|---|---|
| Associate or customer turn | `POST /sessions/{id}/messages` |
| Structured action from the allowlisted set for the current stage | `POST /sessions/{id}/actions/{action}` |
| Human work-queue completion | `POST /sessions/{id}/support/{tid}/resolve`, warehouse assign, etc. |
| Integration event | inbound webhook → `platform/outbox` inbound counterpart → orchestrator signal |

Each of these submits *intent*. The orchestrator evaluates stage prerequisites and decides whether a
transition occurs. `GET /sessions/{id}/actions` returns only the actions legal in the current stage for the
caller's capabilities, so the UI never offers an illegal move — but the backend re-checks regardless.

### 9.2 `/api/config` — Configuration

```
GET    /api/config/overview
GET    /api/config/sources
POST   /api/config/sources
GET    /api/config/sources/{id}
PATCH  /api/config/sources/{id}
DELETE /api/config/sources/{id}
POST   /api/config/sources/{id}/credential            stores in Vault; returns a ref, never a value
POST   /api/config/sources/{id}/validate
POST   /api/config/sources/{id}/activate
GET    /api/config/sources/{id}/namespaces
GET    /api/config/sources/{id}/datasets
GET    /api/config/sources/{id}/datasets/{ds}/schema
GET    /api/config/sources/{id}/datasets/{ds}/preview  bounded, read-only, redacted
GET    /api/config/sources/{id}/usage
GET    /api/config/sources/{id}/audit
GET    /api/config/integrations                         + POST / GET{id} / PATCH / validate
GET    /api/config/business                             + PUT
GET    /api/config/runtime                              active snapshot + checksum
POST   /api/config/runtime/validate
GET    /api/config/modules                              + PATCH{id} (enable/disable/implementation)
GET    /api/config/security/roles                       + capabilities
GET    /api/config/releases                             + POST
GET    /api/config/releases/{id}
POST   /api/config/releases/{id}/validate
POST   /api/config/releases/{id}/approve
POST   /api/config/releases/{id}/activate
GET    /api/config/audit
```
**No endpoint returns a secret value.** Credential reveal is removed entirely.

### 9.3 `/api/graph-schema` — Graph Schema Analyzer

```
POST   /api/graph-schema/analyses                       start analysis over selected sources
GET    /api/graph-schema/analyses
GET    /api/graph-schema/analyses/{id}
POST   /api/graph-schema/analyses/{id}/messages         copilot turn
GET    /api/graph-schema/analyses/{id}/clarifications
POST   /api/graph-schema/analyses/{id}/clarifications/{cid}/answer
GET    /api/graph-schema/analyses/{id}/snapshot
POST   /api/graph-schema/analyses/{id}/mutations        typed mutation commands (§10.4)
GET    /api/graph-schema/analyses/{id}/revisions
GET    /api/graph-schema/analyses/{id}/revisions/{rid}/diff
POST   /api/graph-schema/analyses/{id}/validate
GET    /api/graph-schema/analyses/{id}/validation
POST   /api/graph-schema/analyses/{id}/approve
GET    /api/graph-schema/schemas
GET    /api/graph-schema/schemas/{id}
POST   /api/graph-schema/schemas/{id}/build             → RebuildTrigger; body { activate: bool = true }
GET    /api/graph-schema/schemas/{id}/generations
GET    /api/graph-schema/schemas/{id}/generations/{gid}
POST   /api/graph-schema/schemas/{id}/generations/{gid}/activate
POST   /api/graph-schema/schemas/{id}/sync
GET    /api/graph-schema/schemas/{id}/drift
```

**Activation cannot bypass the lifecycle.** The former `POST /schemas/{id}/activate` is replaced by a
generation-scoped endpoint with three hard constraints:

1. **Target must be `READY_FOR_ACTIVATION`.** Any other status returns `409 Conflict` and changes nothing.
   There is no path from `DRAFT`, `BUILDING`, `CATCHING_UP`, or `VALIDATING` to `ACTIVE`.
2. **It invokes the same code path as `build_and_activate()`** — the activation half of the orchestrator, not
   a reimplementation. It acquires the rebuild lease, performs the CAS on `configuration_active_pointer`'s
   graph counterpart (`active_runtime_snapshot`), transitions the previous generation to `DRAINING`, and hands
   it to the `DrainController`. Lease acquisition, CAS, and drain initiation are one guarded sequence; a
   failure at any point leaves N `ACTIVE` and marks N+1 `FAILED`.
3. **It exists only for the deferred-activation case** — `POST /build` with `{"activate": false}` stops at
   `READY_FOR_ACTIVATION` so an operator can review deep-validation output before cutover. The default
   `{"activate": true}` runs the whole sequence internally and this endpoint is never needed.

`POST /schemas/{id}/sync` acquires a `GenerationWriteReservation` on the active generation before any write
and fails rather than proceeding if the generation moves.

**No endpoint proposes or executes a source-side schema change.**

### 9.4 `/api/ai` — AI Control Center

```
GET    /api/ai/overview
GET    /api/ai/requests                                 filterable trace list
GET    /api/ai/requests/{trace_id}
GET    /api/ai/interceptions                            ?status=PENDING|CLAIMED|COMPLETED|EXPIRED
GET    /api/ai/interceptions/{id}                       redacted context + response schema
POST   /api/ai/interceptions/{id}/claim                 optimistic; exactly one winner
POST   /api/ai/interceptions/{id}/release
POST   /api/ai/interceptions/{id}/cancel
POST   /api/ai/interceptions/{id}/respond                manual response → validation chain
POST   /api/ai/interceptions/{id}/generate-candidate     assisted; never auto-submits
POST   /api/ai/interceptions/{id}/replay                 { mode: SAME_ROUTE | ALTERNATE_ROUTE }
GET    /api/ai/metrics                                   filters: provider/model/agent/task/route/status/time
GET    /api/ai/providers                                 + GET{id}/health
POST   /api/ai/providers/{id}/validate                   explicit only; never at startup
GET    /api/ai/routes                                    + GET /api/ai/tasks
GET    /api/ai/safety
GET    /api/ai/configuration
GET    /api/ai/audit
```

### 9.5 Platform
```
GET    /health/live
GET    /health/ready                                     module health rollup
GET    /api/runtime-config                               frontend bootstrap config
```

---

## 10. State machines

### 10.1 Configuration release
```
DRAFT ──validate──▶ VALIDATED ──approve──▶ APPROVED ──activate──▶ ACTIVE
                        │                                            │
                        └──────────── reject ─────────────▶ DRAFT    ▼
                                                              SUPERSEDED
```
Activation produces an immutable `RuntimeSnapshot` with a SHA-256 checksum. Exactly one `ACTIVE` release.

### 10.2 Graph generation
```
                    ┌──── FAILED (terminal; N stays ACTIVE)
                    │
PREPARING ─▶ BUILDING ─▶ CATCHING_UP ─▶ VALIDATING ─▶ READY_FOR_ACTIVATION ─▶ ACTIVE
                                                                                 │
previous generation:              ACTIVE ─▶ DRAINING ─▶ RETIRED  ◀───────────────┘
                                            (waits for read leases or timeout)
```
Activation is a compare-and-swap on `active_runtime_snapshot`. Generation N is **never** destroyed immediately
after N+1 activates. On any failure the candidate becomes `FAILED` and N remains `ACTIVE`.

### 10.3 AI interception
```
PENDING ──claim──▶ CLAIMED ──respond───▶ RESPONDED   (terminal)
   │                  ├─────replay────▶ REPLAYED    (terminal)
   │                  ├─────cancel────▶ CANCELLED   (terminal)
   │                  └─────release───▶ PENDING
   └──ttl expiry────▶ EXPIRED (terminal)
```
Every transition is an optimistic update on `version`. Two concurrent claims: one succeeds, one gets a
conflict — asserted by `tests/ai/test_interception_concurrency.py`.

**Record fields** — note what is *not* here (§13.6): no `configuration_snapshot`, no `route_snapshot`, no
resolved credentials, no raw prompt or response body.

```
interception_id  trace_id  request_id
session_id  agent_id  task_id
status  reason
configuration_release_id     configuration_checksum      ← IDs, not the snapshot
route_metadata { task_id, route_id, provider_id, model_id, tier }   ← sanitized, no endpoints/keys
prompt_version               graph_generation_id
response_schema              envelope_ref → ai_request_envelopes (encrypted, TTL)
created_at  claimed_at  completed_at  claimed_by  completed_by
response_origin              version
resume_command {
    command_id               ← idempotency key
    status: PENDING | DELIVERED
    workflow_id  run_id  signal_name
    result_ref
    attempts  last_attempt_at  last_error
}
```

**Atomicity invariant (§13.5).** Completion is exactly one guarded document update that sets the terminal
status **and** `resume_command.status = PENDING` together. There is no window in which the interception is
`RESPONDED` with no resume command pending, nor one in which a resume is delivered for an interception that
is not yet terminal.

**Provenance invariant:** a human response is stored with `response_origin = HUMAN_INTERCEPTION` and
`provider = MANUAL_INTERCEPT`, and is never attributed to Gemini, Claude, NVIDIA, or any other provider in
storage, metrics, or UI.

### 10.4 Schema draft and mutation commands
```
DRAFT ──mutate──▶ DRAFT (new revision)
DRAFT ──validate──▶ VALIDATED ──approve──▶ APPROVED ──build──▶ (generation lifecycle §10.2)
   ▲                    │
   └──── mutate ────────┘   (any mutation invalidates VALIDATED)
```
Typed mutation commands — the complete set:
```
AddEntity          RemoveEntity        RenameEntity
AddProperty        RemoveProperty
ChangeIdentifier
AddRelationship    RemoveRelationship  ChangeCardinality
ChangeSourceMapping                    ChangeTransformation
AddGraphIndex      RemoveGraphIndex
AddGraphConstraint RemoveGraphConstraint
ChangeOwnershipPolicy                  ChangeSyncRule
```
The model emits these structures. The compiler — never the model — turns validated structures into graph
operations. No model-authored executable statement ever reaches a database.

**Validation checks (all 13 must pass before approval):** source exists; dataset exists; field exists; type
compatibility; identifiers available; relationships resolvable; cardinality plausible; transformation
supported; search anchors viable; Cypher compiles; query safety passes; graph index definition valid; graph
constraint valid; sync projection executable.

### 10.5 AI prompt context framing

Every AI call that touches source data uses six ordered, hard-separated blocks:
```
1  SYSTEM POLICY          platform-level, non-overridable
2  MODULE POLICY          module-level constraints
3  TASK                   the configured AI task definition
4  SOURCE METADATA        trusted — names, types, cardinalities
5  UNTRUSTED SOURCE SAMPLE  data, never instructions
6  USER REQUIREMENTS      the operator's stated intent
```
Source values never become instructions. This reuses the untrusted-data framing already proven in
`config/ai_gateway.yaml`'s task prompts.

### 10.6 Return session
```
DISCOVERY ▶ ANALYSIS ▶ RETURN_DECISION ▶ [SUPPORT] ▶ RMA ▶ FULFILLMENT
    ▶ PHYSICAL_RETURN ▶ [WAREHOUSE] ▶ RESOLUTION ▶ FEEDBACK
```
`[SUPPORT]` is optional (human work queue). `[WAREHOUSE]` is conditional on an allowlisted condition
identifier. Any stage may transition to `CANCELLED`. The orchestrator owns every transition; agents own none.

---

## 11. Dependency direction rules

```
                       bootstrap
                           │  (knows every module.py, nothing else)
      ┌────────────┬───────┴───────┬──────────────┬─────────────┐
      ▼            ▼               ▼              ▼             ▼
 configuration  business   graph_schema_analyzer  graph         ai
      │            │               │              │             │
      └────────────┴───────────────┴──────────────┴─────────────┘
                           │  (contracts only)
                           ▼
                       platform
```

**Enforced by architecture tests:**

| Rule | Test |
|---|---|
| No agent imports another agent implementation | `tests/agents/test_no_cross_agent_imports.py` |
| No module imports another module (only `bootstrap/adapters/` may) | `tests/platform/test_no_module_cross_imports.py` |
| `graph_schema_analyzer` imports no other module | `tests/graph_schema_analyzer/test_independence.py` |
| Runtime contexts carry no module-specific field (R2a) | `tests/agents/test_context_has_no_module_fields.py` |
| `platform/*` imports no domain module **and names no domain type** | `tests/platform/test_layering.py` |
| Capabilities are keyed on `(capability, contract)`; a shape mismatch fails at publication | `tests/platform/test_capability_keying.py` |
| Reconfiguration is all-or-nothing across modules | `tests/configuration/test_late_restart_required_aborts_all.py` |
| No `db["platform_*"]` literal outside `system_store/` and the manifest | `tests/platform/test_logical_names.py` |
| No provider/model literal outside `ai/providers/` and config | `tests/ai/test_no_hardcoded_models.py` |
| No business table/collection/field literal outside `config/` and fixtures | `tests/graph/test_no_hardcoded_business_names.py` |
| No durable write of source/config data bypasses the redactor | `tests/platform/test_redaction_required.py` |
| Nothing below the handle acquisition resolves the active generation | `tests/graph/test_generation_handle_threading.py` |
| No generic business advance endpoint exists | `tests/api/test_no_generic_advance_endpoint.py` |

These eleven tests are the machine-checkable form of §4.1–4.4 of the plan and §13 of this document. Add each
in the phase that creates the module it guards, not at the end.

---

## 12. Migration map

Current path → target path. `DELETE` means removed with no successor; `→` means the code moves and is adapted.

### 12.1 Backend

| Current | Target | Phase |
|---|---|---|
| `main.py` (916 lines, 36 routers) | `main.py` + `bootstrap/*` | 1, 22c |
| `dynamic_knowledge/internal_store/*` | `platform/system_store/*` | 3 |
| `security/principal.py` | `platform/auth/principal.py` + new role model | 17 |
| `secrets/vault.py`, `secrets/runtime.py` | `platform/secrets/*` | 3 |
| `operations/integrations/outbox.py`, `workers/integration_outbox.py` | `platform/outbox/*` | 16 |
| `configuration/settings.py` | `bootstrap/settings.py` + `configuration/domain/platform.py` | 2 |
| `configuration/runtime_loader.py`, `snapshot.py`, `runtime_activation.py` | `configuration/application/*` | 2 |
| `v2/services.py::ModularConfigurationService` | `configuration/application/release_service.py` | 2 |
| `v2/state_store.py` | `platform/system_store/mongo.py` (semantics) | 3 |
| `v2/services.py::SchemaDesignService` | `graph_schema_analyzer/` (rebuilt, per-entity) | 9 |
| `v2/services.py::V2PlatformServices` | DELETE | 22b |
| `v2/models.py`, `runtime_adapters.py`, `sync_jobs.py` | absorbed / DELETE | 22b, 24 |
| `api/platform_v2.py`, `api/data_source_config_v2.py` | `configuration/api/*` | 15, 22b |
| `dynamic_knowledge/connectors/mongodb.py`, `sqlserver.py` | `configuration/sources/connectors/*` | 8 |
| `data_platform/sources/mongodb/*` | DELETE (superseded) | 24 |
| `data_console/api/sources.py`, `inventory.py`, `browser.py` | `configuration/api/sources.py` | 15, 22a |
| `data_console/api/configuration.py`, `runtime_validation.py` | `configuration/api/runtime.py` | 15, 22a |
| `data_console/api/audit.py` | `platform/audit/*` + `configuration/api/audit.py` | 15, 22a |
| `data_console/api/graph.py`, `graph_evidence.py`, `graph_sync.py` | `graph/*` + `business/api` evidence | 22a |
| `data_console/api/schema_catalog.py` | `graph_schema_analyzer/` | 22a |
| `data_console/api/ai_studio.py`, `data_platform/ai_studio.py` | DELETE | 24 |
| `data_console/api/operational_generation.py`, `data_platform/operational_generation/` | DELETE | 24 |
| `data_console/api/workspaces.py`, `scenarios.py`, `jobs.py` | DELETE | 24 |
| `data_console/api/copilot_operations.py`, `feedback_learning.py` | `business/api/*` | 16, 22a |
| `data_console/infrastructure/probes.py` | `bootstrap/health.py` | 4 |
| `agents/registry.py` + `dynamic_knowledge/agents/registry.py` | `agents/registry/registry.py` (merged) | 5 |
| `agents/order_discovery.py` (legacy) | DELETE | 24 |
| `dynamic_knowledge/order_agent/coordinator.py` | decomposed into `agents/order_discovery/reasoning/nodes.py` — does **not** survive as a coordinator (§14.3) | 7 |
| `dynamic_knowledge/order_agent/search_strategy.py` | `agents/order_discovery/reasoning/` (PLAN_SEARCH / PLAN_NEXT_QUERY nodes) | 7 |
| `dynamic_knowledge/order_agent/{state,contracts,prompt_policy,conversation_repository}.py` | `agents/order_discovery/*` | 5, 7 |
| `agents/order_analysis.py` … `feedback.py` | `agents/<name>/plugin.py` | 5 |
| `dynamic_knowledge/schema.py`, `path_resolver.py`, `fingerprint.py` | `graph/schema/*` | 8 |
| `dynamic_knowledge/graph/*` | `graph/connectors/*`, `graph/projection/*`, `graph/lifecycle/generation.py` | 12 |
| `dynamic_knowledge/sync/*`, `on_demand_sync/*` | `graph/sync/*` | 12 |
| `dynamic_knowledge/lifecycle/*` | `graph/lifecycle/*` + new `leases.py`, `validation.py`, `trigger.py` | 12 |
| `dynamic_knowledge/knowledge/*` | `graph/query/*` | 7 |
| `dynamic_knowledge/api/order_agent.py` | `business/api/sessions.py` + `messages.py` | 16 |
| `data_platform/graph/migrations/*.cypher` | `graph/migrations/*.cypher` | 12 |
| `data_platform/graph/schema.py` (`GraphSchemaManager`) | DELETE once `graph_sync` router is gone | 24 |
| `data_platform/graph/writer.py`, `commands.py` | **investigate then decide** (third Neo4j writer) | 24 |
| `data_platform/mapping/*`, `schema_registry.py` | `graph/schema/mappings.py` or DELETE | 8, 24 |
| `ai_gateway/service.py`, `models.py` | `ai/gateway/*` | 13 |
| `ai_gateway/providers/*` (minus `manual.py`) | `ai/providers/*` | 13 |
| `ai_gateway/providers/manual.py` | DELETE → `ai/interception/` | 14, 24 |
| `ai_gateway/routing.py` | `ai/routing/*` | 13 |
| `ai_gateway/safety.py` | `ai/safety/*` | 13 |
| `ai_gateway/configuration.py` | `configuration/domain/ai.py` | 13 |
| `api/ai_gateway.py` | `ai/api/*` | 13 |
| `api/returns.py`, `associate_returns.py`, `production_workflow.py` | `business/api/sessions.py`, `actions.py` | 16 |
| `api/support.py` + `api/return_support.py` | `business/api/support.py` (merged) | 16 |
| `api/physical_operations.py`, `warehouse_placement.py` | `business/api/warehouse.py` | 16 |
| `api/return_artifacts.py` | `business/api/artifacts.py` | 16 |
| `api/return_agents.py` | `business/api/*` + `agents/registry` introspection | 16 |
| `api/integration_outbox.py` | `platform/outbox/` + `configuration/api/integrations.py` | 15 |
| `api/dependencies.py` | `bootstrap/health.py` | 4 |
| `api/seed.py`, `operations/seed_*.py` | DELETE (dev tooling only) | 24 |
| `api/dependency_simulator.py`, `dependency_simulation/` | DELETE | 24 |
| `api/runtime_config.py` | `bootstrap/` (`/api/runtime-config`) | 22c |
| `operations/orchestrator.py`, `associate_flow.py` | `business/orchestrator/engine.py` | 6, 16 |
| `operations/repository.py`, `models.py`, `events.py` | `business/returns/*` | 16 |
| `operations/return_support/*` | `business/support/*` | 16 |
| `operations/physical/*` | `business/fulfillment/*` | 16 |
| `operations/warehouse/*` | `business/warehouse/*` | 16 |
| `operations/feedback_service.py` | `agents/feedback_learning/` + `business/returns/` | 16 |
| `operations/order_discovery/` | session/locking → `business/returns/`; discovery → DELETE | 16, 24 |
| `operations/sql_business_state.py` | `business/*/repository.py` | 16 |
| `workflows/*` (13 modules) | `business/orchestrator/temporal/*` | 6, 16 |
| `conversation/` | `business/returns/conversation.py` | 16 |
| `canonical/` | **disposition required** — likely `business/*/models.py` | 24 |
| `data_governance/`, `validation/`, `shared/governance.py` | **disposition required** | 24 |
| `resources.py` | `bootstrap/context.py` | 1 |

### 12.2 Frontend

| Current | Target | Phase |
|---|---|---|
| `App.tsx` (V1/V2 switch + redirect) | `App.tsx` (four routes) | 17, 23 |
| `versioning.ts` | DELETE | 23 |
| `routes.ts` (74 routes) | `routes.ts` (4 routes) | 17, 23 |
| `components/Shell.tsx` | `app/Shell.tsx` | 17 |
| `components/*` (shared primitives) | `shared/components/*` | 17 |
| `features/operations/*` (15) | `domains/returns/*` | 18 |
| `features/copilot-v2/*` | `domains/returns/*` | 18, 23 |
| `features/data-console/pages/sources|browser|inventory` | `domains/config/sources/*` | 19 |
| `features/data-console/pages/configuration|runtime-validation` | `domains/config/tabs/*` | 19 |
| `features/data-console/pages/audit|governance` | `domains/config/tabs/AuditTab.tsx` | 19 |
| `features/data-console/pages/schema|graph|graph-sync|graph-evidence` | `domains/graph-schema/*` | 20 |
| `features/data-console/pages/ai-studio` | DELETE | 23 |
| `features/data-console/pages/workspaces|scenarios|imports|exports|jobs` | DELETE | 23 |
| `features/configuration-v2/StudioPages` | `domains/config/*` | 19, 23 |
| `features/data-source-config/*` | `domains/config/sources/*` | 19, 23 |
| `features/dependency-simulator/*` (9) | DELETE | 23 |
| `contracts/dataStudio|dependencySimulator|browser|inventory|jobs|graphExplorer|consoleGovernance` | DELETE | 23 |
| `contracts/orderAgent|operations|associateReturns` | `contracts/returns.ts` | 18 |
| `contracts/dataSourceConfig|graphEvidence` | `contracts/config.ts`, `contracts/graphSchema.ts` | 19, 20 |

---

## 13. Distributed correctness invariants (normative)

Eleven defects were found in review of this design's first draft. Six of them were distributed-system
correctness defects, not documentation gaps. This section is the corrected contract. **Where §13 and any
earlier section disagree, §13 wins.** Each invariant names the test that enforces it.

### 13.1 Modules never hold each other — capability registry

**Defect.** `ModuleRuntimeContext.ai`, `AgentExecutionContext.knowledge`, and analyzer adapters importing
`configuration.sources.registry` / `ai.gateway` / `graph.lifecycle` created compile-time coupling that becomes
circular as modules grow.

**Contract.**
- `ModuleRuntimeContext` and `AgentExecutionContext` carry platform services plus `CapabilityRegistry`, and no
  named field for any module's service (R2a) — and **no domain-owned type at all**. Where a handle must cross
  the boundary, platform declares a neutral protocol in `platform/contracts/` (§7.1) that the domain type
  structurally satisfies: `RuntimeConfigurationHandle` for configuration, `ConsistencyHandle` for graph
  generations.
- A consumer declares the Protocol it needs in its own `ports/` and resolves it via
  `capabilities.resolve(CapabilityName.X, MyPort)`. Structural typing means neither side imports the other.
- **Registrations are keyed by `(capability, contract)`** (§7.3), so one provider can back several
  differently-shaped consumer ports — `AI_INVOCATION` serves both `AgentAiPort` and `SchemaReasoningPort`.
  Keying on capability alone would make those mutually exclusive.
- Four ordered passes (§2.1 steps 8–11): modules are constructed (`create`), modules publish their native
  contracts (`publish_capabilities`), `bootstrap/adapters/` publishes consumer-shaped bindings, then modules
  resolve (`resolve_capabilities`). This is what makes construction order independent of dependency order.
- Concrete binding adapters live in `bootstrap/adapters/`, the only package permitted to import two modules.
- No module has an `adapters/` package — `graph_schema_analyzer/adapters/` in particular does not exist.

**Enforced by** `tests/platform/test_no_module_cross_imports.py`,
`tests/graph_schema_analyzer/test_independence.py`, `tests/agents/test_context_has_no_module_fields.py`.

### 13.2 Configuration activation propagates or refuses — no silent staleness

**Defect.** `POST /releases/{id}/activate` set `status = ACTIVE`, but modules held a `RuntimeSnapshot` captured
at startup. The UI could report ACTIVE while every replica still executed the old configuration, indefinitely.

**Contract.** Activation is atomic (§13.8); adoption is a **two-phase protocol** with an observable per-replica
state.

**Two problems, two mechanisms.** Getting either one alone still leaves a mixed runtime.

**(a) Why two phases.** A single `reconfigure(snapshot)` per module cannot deliver all-or-nothing adoption.
If module A rebuilds its connection pools successfully and module B then returns `RESTART_REQUIRED`, A is
already on the new configuration and B is on the old — unrecoverable without a restart.

**(b) Why per-module commits are still not enough.** Even when every `commit` is an individually atomic,
non-failing pointer swap, the swaps happen one after another. A request admitted between the first and last
observes module A on release X and module B on X-1:

```
   commit A ──┬── commit B ─── commit C
              │
        request enters here  →  sees A@X, B@X-1, C@X-1
```

Module-level atomicity is not replica-level atomicity. The fix is a **single replica-scoped epoch pointer**
plus per-request epoch capture.

```
prepare_reconfigure(epoch X)  on every module
        │   candidate resources built; nothing live mutated; abandonable
        ▼
   all READY / NO_CHANGE ?
        │
   ┌────┴──────────────────────────────────┐
   ▼ yes                                   ▼ no (any RESTART_REQUIRED or raise)
commit_reconfigure(X) on every module   abort_reconfigure(X) on every module
   candidates become ADDRESSABLE           candidates destroyed
   under X — not yet current               live state never touched
        │                                         ▼
        ▼                                  adoption.pending_release_id = X
  ══ ONE replica epoch-pointer swap ══     adoption.requires_restart = true
        X-1 ──────────► X                  /health/ready → DEGRADED
        (single atomic write)              UI: "pending restart on N replicas"
        │
        ▼
  new requests capture X
  in-flight requests keep X-1
        │
        ▼
  X-1 drains → release_epoch(X-1) on every module
```

- **A request captures its epoch once, at the outer boundary** (HTTP admission, Temporal activity start,
  worker loop iteration) and carries it. Every module resolution during that request uses that epoch. A
  request therefore observes exactly one release across every module it touches, for its whole life.
- **All fallible work happens in `prepare`** — validation, pool construction, client creation, cache warming.
  `commit`, `abort`, and `release_epoch` are non-failing by construction. If a `commit` nevertheless raises,
  the replica cannot safely roll back a partial promotion: it marks itself `UNAVAILABLE`, stops serving, and
  requires restart. A hard failure, never a silent mix.
- **The epoch pointer swap is the only moment adoption becomes visible**, and it is one write.
- Old-epoch resources are retained until in-flight requests drain, then released. This is the same
  drain-before-release discipline as graph generations (§13.3), for the same reason.

**Capture and lease acquisition are one atomic operation, not two.** A design that exposes "read the current
epoch" and "register as a holder of it" as separate calls on separate objects (a pointer object plus an
independent lease-tracker object) admits a race: a reader observes epoch X, and before it registers as a
holder, a concurrent reconfiguration swaps to X+1 and — seeing zero holders on X — releases X's resources out
from under the reader. The fix is a single component owning the pointer, the drain state, and the active
leases behind one lock, with only one public entry point for admission (`acquire_current()`) that reads and
registers as one operation. There is no way to express "give me a specific, possibly-stale epoch" — admission
always targets whatever is current at that instant.

**Holders are tracked by unique lease identity, not a bare count.** A plain integer counter cannot distinguish
"this exact acquisition was released twice" from "two different holders each released once" — decrementing on
every `release()` call means a caller that accidentally releases the same acquisition twice silently frees a
slot that some other, still-active holder actually owns, letting an epoch appear fully drained while a genuine
holder is still using it. `acquire_current()` therefore returns an `EpochLease` (a unique `lease_id` plus the
acquired epoch, structurally satisfying `RuntimeEpoch` so it drops in anywhere a plain epoch value was
expected), and the admission tracks a `set` of active lease IDs per epoch rather than a count. Releasing a
lease removes it from the set — an operation that is unconditionally idempotent no matter how many times, or
from how many concurrent threads, it is invoked, because set removal of an absent element is a no-op by
definition, unlike decrementing a number past zero.

Each epoch carries an explicit lifecycle state: `CURRENT → DRAINING → RELEASING → RELEASED`. New leases are
only ever issued against `CURRENT`; a lease acquired while `CURRENT` remains valid to release after the epoch
moves to `DRAINING`; attempting to release a `CURRENT` epoch is rejected outright (the actively serving epoch
can never be torn down); `RELEASING` marks a drained epoch whose module cleanup is in progress but not yet
confirmed complete, and is re-enterable — a failed cleanup attempt leaves the epoch in `RELEASING` rather than
finalizing it, so a retry can pick up where the failure left off; and `RELEASED` is terminal and idempotent to
request again. The lock protects reads and writes of this state and the pointer together, so no operation can
observe or act on a value that a concurrent swap has already superseded.

*(A request-admission read/write lock — writers block admission during the swap — is an acceptable simpler
implementation, but it stalls admission and does nothing for long-running requests. The epoch model is the
default.)*
- **Running workflows stay pinned.** A session records `configuration_release_id` at start and resolves via
  `handle.pinned(release_id)` for its whole life. Pinned releases are retained until no open session
  references them.
- `configuration_adoption` (one document per instance, TTL-heartbeated) makes per-replica state queryable, so
  "ACTIVE" in the UI means "ACTIVE and adopted by N of N replicas" or explicitly names the gap.

**Pinning is structural, not conventional.** A pinned session resolves `handle.pinned(release_id)` once at its
boundary and receives a `RuntimeConfigurationView` — the only object with `section()`. An agent holding a view
physically cannot reach `current()`, so a session pinned to release 41 cannot read release 42's rules (§7.1).

**Concurrent reconfiguration attempts must be serialized, not just fenced.** `begin_swap()` fencing (above)
stops a stale swap from *succeeding*, but without serialization two attempts targeting different epochs can
still run their multi-`await` prepare/commit phases concurrently and interleave: attempt A (epoch 2) and
attempt B (epoch 3) both start, B's commit finishes and swaps first, then A's later commit finishes and calls
`begin_swap` — the fencing check *does* catch this specific interleaving (A's expected-current no longer
matches), but relying on fencing alone to catch every interleaving is fragile once more call sites exist. The
whole reconfigure sequence — prepare through commit-and-swap — is therefore also serialized by one lock scoped
to the coordinator, so two attempts never run prepare/commit concurrently in the first place; fencing remains
as defense in depth for any caller that reaches the admission primitive directly.

**Admission-closed and "replica is UNAVAILABLE" must be one synchronization domain, not two.** A commit
failure setting a status flag on the *coordinator* while request admission is checked against the *admission
object's* lock reopens exactly the TOCTOU shape this section already fixed once: a request can read
"AVAILABLE" a moment before a concurrent fatal commit flips the flag, then be admitted anyway. The
accepting/closed flag lives inside the same lock as the epoch pointer and holder counts, and the one admission
method (`acquire_current()`) checks it under that lock — there is no externally observable state that can go
stale between a status check and an admission.

**Release finalization needs an intermediate state, not a single boolean transition.** Marking an epoch
`RELEASED` in the same step that decides "holders have reached zero" — before the corresponding module cleanup
calls have actually succeeded — can finalize an epoch whose resources were never fully torn down: if module A's
cleanup succeeds and module B's raises, a `RELEASED` epoch can never be revisited to finish B's cleanup. An
intermediate `RELEASING` state exists between `DRAINING` and `RELEASED` specifically so a cleanup failure
leaves the epoch retryable rather than falsely finalized: the retry re-invokes every module's cleanup
(including ones that already succeeded, which is why that cleanup call is documented as idempotent) and only
transitions to `RELEASED` once none of them raise.

**Enforced by** `tests/configuration/test_reconfiguration_protocol.py`,
`tests/configuration/test_pinned_release_retention.py`,
`tests/configuration/test_late_restart_required_aborts_all.py` — the case where the **last** module polled
returns `RESTART_REQUIRED` after every earlier module already prepared, asserting all of them received
`abort_reconfigure` and no live resource was promoted —
`tests/configuration/test_requests_never_observe_mixed_release_during_adoption.py` — continuous concurrent
requests while the release changes, asserting every request reports exactly one release ID across all
participating modules — plus
`tests/agents/test_agent_reads_pinned_configuration_after_new_release_activation.py` and
`tests/agents/test_running_workflow_never_reads_current_release.py`. Lease-identity tracking specifically:
`tests/platform/test_epoch_admission.py`'s `test_duplicate_release_does_not_decrement_another_holder`,
`test_epoch_cannot_release_while_any_unique_lease_remains`, and
`test_concurrent_lease_release_is_idempotent`.

### 13.3 A generation cannot retire under a live session

**Defect.** `return_sessions.graph_generation_id` pins a session to a generation, but retirement waited only
on ephemeral request-scoped read leases. A return that sleeps for days would resume pinned to a retired N.

**Contract.** Two binding modes, configured per workflow in `config/graph/generation.yaml`:

| Mode | Behaviour | Cost |
|---|---|---|
| `REBIND_ON_RESUME` *(default)* | Session records `generation_id_at_start` for audit and holds **no** durable lease. On every resume it resolves the current active generation into `generation_id_current` and **revalidates any graph-derived facts it cached**. | Revalidation work on resume; a fact that no longer holds surfaces as a stage-level conflict, not silent drift. |
| `PIN_STRICT` | Session holds a `GenerationSessionLease` — durable, no TTL, released only on session completion or explicit rebind. | Generation N cannot retire while the lease is open. |

**Retirement gate.** `DrainController` transitions `DRAINING → RETIRED` only when all three are zero:
ephemeral `GenerationReadLease`, `GenerationWriteReservation`, and durable `GenerationSessionLease`.
The configured drain timeout applies **only to the two ephemeral kinds**. A durable session lease is never
timed out — N stays `DRAINING` and the condition is surfaced as an operational alert listing the blocking
sessions and offering explicit rebind. Forced retirement under a durable lease is not implementable.

**Enforced by** `tests/graph/test_drain_blocks_on_session_lease.py`,
`tests/graph/test_rebind_on_resume_revalidates.py`.

### 13.4 One generation handle per operation

**Defect.** A request could read generation N, activation could flip to N+1, and on-demand sync would then
write to "the active generation" — the rerun executing against a different generation than the original query.

**Contract.**
- A `GenerationHandle` is acquired exactly once, at the outermost boundary of an operation (HTTP request entry,
  workflow activity start, sync run start), and threaded explicitly through every subsequent call:
  `query → miss → strong-anchor guard → targeted source read → projection → write → rerun`.
- `graph/sync/on_demand.py` takes the handle as a **required argument**. No code below the acquisition point
  may call anything that resolves the active generation.
- Every graph write is fenced on `handle.fencing_token`.
- `handle.assert_current()` at each stage boundary. On `GenerationChanged` the operation aborts and restarts
  from the top with a fresh handle, up to a bounded retry count, then fails. It never continues on stale state
  and never silently re-resolves.

**Enforced by** `tests/graph/test_generation_handle_threading.py` (a static check that `on_demand`, the
projector, and the writer have no path to the active-generation resolver), and
`tests/graph/test_on_demand_restarts_on_generation_change.py`.

### 13.5 Interception completion and workflow resume are one write

**Defect.** Persisting `RESPONDED` then resuming the caller loses the resume on a crash between the two —
the AI request is permanently complete and the workflow is stuck forever. The reverse order duplicates
responses.

**Contract.** The resume command is **embedded in the interception document**, so completion is a single
atomic guarded update — no transaction, no second collection, no window:

```
update ai_interceptions
where  interception_id = X and version = V and status = CLAIMED
set    status = RESPONDED,
       completed_at = now, completed_by = operator,
       response_origin = HUMAN_INTERCEPTION,
       resume_command = { command_id: uuid, status: PENDING, workflow_id, run_id,
                          signal_name, result_ref, attempts: 0 },
       version = V + 1
```

- `ai/interception/resume_worker.py` polls `resume_command.status = PENDING` and delivers the signal
  **at least once**, with exponential backoff, marking `DELIVERED` only after the signal is accepted.
- Workflow-side signal handling is **idempotent on `command_id`** — a redelivered resume is a no-op.
- Crash after the update, before delivery: the worker finds the pending command and delivers it. Correct.
- Crash after delivery, before marking `DELIVERED`: redelivered, deduplicated workflow-side. Correct.
- The same embedded-command pattern applies to `REPLAYED` and `CANCELLED`, which also unblock a caller.

**Enforced by** `tests/ai/test_resume_command_atomicity.py`,
`tests/ai/test_resume_redelivery_is_idempotent.py`.

### 13.6 Nothing sensitive is written durably unclassified

**Defect.** `configuration_snapshot` on the interception record, immutable AI replay envelopes, and analyzer
source snapshots could all persist resolved credentials, customer data, or raw source samples. UI redaction
does nothing about data already on disk.

**Contract.** Three rules, by data kind:

**Configuration.** Persist `configuration_release_id` + `configuration_checksum`, never the snapshot.
Route provenance persists as sanitized `route_metadata` — `task_id`, `route_id`, `provider_id`, `model_id`,
`tier`. Never endpoints, headers, keys, or anything Vault resolved.

**AI replay envelopes.** The immutable request envelope is the only thing that can reproduce a request, so it
must persist — but in `ai_request_envelopes`, separate from the trace, **encrypted** via Vault transit
(`platform/secrets/envelope.py`), gated behind a distinct `ai.replay.read` capability, and TTL-expired per
`config/ai/interception.yaml`. Default retention is short. `ai_traces` holds metrics only — no prompt bodies,
no response bodies.

**Analyzer source snapshots.** Metadata (names, types, cardinalities) is always plaintext and always retained.
Samples are governed by the source's sampling policy and recorded as `sample_classification`:

| Classification | Meaning |
|---|---|
| `NONE` | Metadata-only analysis. Samples used transiently in the AI call, never persisted. |
| `REDACTED` | Samples persisted after `platform/secrets/redaction.py`. The default when sampling is enabled. |
| `ENCRYPTED` | Raw samples persisted encrypted in `source_samples` with a mandatory `expires_at`. Requires the source definition to opt in explicitly. |

**Mechanism.** `platform/secrets/redaction.py` is a mandatory pass before any durable write of a payload
derived from a source, a resolved secret, or a customer record (R8). A structure that legitimately needs raw
content is declared `encrypted: true` with a `retention` in `system_store.yaml`, and the store layer refuses a
plaintext write to it.

**Enforced by** `tests/platform/test_redaction_required.py` (static: durable-write call sites reachable from
source/config data must pass through the redactor or an `encrypted` structure), and
`tests/ai/test_no_secrets_in_interception_record.py`.

### 13.7 The bootstrap lease is fenced and heartbeated

**Defect.** A TTL lock with an owner ID is not sufficient. A migration slower than the TTL lets a second
instance acquire the lock and run the same migration concurrently.

**Contract.**
- `FencedLease` = `lease_id` + `owner` + `fencing_token` + `expires_at`, where `fencing_token` comes from a
  monotonic `$inc` on `fencing_tokens` (scope = lock name). Tokens never repeat and always increase.
- A **background heartbeat** renews `expires_at` at a fraction of the TTL for as long as the holder works.
- If a heartbeat renewal fails — expired, or the token was superseded — the holder **aborts immediately** and
  raises. It does not finish the migration it was running.
- Every migration write **and** every version-ledger write is a conditional update guarded on
  `fencing_token == <held token>`. A stale holder's writes are rejected at the store, so even a paused-process
  scenario cannot corrupt state.
- Each migration is either wrapped in a transaction or independently idempotent, so a partial application is
  safely re-runnable.

**Enforced by** `tests/platform/test_lease_heartbeat.py`,
`tests/platform/test_fenced_writes_reject_stale_token.py`,
`tests/platform/test_migration_idempotence.py`.

### 13.8 Exactly one ACTIVE release, atomically

**Defect.** "Exactly one ACTIVE" was asserted but never enforced. Two replicas could activate concurrently and
both succeed.

**Contract.** The replica set required for this already exists (`mongodb-rs-init`).

```
BEGIN TRANSACTION
    current = find configuration_releases where status = ACTIVE
    assert target.status == APPROVED                       else abort 409
    update current  set status = SUPERSEDED, superseded_by = target.release_id
    update target   set status = ACTIVE, activated_at = now
    update configuration_active_pointer
        where _id = "active" and version = V                ← CAS
        set release_id = target.release_id, checksum = …, version = V + 1
COMMIT
```

Plus a **partial unique index** on `configuration_releases` where `status = "ACTIVE"` — a defence-in-depth
constraint that makes two ACTIVE releases unrepresentable even if the transaction logic is later changed.
The loser of a race gets a write conflict and returns `409`, changing nothing.

The graph generation equivalent (`active_runtime_snapshot`) already uses single-document CAS, which is
sufficient there because it is one document; the same partial-unique defence applies to
`graph_generations.status = ACTIVE` scoped per snapshot name.

`pymongo`'s async driver makes `session.start_transaction()` itself a coroutine — it must be
`await`ed to obtain the context manager (`async with await session.start_transaction():`), not entered
directly. A missing `await` type-checks under a permissive mock but raises `TypeError` against the real
driver, so the transaction never actually runs. `configuration/application/activation.py` and
`workflows/persistence.py` both follow the `await`ed form; any new transactional adapter must too.

**Enforced by** `tests/configuration/test_concurrent_activation.py` — real `AsyncMongoClient` against the
replica set (not a hand-rolled session mock, which cannot exercise real transaction rollback/isolation),
asserting: exactly one of two racing activations wins; the pointer's `release_id` and `checksum` match the
actual winner, not just "a" release; the pointer version advances by exactly one; the loser is left
untouched — still `APPROVED`, no `activated_at`, no `superseded_by`; and the previously-active release is
superseded by the winner specifically, never by the loser.

### 13.9 No API bypasses an orchestrated transition

Covered in full at §9.1 (no `advance` endpoint; progress is event- and action-driven) and §9.3 (activation is
generation-scoped, requires `READY_FOR_ACTIVATION`, and executes the orchestrator's own CAS + drain sequence).

**Enforced by** `tests/api/test_no_generic_advance_endpoint.py`,
`tests/graph/test_activation_rejects_non_ready_generation.py`.

### 13.10 Summary of new structures and tests

| Invariant | New structures | New modules |
|---|---|---|
| 13.1 | — | `platform/contracts/`, `platform/capabilities/`, `bootstrap/adapters/` |
| 13.2 | `configuration_active_pointer`, `configuration_adoption` | `configuration/domain/handle.py`, `bootstrap/reconciler.py`, two-phase `ModuleRuntime`, `bootstrap/epoch.py`'s `EpochAdmission` (fenced `begin_swap`, `CURRENT`/`DRAINING`/`RELEASING`/`RELEASED`, accepting-flag under the same lock, `EpochLease` unique-identity holder tracking), `ReconfigurationCoordinator`'s per-instance reconfigure lock |
| 13.3 | `generation_session_leases` | `graph/lifecycle/binding.py`, extended `leases.py` |
| 13.4 | — | `graph/lifecycle/handles.py` |
| 13.5 | `resume_command` (embedded) | `ai/interception/resume_worker.py` |
| 13.6 | `ai_request_envelopes`, `source_samples` | `platform/secrets/redaction.py`, `envelope.py`, `system_store/encryption.py` |
| 13.7 | `fencing_tokens` | `system_store/fencing.py`, rewritten `locking.py` |
| 13.8 | `configuration_active_pointer` | `configuration/application/activation.py` |

---

## 14. Durable reasoning runtime — LangGraph (normative)

LangGraph is the durable reasoning runtime inside **exactly two** components. It is an implementation detail
behind existing contracts, not a platform orchestrator.

### 14.1 Boundary

```
Temporal ─── durable business orchestration, agent sequencing, retries,
   │         business waits, integrations, long-lived return state
   │
   ├── Order Discovery Agent ──── LangGraph reasoning runtime
   ├── Order Analysis Agent            (no LangGraph)
   ├── Return Workflow Agent           (no LangGraph)
   ├── Return Fulfillment Agent        (no LangGraph)
   ├── Bay Assignment Agent            (no LangGraph)
   └── Feedback Learning Agent         (no LangGraph)

Graph Schema Analyzer (independent module) ──── LangGraph reasoning runtime

LangGraph nodes ──► AgentAiPort / SchemaReasoningPort  ──► AI Gateway
                ──► KnowledgePort / GraphTargetPort
                ──► SourceDiscoveryPort
                ──► validation ports
```

**LangGraph does not replace** Temporal, the Return Session Orchestrator, the Agent Registry, the AI Gateway,
the Module Registry, the Graph Generation Lifecycle, the System Store, or the Configuration Control Plane.

**The business-agent count stays at six.** Graph Schema Analyzer remains an independent module with a
reasoning engine; it does not become a seventh workflow agent, and the Return Session Orchestrator continues
to know nothing about it.

**No external consumer may depend on a LangGraph object.** `AgentPlugin.execute()` and the Analyzer's port
signatures are unchanged. No graph, state dict, `CompiledGraph`, or checkpointer appears in any public type.

**Dependencies.** `langgraph` and `langgraph-checkpoint` go into `backend/pyproject.toml`, resolved through
`uv.lock` (D8 — no second dependency manager). Provider integration packages —
`langchain-openai`, `langchain-anthropic`, `langchain-google-genai`, and any equivalent — **must not** be
added; their absence is asserted at the dependency level, not just in code (§14.12).

### 14.2 Checkpoint persistence

Production reasoning uses a persistent checkpointer. `InMemorySaver` / `MemorySaver` are **forbidden** outside
unit tests.

`platform/reasoning/checkpoint.py` implements `SystemStoreCheckpointSaver` against LangGraph's
`BaseCheckpointSaver` interface, resolving storage as:

```
SystemStore → logical structure → configured physical collection
```

The library never chooses a collection name. If the chosen LangGraph checkpoint implementation is adapted
rather than written from scratch, its collection names are still supplied from the manifest — an
implementation that cannot be told its collection names is not acceptable (R4).

Both structures are `encrypted: true` (§8). If a future checkpoint implementation needs additional
persistence, it is declared in the same manifest — no library-created undocumented collections.

**Retention is keyed to terminal state, never to creation time.** A fixed TTL from creation would delete the
checkpoints of a run that is still resumable — an Analyzer clarification or a paused return can legitimately
stay open longer than any retention window, and losing its checkpoints makes the thread unresumable. That
would defeat the entire reason for durable reasoning.

```
reasoning_run.lifecycle_state
    RUNNING | INTERRUPTED | WAITING          → expires_at = null      (never expires)
    COMPLETED | FAILED | CANCELLED | ABANDONED
                                             → expires_at = terminal_at + terminal_retention
```

A Mongo TTL index ignores documents whose `expires_at` is null or absent, so "no expiry while live" needs no
special-casing. On the terminal transition, `retention.py` stamps `expires_at` across **all three** of the
run's structures together — checkpoints, checkpoint writes, and action receipts. A receipt must never expire
before the execution it protects can still resume, so the three always share one expiry.

**Abandonment sweeper.** "Live runs never expire" would otherwise grow without bound when a user simply never
answers a clarification. A sweeper transitions idle `INTERRUPTED` / `WAITING` runs to `ABANDONED`, which
starts the retention clock. `abandon_after_hours` is generous by default (30 days) and is a business decision,
not a storage one — abandonment is audited and surfaced in the AI Control Center, never a silent deletion.

**Abandonment must not race pending external work.** A run in `PENDING_EXTERNAL` is waiting on something that
can still complete — an open interception, an in-flight sync. Abandoning it on an idle timer would let an
operator answer an interception on day 31 and have the resume worker deliver a command to a reasoning
execution that is already abandoned and heading for deletion, leaving the Temporal workflow stuck or
producing a late invalid resume.

**Precondition — the sweeper skips a run unless all of these hold:**
```
no unresolved clarification interrupt
no receipt in STARTED or PENDING_EXTERNAL
no open AI interception referencing this run
no resume_command in PENDING
no active Temporal wait bound to this reasoning run
```
A run that is idle past the threshold but fails a precondition is **not** abandoned. It is flagged
`ABANDONMENT_BLOCKED` with the blocking reference listed, and surfaced for operator action. Unbounded growth
becomes a visible operational queue rather than a silent correctness hazard.

**Forced abandonment: one Mongo transaction, then a durable signal.** An operator may abandon a blocked run
explicitly. **The Temporal signal cannot join a Mongo transaction**, so claiming one atomic step across both
would be false — a crash after commit but before the signal leaves the workflow waiting forever. Use the same
outbox discipline as §13.5:

```
── Mongo transaction ─────────────────────────────────────────────
   run.lifecycle_state                  → ABANDONED
   open interceptions                   → CANCELLED
   STARTED / PENDING_EXTERNAL receipts  → FAILED_FINAL (REASONING_ABANDONED)
   expires_at                           → stamped on checkpoints, writes, receipts
   reasoning_resume_commands            → INSERT { command_id, status: PENDING,
                                                   workflow_id, run_id,
                                                   signal: REASONING_ABANDONED }
── COMMIT ────────────────────────────────────────────────────────
                    │
                    ▼
        resume worker polls PENDING
                    │
                    ▼
        Temporal.signal(..., command_id)      ← at least once
                    │
                    ▼
        command → DELIVERED
```

Crash after commit, before signal: the worker finds the pending command and delivers it. Crash after signal,
before marking `DELIVERED`: redelivered, and the workflow deduplicates on `command_id`. Workflow-side handling
of `REASONING_ABANDONED` is idempotent, exactly as for the interception resume path.

`reasoning_resume_commands` is a distinct logical structure because an abandonment command belongs to the
reasoning run, not to any one interception — and the abandonment transaction already spans several documents,
so there is no single-document atomicity to preserve. (The interception path in §13.5 keeps its *embedded*
command precisely because that one **is** a single-document write.)

The workflow decides what an abandoned reasoning run means for the business session — it is never left waiting
on a signal that can no longer arrive.

**Late external completion is rejected, never resumed.** Every resume path re-reads `lifecycle_state` first.
A completion arriving for an `ABANDONED` run is refused, audited, and reported to the operator who submitted
it — it never reanimates the thread. This is the same guard that prevents a superseded `GenerationChanged`
attempt from being resumed.

```yaml
reasoning:
  checkpoint_retention:
    active_runs_expire: false          # not configurable to true in production
    terminal_retention_hours: 168
    abandon_after_hours: 720
```

**Checkpoints are never the authoritative business record.** Canonical state stays in `ReturnSession`,
`Conversation`, `AnalysisSession`, `GraphSchemaDraft`, and `ConfigurationRelease`. A checkpoint is
reconstructible reasoning position; losing one loses progress, never truth.

**Thread IDs** are bounded and deterministic, from `ReasoningThreadIdFactory` — never user-supplied strings.

**A reasoning thread is one reasoning attempt, not one conversation.** Reusing a single
`order-discovery:<conversation_id>` thread across every turn would carry `final_result`, `candidate_refs`,
`candidate_scores`, `query_budget`, `search_plan`, and a stale `clarification` from the previous turn into the
next one. Correctness would then depend on every state field being perfectly reset by a reducer — a hidden
invariant nobody can verify. Separate the three concepts:

| Concept | Lifetime | Home |
|---|---|---|
| `conversation_id` | the whole business conversation | canonical `conversations` in SystemStore |
| `turn_id` | one associate/customer business turn | allocated when the turn is recorded in the conversation |
| `reasoning_run_id` | one reasoning attempt for one turn | `<conversation_id>:<turn_id>:<attempt>` |

```
Order Discovery      thread_id = order-discovery:<conversation_id>:<turn_id>:<attempt>
Graph Schema Analyzer thread_id = graph-schema:<analysis_id>
```

| Event | Thread |
|---|---|
| new business turn | **new** — new `turn_id`, `attempt = 1` |
| clarification answer to an open interrupt | **same** |
| Temporal activity retry | **same** |
| AI interception resume | **same** |
| backend restart | **same** |
| `GenerationChanged` restart | **new** — same `turn_id`, `attempt + 1`; the superseded attempt is abandoned per §14.2 |

**Distinguishing a clarification answer from a new turn is explicit, not inferred from message shape.** If the
session has an outstanding interrupt on its current thread, the incoming input is routed as a resume for that
thread. Otherwise it starts a new turn. `turn_id` is allocated by the canonical conversation write — not
generated inside the agent — so it is stable across Temporal retries.

Conversation memory continues to come from canonical SystemStore conversation state, never from a previous
turn's LangGraph working state. This is what keeps durability from quietly turning reasoning scratch space
into business memory.

**The Analyzer is deliberately not per-turn.** An `AnalysisSession` *is* the unit of work — long-lived,
iterative, and with every state field analysis-scoped by design. `graph-schema:<analysis_id>` is correct and
should not be "fixed" symmetrically.

### 14.3 Order Discovery reasoning graph

`OrderDiscoveryAgentPlugin.execute()` is a thin façade: resolve ports, acquire the `GenerationHandle`, start
or resume the graph on the conversation's thread, map the terminal state to `AgentResult`. The former
`DynamicOrderAgentCoordinator` decomposes into nodes; it does not survive as a coordinator.

```
START → LOAD_CONTEXT → UNDERSTAND_REQUEST → PLAN_SEARCH → QUERY_GRAPH → EVALUATE_RESULTS
                                                              ▲               │
  ┌───────────────────────────────────────────────────────────┤               │
  │                                                           │        ┌──────┴───────────────────────┐
  │                                                           │        │                              │
  │  needs_more_search → PLAN_NEXT_QUERY ─────────────────────┤   sufficient                   graph_miss
  │  needs_aggregation → AGGREGATE → EVALUATE_RESULTS         │        │                              │
  │  graph_miss → CHECK_STRONG_ANCHOR → TARGETED_SYNC ────────┘        ▼                              ▼
  │  clarification_required → INTERRUPT_FOR_CLARIFICATION      RANK_CANDIDATES            CHECK_STRONG_ANCHOR
  │                              → RESUME → UNDERSTAND_REQUEST        ▼                              ▼
  └───────────────────────────────────────────────────────────  VERIFY_RESULT → RESPOND      TARGETED_SYNC
```

**State is typed, bounded, and reference-based** — `reasoning_run_id`, `conversation_id`, `session_id`,
`configuration_release_id`, `graph_generation_id`, `user_turn_ref`, `intent`, `strong_anchors`, `search_plan`,
`query_attempt`, `query_budget`, `query_execution_refs`, `candidate_refs`, `candidate_scores`,
`clarification`, `targeted_sync_requested`, `final_result`, `failure`. **No source records in state.**

**Durability.** Failure after `PLAN_SEARCH`, `QUERY_GRAPH`, `EVALUATE_RESULTS`, or `TARGETED_SYNC` resumes
from a safe checkpoint. A Temporal activity retry **reuses the same thread ID** — it never opens a new
reasoning thread for the same failed invocation.

### 14.4 Analyzer reasoning graph

```
START → LOAD_SOURCE_SNAPSHOT → ANALYZE_STRUCTURE → IDENTIFY_GAPS
                                      ▲                  │
                                      │      ┌───────────┴────────────┐
                                      │  clarification_required   sufficient_context
                                      │      │                        │
                                      │  INTERRUPT_FOR_CLARIFICATION  ▼
                                      └──── → RESUME            PROPOSE_SCHEMA
                                                                      ▼
                                                              VALIDATE_PROPOSAL ◄──────┐
                                                                      │                │
                                              ┌───────────────────────┴──────┐         │
                                       validation_failure                 valid        │
                                              ▼                              ▼         │
                                    REASON_ABOUT_FAILURES              USER_REVIEW      │
                                              ▼                       │         │      │
                                       REVISE_PROPOSAL ───────────────┘    modification │
                                                                       accept   → APPLY_TYPED_MUTATION
                                                                          ▼              └──────┘
                                                                  READY_FOR_APPROVAL
```

**State:** `analysis_id`, `configuration_release_id`, `source_snapshot_id`, `source_schema_hash`,
`requirements`, `clarification_count`, `draft_id`, `revision_id`, `validation_result_id`,
`validation_attempt`, `reasoning_notes`, `next_action`, `completion_status`. **No raw source samples** — they
stay governed by the `NONE` / `REDACTED` / `ENCRYPTED` classification (§13.6).

**Reasoning stops at `READY_FOR_APPROVAL`.** It must never perform graph generation activation,
`ActiveRuntimeSnapshot` CAS, draining, retirement, configuration activation, or any source DDL/DML. Those stay
with `ApprovalService`, `BuildService`, `RebuildTrigger`, `GenerationLifecycleOrchestrator`, and
`DrainController`.

```
LangGraph      = think / inspect / clarify / propose / revise / validate
Graph lifecycle = build / fence / activate / drain / retire
```

**Interrupt payloads carry references and sanitized values only** — `analysis_id`, `draft_id`, `revision_id`,
the question, and allowed structured choices. Never raw source samples.

### 14.5 Checkpoint content is allowlisted

Checkpoint state must never contain Vault secrets, database passwords, API keys, credential-bearing connection
strings, raw configuration snapshots, raw unredacted source documents, large customer records, or provider
authentication headers.

Prefer references: `configuration_release_id`, `graph_generation_id`, `source_snapshot_id`, `evidence_ref`,
`query_execution_id`, `candidate_id`, `schema_revision_id`.

`platform/reasoning/redaction.py` enforces this on the write path — a state key not on the component's
declared allowlist is rejected, not silently redacted, so the violation surfaces in development rather than
becoming a quiet data-shape change. Storage is encrypted and retention-controlled regardless (§14.2).

### 14.6 Node idempotency — mandatory

**LangGraph re-executes an interrupted node from its beginning on resume.** Any side effect performed before
the `interrupt()` call runs again. Every side-effecting tool is therefore idempotent under the key:

```
reasoning_run_id + node_name + logical_action_id
```

`platform/reasoning/receipts.py` persists these in `reasoning_action_receipts`. Required for at minimum: AI
Gateway invocation, targeted sync requests, conversation updates, checkpoint-adjacent persistence, and audit
events. A resumed node must never produce a second targeted sync.

**A receipt is a state machine, not a cached value.** A naive "record the result, return it on a hit" design
livelocks the interception path: an intercepted AI call returns `InterceptionPending`, that gets cached as the
action's result, and every resume then replays the same pending marker and interrupts again — forever, even
after the operator has answered.

```
STARTED ──────────► COMPLETED          (normal success)
   │        ├─────► PENDING_EXTERNAL   (suspended awaiting something outside this run)
   │        ├─────► FAILED_RETRYABLE   (transient; the node may re-attempt)
   │        └─────► FAILED_FINAL       (terminal; the node maps it to a typed outcome)
   │
PENDING_EXTERNAL ─► COMPLETED | FAILED_FINAL   (resolved via external_ref)
```

**`PENDING_EXTERNAL` is never terminal and never returned as a result.** It records `external_ref` — the
`interception_id` for an intercepted AI call, the `sync_run_id` for a targeted sync — and resolution on resume
goes through that reference:

```
resumed node → receipt lookup
   ├─ none                → perform the action; write STARTED first
   ├─ STARTED             → resolve by external_ref against the target system (never blind re-execute);
   │                        unresolvable + target not idempotent → FAILED_RETRYABLE, operator-visible
   ├─ PENDING_EXTERNAL    → resolve external_ref
   │                          ├─ still pending  → interrupt again (no new side effect)
   │                          └─ completed      → fetch the validated result,
   │                                              receipt = COMPLETED, continue
   ├─ COMPLETED           → return result_ref, no side effect
   ├─ FAILED_RETRYABLE    → re-attempt under a new attempt number
   └─ FAILED_FINAL        → raise the typed outcome
```

`STARTED` is written **before** the action, with the deterministic external key, so a crash mid-action is
resolvable rather than ambiguous. This also gives AI replay and manual response one deterministic relationship
back to the originating reasoning action: `interception_id → external_ref → (reasoning_run_id, node_name,
logical_action_id)`.

### 14.7 Bounded autonomy

Budgets are validated configuration. No unbounded cycle is representable.

```yaml
# agents/order_discovery.yaml
reasoning:
  engine: LANGGRAPH
  max_steps: 20
  max_graph_queries: 8
  max_targeted_syncs: 1
  max_clarifications: 3
  max_replans: 5

# modules/graph_schema_analyzer.yaml
reasoning:
  engine: LANGGRAPH
  max_steps: 40
  max_clarifications: 10
  max_validation_revisions: 8
  max_source_tool_calls: 20
  max_ai_calls: 15
```

Exhausting a budget in Order Discovery raises `ReasoningLimitExceeded`; in the Analyzer it terminates at
`NEEDS_HUMAN_REVIEW`. Neither continues indefinitely, and neither fabricates a result.

### 14.8 Two suspension mechanisms, one protocol

**This is the integration point between §13.5 and LangGraph, and it is load-bearing.** A reasoning node can
suspend for two different reasons, and both must release the Temporal worker rather than block it.

| Cause | Mechanism |
|---|---|
| Human clarification needed | LangGraph `interrupt()` |
| AI call intercepted for human response | AI Gateway raises `InterceptionPending` |

Both follow the same shape:

```
Temporal activity
      ↓
reasoning graph runs
      ↓
interrupt()  ─or─  AgentAiPort raises InterceptionPending
      ↓
checkpoint persisted (thread position saved)
      ↓
plugin returns  CLARIFICATION_REQUIRED  or  AI_INTERCEPTION_PENDING
      ↓
activity COMPLETES — the worker is released, nothing blocks
      ↓
workflow records a waiting state
      ↓
   ┌──────────────────────────┴───────────────────────────┐
   │ clarification: UI shows question, user replies       │
   │ interception: operator responds; the resume_command  │
   │   embedded in the interception document (§13.5) is   │
   │   delivered at-least-once as a workflow signal       │
   └──────────────────────────┬───────────────────────────┘
      ↓
new activity, SAME thread_id
      ↓
graph resumes from the checkpoint — node re-executes from its start (§14.6)
```

An `InterceptionPending` raised inside a node must be mapped to a graph interrupt, not swallowed or retried
in place. **No Temporal activity ever blocks waiting for a human**, through either path.

**The interception path requires `PENDING_EXTERNAL` (§14.6).** When the node re-executes on resume it re-runs
the AI call site. Without a receipt state that resolves through `interception_id`, the node would either
issue a duplicate AI request or replay a cached `InterceptionPending` and interrupt forever. The receipt is
what closes the loop:

```
first pass   : AI call → InterceptionPending → receipt = PENDING_EXTERNAL(external_ref=interception_id)
                                             → interrupt
resume, still pending : receipt → interception still open → interrupt again, no new request
resume, answered      : receipt → validated manual response → receipt = COMPLETED → node continues
```

### 14.9 Typed error semantics

Both graphs map internal exceptions to typed outcomes and never fabricate a business answer:

```
AIUnavailable          KnowledgeUnavailable      GenerationChanged
SourceUnavailable      CheckpointFailure         ReasoningLimitExceeded
InvalidModelOutput     SafetyRejected            HumanInputRequired
InterceptionPending
```

Order Discovery's existing rule holds: if required LLM reasoning fails and no configured AI route succeeds,
return an explicit AI failure — never deterministic fake business reasoning.

**`GenerationChanged` handling (§13.4 composition).** The `GenerationHandle` is acquired *before* the graph is
invoked and threaded into every node that touches graph knowledge; LangGraph never resolves the current
generation itself. On `GenerationChanged`:

```
abort the current reasoning branch
release the handle
acquire a fresh handle
attempt + 1  →  new reasoning_run_id  →  NEW thread (§14.2)
    ← a fresh checkpoint namespace, so no generation-derived state can survive,
      and action receipts (keyed on reasoning_run_id) do not suppress legitimate re-execution
abandon the superseded attempt, revoking its outstanding external work (§14.2 abandonment rules)
re-enter at LOAD_CONTEXT; conversation_id and turn_id are unchanged, so business
continuity is preserved through canonical conversation state, not through checkpoint reuse
```

Reasoning never continues across generations, and never resumes a superseded attempt.

### 14.10 Observability

Every reasoning execution emits into existing platform observability — `reasoning_run_id`, `thread_id`,
`session_id`/`analysis_id`, `agent_id`/`module_id`, `node_name`, `configuration_release_id`,
`graph_generation_id`, AI trace IDs, graph query execution IDs, `started_at`, `completed_at`, `duration`,
`outcome`.

**LangSmith is not a production dependency.** The AI Control Center and platform observability remain
authoritative. LangSmith may be added later as optional, separately configured development diagnostics only.

**AI Control Center surfaces** reasoning runs inside existing request/module views:

| Order Discovery | Graph Schema Analyzer |
|---|---|
| reasoning run, current/last node, step count, graph queries, AI calls, clarifications, targeted syncs, final status | reasoning run, current/last node, clarifications, proposal revisions, validation iterations, AI calls, final status |

**Never expose hidden chain-of-thought.** Display node and action names, tool activity, structured decisions,
validation results, and trace IDs — not model private reasoning text.

### 14.11 Configuration

`config/reasoning.yaml` holds runtime settings (§4). Per-component opt-in is explicit:

```yaml
agents:
  order_discovery:
    reasoning_engine: LANGGRAPH

graph_schema_analyzer:
  reasoning_engine: LANGGRAPH
```

No other agent or module uses LangGraph without a separately reviewed configuration and implementation change.

### 14.12 Architecture tests

| Rule | Test |
|---|---|
| Checkpointer resolves through SystemStore, never a raw collection | `tests/reasoning/test_checkpoint_uses_system_store.py` |
| Reasoning state survives process restart | `tests/reasoning/test_checkpoint_survives_restart.py` |
| A live run's checkpoints never acquire an expiry | `tests/reasoning/test_active_run_checkpoints_never_expire.py` |
| `PENDING_EXTERNAL` resolves instead of livelocking | `tests/reasoning/test_pending_external_receipt_resolves.py` |
| The sweeper never abandons a run with pending external work | `tests/reasoning/test_abandonment_blocked_by_pending_external.py` |
| Forced abandonment revokes interceptions and signals the workflow | `tests/reasoning/test_forced_abandonment_is_atomic.py` |
| A completion arriving for an abandoned run is rejected | `tests/reasoning/test_late_completion_after_abandonment_rejected.py` |
| A new business turn starts a clean reasoning thread | `tests/agents/test_new_turn_does_not_reuse_previous_reasoning_state.py` |
| A clarification answer resumes the same run | `tests/agents/test_clarification_resume_reuses_same_reasoning_run.py` |
| No secrets or raw records in checkpoint state | `tests/reasoning/test_checkpoint_contains_no_secrets.py` |
| Every loop is bounded by configuration | `tests/reasoning/test_bounded_reasoning.py` |
| No node constructs a provider client or chat model | `tests/reasoning/test_nodes_do_not_construct_ai_providers.py` |
| No provider integration package is a dependency | `tests/reasoning/test_no_langchain_provider_packages.py` |
| No LangGraph type appears in a public signature | `tests/reasoning/test_langgraph_not_in_public_api.py` |
| Reasoning graphs never invoke another agent | `tests/reasoning/test_no_cross_agent_subgraphs.py` |
| Order Discovery resumes on the same thread | `tests/agents/test_order_discovery_reasoning_resume.py` |
| Clarification interrupt persists and resumes | `tests/agents/test_order_discovery_clarification_interrupt.py` |
| Resumed node does not re-issue targeted sync | `tests/agents/test_order_discovery_targeted_sync_idempotency.py` |
| Generation change restarts reasoning cleanly | `tests/agents/test_order_discovery_generation_change_restart.py` |
| Analyzer resumes on the same thread | `tests/graph_schema_analyzer/test_reasoning_resume.py` |
| Analyzer clarification interrupt persists | `tests/graph_schema_analyzer/test_clarification_interrupt.py` |
| Validation/revision loop terminates | `tests/graph_schema_analyzer/test_validation_revision_loop.py` |
| Reasoning cannot activate a generation | `tests/graph_schema_analyzer/test_reasoning_cannot_activate_generation.py` |
| Reasoning cannot mutate a source | `tests/graph_schema_analyzer/test_reasoning_cannot_mutate_source.py` |

---

## 15. Definition of structurally done

- Every directory in §2–§6 exists with its README where marked ★.
- Every contract in §7 is implemented and imported by at least one consumer.
- Every structure in §8 is declared in `system_store.yaml` and created by bootstrap.
- Every route in §9 exists in the generated OpenAPI, and no route outside §9 does.
- Every state machine in §10 has a test asserting its illegal transitions fail.
- All architecture tests in §11 pass.
- **Every invariant in §13 has its named test present and passing.**
- **Every check in §14.12 passes**, and LangGraph appears in no public type signature.
- Every row in §12 is resolved — moved, deleted, or explicitly dispositioned in a commit message.
- One package manager, one lockfile, consumed identically by Docker, host scripts, and CI (§5.2a).
- **No `platform.*` module names a type owned by `configuration`, `graph`, `agents`, `business`, `ai`, or
  `graph_schema_analyzer`** — cross-boundary handles go through `platform/contracts/` (§7.1).
- **No module has an `adapters/` package.** Cross-module binding exists only in `bootstrap/adapters/`.
- Every `ModuleRuntime` implements `prepare_reconfigure` / `commit_reconfigure` / `abort_reconfigure`, with
  all fallible work in `prepare` (§13.2).
