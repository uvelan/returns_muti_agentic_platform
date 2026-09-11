# PART B — Manifest-driven configuration, Neo4j release, runtime precedence & bootstrap

Status: near-complete. ID prefix `CFG-B-`. Scope: `backend/config/manifest.yaml` + its 18 modules,
`ai_gateway.yaml`, `returns/production.yaml`.

Companion file: **`PART_B1_production_yaml_deadkeys.md`** holds the full leaf-by-leaf grep evidence
for every second-level key of `returns/production.yaml` (26 top-level sections) — this file
summarizes those findings in §1.7/§5 and cites PART_B1 rather than repeating its tables, to stay
under the line budget. Everything else (manifest modules, `ai_gateway.yaml`, load path, precedence,
bootstrap, defects) is native to this file.

Provenance: this report merges direct reading by this session with three background research passes
(manifest-module dead-key audit, `ai_gateway.yaml` dead-key audit — both folded into §1/§5 below —
and a dedicated release/precedence/bootstrap pass whose findings are §2-§4 largely verbatim, since it
read every named file in full with exact line citations, including a live test-suite cross-check and
a correction sourced from `evidence/orchestration/06_FINDINGS.json`'s F-0084 record).

## 0. Headline finding (read this first)

There are **three** configuration-loading paths in this codebase, not one, and the design doc's
described path is the one that is *not* live:

1. **Path A — the manifest system.** `manifest.yaml` → `configuration/application/loader.py` →
   `configuration/application/compatibility.py::LegacyCompatibilityAdapter.build_canonical_snapshot()`
   → `configuration/domain/release_model.py::RuntimeSnapshot`. This is what `backend/config/README.md`
   describes as authoritative. **Confirmed test-only** by three independent passes:
   `build_snapshot_from_legacy_configs`/`build_canonical_snapshot`/`LegacyCompatibilityAdapter` are
   referenced only from `configuration/application/adapters.py` (a re-export), `configuration/domain/release.py`
   (a docstring mention), `backend/config/README.md`, and `backend/tests/configuration/test_canonical_application.py`.
   Zero hits under `main.py`, `runtime_loader.py`, `runtime_activation.py`, or any `ai/gateway`/`ai/routing`
   module. `ConfigurationValidator` (`application/validator.py`) and `application/precedence.py`
   (its `BOOTSTRAP_ENV_ALLOWLIST`/`PrecedenceViolationError`) are equally test-only — `/api/config`
   validates through `releases.py`/`ConfigurationSnapshotBuilder` instead, an unrelated, graph-backed
   system. See CFG-B-D01.

2. **Path B — the Neo4j release system (the live one).**
   `configuration/return_configuration.py::load_return_configuration` (`:1982`, reads
   `returns/production.yaml` directly), `ai/routing/tasks.py::load_ai_gateway_configuration`
   (`:378`, reads `ai_gateway.yaml` directly, called from `main.py:476` and `runtime_loader.py:70`),
   `dependency_simulation/configuration.py::load_dependency_simulation_configuration` (reads
   `dependency_simulation.yaml` directly). These three become the `RETURN_PLATFORM`, `AI_GATEWAY`,
   `DEPENDENCY_SIMULATION` domains published to Neo4j by
   `configuration/cli/bootstrap_graph_configuration.py`, and read back at process start by
   `configuration/runtime_loader.py::resolve_process_configuration` via
   `configuration/snapshot.py::ConfigurationSnapshotBuilder.build_snapshot`. **This path never touches
   `manifest.yaml`.**

3. **Path C — the Agent-editing API (live, but structural-only, and narrow).** Confirmed mounted:
   `main.py:111` imports `configuration.api.agents.router`, `main.py:575` constructs
   `AgentConfigurationService`, `main.py:1429` mounts it. This service
   (`configuration/application/agent_configuration.py`) uses `ConfigurationLoader` + `manifest.yaml`
   directly (not `compatibility.py`) to read/validate/propose edits to `agent.*` modules
   (`agents/*.yaml`), and stores accepted edits as an optional `AGENT_MODULES` release domain
   (`configuration/snapshot.py:35`, written by `bootstrap/adapters/governance_agent_configuration.py:38,102`,
   read back via the `_released_agent_modules()` closure at `main.py:560-564`). So `agents/*.yaml`
   payload keys ARE read — structurally, for display/edit — but this is the Configuration UI's Agents
   screen, not agent execution. Real agent runtime config is a *different, same-named-but-separate*
   `agents:` block **inside `returns/production.yaml`** (`ReturnPlatformConfiguration.agents`,
   `return_configuration.py:1653`), consumed directly by `backend/src/return_platform/agents/*.py`
   (§1.7, §6). The two `agents:` catalogues don't even use matching key names (`bay_allocation` vs
   `bay_assignment`, `learning` vs `feedback_learning`), and Path A/C's `AgentConfigNode` declares
   fields (`implementation`, `task_queue`, `state_namespace`, `prompt_ref`, `ai_route_ref`,
   `max_concurrency`, `retry_policy`) that are **false cognates** of identically-named, but
   actually-live, fields on Path B's `AgentConfiguration` (`return_configuration.py:62-83`) — a strong
   source of audit/operator confusion (CFG-B-D03).

