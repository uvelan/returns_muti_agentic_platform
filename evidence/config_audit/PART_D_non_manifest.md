# Part D — Configuration NOT loaded through manifest.yaml

Scope: files under `backend/config/` that are not manifest modules and not the two
Part-B singleton compatibility files (`ai_gateway.yaml`, `returns/production.yaml`),
plus `backend/assets.yaml`, seed/support-configuration Python modules, and frontend
hardcoded operational constants. ID prefix `CFG-D-`. Confirmed via `backend/config/manifest.yaml`
(read in full) that its `modules:` map covers only: `agent.*` (agents/*.yaml),
`policy.*` (policies/*.yaml), `workflow.return_session`, `sync.order_partial`,
`sync.order_full`, `source.sales_inv`, `mapping.sales_inv_order`,
`graph.order_discovery`, `platform.system_store` — so `graph/order_discovery.yaml`
IS a manifest module (Part B), confirmed out of Part D scope.

Legend: FILE_ONLY = loaded from disk, no DB copy. RELEASE = merged into the Neo4j
`ACTIVE_RELEASE` configuration graph (Part B's precedence chain). DEAD = no runtime
caller found via `grep -r` across `backend/src` (and `backend/tests` where checked).

---

## 1. `dynamic_knowledge/active-schema.return-order.yaml` + `active-schema.example.yaml` + `internal_manifests/*.yaml`

### Loader / persistence chain (verified)

- `backend/src/return_platform/configuration/settings.py:70` — `dynamic_knowledge_schema_path`, default
  `config/dynamic_knowledge/active-schema.return-order.yaml` (settings.py:18-19).
- `backend/src/return_platform/dynamic_knowledge/config_loader.py:28-42` `load_active_schema()` — reads the YAML,
  recomputes `sha256_digest` over the document minus `configuration_checksum` and rejects a mismatch
  (`ConfigurationIntegrityError`).
- `backend/src/return_platform/dynamic_knowledge/config_loader.py:51-80` `resolve_active_schema()` — the actual
  runtime read path: prefers `releases.active()` (Mongo), falls back to the file if no release is published, and
  falls back to the file (with `logger.exception`) if the Mongo read raises. **Both origins produce the same
  `ActiveSchema` pydantic model; there is no merge.**
- `backend/src/return_platform/dynamic_knowledge/release_store.py:55-57` — Mongo collections in database
  `settings.mongo_database` (default `return_platform`, the OWNED platform Mongo):
  `graph_schema_releases` (immutable, unique index on `configurationReleaseId`), `graph_schema_active_release`
  (single pointer doc, `_id: "active"`), `graph_schema_migration_plans`.
- `backend/src/return_platform/dynamic_knowledge/release_seed.py:63-103` `seed_release_from_file()` — called once
  at startup, `main.py:902-919`. Publishes the file's content as release id
  `f"{base_id}-{checksum[:12]}"` and activates it **only if no pointer document exists yet**
  (`release_store.py` `active()` returns `None`). If a pointer already exists (whether it's the platform's own
  seeded release from an earlier boot or an operator-activated one), seeding is a no-op — confirmed by
  `release_seed.py:76-90` (`SeedOutcome("ALREADY_ACTIVE"...)` / `SeedOutcome("OPERATOR_RELEASE_ACTIVE"...)`).
  **A newer YAML on disk does NOT overwrite an operator-activated release** — this is a documented, deliberate
  design (release_seed.py:16-19), not a gap.
- `backend/scripts/reseed_schema_release.py` — the manual override: `--check` reports drift between file and
  active release; `--apply` publishes+activates the file's current content over whatever is active. Never run
  automatically.
- **Absent collection/document at startup**: `release_store.py:210-231` `active()` returns `None` if the pointer
  document is missing (first-ever boot — ordinary), and logs `active_release_missing` + returns `None` if the
  pointer names a release that is not in `graph_schema_releases` (pointer integrity break — refuses to guess,
  falls back to file via `config_loader.py:74-75`).
- **Release/versioning model**: releases are immutable (insert-only, `DuplicateKeyError` → `ReleaseAlreadyPublished`
  on republish of the same id, `release_store.py:97-118`); activation is a separate pointer write
  (`release_store.py:132-171`) that also records a `MigrationPlan` (graph rebuild vs. incremental) via
  `dynamic_knowledge/release_migration.py`. No draft/reseal state machine at this layer — publish is
  publish-once; there is a separate PUT `/api/schema-releases/active/document` (Part C's API surface,
  `api/schema_releases.py`) for editing.
- **What the runtime actually reads**: Mongo-published active release when one exists, else the file. Every
  order-agent turn and sync path reads `ActiveSchema` through `resolve_active_schema`
  (consumers: `dynamic_knowledge/integration/targeted_sync.py`, `dynamic_knowledge/order_agent/*`,
  `data_platform/graph/sync_service.py`).

### Top-level section inventory (structural vs business)

| ID | Section | Kind | Purpose | Business? |
|---|---|---|---|---|
| CFG-D-001 | `configuration_release_id`/`checksum`/`release_status`/`approved_by`/`approved_at`/`schema_version`/`policy_version`/`prompt_version`/`compiler_version`/`runtime_mode` | Metadata | Release identity/versioning pins (`schema.py:758-767`) | Operational |
| CFG-D-002 | `sources` (dict of `SourceAssetDefinition`) | Structural | Physical connector bindings (Mongo/SQL collection/table refs) | Structural |
| CFG-D-003 | `entities.*.fields.*.capabilities` (`FieldCapabilities`: searchable/filterable/distinct/aggregatable/displayable/on_demand_sync_anchor/operators/aggregations) | **Business rule** | What the order-discovery agent is allowed to do with a field — this is the brief's "capabilities" subsection (`schema.py:159-171`) | **Business** |
| CFG-D-004 | `entities.*.fields.*.permissions` (`FieldPermissions`: searchable_by/displayable_by/on_demand_sync_by/masking) | **Business rule** | Role-gated field visibility | **Business** |
| CFG-D-005 | `entities.*.fields.*.selectivity.identifier_likelihood` (`IdentifierLikelihood` enum: DECLARED_UNIQUE/LIKELY/POSSIBLE/UNLIKELY/UNKNOWN) | **Business rule** | This IS the brief's "identification signals" concept — how strongly a field identifies one record; drives ranking in `order_agent/identification.py`, `order_agent/planner.py` (`schema.py:185-260`) | **Business** |
| CFG-D-006 | `entities.*.strong_anchors` (`StrongAnchorDefinition`: fields/allowed_operators/minimum_fields_present/maximum_expected_matches/on_demand_sync_allowed) | **Business rule** | This IS the brief's "search strategy" concept — configuration-owned selective source lookup contract; consumed by `order_agent/identification.py`, `order_agent/planner.py` (`schema.py:495-517`) | **Business** |
| CFG-D-007 | `entities.*.{source_contract_status,source_access,deletion_policy,explode,where,distinct,key_resolution,ownership_policy}` | Structural | Extraction/exploding/keying mechanics | Structural |
| CFG-D-008 | `graph` (database/nodes/relationships/constraints/indexes) | Structural | Neo4j projection shape | Structural |
| CFG-D-009 | `agent_policies.*` (`AgentPolicy`: allowed_business_capabilities/allowed_roles/allowed_entity_ids/standard_model_refs/max_reasoning_steps/max_graph_queries_per_turn/max_correction_attempts/max_clarifications/max_replans/max_targeted_syncs_per_turn/generation_binding) | **Business rule** | Per-agent safety/capability ceiling (`schema.py:725-750`) | **Business** |
| CFG-D-010 | `internal_manifests/{mongodb,mssql,neo4j,postgresql}.yaml` (603 lines each) | Orphaned schema | Shape matches `InternalSchemaManifest` in `dynamic_knowledge/internal_store/contracts.py:37-42` exactly (manifest_version/connector_type/objects), consumed in principle by `InternalStoreBootstrapper.bootstrap()` (`internal_store/bootstrap.py:21`) | N/A — DEAD |

**Note on the brief's terms**: `grep`-ed `known_values`, `colour`/`color`, `narrow_with`, `searches_only_with`
across both schema YAMLs and `schema.py` — **no matches**. These terms do not exist in the current schema version;
the brief's author appears to be recalling an earlier/different shape. The closest real equivalents are
`identifier_likelihood` (identification signals) and `strong_anchors` (search strategy), documented above as
CFG-D-005/006. Marked **UNVERIFIED / LIKELY STALE BRIEF ASSUMPTION**.

**CFG-D-010 defect**: `internal_manifests/mongodb.yaml`, `mssql.yaml`, `neo4j.yaml`, `postgresql.yaml` are never
read by any loader — `grep -rn "internal_manifests"` across `backend/src` returns zero hits, and
`InternalSchemaManifest(...)` / `InternalStoreManifest(...)` is never constructed anywhere in `backend/src` or
`backend/tests`. `InternalStoreBootstrapper` itself (`internal_store/bootstrap.py`) is exercised only by
`backend/tests/dynamic_knowledge/test_internal_bootstrap.py`, which is not proven here to build its manifest from
these files (no grep hit ties the test to the YAML). Four 603-line files, fully dead.

---

## 2. `schema_registry.yaml`, `data_assets.yaml`, `live_validation/data_assets.sampling.yaml`

| ID | File | Loader | DB copy | Live? |
|---|---|---|---|---|
| CFG-D-011 | `schema_registry.yaml` (3965 lines) | `settings.py:63` `schema_registry_path` → `data_platform/schema_registry.py:170` `load_schema_registry()` (`@lru_cache`, called once at `main.py:469`) | FILE_ONLY, no DB copy found | **Live** — `RuntimeResources.schema_registry` fans out to 19 files: Graph Analyzer (`graph_analyzer/api.py`, `service.py`), operational/seed generation (`data_platform/operational_generation/{generator,service,planner,validator,relationships,policy_resolver,guard}.py`, AI Studio's write-policy gate), `source_connectors/contracts.py`, `configuration/api/sources.py`, `api/seed.py` |
| CFG-D-012 | `data_assets.yaml` (15 lines, 1 asset: `source.sales-orders`) | `settings.py:62` `catalog_path` → `data_governance/catalog_loader.py:288` `load_asset_catalog()`, called once `main.py:468` | FILE_ONLY, no DB copy | **Live** — `RuntimeResources.catalog` feeds `configuration/api/audit.py` (governance audit surface) |
| CFG-D-013 | `live_validation/data_assets.sampling.yaml` | **None found.** File's own header comment says it is "never loaded by the production FastAPI lifespan" and is meant to be loaded by "the live sampling validator" after creating sandbox fixtures | N/A | **DEAD in this checkout** — `grep -rn "live_validation\|governance_sampling_fixture\|data_assets.sampling"` across `backend` (src + config) returns **zero** hits outside the file itself. Whatever "live sampling validator" the comment refers to does not exist in this repository, or was removed without removing the fixture file. |

Both `schema_registry.yaml` and `data_assets.yaml` govern data-generation/write policy (`write_policy`,
`generated_data_policy`, `pii_policy`, `rollback_policy`, `graph_sync_policy`, `sampling`) — this is genuinely
business/operational configuration (which stores may be written to, by what mechanism, with what PII handling),
version-controlled and re-read via `@lru_cache(maxsize=8)` per unique path (not per-process singleton — a second
distinct path gets its own cache slot, harmless since only one path is ever configured per process).

**Inventory — schema_registry.yaml top-level / business subsections (not enumerating all 3965 lines' assets):**

| ID | Section | Business? | Notes |
|---|---|---|---|
| CFG-D-011a | `assets[].write_policy` (DENIED/DOMAIN_API_ONLY/SOURCE_ADMIN_WRITER/DIRECT_OPERATIONAL_INSERT/DERIVED_PROJECTION) | **Business** | Gates whether generated/seeded data may write to an asset at all |
| CFG-D-011b | `assets[].pii_policy` (STRICT_SYNTHETIC/NONE) | **Business** | Governance/compliance rule |
| CFG-D-011c | `assets[].collision_policy`, `rollback_policy`, `graph_sync_policy` | **Business** | Seed/generation behavior on conflict and rollback |
| CFG-D-011d | `graph.nodes[]`/`graph.relationships[]` | Structural | Neo4j label/property/type mapping for the schema-registry's own (separate) graph model — distinct from the dynamic-knowledge `graph` section in item 1 |

---

## 3. `dependency_simulation.yaml`

| ID | Field | Purpose | Business? |
|---|---|---|---|
| CFG-D-016 | `enabled` | Master on/off for the dependency simulator | Business |
| CFG-D-017 | `defaultScenario` (SUCCESS) | Default simulated-outcome scenario | Business |
| CFG-D-018 | `ai.*` (enabled/taskId/providerOrder/timeoutSeconds/maxOutputTokens/temperature/fallbackAlwaysEnabled/pricingMicrousdPerMillionTokens) | AI-narrated simulation text generation | Operational |
| CFG-D-019 | `dependencies.{OMC,PARCEL,FREIGHT,LSI}.operations[]` | Which simulated operations each external dependency exposes | Business |
| CFG-D-020 | `dependencies.{PARCEL,FREIGHT}.statusSequence[]` | Simulated status progression order | Business |

**Loader / DB status — corrects the brief's premise.** `settings.py:65-67`
`dependency_simulation_configuration_path` (default `config/dependency_simulation.yaml`) is loaded by
`backend/src/return_platform/dependency_simulation/configuration.py:76` `load_dependency_simulation_configuration()`,
called at **three** sites: `main.py:473-475`, `configuration/runtime_loader.py:71-73`, and
`configuration/cli/bootstrap_graph_configuration.py:257`. Critically, `runtime_loader.py:88-97` passes it into
`ConfigurationSnapshotBuilder(repository).build_snapshot(..., default_dependency_simulation_configuration=...)` —
**the exact same `ConfigurationSnapshotBuilder` pipeline that merges `ai_gateway.yaml` and
`returns/production.yaml` into the Neo4j `ACTIVE_RELEASE`** (`runtime_loader.py:92-95`, confirmed by the
`if snapshot.dependency_simulation_configuration is None: raise` guard at line 112-113). It is also a first-class
PATCH-able release domain: `configuration/api/releases.py` imports `DEPENDENCY_SIMULATION_DOMAIN_KEY` and branches
on it in `save_domain_config` (lines 305, 353) alongside `AI_GATEWAY_DOMAIN_KEY` and `RETURN_PLATFORM_DOMAIN_KEY`.

**Conclusion: `dependency_simulation.yaml` is not a standalone file-only config — it is a third BASELINE file in
the same BOOTSTRAP_ENV → BASELINE → ACTIVE_RELEASE (Neo4j) precedence chain that Part B documents for
`ai_gateway.yaml`/`returns/production.yaml`.** DB store = Neo4j configuration graph (release domain
`dependency_simulation`), same mechanism as Part B's files — OWNED/DERIVED per the brief's Neo4j classification.
No dead keys found; all top-level keys are read by `DependencySimulationConfiguration` (`StrictModel`, `extra`
not `forbid`-verified — UNVERIFIED whether unknown keys are rejected, not fully read the class body beyond line 76).

---

## 4. `data_platform/{canonical_mappings,graph_projection,sources,sync_pipelines}.yaml`

| ID | File | Lines | Loader |
|---|---|---|---|
| CFG-D-021 | `canonical_mappings.yaml` | 86 | `data_platform/mapping/loader.py:57` (`_ConfigurationFileSpec("canonical_mappings.yaml", ("canonical_mappings",))`) |
| CFG-D-022 | `graph_projection.yaml` | 44 | `loader.py:58-61` |
| CFG-D-023 | `sources.yaml` | 11 | `loader.py:56` |
| CFG-D-024 | `sync_pipelines.yaml` | 23 | `loader.py:62` |

`load_data_platform_mapping_configuration()` (`data_platform/mapping/loader.py`) reads all four as one bounded,
digested bundle (`DataPlatformMappingBundle`), feeding `data_platform/mapping/{projection,normalizer,compiler}.py`
and `data_platform/graph/{sandbox,sandbox_runner,commands}.py`, plus one real adapter
`data_platform/sources/mongodb/customer.py`.

**DEAD AT RUNTIME.** `grep -rn "data_platform\.mapping\|data_platform\.graph\.sandbox\|customer_cdm\|CUSTOMER_CDM"
backend/src/return_platform/main.py` → zero hits, and `grep -rln "data_platform.mapping\|data_platform.graph.sandbox"
backend/src/return_platform/api backend/src/return_platform/configuration/api` → zero hits. Of the 28 files that
reference `data_platform.mapping` anywhere in the repo, all but the module implementation itself
(`loader.py`/`normalizer.py`/`projection.py`/`compiler.py`/`handlers/*`) are `backend/tests/*` (16 test files) or
the one adapter file. No API route, no `main.py` startup call, no scheduler wires this subsystem into the running
application. `sources.yaml`'s own comment confirms the intent: "intentionally non-executable until the matching
governance catalog asset is declared in the target environment's `data_assets.yaml`" — and `data_assets.yaml`
(item CFG-D-012, 1 asset: `source.sales-orders`) never declares `source.mongodb.customer_outbound_cdm`, the asset
`sources.yaml:8` requires.

This is a self-contained, test-exercised "Customer CDM → canonical → graph" mapping engine, conceptually
overlapping the manifest's live order-sync engine (`sync.order_full`/`sync.order_partial` +
`mappings/sales_inv_order.yaml`, Part B) but for a *different* entity (Customer, never Order) and structurally
incapable of running because its one declared source asset has no catalog entry. Not a duplicate-meaning
collision with Part B's live sync — it targets disjoint entities — but it is dead weight: four config files +
~10 implementation modules + 16 test files with no path to production traffic.

**Inventory (business content, not exhaustive):**

| ID | File.Key | Purpose | Business? |
|---|---|---|---|
| CFG-D-021a | `canonical_mappings[].source_paths`/`canonical_field` | Field-level source→canonical mapping | Business (if ever live) |
| CFG-D-022a | `graph_nodes[].label`/`key_field`/`properties` | Graph projection shape | Structural |
| CFG-D-023a | `source_assets[].catalog_asset_id` | Binding to a `data_assets.yaml` entry (currently unresolved) | Structural |
| CFG-D-024a | `sync_pipelines[].stages[].depends_on` | Pipeline stage ordering | Operational |

---

## 5. Seed configuration, `backend/assets.yaml`, `reasoning.yaml`, SQL/Neo4j config tables

| ID | File | Loader | Live? |
|---|---|---|---|
| CFG-D-031 | `seed/generation.yaml` | Comment-only self-description: "Volumes and knobs for `scripts/generate_seed_data.py`" — script-invoked, not app-startup | **Script-only**, not read by the running backend process |
| CFG-D-032 | `seed/e2e_seed_manifest.json` (379 lines: schemaVersion/generatorVersion/counts/runtimeOptions/customerCatalog/productCatalog/scenarios) | `operations/seed_manifest.py:16-36` `_seed_manifest_path()` — module-import-time load (`SEED_MANIFEST_PATH`, no `PLATFORM_*` setting; hardcoded path search: `<repo>/backend/config/seed/e2e_seed_manifest.json`, then two CWD-relative fallbacks) | **Live** — `operations/seed_manifest.py` is imported by `api/seed.py`, `operations/seed_coordinator.py`, `operations/repository.py`, `operations/sql_business_state.py`, `api/order_lines.py`, `workflows/case_customer_identity.py`, `workflows/case_order_date.py` — this is production-reachable through an API route, not just a script |
| CFG-D-033 | `backend/assets.yaml` (repo root of `backend/`, NOT `backend/config/`) | **None.** Byte-for-byte identical content to `backend/config/data_assets.yaml` (both: 1 asset, `source.sales-orders`) | **DEAD** — `grep -rn "assets\.yaml"` across `backend` shows only `backend/config/data_assets.yaml` referenced by path (`settings.py:11`, `Dockerfile:122` `PLATFORM_CATALOG_PATH=/app/config/data_assets.yaml`); `backend/assets.yaml` is never named anywhere |
| CFG-D-034 | `reasoning.yaml` | `platform/reasoning/configuration.py:51` `load_reasoning_configuration()` — function **defined and exported** (`platform/reasoning/__init__.py:18,79`) but **never called** | **DEAD** — `grep -rn "load_reasoning_configuration\("` across all of `backend` (src + tests) matches only its own `def` line. No `PLATFORM_REASONING_*` setting exists in `settings.py` either, so there is no configured path for it even if a caller existed |
| CFG-D-035 | `platform.bay_configuration` (SQL Server table, `configuration/sql_migrations/002_domain_models.sql:69-79`, bootstrap MERGE at line 134-141) | Config table, not transactional: 6 bootstrap rows for warehouse `WH-CHENNAI-01` seeded by the migration itself | **Live but structurally orphaned from generated data** — its only other writer is `backend/scripts/seed_warehouse_bay_configuration.py` (manual script, not run at startup), which exists specifically because the generator's synthetic warehouse ids (`686`, `1969`, `1305`, ...) are disjoint from the migration's hardcoded `WH-CHENNAI-01`, so `observe_eligible_bays` finds no candidate bays for any generated order unless the script is run (per that script's own docstring, `seed_warehouse_bay_configuration.py:1-24`) |
| CFG-D-036 | `platform.return_policy_version` (SQL Server table, `sql_migrations/003_production_return_platform.sql:198`) | Config table (policy version tracking) | UNVERIFIED consumer — not traced further; flagged for Part B/C cross-check since it likely correlates with the release/policy versioning model |
| CFG-D-037 | `dbo.e2e_seed_scenarios` (SQL Server table, `sql_migrations/001_return_business_state.sql:45`) | Seed-scenario config table, companion to CFG-D-032's JSON `scenarios[]` | Business (seed data) |
| CFG-D-038 | Neo4j config migrations `0010_configuration_constraints.cypher`, `0011_configuration_indexes.cypher`, `0014_configuration_release_metadata.cypher` (`data_platform/graph/migrations/`), applied by `configuration/cli/apply_neo4j_migrations.py` via `backend/scripts/apply_neo4j_migrations.py` | Creates constraints/indexes/metadata nodes supporting the `ACTIVE_RELEASE` configuration graph (Part B territory) | Structural — DB schema tooling, not itself a business-value config source |
| CFG-D-039 | `graph/order_discovery.yaml` | Manifest module `graph.order_discovery` (`manifest.yaml:43-44`) | **OUT OF SCOPE for Part D** — confirmed Part B owns it via the manifest |

`backend/assets.yaml` (CFG-D-033) and `reasoning.yaml`'s loader (CFG-D-034) are the two cleanest REMOVE/dead-code
candidates in this Part: one is a byte-identical orphaned duplicate file, the other is a fully-wired,
fully-typed, exported loader function with zero callers and zero configured path.

---

## 6. Support/context/gate/ingress/resolver `*_configuration.py` modules — determination

Checked all five: `context_assembly_configuration.py`, `support_gate_configuration.py`,
`support_ingress_configuration.py`, `support_resolver_configuration.py`, `support_template_configuration.py`.

**Determination: none of these are Mongo-backed. All five are pure pydantic schema classes embedded as fields on
`ReturnPlatformConfiguration`** (`configuration/return_configuration.py:1714-1738`:
`support_template`, `support_gate`, `context_assembly`, `support_ingress`, `support_resolver`). Confirmed by
`grep -n "support_template\|support_gate\|support_ingress\|support_resolver\|context_assembly"
configuration/return_configuration.py` → only import lines + the five `Field(...)` declarations at those line
numbers. Also confirmed **zero** hits for these five module names inside `configuration/domain/*.py` (the
Neo4j-graph domain layer), meaning they do not have a separate DB-node representation of their own — they travel
as part of the same `ReturnPlatformConfiguration` JSON blob that `returns/production.yaml` seeds and that lives in
the Neo4j `ACTIVE_RELEASE` graph.

**This is entirely Part B's territory (the release), not Part D's.** No Mongo collection, no separate YAML file,
no file-only default distinct from `returns/production.yaml`'s baseline. Documented here only to close out the
brief's explicit instruction to determine this; no inventory/DB-mapping rows added for these five modules to avoid
duplicating Part B's work. One cross-cutting note for Part B: each field is individually defaulted
(`Field(default_factory=...)`), so **a release cut before a given block existed still loads** — this is
deliberate per each module's docstring (e.g. `support_gate_configuration.py:112-121`), not a gap.

---

## 7. Frontend hardcoded operational constants (`frontend/src/**`)

`frontend/vite.config.ts` reads only `FRONTEND_BACKEND_TARGET` (required, throws if unset) via `loadEnv(mode, repositoryRoot, "FRONTEND_")`,
and `VITE_MOCK_MODE` (build-time guard against shipping mock mode). `grep "import.meta.env.VITE_"` across
`frontend/src` found exactly one use (`main.tsx:63`, the same mock-mode flag). No `VITE_*` env var carries an API
base URL, poll interval, or page size — all of the following are hardcoded TypeScript constants:

| ID | File:line | Constant | Value | Should be backend-owned? |
|---|---|---|---|---|
| CFG-D-050 | `frontend/src/api/cases.ts:361` | `CASE_POLL_INTERVAL_MS` | 10_000 | Operational — currently a frontend-only constant; no backend equivalent found. Candidate for a backend-served operational default (e.g. via `/api/config/runtime`) if per-deployment tuning is ever needed |
| CFG-D-051 | `frontend/src/api/casePanel.ts:84` | `PANEL_POLL_INTERVAL_MS` | 10_000 | Same as above |
| CFG-D-052 | `frontend/src/domains/ai/AiControlCenterPage.tsx:874` | inline `refetchInterval` | 15_000 | Operational, hardcoded |
| CFG-D-053 | `frontend/src/domains/ai/AiControlCenterPage.tsx:883` | inline `refetchInterval` | 60_000 | Operational, hardcoded |
| CFG-D-054 | `frontend/src/domains/shipments/ShipmentConsolePage.tsx:156` | inline `refetchInterval` | 15_000 | Operational, hardcoded |
| CFG-D-055 | `frontend/src/features/graph-analyzer/analyzerQueries.ts:27,36` | inline `refetchInterval` (RUNNING/PREPARING states) | 1_500 | Operational, hardcoded |
| CFG-D-056 | `frontend/src/api/graphAnalyzer.ts:59` | `pageSize` query param | `"25"` (string literal) | Operational, hardcoded — no user control, no backend default |
| CFG-D-057 | `frontend/src/domains/ai/AiControlCenterPage.tsx:1652` | `OLLAMA:` default base URL shown in UI | `"http://localhost:11434/v1"` | Low severity — matches backend `settings.py:324` `ollama_base_url` default exactly; display-only convenience, not a live override path |

None of these are read from a backend config API at runtime (Part C's `/api/config/runtime` was not checked for
whether it *could* carry them — that is Part C's surface). All seven poll-interval/page-size values are
compile-time constants; changing any of them requires a frontend rebuild+redeploy, not a config-API PATCH.

---

## DB MAPPING

| Config ID | File | DB store | Collection/Table/Node | Same value as file? | Missing in DB? | DB only? | Duplicate? | Notes |
|---|---|---|---|---|---|---|---|---|
| CFG-D-001..009 | active-schema.return-order.yaml | Mongo `return_platform` (platform, OWNED) | `graph_schema_releases`, `graph_schema_active_release`, `graph_schema_migration_plans` | Seeded copy matches file at first boot; diverges the moment an operator activates a different release (by design) | No — seeded at first boot if Mongo reachable | No — file is a real fallback, not decorative | No | See section 1 |
| CFG-D-010 | internal_manifests/*.yaml | None | — | N/A | N/A | N/A | N/A | Files never reach a DB; loader path does not exist |
| CFG-D-011/011a-d | schema_registry.yaml | None found | — | N/A (FILE_ONLY) | N/A | No | No | `@lru_cache` only, no persistence |
| CFG-D-012 | data_assets.yaml | None found | — | N/A (FILE_ONLY) | N/A | No | Yes — see CFG-D-033 | Byte-identical to `backend/assets.yaml` |
| CFG-D-013 | live_validation/data_assets.sampling.yaml | None | — | N/A | N/A | N/A | No | Dead file |
| CFG-D-016..020 | dependency_simulation.yaml | Neo4j `ACTIVE_RELEASE` graph (OWNED/DERIVED, via `ConfigurationGraphRepository`) | Release domain `dependency_simulation` (`DEPENDENCY_SIMULATION_DOMAIN_KEY`) | Same at first boot / after `bootstrap_graph_configuration.py` republish; PATCH-able independently after | No | No | No | Same mechanism as Part B's `ai_gateway`/`return_platform` domains |
| CFG-D-021..024 | data_platform/*.yaml | None | — | N/A | N/A | No | No | Loader exists but nothing calls it in production |
| CFG-D-031 | seed/generation.yaml | None (writes go to `return_source` Mongo via the script, not a config store) | — | N/A | N/A | No | No | Script-time only |
| CFG-D-032 | seed/e2e_seed_manifest.json | None — read fresh at import time | — | N/A (FILE_ONLY, module-cached in-process) | N/A | No | No | No `PLATFORM_*` override exists for its path |
| CFG-D-033 | backend/assets.yaml | None | — | N/A | N/A | No | **Yes, duplicate of CFG-D-012** | Dead file |
| CFG-D-034 | reasoning.yaml | None | — | N/A | N/A | No | No | Loader unreachable |
| CFG-D-035 | platform.bay_configuration | SQL Server (`return_platform` DB, EXTERNAL/source per brief's SQL Server classification — UNVERIFIED whether this specific DB is the "source" mssql or an operational one; `sql_migrations` run against `[return_platform]` per `USE [return_platform];` at `002_domain_models.sql:1`, which is the **platform's own** SQL Server database, so this is platform-OWNED, not source-external) | `platform.bay_configuration` table | Bootstrap 6 rows always present; generated-order warehouse ids never match unless script is run | Effectively yes for any generated corpus | No — table exists once migrations run | No | Manual sync script required, not automatic |
| CFG-D-036 | platform.return_policy_version | SQL Server (`return_platform`, platform-OWNED) | `platform.return_policy_version` | UNVERIFIED | UNVERIFIED | No | UNVERIFIED | Flagged, not fully traced |
| CFG-D-037 | dbo.e2e_seed_scenarios | SQL Server (`return_platform`, platform-OWNED) | `dbo.e2e_seed_scenarios` | UNVERIFIED against CFG-D-032's JSON `scenarios[]` | UNVERIFIED | No | Possible overlap with CFG-D-032 — not confirmed | Flagged for follow-up |

---

## BOOTSTRAP MATRIX

| Config | DB missing at startup | Current behaviour | Expected | Safe write allowed? | Fix |
|---|---|---|---|---|---|
| active-schema (CFG-D-001..009) | Pointer doc missing | Falls back to file, logs nothing alarming (ordinary first-boot state) | Same | Mongo `return_platform` = **OWNED**, safe to seed | None needed — working as designed |
| active-schema pointer names missing release | Pointer exists, release doc gone | Falls back to file, logs `active_release_missing` (`release_store.py:227-231`) | Same, but this is a *data-integrity* alarm, not an ordinary state — should page/alert, currently just an ERROR log | OWNED | Consider surfacing this specific case distinctly in `/api/config/adoption` (Part C) rather than looking identical to "never published" |
| dependency_simulation (CFG-D-016..020) | Neo4j release has no `dependency_simulation` domain | `runtime_loader.py:112-113` **raises** `RuntimeError("Runtime snapshot has no dependency simulation configuration")` — hard startup failure | Same — correct fail-closed behaviour since this is a merged release domain, not an optional file | OWNED (Neo4j graph) | None needed |
| schema_registry / data_assets (CFG-D-011/012) | N/A — no DB | Always file-read, `lru_cache` per path | Same | N/A (FILE_ONLY) | None |
| platform.bay_configuration (CFG-D-035) | Table missing generated-corpus warehouse rows | `observe_eligible_bays` returns empty `eligibleBayIds` for every generated order — "structurally impossible" bay placement per the script's own docstring | Operator must run `seed_warehouse_bay_configuration.py` manually after `generate_seed_data.py` | SQL Server `return_platform` = platform-OWNED, safe write allowed | Either wire this script into the seed pipeline (`operations/seed_coordinator.py`/`api/seed.py`) so it runs automatically after generation, or document the manual step prominently — currently only documented in the script's own docstring |
| internal_manifests (CFG-D-010) | No DB path exists at all | N/A | N/A | N/A | REMOVE or wire up |
| reasoning.yaml (CFG-D-034) | No DB path, no caller | N/A | N/A | N/A | REMOVE or wire up |

---

## STORAGE RECOMMENDATION

| Config ID | Current | Recommended | Reason | Migration |
|---|---|---|---|---|
| CFG-D-001..009 | Mongo release + file fallback | Keep as-is (DB_MANAGED with FILE_ONLY fallback) | Working design, matches the release-store pattern used elsewhere | None |
| CFG-D-010 | FILE_ONLY, unreferenced | REMOVE or wire into `InternalStoreBootstrapper` with an actual loader + a settings path | Dead weight, 4×603 lines | Add a `load_internal_schema_manifest(path)` loader + a `PLATFORM_INTERNAL_SCHEMA_MANIFEST_*_PATH` setting if the internal-store bootstrap feature is meant to ship, else delete both the YAML and `internal_store/` module |
| CFG-D-011/012 | FILE_ONLY | Keep FILE_ONLY (governance catalogs are meant to be version-controlled and reviewed like code) | Matches `catalog_loader.py`'s own design intent (SHA-256 evidence, size caps, duplicate-key rejection — clearly built for auditability, not runtime mutation) | None |
| CFG-D-013 | FILE_ONLY, orphaned | REMOVE, or restore the "live sampling validator" that the file's own comment promises | Currently a misleading artifact — references tooling that doesn't exist in this checkout | Confirm with repo history whether the validator was deleted; if intentionally retired, delete this file too |
| CFG-D-016..020 | Neo4j release domain (same as Part B) | Keep as-is | Consistent with `ai_gateway`/`return_platform` domains | None — but the brief's Part D/Part B split for this file should be corrected in any future audit round |
| CFG-D-021..024 | FILE_ONLY, dead loader | REMOVE (config + `data_platform/mapping/*` + `data_platform/graph/sandbox*`) or finish wiring (needs a real `source.mongodb.customer_outbound_cdm` catalog entry + a startup/API call site) | 4 files + ~10 modules + 16 tests with no production path | Decide feature fate first; this is a build/product decision, not purely a config one |
| CFG-D-031 | FILE_ONLY, script-only | Keep FILE_ONLY | Appropriate for a one-shot generator's knobs | None |
| CFG-D-032 | FILE_ONLY, hardcoded path search | Add a `PLATFORM_*` settings field instead of the 3-candidate path-guessing in `_seed_manifest_path()` | The current fallback (`Path.cwd()`-relative) is fragile outside a known working directory (already flagged by the worktree `.pth` trap risk this user has hit before) | Add `seed_manifest_path: Path` to `Settings`, default to current location, thread through `operations/seed_manifest.py` |
| CFG-D-033 | FILE_ONLY, orphaned duplicate | **REMOVE** | Exact duplicate of CFG-D-012, referenced nowhere | `git rm backend/assets.yaml` |
| CFG-D-034 | FILE_ONLY, orphaned loader | REMOVE the loader+file, or wire `load_reasoning_configuration` into `main.py`/`bootstrap/` startup with a real settings path | Currently pure dead code with a docstring implying it should matter ("Loader for `config/reasoning.yaml`") | Confirm with the reasoning/checkpoint feature owner before deleting — may be intentionally staged for a not-yet-landed feature |
| CFG-D-035..037 | SQL Server config tables | Keep DB_MANAGED | Correct for operational data that must survive restarts and vary per deployment | Automate CFG-D-035's population (see Bootstrap Matrix) |
| CFG-D-050..057 | Frontend hardcoded constants | Keep as CODE for now; consider ENV/DB_MANAGED only if operators are expected to tune polling cadence per deployment | No evidence any operator has asked to change these; premature to move | None unless requested |

---

## DEAD/UNNECESSARY

| Config | Verdict | Evidence |
|---|---|---|
| `internal_manifests/{mongodb,mssql,neo4j,postgresql}.yaml` | **REMOVE** (or wire up) | Zero references anywhere in `backend/src`; `InternalStoreBootstrapper` only test-exercised |
| `live_validation/data_assets.sampling.yaml` | **REMOVE** (or restore intended tooling) | Zero references outside itself; the "live sampling validator" it names does not exist in this checkout |
| `backend/assets.yaml` | **REMOVE** | Byte-identical orphaned duplicate of `backend/config/data_assets.yaml`; unreferenced |
| `reasoning.yaml` loader (`load_reasoning_configuration`) | **REMOVE or WIRE UP** | Fully implemented, exported, zero callers, zero settings path, zero tests |
| `data_platform/{canonical_mappings,graph_projection,sources,sync_pipelines}.yaml` + `data_platform/mapping/*` + `data_platform/graph/sandbox*` | **KEEP if product intends to ship Customer-CDM sync; otherwise REMOVE** | Structurally can't run (`sources.yaml`'s required catalog asset doesn't exist); only reachable from tests |
| `dependency_simulation.yaml` | **KEEP, reclassify** | Live, DB-backed, PATCH-able exactly like Part B's files — brief's Part D/B split doesn't match the code |
| `schema_registry.yaml`, `data_assets.yaml`, `e2e_seed_manifest.json` | **KEEP** | Actively consumed by many modules |
| `seed/generation.yaml` | **KEEP** | Script-scoped but that's its intended scope |
| SQL config tables (`platform.bay_configuration`, `platform.return_policy_version`, `dbo.e2e_seed_scenarios`) | **KEEP** | Structural to the platform; `bay_configuration` needs its bootstrap gap fixed (see Bootstrap Matrix), not removed |

---

## DEFECTS

| ID | Sev | File:line | Root cause | Current | Expected | Fix | Test |
|---|---|---|---|---|---|---|---|
| CFG-D-DEF-01 | P2 | `backend/assets.yaml` | Orphaned duplicate never cleaned up after `data_assets.yaml` moved under `config/` | File exists, unreferenced, byte-identical to `backend/config/data_assets.yaml` | Deleted | `git rm backend/assets.yaml` | `grep -rn "assets\.yaml" backend` shows only the config-path reference remains |
| CFG-D-DEF-02 | P2 | `backend/src/return_platform/platform/reasoning/configuration.py:51` | `load_reasoning_configuration` was built (with full validation, docstring naming `config/reasoning.yaml`) but never wired into startup, and no `PLATFORM_*` setting for its path was ever added to `settings.py` | Dead function, dead file relationship | Either called from `main.py`/`bootstrap/lifespan.py` with a real settings field, or removed along with `reasoning.yaml` if the reasoning-checkpoint feature reads its config another way | Add `reasoning_configuration_path: Path` to `Settings` + a `main.py` call site, or delete both | New test asserting `reasoning.yaml` is loaded at startup and its `enabled`/`checkpointStore` values reach the checkpoint store construction |
| CFG-D-DEF-03 | P2 | `backend/config/live_validation/data_assets.sampling.yaml:1-8` | File's own header promises a "live sampling validator" consumer that does not exist anywhere in `backend/src` or `backend/tests` | Misleading, unreferenced fixture file | Either the validator is restored or the file (and its claim) is removed | Confirm with git history (`git log --follow` on the file) whether the validator was deleted in a later commit; act accordingly | N/A — documentation/cleanup defect |
| CFG-D-DEF-04 | P3 | `backend/config/dynamic_knowledge/internal_manifests/*.yaml` | Four 603-line manifest files authored for `InternalSchemaManifest`/`InternalStoreBootstrapper` but no loader was ever written to read them from disk | Dead weight | A `load_internal_schema_manifest(path)` function + settings wiring, or deletion | `grep -rn "internal_manifests" backend/src` returns non-empty once fixed |
| CFG-D-DEF-05 | P1 | `backend/scripts/seed_warehouse_bay_configuration.py` (manual) vs `backend/scripts/generate_seed_data.py` (also manual, no auto-chaining) | `platform.bay_configuration`'s only migration-seeded rows are for `WH-CHENNAI-01`; the seed generator mints warehouse ids from real order data (`686`, `1969`, `1305`, ...) that never match, and nothing in `operations/seed_coordinator.py` or `api/seed.py` calls `seed_warehouse_bay_configuration.py` automatically | An operator who runs `generate_seed_data.py` (or the `/api/seed` route) without separately remembering to run `seed_warehouse_bay_configuration.py` gets a corpus where **every** generated order has zero eligible bays — confirmed structurally impossible per the script's own docstring, not a flaky/rare case | Bay Assignment Agent should have eligible bays for generated demo/e2e orders out of the box | Call `seed_warehouse_bay_configuration.py`'s logic from `operations/seed_coordinator.py` after generation, or document the two-step requirement prominently in `api/seed.py`'s response/docs | An e2e test that runs seed generation via the API and then asserts `observe_eligible_bays` returns non-empty for at least one generated order |
| CFG-D-DEF-06 | P3 | `backend/config/data_platform/sources.yaml:1-9` (comment) vs `data_platform/mapping/loader.py` | The file self-documents as "intentionally non-executable" but the loader (`load_data_platform_mapping_configuration`) has no feature flag or guard reflecting that — it will happily parse and validate all four files even though the pipeline can never run end-to-end | Confusing for anyone extending this subsystem without reading the comment | Either gate the whole subsystem behind an explicit "not yet enabled" marker the loader checks, or remove it if abandoned | N/A |
| CFG-D-DEF-07 | P3 | Brief's own premise (BRIEF.md file-scope list) vs `configuration/runtime_loader.py:71-97`, `configuration/api/releases.py:23,231,305,353` | `dependency_simulation.yaml` was assigned to Part D as if it were a standalone file-only config, but it is merged into the same Neo4j `ACTIVE_RELEASE` snapshot and PATCH-able domain mechanism as Part B's `ai_gateway.yaml`/`returns/production.yaml` | Audit-process defect, not a code defect | Future audits should list `dependency_simulation.yaml` under the same owner as `ai_gateway.yaml`/`returns/production.yaml` | Cross-reference this file in Part B's writeup | N/A |

---

STATUS: COMPLETE