**A fourth, parallel release-and-fallback system exists for a different config family**:
`dynamic_knowledge/config_loader.py::load_active_schema` reads `dynamic_knowledge/active-schema.return-order.yaml`
(via `settings.dynamic_knowledge_schema_path`) as the fallback for the Graph Schema Analyzer's own
schema-release/activation mechanism (own docstring: "A release the Graph Schema Analyzer published and
activated is what the runtime loads once one exists; the YAML file is... the fallback"). This has the
same shape as Path B (published release wins, file is baseline-only) but is a **separate** activation
pointer, not carried by the `RETURN_PLATFORM`/`AI_GATEWAY`/`DEPENDENCY_SIMULATION` release —
`runtime_activation.py:12-16` names it explicitly as a sibling `ActivationParticipant` adopted "under
this lock, behind this poll guard" alongside the configuration release. Confirmed live call sites:
`api/schema_releases.py:137,184,247`, `api/source_bindings.py:121`, `data_platform/graph/sync_service.py:773`.

`platform/system_store.yaml` bypasses everything above by design: loaded directly with `yaml.safe_load`
in `platform/system_store/manifest_loader.py:79` ("Deliberately bypasses the full configuration
release/manifest pipeline," docstring `:1-14`), called from `bootstrap/system_store.py:53` and
`housekeeping/composition.py:161-164` — both real runtime modules.

Everything below is organized per the brief's 7 sections, with this finding threaded through.

---

## 1. INVENTORY

Grouping note: `ai_gateway.yaml` (1060 lines, ~23 near-identical task blocks) cannot fit
one-row-per-leaf inside the line budget, so repeated per-item schemas get one schema row with a
cardinality note. `returns/production.yaml`'s full 26-section leaf-by-leaf table lives in
**PART_B1_production_yaml_deadkeys.md**; §1.7 here gives only the top-level map plus the handful of
rows this report's own findings depend on.

### 1.1 manifest.yaml

| ID | Key | Purpose | Runtime read? | Consumer |
|---|---|---|---|---|
| CFG-B-001 | `schema_version` | manifest format version gate | YES | `loader.py:135-139` rejects unsupported versions |
| CFG-B-002 | `release_id` | manifest's own release label | YES (display only, dead path) | `compatibility.py:371` → `RuntimeSnapshot.modules.release_id` (Path A, no live reader) |
| CFG-B-003 | `status` | gates DRAFT refusal | YES (dead path only) | `compatibility.py:134-139`, reached only from Path A |
| CFG-B-004 | `modules` | id→path index, sole authority for which files load | YES | `loader.py:179-258`; also resolved by Path C (`agent_configuration.py`) for `agent.*` |

### 1.2 Manifest AGENT modules (agents/*.yaml, 8 files)

`AgentConfigNode` (`configuration/domain/agents.py:6-24`)/`AgentsConfig` have **zero readers outside
`configuration/`** for business logic (`grep -rln "AgentsConfig\|AgentConfigNode" backend/src | grep -v /configuration/` → empty). Reachable for structural read/edit via Path C only.

| ID | Key | Status | Evidence |
|---|---|---|---|
| CFG-B-010 | `payload.name` | LIVE (Path C display) / DEAD (business logic) | `agent_configuration.py::list_agents` reads it for the UI; no execution-path reader |
| CFG-B-011 | `payload.enabled` | LIVE (Path C) / DEAD (business logic) | real enable/disable is `production.yaml.agents.<n>.enabled` (CFG-B-092) |
| CFG-B-012 | `payload.execution_mode` | DEAD | `AgentConfigNode` field only, no other reader |
| CFG-B-013 | `payload.ai_assisted` (7 of 8 files) | DEAD | `AgentConfigNode` field only |
| CFG-B-014 | `payload.input_contexts`/`output_context` | DEAD | no reader |
| CFG-B-015 | `payload.capabilities` (6 of 8)/`forbidden_capabilities` | DEAD | `grep -rn "forbidden_capabilities" backend/src` → only the domain model |
| CFG-B-016 | `payload.direct_agent_calls_allowed` (all 8) | DEAD | parsed into `AgentConfigNode` and `WorkflowDefinition`, no runtime consumer of the value |
| CFG-B-017 | `payload.idempotency_required` | DEAD | no reader |
| CFG-B-018 | `implementation, task_queue, state_namespace, prompt_ref, ai_route_ref, max_concurrency, retry_policy` (modeled, never set in the 8 files) | DEAD on this side | **False cognates** of identically-named LIVE fields on `AgentConfiguration` (`return_configuration.py:62-83`, reads `production.yaml`); real `task_queue` routing is `Settings.return_workflow_task_queue` (`settings.py:130`) |
| CFG-B-019 | `status` (top-level, per file, all say `DRAFT`) | Cosmetic | Not `ReleaseStatus`-checked; carried into `ModuleConfigNode.status` with no gate |

### 1.3 Manifest POLICY modules (policies/*.yaml, 4 files) — confirmed dead twice over

By `backend/config/README.md` ("No code loads them") + `compatibility.py:274-278` ("preserved in
ModulesConfig only"), and independently reconfirmed by field-name grep for every leaf key:

| File | Keys checked | Evidence |
|---|---|---|
| CFG-B-020 candidate_scoring.yaml | `high_confidence`, `ambiguity_gap`, `exact_anchor_required_for_auto_rank`, `strong_anchor_weights` | zero matches. (A *differently-named* live field, `ambiguity_gap_millionths`, exists on `production.yaml.discovery` — false cognate, not this file) |
| CFG-B-021 clarification.yaml | `ask_smallest_unresolved_question`, `suppress_answered_questions`, `suppress_inferable_questions`, `candidate_cap_behavior` | zero matches |
| CFG-B-022 privacy.yaml | `graph_property_mode`, `prohibited_categories`, `redact_sensitive_preview_values`, `require_authorization_scope` | zero matches |
| CFG-B-023 return_eligibility.yaml | `deterministic_facts_first`, `require_sealed_discovery_context`, `require_source_revision`, `require_prior_return_evidence` | zero matches; real policy is `production.yaml.return_eligibility_policy` |

### 1.4 Manifest WORKFLOW / SYNC / SOURCE / MAPPING / GRAPH modules

| ID | File | Key | Status | Evidence |
|---|---|---|---|---|
| CFG-B-030 | workflows/return_session.yaml | `payload.stages`, `context_only_handoffs`, `direct_agent_calls_allowed` | DEAD | `WorkflowDefinition(...)` only ever constructed at `compatibility.py:199` (Path A) and test fixtures. **The live stage sequence is a Python constant, `DEFAULT_STAGE_SEQUENCE`, used whenever `workflow_definition` is `None` — always, in production.** |
| CFG-B-031/032 | sync/order_partial.yaml, sync/order_full.yaml | `payload.mode/projection_profile/max_candidates/strong_anchors/graph_readback_required` | DEAD | Path A only. `order_full.yaml` additionally declares `identity_expression`, `line_identity_expression`, `require_exactly_one_full_order_id`, `hydrate_all_authoritative_lines`, `stale_line_cleanup` — not even modeled fields on `GraphSyncConfig`, caught only by a generic payload catch-all. Possible overlap with live `data_platform/sync_pipelines.yaml` — see §6 |
| CFG-B-033 | sources/sales_inv.yaml | `payload.connector_type`, `connection_reference`, `database_reference`, `collection`, `access_mode`, `record_identity_expression`, `order_id_path`, `customer_id_path`, `line_path`, `line_number_path`, `schema_fingerprint_required` | DEAD | Path A only; live source config is `data_platform/sources.yaml`. (The live `/api/config/sources` Data Console registry, `configuration/api/sources.py`, is unrelated — health-probes for configured connections — and doesn't read this file either.) |
| CFG-B-034 | mappings/sales_inv_order.yaml | `payload.canonical_entity`, `full_order_id`, `full_order_line_id`, `allowlisted_order_fields`, `allowlisted_line_fields` | DEAD | Path A only; live mapping is `data_platform/canonical_mappings.yaml` |
| CFG-B-035 | graph/order_discovery.yaml | `payload.schema_name`, `nodes`, `relationships`, `constraints`, `projection_profiles`, `prohibit_raw_source_payload` | DEAD | Path A only; `grep -rn "ORDER_DISCOVERY_MINIMAL"` (the `schema_name`) → no hits elsewhere. Live schema is `dynamic_knowledge/active-schema.return-order.yaml` via the sibling activation system (§0) |

### 1.5 Manifest PLATFORM module (platform/system_store.yaml, 204 lines)

**Two loaders.** Loader A (Path A, test-only): `compatibility.py:280-317` → `SystemStoreConfig`.
Loader B (live, bypasses manifest — §0): `manifest_loader.py:79 load_system_store_config()` → a
**local, different** model `_SystemStoreConfigPayload` (`extra="ignore"`).

| ID | Key | Loader B (live) status | Evidence |
|---|---|---|---|
| CFG-B-040 | `provider` | LIVE | `manifest_loader.py:96 _require_serviceable_provider`, enforced against `SUPPORTED_PROVIDERS={"MONGODB"}` |
| CFG-B-040b | `allowed_providers` | LIVE | `manifest_loader.py:108-112` |
| CFG-B-042a | `auto_bootstrap_missing_structures` | LIVE | `bootstrap/system_store.py:~65`, passed to `bootstrapper.bootstrap(...)` |
| CFG-B-042b | `fail_closed_on_drift` | LIVE | `bootstrap/system_store.py:~62`, passed to `SystemStoreBootstrapper(...)` |
| CFG-B-042c | `migration_mode` | **DEAD on the live path** — not a field on `_SystemStoreConfigPayload`, silently dropped | `grep -rn migration_mode backend/src` → only `compatibility.py:313` (Path A), `domain/system_store.py:20` |
| CFG-B-042d | `migration_lock_required` | **DEAD on the live path**, same reason | `grep -rn migration_lock_required backend/src` → only `compatibility.py:315`, `domain/system_store.py:22` |
| CFG-B-041a-d | `structures.*.physical_name/schema_version/encrypted/indexes[]` | LIVE | `manifest_loader.py:124-133`; `contracts.py:38-49,142`; `mongo.py:263-280` (`ensure_indexes` actually creates/compares Mongo indexes) |

Net: every key is LIVE via Loader B **except** `migration_mode`/`migration_lock_required` — a real
config-vs-code drift, CFG-B-D06.

### 1.6 Singleton: ai_gateway.yaml

Two loaders: dead Path A (`compatibility.py:334-343` → `AiConfig`, loose `Mapping[str, Any]`) and live
Path B (`ai/routing/tasks.py::load_ai_gateway_configuration` → `AIGatewayConfiguration`, `tasks.py:303`,
`StrictModel`, `extra="forbid"` at every level). 25 files reference `AIGatewayConfiguration`.

| ID | Section.Key | Status | Consumer (file:line) |
|---|---|---|---|
| CFG-B-050 | `schemaVersion`, `domain` | Schema-live, no business branch found | `tasks.py:303-306` |
| CFG-B-052a-d | `circuitBreaker.failureThreshold/openSeconds/authFailureOpenSeconds/rateLimitCooldownSeconds` | LIVE | `ai/routing/selection.py:422,394,399,404`; bound as `cfg = self.configuration.circuitBreaker` at `:386`, inside `record_failure` |
| CFG-B-053 | `retry.maximumAttemptsPerRoute/maximumTotalAttempts/initialBackoffMilliseconds/maximumBackoffMilliseconds/jitter` | LIVE | `ai/gateway/final_dispatch.py` ~804-808, ~1003, ~1069-1074 |
| CFG-B-054a | `rateLimits.application.requestsPerMinute/tokensPerMinute` (no `maximumConcurrency` for this tier — correctly optional) | LIVE | `selection.py:302,336-337` |
| CFG-B-054b | `rateLimits.{lightweight,standard}.requestsPerMinute/tokensPerMinute/maximumConcurrency` | LIVE | `selection.py:303-307,336-337,344` |
| CFG-B-055 | `providerLimits.<PROVIDER>.*` (7 providers) | LIVE | `selection.py:309-349`, same dict-access reader keyed by `route.provider_name` |
| CFG-B-056a | `modelContexts[].provider/model/maximumContextTokens` | LIVE | `tasks.py:291-292,299,329,343-346` (`context_shortfall`), called from `selection.py:120,133,159,234` |
| CFG-B-056b | `modelContexts[].source` | READ_BUT_NO_EFFECT | `tasks.py:300`, schema-validated only, round-trips into releases (`:391-407`) |
| CFG-B-057 | `tasks.<TASK>.tier` (23 tasks) | LIVE | `selection.py:157`, `ai/gateway/service.py:148,408,488,521` |
| CFG-B-058a-c | `tasks.<TASK>.promptVersion/systemPrompt/systemPromptSections` | LIVE | `final_dispatch.py:521`, `service.py:465,482`, `structured_invocation.py:468`; sections composed into `systemPrompt` at validation time (`tasks.py:180-223`) |
| CFG-B-059a | `tasks.<TASK>.fallbackStrategy` | LIVE (typed field, `ai/gateway/models.py:40`); exact dispatch branch UNVERIFIED | field confirmed, branch not traced |
| CFG-B-059b | `tasks.<TASK>.fallbackTemplate` | LIVE | `service.py:407` |
| CFG-B-060a/b | `tasks.<TASK>.maximumOutputTokens/maximumInputTokens` | LIVE | `service.py:639,331`, `structured_invocation.py:492`, `selection.py:139,244` |
| CFG-B-061 | `tasks.<TASK>.allowTierEscalation` | LIVE | `structured_invocation.py:495` |
| CFG-B-062 | `tasks.<TASK>.allowedProviders` | LIVE | `structured_invocation.py:322,363`, `selection.py:157,223` |
| CFG-B-063 | `tasks.<TASK>.allowedInputKeys` | LIVE | `service.py:318`, `structured_invocation.py:391` |
| CFG-B-064 | `AiConfig.routes/providers/safety/interception` (Path A model only, `domain/ai.py:29-32`) | **DEAD, doubly** | Absent from the yaml file AND from `AIGatewayConfiguration` |

The brief's "routes, providers" for `ai_gateway.yaml` don't exist live (CFG-B-064); that concept maps
onto `returns/production.yaml`'s `runtime_integrations.ai_providers[]` instead (CFG-B-101, §1.7).

### 1.7 Singleton: returns/production.yaml — summary (full detail in PART_B1)

**Critical defect (CFG-B-D02):** `compatibility.py:345-357` (Path A) extracts only `features`/
`platform` top-level keys; the file has **neither** (`grep -n "^features:\|^platform:"` → zero hits).
Its 26 real top-level keys all map 1:1 onto `ReturnPlatformConfiguration` fields
(`return_configuration.py:1650-1740`, confirmed by PART_B1's Table 0), so nothing is silently dropped
at the top level — Path A's branch is simply a no-op against this file, on top of being unreachable.
The real parser is `ReturnPlatformConfiguration` (`:1650-1772`) via `load_return_configuration`
(`:1982`) — Path B, live.

| Top key | Status | Note |
|---|---|---|
| `schema_version`, `assumption_set_version` | READ_NO_EFFECT | stored, not branched on |
| `agents` | YES — **the real per-agent runtime config** (CFG-B-092) | `agents/bay_assignment.py:39`, `feedback.py:18`, `fulfillment.py:18`, `order_analysis.py:77,83`, `order_discovery.py:38`, `return_workflow.py:20`, `support_response.py:51` each do `configuration.agents["<key>"]`; the dead-knob fields (`human_confirmation_required`, `capabilities`, `task_queue`, etc.) are documented dead in the model's own docstring and independently spot-checked (real task-queue routing is `Settings.return_workflow_task_queue`) |
| `discovery`, `source_resolution`, `clarification_policy`, `return_policy`, `workflow`, `support`, `omc`, `integrations`, `context_assembly`, `copilot`, `runtime_integrations`, `return_eligibility_policy`, `shipment_tracking`, `support_gate`, `support_template` | YES (LIVE, at least partially — see PART_B1 for exact leaf-level citations and the handful of UNVERIFIED leaves within each) | e.g. `runtime_integrations.ai_providers[]` is where the brief's "routes/providers" concept actually lives (CFG-B-101) |
| `selection_vocabulary`, `business_calendars` | YES | `bootstrap/api.py:225,230`; `resolve_business_deadline` |
| `bay`, `return_case` | LIVE-likely, not leaf-isolated | PART_B1 §Table1 flags these UNVERIFIED at leaf level |
| `extensions` (5 flags) | **READ_BUT_NO_EFFECT — confirmed dead**, two independent passes | `ExtensionConfiguration` validates cross-dependencies only; zero downstream readers of any flag name |
| `feature_flags` (4 flags) | **READ_BUT_NO_EFFECT — confirmed dead**, two independent passes; corrects this audit's own initial premise that this block is "the real feature flags" | zero downstream readers, including frontend |
| `policy_evaluation` | YES | `PolicyEvaluationConfiguration`; exact case-evaluation call site UNVERIFIED (Part A territory) |
| `support_ingress`, `support_resolver` | YES (section-level; leaves not all individually traced) | `SupportIngressConfiguration`/`SupportResolverConfiguration` |

---

## 2. LOAD PATH

### Path A — manifest system (dead in production; Path C reads it structurally only)

```
backend/config/manifest.yaml
  -> loader.py::ConfigurationLoader.load_manifest()                                     [loader.py:115-173]
  -> load_manifest_entries()  (agents/*.yaml, policies/*.yaml, workflows/return_session.yaml,
     sync/*.yaml, sources/sales_inv.yaml, mappings/sales_inv_order.yaml,
     graph/order_discovery.yaml, platform/system_store.yaml)                            [loader.py:179-258]
  -> compatibility.py::LegacyCompatibilityAdapter.build_canonical_snapshot()
     - refuses if manifest.status == DRAFT                                              [compatibility.py:134-139]
     - routes by module_type into AgentsConfig/WorkflowConfig/SourcesConfig/
       GraphConfig(.graphs/.mappings/.sync)/ModulesConfig/SystemStoreConfig/IntegrationsConfig
                                                                                          [compatibility.py:156-330]
     - ALSO loads ai_gateway.yaml (explicit name) -> AiConfig                            [compatibility.py:334-343]
     - ALSO loads returns/production.yaml (explicit name), extracts its
       (non-existent) `features`/`platform` keys -> FeaturesConfig/PlatformConfig        [compatibility.py:345-357]
  -> configuration/domain/release_model.py::RuntimeSnapshot                              [compatibility.py:366-386]
```
Caller graph: `backend/tests/configuration/test_canonical_application.py` only (`CONFIG_DIR` there
resolves to the real `backend/config`, `:51` — the test exercises real files but asserts nothing about
`features`/`platform` content, so CFG-B-D02 has no test catching it). `RuntimeSnapshot` (Path A) is a
distinct class from the live `PinnedConfigurationSnapshot` (`configuration/snapshot.py:39`) — no
shared base, no shared reader. A third, textually similar but unrelated name,
`dynamic_knowledge/graph/generation.py::ActiveRuntimeSnapshot`, is the Dynamic Knowledge graph's
blue/green cutover pointer and is unrelated to configuration (naming collision, CFG-B-D01).

### Path B — Neo4j release system (the live path)

```
backend/config/returns/production.yaml -> return_configuration.py::load_return_configuration    [:1982]
                                        -> ReturnPlatformConfiguration.model_validate(...)        [:1650-1772]
backend/config/ai_gateway.yaml         -> ai/routing/tasks.py::load_ai_gateway_configuration      [:378]
                                        -> AIGatewayConfiguration.model_validate(...)             [:303]
backend/config/dependency_simulation.yaml -> dependency_simulation/configuration.py::load_dependency_simulation_configuration

  === Bootstrap (configuration/cli/bootstrap_graph_configuration.py; backend/scripts/bootstrap_graph_configuration.py
      is a 7-line shim that only imports and calls its run()) ===
  -> merges RETURN_PLATFORM with the ACTIVE Neo4j release's payload via _carry_forward()
     (per-top-level-key, digest-based)                                                    [:61-109,328-358]
  -> AI_GATEWAY/DEPENDENCY_SIMULATION are re-read from disk fresh every run with NO
     carry-forward/baseline logic at all — always packaged-file-wins for these two          [:255-258]
  -> publishes DRAFT -> VALIDATED -> RELEASED via graph_repository.py::Neo4jConfigurationGraphRepository
                                                                                            [:433-464]
     under RETURN_PLATFORM_DOMAIN_KEY / AI_GATEWAY_DOMAIN_KEY / DEPENDENCY_SIMULATION_DOMAIN_KEY
                                                                                            [snapshot.py:25-27]

  === Every process start (configuration/runtime_loader.py::resolve_process_configuration) ===
  1. Settings() from PLATFORM_* env + Vault bootstrap resolve                              [runtime_loader.py:57-68]
  2. Load packaged YAML files as fallback baseline                                          [:69-73]
  3. Connect to Neo4j; non-prod falls back to an in-memory empty repo on failure; prod raises [:74-86]
  4. ConfigurationSnapshotBuilder.build_snapshot(...) reads the ACTIVE RELEASED release,
     validates checksum + each domain payload; on failure, if allow_baseline_fallback,
     falls back to the packaged-file baseline instead of raising                          [snapshot.py:109-196]
  5. graph_settings = runtime_integrations.py::apply_graph_runtime_configuration(...)
     — applied only `if not <source>.bootstrap_managed` per infra source                   [runtime_integrations.py:76+]
  6. Second Vault resolve pass against graph_settings                                       [:102-109]

  === Ongoing, per already-running process (configuration/runtime_activation.py) ===
  -> RuntimeConfigurationActivator.refresh(), guarded to at most once per 5s               [runtime_activation.py:198-220]
  -> driven by FastAPI request middleware (API process) or run_runtime_activation_loop's
     5s poll timer (workers)                                                                [:452-479]
  -> refuses (raises, keeps last-good release) if the new release would change an
     infra-owned _RESTART_REQUIRED_SETTINGS field (mongo_dsn, neo4j_uri, ...)              [:71-85,306-316]
```

Domain keys stored (`snapshot.py:25-35`): `RETURN_PLATFORM`, `AI_GATEWAY`, `DEPENDENCY_SIMULATION`,
optionally `AGENT_MODULES` (Path C edits only). **No** `MANIFEST`/`POLICY`/`WORKFLOW`/`SYNC`/`SOURCE`/
`MAPPING`/`GRAPH`/`PLATFORM` domain key exists — confirmed by `bootstrap_graph_configuration.py:383-389`'s
`domain_payloads` dict listing only the three.

### Path C — Agent-editing API

```
backend/config/agents/*.yaml -> loader.py::ConfigurationLoader (direct, not via compatibility.py)
  -> configuration/application/agent_configuration.py::AgentConfigurationService
  -> proposals through ProposalKernel/governance -> optionally written as
     AGENT_MODULES release domain (bootstrap/adapters/governance_agent_configuration.py:38,102)
  -> read back by main.py:563 for the Configuration UI's Agents screen only
```

Two other real runtime YAML readers worth noting for completeness: `dynamic_knowledge/config_loader.py:29`
(the sibling schema-release system, §0) and `data_platform/schema_registry.py:176`
(`load_schema_registry`, `@lru_cache(maxsize=8)` at `:168` — the one genuine in-process memoization
cache found anywhere in this area, on a config family unrelated to the release/snapshot mechanism).

---

## 3. RELEASE & PRECEDENCE

**(a) ENV vs files vs Neo4j — who wins at process start.**
`Settings()` (`PLATFORM_*` env, `settings.py:52-60`) supplies connection/bootstrap parameters only —
Neo4j URI, Vault address, file paths — never business configuration. Packaged files load next purely
as the `default_*` fallback for `build_snapshot`. **The Neo4j RELEASED release wins whenever reachable
and valid** (`snapshot.py:109-196`); only on failure, and only if `allow_baseline_fallback`, does it
fall back to the packaged file (§3c). **For infrastructure connection fields specifically**, precedence
inverts: `apply_graph_runtime_configuration` (`runtime_integrations.py`) applies the release's value to
a given infra source (`platform-mongodb`, `source-mongodb`, `configuration-neo4j`, `omc-sqlserver`,
`valkey`, `temporal`) only `if not <source>.bootstrap_managed` (`:76,87,98,104,111,119,126,130,138`) —
a `bootstrap_managed` source (the ordinary case for compose-injected infra) keeps whatever
`Settings()`/Vault already gave it; **the release cannot touch it**. Enforced a second time,
independently, by `runtime_activation.py:71-85`'s `_RESTART_REQUIRED_SETTINGS` set (`mongo_dsn`,
`neo4j_uri`, `sqlserver_host`, `valkey_host`, `temporal_target`, etc.): if a later release tries to
change one of these on an already-running process, `refresh()` raises rather than applying it
(`:306-316`) — infra settings cannot change without a restart even when the release changes them. For
every other (business) key, the release *is* the effective value; env vars never supply it in
production.

**(b) bootstrap_graph_configuration.py behavior, per case** (full 545-line file read; line numbers
against `configuration/cli/bootstrap_graph_configuration.py`):

- **No release:** `active is None` (`:246`) → carry-forward skipped entirely → publishes the packaged
  files verbatim as a new DRAFT→VALIDATED→RELEASED release (`:398-482`).
- **Release this script produced before, unchanged:** merged `domain_payloads` equals what's stored
  (`:401-403`) → **nothing republished**, only `packaged_key_digests` metadata refreshed if decidable,
  prints `UNCHANGED`, head revision does not move.
- **Release an operator edited via the Configuration API — the general mechanism:** `_carry_forward`
  (`:71-109`) is a **per top-level-key**, SHA-256-digest-based three-way merge. **No machine id or
  actor field is used anywhere** — the only signal is `PACKAGED_KEY_DIGESTS` (`:58`, per-key digest of
  the packaged file's value *at the moment the release was last published by this script*,
  `_key_digests`, `:61-68`, stored in release metadata, which sits outside the release's checksum —
  `graph_repository.py:127-130` — so it can be written after RELEASED). Per key: if the release's
  current digest still equals the recorded baseline, nobody touched it since → packaged file's new
  value wins; if it has moved → release's value is kept (operator edit, or a prior bootstrap writing AI
  receipts into `runtime_integrations`). **If no baseline exists at all, the merge cannot decide
  anything and the release wins outright for every key** (`:93-102`), with dropped packaged keys only
  logged as a warning (`:336-346`) — not a hard failure, not a non-zero exit. `--adopt-packaged`
  (`:347-354,514-523`) is the **only path that can overwrite an operator's edits**.
  - Schema-drift sub-case: an active payload that fails current-schema validation falls back to the
    packaged configuration wholesale, loudly warning operator values are dropped (`:355-368`) —
    documented fix for a prior deadlock where a stale release could never be superseded.
  - Historical defect quoted in-code: a new top-level key added to `production.yaml` after a release
    was cut is absent from `active_payload`, so without the baseline mechanism it never reaches a
    deployment that had ever published — cited as the cause of `copilot.order_discovery_agent_id`
    staying `null` in production (`:298-310`).
- **`AI_GATEWAY`/`DEPENDENCY_SIMULATION` have NO carry-forward/baseline mechanism at all** — re-read
  fresh from disk every run (`:255-258`) with no per-key merge against the active release; there is no
  operator-edit-preservation concept for these two domains (no Configuration-API write surface exists
  for them). The packaged file always wins for these two, unconditionally.
- **Answering a live example measured against this deployment's actual graph** (RELEASED release
  `run0001-d0009-...-20260902-1108`, `created_by=dev-operator`, head revision 64; file has content the
  release lacks — `agents.support_response`, five `return_policy.return_method_derivation.ship_via_methods`
  codes, populated `source_resolution.*_paths`; release has operator-only values —
  `policy_evaluation.enabled=true`, `support_ingress.nl_enabled=true` where the file says `false`):
  this release was created via the Configuration API's create-release path, which **copies domain
  payloads from the active release but never calls `set_release_metadata`** (`releases.py:205-243`) —
  so `PACKAGED_KEY_DIGESTS` is absent on this lineage, `baseline_decidable=False` (`:329`), and every
  bootstrap run against it takes the **undecidable branch**: the release wins outright, wholesale, for
  each of the top-level keys involved (`agents`, `return_policy`, `source_resolution`, `policy_evaluation`,
  `support_ingress` are five separate top-level keys — `_carry_forward` operates at that granularity,
  not per-leaf). Net effect: **the operator's `policy_evaluation`/`support_ingress` edits are preserved
  by the same default that keeps everything else old** — they were never actually at risk here — **but
  the file's newer content nested inside `agents`/`return_policy`/`source_resolution` (the
  `support_response` block, the ship_via codes, the populated paths) is silently NOT adopted**, only
  logged as `packaged_configuration_not_adopted` for the affected keys, and the run prints `UNCHANGED`
  with no republish (because `merged == active_payloads` always holds on the undecidable path — it
  takes active's value for every key present in both). Running `--adopt-packaged` would fix the
  orphaned new content but would **also** clobber the operator's `policy_evaluation.enabled=true`/
  `support_ingress.nl_enabled=true`, since that flag takes the packaged file wholesale for every key
  with no exceptions. There is no way to adopt one without risking the other on this specific release
  lineage.

**(c) `allow_baseline_fallback` dev vs prod.** `runtime_loader.py:91`:
`allow_baseline_fallback=(environment in {"development","test"})`.
- **prod/staging:** `False` → any failure re-raises `RuntimeError` (`snapshot.py:186-187`) — cannot
  start silently on stale config.
- **dev/test:** `True` → failure logs a warning and serves `source="VERSION_CONTROLLED_BASELINE"` built
  directly from packaged files (`snapshot.py:78-107,188-196`).
Paired with `require_all_behavior_domains=(environment not in {development,test})`
(`runtime_loader.py:96`) — prod additionally refuses a release missing `AI_GATEWAY`/
`DEPENDENCY_SIMULATION` outright (`snapshot.py:151-164`). **`RuntimeConfigurationActivator.refresh()`
always calls `build_snapshot` with `allow_baseline_fallback=False` hard-coded** (`runtime_activation.py:252`)
— a running process re-checking for a new release never silently falls back to baseline, in any
environment; only the very first snapshot built at process startup can.

**(d) Direct-YAML readers bypassing the release** (grep basis: `yaml.safe_load`, `open(...yaml)`, and
the four named settings/paths — no undiscovered reader found for any of them):

| Reader | Reads | Runtime or tooling-only |
|---|---|---|
| `return_configuration.py:1990 load_return_configuration` | `returns/production.yaml` | **Runtime — baseline/fallback**, called at every startup as `build_snapshot`'s default argument. Also a **dev/test-only bypass** at `operations/associate_flow.py:406`, `operations/orchestrator.py:169`, `dynamic_knowledge/integration/runtime_factory.py:161` — each guarded `if settings.environment not in {"development","test"}: raise RuntimeError(...)` before falling through, so production can never take this branch |
| `ai/routing/tasks.py:381 load_ai_gateway_configuration` | `ai_gateway.yaml` | Same pattern; dev/test-only bypass guards at `ai/gateway/service.py:213-222`, `dependency_simulation/ai.py:88-94` |
| `dependency_simulation/configuration.py:81` | `dependency_simulation.yaml` | Same pattern; only call sites are the startup baseline load and the bootstrap CLI |
| `platform/system_store/manifest_loader.py:81` | `platform/system_store.yaml` | **Real runtime path, by explicit design exception** (§0/§1.5) — never carried by the Neo4j release at all |
| `dynamic_knowledge/config_loader.py:29` | `active-schema.return-order.yaml` | **Real runtime path — a separate configuration family with its own activation pointer**, not a bypass of the RETURN_PLATFORM release, a parallel sibling (§0) |
| `platform/reasoning/configuration.py:54 load_reasoning_configuration` | reasoning YAML | **Dead/unused** — only re-exported by `platform/reasoning/__init__.py:18,79`, no other call site found (`reasoning.yaml`'s actual live consumer is a different function; UNVERIFIED whether this specific loader is exercised by any test) |
| `data_platform/schema_registry.py:176 load_schema_registry` | `settings.schema_registry_path` | **Real runtime path**, unrelated to the return-configuration release; `@lru_cache(maxsize=8)` — the one real cache found in this area |
| `configuration/application/loader.py:275,300` | `manifest.yaml` + modules | **Mixed** — live only via Path C (`agent_configuration.py`, wired into `main.py:575-577`); dead via Path A (`compatibility.py`, no caller outside tests) |

No direct reader of `production.yaml`/`ai_gateway.yaml`/`dependency_simulation.yaml` bypasses the
release in production — every direct-read call site is either the startup baseline-load `build_snapshot`
may override, or explicitly gated to dev/test only and raises in production.

**(e) Restart required to adopt a new release?** Not strictly — but not "live" either.
`process_adoption.py:1-20`'s own docstring: *"`ACTIVATED != LIVE`. Promoting a release to RELEASED
moves the graph pointer; it does not move the API process, the five workers, or the model the Order
Agent actually calls."* The single reconciler, `RuntimeConfigurationActivator.refresh()`
(`runtime_activation.py:198-389`), is driven by FastAPI request middleware for the API process and by
`run_runtime_activation_loop`'s fixed 5-second poll timer for workers (`:452-479`) — `refresh()` itself
re-checks the graph head **at most once every 5 seconds per process**, under a lock (`:198-220`). So a
promoted release becomes visible to a given process within roughly 5 seconds of its next
poll/request — **no restart needed for an ordinary release change.** A restart (or at least a refused
adoption) **is** required only when the release changes an infra-owned `_RESTART_REQUIRED_SETTINGS`
field (`:71-85`): `refresh()` detects the mismatch and raises rather than applying it (`:306-316`),
leaving the process on its last-good release (the poll loop logs and retries next cycle rather than
crashing, `:474-478`). `process_adoption.py`'s `evaluate_release_adoption` (`:263-325`) reports `LIVE`
only once every required process class has **every live instance** reporting the activated
`(release_id, head_revision)` pair, `ACTIVATING` otherwise — because "promoted" and "fully adopted
fleet-wide" are different instants, bridged by this 5-second-per-process poll, not by a push.

**(f) Caching.** No `@lru_cache`, no TTL, no cross-process invalidation on the
`PinnedConfigurationSnapshot`/`ReturnPlatformConfiguration` path — it is a **process-local, mutable
slot** (`app.state.return_configuration_snapshot`, `main.py:621`) built once at startup and thereafter
swapped atomically only by `RuntimeConfigurationActivator.refresh()`'s 5-second-guarded poll
(`runtime_activation.py:373-389`, "these two assignments form the process-level activation boundary")
— not a cache in the memoization sense. Multi-instance disagreement is expected, not prevented: every
replica polls independently with no shared invalidation signal (a Configuration-API promotion pushes
nothing to running processes), so replicas can disagree for as long as their next 5-second poll takes;
`process_adoption.py` exists to make that window observable. The one genuine `@lru_cache` found in this
whole area is `data_platform/schema_registry.py:168` (`maxsize=8`, keyed by path, per-process, no TTL)
— a different configuration family, unrelated to the release mechanism.

**F-0084 — revised finding.** The original record (`evidence/orchestration/06_FINDINGS.json`) flags its
own recorded trigger ("republishes when the active release is not one it produced") as wrong, per its
`trigger_correction` field: a release the API produced was NOT republished on one restart and WAS on
another. **Resolved root cause: model round-trip normalisation, not producer identity.** The
Configuration API's merge-patch endpoint (`configuration/api/releases.py:263-278`, RFC 7396: a patch
value of `None` pops the key) **saves the raw patched dict**, not `model_dump()` of the validated model
(`releases.py:342-364`) — so a released payload can genuinely be missing an optional key (e.g.
`policy_evaluation.disabled_reason`) rather than carrying it as `null`. On the next bootstrap run,
`ReturnPlatformConfiguration.model_validate(merged_payload)` then `.model_dump(mode="json")`
(`bootstrap_graph_configuration.py:356-358,382`) **re-materialises the missing key as `null`**, so
`active_payloads == domain_payloads` (`:401-403`) is false by exactly that one leaf → republish, new
content-hashed `release_id`, head revision bump (`graph_repository.py:514-520`,
`created_by='linux-runtime-bootstrap'` regardless of host OS). The *next* restart round-trips
byte-identically against that now-canonical stored payload → `UNCHANGED`. **Rule: bootstrap republishes
and bumps the head revision whenever the stored `RETURN_PLATFORM` payload is not a pydantic-canonical
serialisation — any API merge-patch that deletes a key qualifies — and self-heals after one bump.**
**Compounding mechanism, also confirmed:** the API's create-release path
(`configuration/api/releases.py:205-243`) copies domain payloads from the active release but **never
calls `set_release_metadata`**, so every API-created release starts with no `PACKAGED_KEY_DIGESTS`
baseline — which is the mechanism behind the live-graph example quoted in §3(b) above, and is why the
`packaged_configuration_not_adopted` warning fires repeatedly on API-produced release lineages. A
separate promotion path, `configuration/application/release_promotion.py:243`, does copy
`active.metadata` forward — which of the two paths the operator UI actually exercises is UNVERIFIED
(not read in full this pass). No test in `backend/tests/test_graph_configuration_bootstrap.py` (11
tests, all read) exercises either the round-trip-normalisation trigger or the metadata-not-copied
compounding case — both UNVERIFIED at the test level, confirmed only by code reading. See CFG-B-D04.

---

## 4. BOOTSTRAP MATRIX

| Domain | Graph DB absent | Release absent | Key added in newer version | Operator value preserved? | Safe write allowed? | Test citation |
|---|---|---|---|---|---|---|
| RETURN_PLATFORM (bootstrap CLI) | `main()` has **no try/except around `driver.verify_connectivity()`** (`:236-244`) — an unreachable Neo4j propagates uncaught, script exits non-zero. UNVERIFIED by test. | `active is None` (`:246`) → publish packaged file verbatim as new release | New top-level key not in `active_payload`: adopted unconditionally, regardless of baseline (`:106-107`) | Yes, if a `PACKAGED_KEY_DIGESTS` baseline exists and the key's digest still matches; if no baseline, release wins for old keys by default only (not certainty), drop only logged | No unconditional overwrite except `--adopt-packaged` (opt-in, documented as the only such path) | `test_a_key_the_active_release_predates_is_adopted_from_the_packaged_file` (`test_graph_configuration_bootstrap.py:229-253`); operator-preservation: `:321-346`; no-baseline: `:421-449`; `--adopt-packaged`: `:453-469` |
| RETURN_PLATFORM (runtime load) | Prod: `RuntimeError`, process doesn't start (`runtime_loader.py:85`). Dev/test: falls back to in-memory repo → baseline snapshot | Prod: re-raises, refuses to start (`snapshot.py:186-187`). Dev/test: logs + serves `VERSION_CONTROLLED_BASELINE` | Absent from stored payload → pydantic fills the model default; runtime has no opinion, just validates whatever's stored | N/A — pure read | N/A — never writes | `test_snapshot_builder_uses_explicit_recovery_snapshot` (`test_graph_configuration.py:96-113`); `test_snapshot_builder_loads_published_graph_release` (`:115-143`); `test_production_snapshot_requires_every_behavior_domain` (`:146-170`). Neo4j-unreachable-in-production case: UNVERIFIED by test |
| AI_GATEWAY / DEPENDENCY_SIMULATION | Same shared-connection semantics as RETURN_PLATFORM | Same fallback rules; additionally prod refuses a release missing either domain outright regardless of `allow_baseline_fallback` (`snapshot.py:151-164`) | **No carry-forward/baseline mechanism at all** — re-read fresh from disk every bootstrap run (`:255-258`); a new key always ships on next run | N/A / always packaged-wins — no operator-edit write surface exists for these two domains | Always "safe" by construction, but also always overwrites — no operator control exists | `test_production_snapshot_requires_every_behavior_domain` covers missing-domain refusal only; the "always fresh, no carry-forward" behavior has no dedicated test found — UNVERIFIED |
| AGENT_MODULES (optional) | Same shared-connection semantics | Optional by design (`snapshot.py:28-35`) — absent → falls back to packaged manifest files via `AgentConfigurationService` | Not written by bootstrap at all (not in its `domain_payloads`, `:383-389`); only the Configuration API's Agents-edit flow writes it | Presumably yes (bootstrap never touches this domain) — UNVERIFIED, write path (`api/agents.py`) not read this pass | UNVERIFIED | UNVERIFIED — closest candidate `test_agent_configuration_releases.py`, not read this pass |

---

## 5. DEAD/UNUSED

Full leaf-by-leaf evidence for `returns/production.yaml` is in **PART_B1_production_yaml_deadkeys.md**
(headline corrections there: `extensions.*` and `feature_flags.*` are confirmed dead by two independent
passes — correcting this audit's own working premise that `feature_flags` was "the real feature
flags"; every top-level key does land in a pydantic field, so nothing is dropped at that level). This
section lists what's native to manifest modules, `ai_gateway.yaml`, and cross-cutting findings:

| Key(s) | File | Verdict | Evidence |
|---|---|---|---|
| `payload.*` beyond envelope, all 8 files | `agents/*.yaml` | DEAD (business logic); LIVE (Path C display only) | §1.2 |
| `payload.*`, all 4 files | `policies/*.yaml` | DEAD | §1.3, double-confirmed |
| `payload.stages/context_only_handoffs/direct_agent_calls_allowed` | `workflows/return_session.yaml` | DEAD | live stage sequence is the `DEFAULT_STAGE_SEQUENCE` Python constant |
| `payload.mode/...` + order_full's unmodeled extras | `sync/order_partial.yaml`, `sync/order_full.yaml` | DEAD | possible live duplicate at `data_platform/sync_pipelines.yaml` (§6, UNVERIFIED) |
| `payload.*` | `sources/sales_inv.yaml` | DEAD | live source config is `data_platform/sources.yaml` |
| `payload.*` | `mappings/sales_inv_order.yaml` | DEAD | live mapping is `data_platform/canonical_mappings.yaml` |
| `payload.*` | `graph/order_discovery.yaml` | DEAD | live schema is `dynamic_knowledge/active-schema.return-order.yaml` |
| `migration_mode`, `migration_lock_required` | `platform/system_store.yaml` | **DEAD on the live loader specifically** (silently dropped, `extra="ignore"`) | §1.5, CFG-B-D06 |
| `features`/`platform` singleton-extraction branch | `compatibility.py:345-357` | DEAD (no-op + unreachable) | CFG-B-D02 |
| `AiConfig.routes/providers/safety/interception` | `configuration/domain/ai.py:29-32` | DEAD, doubly | CFG-B-064 |
| `modelContexts[].source` | `ai_gateway.yaml` | READ_BUT_NO_EFFECT (round-trips into releases) | §1.6 |
| `platform/reasoning/configuration.py::load_reasoning_configuration` | (function, not a config key) | DEAD — no caller besides its own package re-export | §3(d) |
| `live_validation/data_assets.sampling.yaml` | whole file | DEAD (unloaded) | not in `manifest.yaml`; README confirms |
| `extensions.*` (5 flags), `feature_flags.*` (4 flags), `agents.<n>.` dead-knob fields, `clarification_policy.phrasing_owner` (likely) | `returns/production.yaml` | see PART_B1 | full grep evidence there |

Not independently re-verified (flagged, not silently assumed): `bay.*`, `return_case.*` leaves,
`integrations.*.enabled/.authority`, `support_ingress.*`/`support_resolver.*` individual leaves,
`return_eligibility_policy.*` internal rule leaves against the evaluator, `discovery`'s
identification/progressive-discovery sub-trees leaf-by-leaf — all in PART_B1's own UNVERIFIED list.

---

## 6. DUPLICATES

| Group | A | B | Same meaning? | Same consumer? | Keep | Action |
|---|---|---|---|---|---|---|
| Agent config, two systems, mismatched names | `agents/*.yaml` (8 files: `bay_allocation`, `learning`, `return_session_orchestrator`, `graph_schema_design`, ...) | `production.yaml`'s `agents:` block (7 entries: `bay_assignment`, `feedback_learning`, `support_response`, ...) | Nominally yes; A inert (§1.2), B live (CFG-B-092). Sets don't fully overlap; `bay_allocation`↔`bay_assignment`, `learning`↔`feedback_learning` are renamed pairs | No | B | Rewire or retire A; reconcile the mismatched names; false-cognate field names (§0) compound the confusion risk in the Agents Control Center UI |
| System store config, two loaders | `compatibility.py:280-317` → `SystemStoreConfig` (dead) | `manifest_loader.py` direct loader (live) | Same file, same intent | No — B's docstring deliberately avoids importing the domain model (R2a) | B | A is unreachable dead code; B drops `migration_mode`/`migration_lock_required` that A still models — CFG-B-D06 is the actionable half |
| Two "RuntimeSnapshot"-named things | `domain/release_model.py::RuntimeSnapshot` (dead) | `snapshot.py::PinnedConfigurationSnapshot` (live) | No — different shapes; also collides with unrelated `dynamic_knowledge/graph/generation.py::ActiveRuntimeSnapshot` | N/A | N/A | Naming-collision documentation defect, CFG-B-D01 |
| Sync config, possibly two systems | `sync/order_partial.yaml`, `sync/order_full.yaml` (dead, §5) | `data_platform/sync_pipelines.yaml` (non-manifest, plausibly live) | Likely | UNVERIFIED — consumer not traced this session (outside declared file scope) | Presumably B | Needs Part A/C cross-check |
| `ai_gateway.yaml` vs `reasoning.yaml` — **not a duplicate** | `ai_gateway.yaml.tasks.<TASK>.*` (model routing/tier/prompt) | `reasoning.yaml` (checkpoint store/retention/execution-bounding) | **No** — different domains entirely | N/A | N/A | Initial hypothesis ruled out by reading both files; `reasoning.yaml` → `platform/reasoning/configuration.py`, a separate live subsystem (though its own loader function is itself dead per §3d — worth a separate look, not a duplicate issue) |
| AI retry/concurrency/rate config, partial overlap | `ai_gateway.yaml` `retry.*`/`rateLimits.*`/`providerLimits.*` (structured, live) | `settings.py:234-238` `ai_timeout_seconds`, `ai_global_timeout_seconds`, `ai_max_attempts_per_provider`, `ai_max_concurrency`, `ai_requests_per_minute` | Partial — both govern AI call pacing at different granularity | Mixed: `ai_max_attempts_per_provider`/`ai_max_concurrency` **DEAD** (no reader anywhere). `ai_timeout_seconds`/`ai_global_timeout_seconds` read at `final_dispatch.py:695,850-852` (duration, complementary to `retry`'s count — not a true duplicate). `ai_requests_per_minute` read at `operations/repository.py:1620` (`consume_ai_quota`, called from `ai/gateway/service.py:542`) — a flat Mongo-backed quota bucket, separate machinery from the in-memory `rateLimits`/`providerLimits` gate | `ai_gateway.yaml`'s structured config, for the overlapping concepts | Delete the two dead Settings fields (CFG-B-D07); rename/document the other three so their names don't imply they override the yaml's `retry`/`rateLimits` |
| `ai_gateway.yaml` vs `settings.py` model lists — **not a duplicate** | `ai_gateway.yaml.modelContexts` (context-window overrides, 1 entry) | `settings.py:275-327` per-provider/tier candidate model pools, env-injected | No — `ai_gateway.yaml` never lists which models exist; `settings.py`'s lists are the actual candidate pool, consumed by `ai/routing/routes.py` | N/A | N/A | Complementary, not duplicate; explicitly ruled out |
| Stale prompt-version setting | `settings.py:254 ai_prompt_version = "return-eligibility-v1"` | `ai_gateway.yaml` task `RETURN_ELIGIBILITY_V1.promptVersion: return-eligibility-v2` | Same concept, A stale/dead | `grep -rn "ai_prompt_version"` → only the declaration, no reader | B | Delete `Settings.ai_prompt_version` (CFG-B-D07) — predates and disagrees with (`v1` vs `v2`) the live mechanism |
| `production.yaml` `features`/`platform` key-name mismatch | `compatibility.py:346-347` expects `features`/`platform` | real key is `feature_flags`, no `platform` key at all | Related, not a true duplicate — dead consumer looking for keys the producer never had | Path A only (dead anyway) | `feature_flags` via Path B | Part of CFG-B-D02 |

---

## 7. DEFECTS

| ID | Severity | Files | Root cause | Current behavior | Expected | Fix | Test |
|---|---|---|---|---|---|---|---|
| CFG-B-D01 | P2 | `compatibility.py`, `application/adapters.py`, `domain/release_model.py`, `application/precedence.py`, `application/validator.py` | The entire manifest-driven "canonical snapshot"/precedence-evaluator system the design doc and README describe as authoritative is never called by the running application — test-only | Manifest module system has no production effect beyond Path C's narrow Agents-editing surface | Wire it into a real consumer, or document plainly that it's a design/editing scaffold, not a runtime path | Add an integration test asserting a real request path changes when a manifest module's payload changes, or remove the runtime-authority claim from `backend/config/README.md` |
| CFG-B-D02 | P1 | `compatibility.py:345-357` | Reads `production.yaml`'s `features`/`platform` keys; the file has neither (real key is `feature_flags`, no `platform` key) | `FeaturesConfig`/`PlatformConfig` on the already-dead `RuntimeSnapshot` are always empty/default; no test catches this against the real fixture | Rename the lookup to `feature_flags`, or remove the branch with D01 | none found asserting real content |
| CFG-B-D03 | P3 | `agents/*.yaml` vs `production.yaml`'s `agents:` block | Two independently-keyed "agents" catalogues, one dead one live, non-matching names, false-cognate field names on the dead one | Operator edits the dead copy via the Configuration UI believing it changes behavior | Rename/reconcile the two, or repoint the Agents UI at the live block | none found |
| CFG-B-D04 | P1 (revised from "confirms F-0084" — root cause now precisely identified, upgraded severity given a live-graph example is currently in this exact state) | `configuration/api/releases.py:263-278,342-364,205-243`; `cli/bootstrap_graph_configuration.py:356-358,382,401-403` | (1) The merge-patch API saves the raw patched dict rather than `model_dump()` of the validated model, so a deleted-key payload doesn't round-trip byte-identically through bootstrap's own canonicalization, causing spurious republish/head-bump. (2) The API's create-release path never calls `set_release_metadata`, so every API-created release lineage has no `PACKAGED_KEY_DIGESTS` baseline, forcing every subsequent bootstrap onto the undecidable release-wins-for-everything branch | On this deployment's own RELEASED release right now: `agents.support_response`, five `return_policy.return_method_derivation.ship_via_methods` codes, and populated `source_resolution.*_paths` in the packaged file are silently NOT adopted (only logged); `--adopt-packaged` would fix that but risks clobbering the operator's `policy_evaluation.enabled=true`/`support_ingress.nl_enabled=true` in the same stroke | (1) Bootstrap republish should be gated on semantic diff, not raw-dict equality; canonicalize on save or on compare. (2) API create-release should copy `active.metadata` forward (as `release_promotion.py:243`'s path already does) | Merge-patch that deletes an optional key, then two bootstrap runs; assert head revision moves by 0 not 1. Create a release via the API create-release endpoint, assert its metadata carries a `PACKAGED_KEY_DIGESTS` baseline. Neither exists today |
| CFG-B-D05 | P3 | `configuration/domain/ai.py:29-32` | `AiConfig` declares `routes`/`providers`/`safety`/`interception` fields absent from both the real yaml and the live `AIGatewayConfiguration` schema | Vestigial fields on an already-dead model | Remove with the rest of Path A cleanup, or document as reserved | none found |
| CFG-B-D06 | P2 | `platform/system_store/manifest_loader.py` (`_SystemStoreConfigPayload`) vs `platform/system_store.yaml` | The live loader's local model uses `extra="ignore"` and has no `migration_mode`/`migration_lock_required` fields | An operator setting either in the packaged file has zero effect on live bootstrap/migration-safety behavior, with no warning | Add the two fields to `_SystemStoreConfigPayload` and wire them in, or remove them from the yaml/Path A model so the file doesn't claim a control that doesn't exist | none found |
| CFG-B-D07 | P3 | `configuration/settings.py:236-237,254` | `ai_max_attempts_per_provider`, `ai_max_concurrency`, `ai_prompt_version` have zero readers; superseded by `ai_gateway.yaml`'s structured `retry`/`providerLimits` and per-task `promptVersion` respectively | Three env-configurable settings that do nothing | Remove, or document as an intentional future override hook | none found |

---

STATUS: PARTIAL (near-complete). Evidence-complete from direct reading, merged across this session and
three cross-verified background passes: §0 headline finding (three-plus-one loading paths), §1 full
inventory of every manifest module and `ai_gateway.yaml` (production.yaml's full leaf detail lives in
the companion PART_B1 file), §2 all load paths including the two sibling systems (Dynamic Knowledge
schema activation, system-store bootstrap), §3(a)-(f) each with exact code citations plus a live
test-suite cross-check and a corrected, code-confirmed root cause for the F-0084 finding (including a
concrete answer, backed by a real diff against this deployment's own Neo4j graph, to whether an
operator-published release's bootstrap re-run adopts new packaged keys or preserves operator edits —
answer: for this deployment's specific release lineage, it does neither cleanly, because the lineage
has no digest baseline at all), §4 bootstrap matrix with test citations for 3 of 4 domain rows, §5
dead/unused (grep-evidenced for every manifest module, `ai_gateway.yaml`, and cross-references into
PART_B1 for `production.yaml`), §6 (8 duplicate/near-duplicate/ruled-out groups), §7 (7 defects, one
upgraded to P1 with a live-graph example).

Explicitly flagged remaining gaps: whether the operator Configuration UI's release-creation flow uses
`releases.py`'s no-metadata-copy path or `release_promotion.py:243`'s metadata-preserving path
(UNVERIFIED, would resolve whether CFG-B-D04's compounding mechanism is universal or one specific
flow); FastAPI middleware's exact call site for `RuntimeConfigurationActivator.refresh()` (referenced
but not located precisely); `AI_GATEWAY`/`DEPENDENCY_SIMULATION`'s "always packaged-wins" behavior
lacks a dedicated test (flagged, not assumed passing); AGENT_MODULES bootstrap-survival semantics
(§4, UNVERIFIED, `api/agents.py` write path and `test_agent_configuration_releases.py` not read this
pass); and the `sync/order_*.yaml` vs `data_platform/sync_pipelines.yaml` overlap (flagged for a
Part A/C cross-check, outside this part's declared file scope).
