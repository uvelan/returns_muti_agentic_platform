# PART E — Hardcoded Configuration Candidates & Configuration Test Coverage (CFG-E)

Status: DRAFT (in progress) | Scope: `backend/src/return_platform` (excl. tests) for Tasks 1 & 2 frontend; `backend/tests/` + `frontend/src` tests for Task 3.
Backend not running; all statements are static-analysis, file:line cited. No code edited.

---

## Task 1 — Hardcoded configuration candidates (backend business logic)

| ID | file:line | Literal | What it controls | Already configurable elsewhere? | Recommendation |
|---|---|---|---|---|---|
| CFG-E-001 | `dynamic_knowledge/order_agent/search_strategy.py:61` | `MAX_CACHED_CANDIDATES = 25` | Max ranked order-search candidates kept per turn (pagination window; also feeds fulltext `candidate_limit` default at line 132 and fulltext plan cap at line 400) | No — module constant, not read from `IdentificationCatalogue`/Settings | MOVE_TO_SCHEMA — this is exactly the "25-row limit" a deployment with a larger/smaller candidate pool would want to tune per identification catalogue |
| CFG-E-002 | `dynamic_knowledge/order_agent/search_strategy.py:62` | `RESULT_PAGE_SIZE = 5` | How many candidates are shown to the associate/model per turn before "show more" | No | MOVE_TO_SCHEMA — UX/business tuning knob, same rationale as CFG-E-001 |
| CFG-E-003 | `dynamic_knowledge/order_agent/search_strategy.py:127-133` | `CustomerFulltextPolicy` defaults: `max_edit_distance=2`, `one_edit_min_token_length=4`, `two_edit_min_token_length=8`, `relative_score_floor=0.55` | Fuzzy-match tolerance and narrowing floor for the customer full-text index | Docstring (line 115-117) says defaults "mirror `ProgressiveDiscoveryConfiguration`" — i.e. a config-sourced policy object is expected to override these; UNVERIFIED whether `ProgressiveDiscoveryConfiguration` is actually wired end-to-end (not traced further — out of scope overlap with Part B/C's release-domain work) | KEEP_IN_CODE as defaults (fallback-safety is the documented intent), but flag for Part B/C to confirm `ProgressiveDiscoveryConfiguration` is actually reachable from the release; if not, MOVE_TO_RELEASE |
| CFG-E-004 | `dynamic_knowledge/order_agent/search_strategy.py:128` | `index_name: str = "customer_name_search_v2"` | Which Neo4j fulltext index the customer search reads | Same as CFG-E-003 — same policy object | KEEP_IN_CODE (see CFG-E-003) |
| CFG-E-005 | `dynamic_knowledge/order_agent/coordinator.py:86` | `_RECURSION_LIMIT = 256` | LangGraph hard recursion ceiling for one agent invocation | No, but derived: comment says it must comfortably exceed `policy.max_reasoning_steps` (itself configurable, "up to 32") | KEEP_IN_CODE — technical safety net expressed as a multiple of a already-configurable business ceiling, not itself a policy value |
| CFG-E-006 | `ai/providers/openai_compatible.py:61` | `if "nemotron" in self.model.lower():` | Whether the provider requests strict `json_schema` mode or falls back to plain `json_object` mode | No — string match on model id, not a per-model capability flag anywhere in `ai_gateway.yaml`/`AIGatewayConfiguration` | MOVE_TO_SCHEMA — a per-model `supportsJsonSchema: bool` capability on the model/route entry would let an operator onboard a new NVIDIA model without a code change; today any non-Nemotron NVIDIA model that also lacks schema support silently gets wrong behavior |
| CFG-E-007 | `configuration/process_adoption.py:227` | `timedelta(seconds=max(1, ttl_seconds) * 3)` | Adoption-report expiry: 3x the reported TTL | Partially — `ttl_seconds` itself is a parameter (traced to `worker_readiness_ttl_seconds` Setting elsewhere), but the `* 3` multiplier is a fixed ratio | KEEP_IN_CODE — well-documented derived ratio ("one missed report is a slow poll, three is a process that is gone"), not an independent policy value |
| CFG-E-008 | `configuration/runtime_activation.py:184,455,522,619,692` | `refresh_interval_seconds: float = 5.0` (five separate default params, same value) | How often a worker process polls Neo4j for a newly promoted/adopted configuration release | **No.** Traced the only production caller, `workers/integration_outbox.py:186` `build_worker_runtime_activation(...)`, which does **not** pass `refresh_interval_seconds`, so every worker always runs the hardcoded 5.0s default. No `Settings` field or release key feeds it. | MOVE_TO_ENV — this is precisely a config-propagation-latency knob (how fast a worker notices a release change), directly relevant to this audit; add `PLATFORM_RUNTIME_REFRESH_INTERVAL_SECONDS` |
| CFG-E-009 | `bootstrap/reconciler.py:50` | `await asyncio.sleep(5)` | `ConfigurationReconciler.run()` poll cadence (pointer check / epoch drain / heartbeat loop) | No — bare literal, no parameter, no Settings field | MOVE_TO_ENV — same class of knob as CFG-E-008, currently not even a constructor parameter (harder to override than CFG-E-008) |
| CFG-E-010 | `dynamic_knowledge/knowledge/cypher_compiler.py:75-76` | `FULLTEXT_GENERATION_HEADROOM = 10`, `FULLTEXT_MAX_INDEX_ROWS = 2_000` | Over-fetch multiplier and hard cap for fulltext index reads (mitigates stale-generation dilution) | No | KEEP_IN_CODE — extensively documented as a mitigation tied to a specific defect and to `housekeeping/graph_generations.py`'s retention mechanics, not an independent business policy |
| CFG-E-011 | `dynamic_knowledge/order_agent/planner.py:137` | `_CONFIGURED_SCORE_CEILING = 0.95` | Ceiling a catalogue-configured `clarification_priority` may reach before a measured (profiled) signal outranks it | No | KEEP_IN_CODE — internal ranking-algorithm calibration constant, heavily justified in-line; moving it to config without moving the whole scoring formula would be cosmetic |
| CFG-E-012 | `dynamic_knowledge/order_agent/planner.py:158` | `_UNKNOWN_CEILING = 0.5` | Ceiling for a field the current candidate set carries no evidence for | No | KEEP_IN_CODE — same reasoning as CFG-E-011 |
| CFG-E-013 | `workflows/return_case_workflow.py:111,114,118` | `_PERSIST_TIMEOUT=30s`, `_DRAFT_TIMEOUT=5m`, `_SYNC_TIMEOUT=2m` | Temporal `start_to_close_timeout` for persistence / support-draft / sync activities | No — module constants, not read from `ReturnCaseTimings` or Settings | MOVE_TO_RELEASE — activity timeouts are classic per-deployment tuning (slower downstream Mongo/Support API in one environment vs. another); currently uniform across all deployments |
| CFG-E-014 | `workflows/return_case_workflow.py:122,126,133` | `_PERSIST_RETRY=RetryPolicy(maximum_attempts=5)`, `_DRAFT_RETRY`/`_BEST_EFFORT_RETRY=RetryPolicy(maximum_attempts=2)` | Temporal retry attempt counts for the same three activity classes | No | MOVE_TO_RELEASE — same rationale as CFG-E-013; retry budget is an operational dial, not domain logic |
| CFG-E-015 | `workflows/return_case_workflow.py:993-994` | `_DETAILS_POLL_MIN_SECONDS=15`, `_DETAILS_POLL_MAX_SECONDS=180` | Bounds on the adaptive poll step used while waiting for `return_details_wait_seconds` (line 2183-2185: `step = clamp(deadline//10, 15, 180)`) | No | KEEP_IN_CODE — derived clamp bounds around an already-configurable deadline (`return_details_wait_seconds`), changing the deadline already changes effective cadence within these bounds |
| CFG-E-016 | `workflows/return_case_workflow.py:255` | `_TRACKED_SUPPORT_EVENT_IDS = 64` | Size of the dedup window for support event ids tracked per case | No | KEEP_IN_CODE — internal memory-bound implementation detail, not a business-visible policy |
| CFG-E-017 | `workflows/order_discovery_workflow.py:45` | `_TURN_ACTIVITY_TIMEOUT = timedelta(minutes=10)` | `start_to_close_timeout` for one order-discovery reasoning turn's activity | No | MOVE_TO_RELEASE — a slow AI provider or a deliberately long-running turn budget (already the domain of `policy.max_reasoning_steps`) is exactly the kind of thing a deployment would want to raise without a redeploy |
| CFG-E-018 | `workflows/order_discovery_workflow.py:58` | `_CONVERSATION_IDLE_TIMEOUT = timedelta(days=7)` | How long an abandoned order-discovery conversation workflow stays open before Temporal considers it stale | No | MOVE_TO_RELEASE — a retention/idle policy, plausibly different per customer's support SLAs |
| CFG-E-019 | `dynamic_knowledge/order_agent/temporal_grounding.py:139-141` | `last_7_days`/`last_30_days` window widths (`timedelta(days=7)`, `timedelta(days=30)`) inside the fixed `RELATIVE_DATE_PHRASES` vocabulary | The date ranges substituted for these two named phrases in the per-turn grounding block shown to the reasoning model | No | KEEP_IN_CODE — tightly coupled to the fixed English phrase vocabulary (`RELATIVE_DATE_PHRASES`) and prompt wording; changing the day-count without changing the phrase name would make the model's own vocabulary lie about itself. Flagged per BRIEF's explicit "last 30 days" example, but recommend against moving in isolation |
| CFG-E-020 | `data_platform/operational_generation/adapters/direct_mongodb.py:47` | `os.environ.get("PLATFORM_DIRECT_OPERATIONAL_CREDENTIALS")` | `is_ready()` readiness gate for the direct-Mongo operational-generation adapter | **No — bypasses `Settings` entirely.** Raw `os.environ.get`, presence-only check, not a typed field | MOVE_TO_ENV (into `Settings`) — an env read outside `settings.py` is invisible to the Settings inventory (Part A) and to any config-audit tooling that walks `Settings` fields; should be a proper `Settings` bool/secret field |
| CFG-E-021 | `data_platform/operational_generation/adapters/platform_domain_api.py:46` | `os.environ.get("PLATFORM_DOMAIN_API_CREDENTIALS")` | `is_ready()` readiness gate for the domain-API operational-generation adapter | Same issue as CFG-E-020 | MOVE_TO_ENV — same reasoning |
| CFG-E-022 | `operations/seed_manifest.py:64-71` | `os.getenv(environment_variable, "")` (env var name itself is data-driven from the seed manifest JSON's `runtimeOptions.recordLimitEnvironmentVariable`) | Seed-data record-count cap for dev/test seeding | Partially — the *name* of the env var and the minimum are already config-driven via the seed manifest JSON; only the *read* bypasses `Settings` | KEEP_IN_CODE — this is dev/test seed tooling, not production business logic, and the indirection is already externalized to the seed manifest; low audit value |
| CFG-E-023 | `ai/pricing.py` (module docstring, not a literal) | N/A — checked because the BRIEF calls out "ranking weights" and hardcoded rates as a class of risk | Per-model $/M-token pricing | **Yes, explicitly** — `AIGatewayConfiguration.pricing`, released and checksummed (see file header comment) | Not flagged — deliberately excluded, listed here to show it was checked |
| CFG-E-024 | `operations/support_template_draft.py:278` | `colour="Blue"` and surrounding sample order/customer literals | Preview-only sample payload for template-rendering screens | N/A (not business logic — a fixture for the template preview UI, never touches a real case) | Not flagged |
| CFG-E-025 | `configuration/return_configuration.py:813-860` (shipment ladder rungs), `operations/return_support/resolution_ladder.py` (`RUNG_FACTS`/`RUNG_GRAPH`/`RUNG_TOOL`) | Shipment-status ladder rungs, resolution-ladder stage names | "as the operator declares it" (line 813) — already sourced from the release | Already configurable elsewhere (return configuration release) | Not flagged |
| CFG-E-026 | `dynamic_knowledge/order_agent/graph_nodes.py:646,728,886,902,919`, `identification.py`, `planner.py` | `policy.max_reasoning_steps`, `policy.max_graph_queries_per_turn`, `policy.max_targeted_syncs_per_turn`, `policy.max_clarifications` | Per-turn/per-conversation agent budgets | Already configurable (an `AgentPolicy`/release-sourced object; not a code literal) | Not flagged — verified these are `policy.*` attribute reads, not hardcoded numbers, despite the BRIEF's suggestion to look for "step ceilings / max turns" in these files |
| CFG-E-027 | `ai/routing/selection.py`, `ai/gateway/final_dispatch.py` | Circuit-breaker thresholds, backoff, rate limits | Already configurable (`AIGatewayConfiguration.circuitBreaker`/`.retry`/`.rateLimits`) — see PART_B, confirmed LIVE with grep evidence there | Already configurable elsewhere (ai_gateway.yaml, Part B) | Not flagged — re-grepped for hardcoded fallback constants in both files; found none (`_ALLOW_VERDICT`, `_TERMINAL_FOR_ROUTE` etc. are enum/verdict singletons, not thresholds) |
| CFG-E-028 | Repo-wide grep for `6144` (BRIEF's example token-cap literal) | — | — | — | UNVERIFIED / NOT FOUND — `grep -rn "6144" backend/src frontend/src backend/config` returned zero matches; this literal does not exist in the current tree (BRIEF example may be stale or refer to a different revision) |
| CFG-E-029 | Repo-wide grep for hardcoded `http://`/`https://` integration hosts in business logic (excl. `settings.py`, providers reading from `settings.*`) | — | — | — | Not flagged — zero matches outside `secrets/vault.py:84`'s scheme-prefix *check* (`"http://"`/`"https://"` as string prefixes to validate against, not a target host) |
| CFG-E-030 | `dynamic_knowledge/lifecycle/handle.py:50` | `DEFAULT_READ_LEASE_TTL_SECONDS = 900` | Default TTL for a graph read-lease handle when no explicit TTL is supplied | UNVERIFIED whether any caller overrides this — not traced further (time-boxed) | MOVE_TO_ENV (tentative) — flagged for a follow-up pass to confirm whether callers always override; if never overridden, same class of issue as CFG-E-008 |

### Deliberately NOT flagged (representative sample, not exhaustive)

- `ai/pricing.py` per-model rates — explicitly sourced from the released `AIGatewayConfiguration.pricing` (module docstring is explicit about this design choice).
- `dynamic_knowledge/order_agent/planner.py` `_CONFIGURED_SCORE_CEILING`/`_UNKNOWN_CEILING` — algorithm-calibration constants with multi-paragraph in-line justification tied to a specific measured regression; moving only the number without the formula would not give an operator a meaningful lever.
- `configuration/process_adoption.py`'s `* 3` heartbeat-expiry multiplier — a documented ratio derived from an already-configurable TTL, not an independent value.
- Shipment-status ladder rungs (`configuration/return_configuration.py:813+`) and resolution-ladder stage constants — the ladder itself is operator-declared in the release; the stage *names* (`RUNG_FACTS` etc.) are fixed algorithm structure, not data.
- `policy.max_reasoning_steps` / `max_graph_queries_per_turn` / `max_targeted_syncs_per_turn` / `max_clarifications` in `graph_nodes.py`/`coordinator.py` — verified these are attribute reads off a policy object, not literals, despite being exactly the "step ceilings / max turns" class the BRIEF asked to check.
- `dynamic_knowledge/knowledge/cypher_compiler.py` fulltext headroom/cap — documented mitigation tied to a specific defect and to housekeeping retention, not a standalone policy knob.
- `env.ts`'s `VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED` — correctly implemented env-driven feature flag; not a candidate, cited only to show the pattern was checked.

---

## Task 2 — Frontend hardcoded configuration (`frontend/src`)

| ID | file:line | Literal | What it controls | Already configurable elsewhere? | Recommendation |
|---|---|---|---|---|---|
| CFG-E-031 | `api/casePanel.ts:84` | `PANEL_POLL_INTERVAL_MS = 10_000` | Case-detail panel poll cadence | No | MOVE_TO_ENV — a `VITE_*` build-time override would let a deployment trade freshness for API load without a rebuild-per-behavior-change; low urgency |
| CFG-E-032 | `api/cases.ts:361` | `CASE_POLL_INTERVAL_MS = 10_000` | Case-list poll cadence (skipped when lifecycle is terminal, line 385) | No | KEEP_IN_CODE — same value as CFG-E-031, deliberately kept in sync; moving one without the other risks visible drift |
| CFG-E-033 | `domains/ai/AiControlCenterPage.tsx:857-858` | `setInterval(..., 15_000)` | UI re-render tick for interception-row expiry, deliberately matched to the query poll cadence below | No | KEEP_IN_CODE — explicitly documented as matched to `refetchInterval: 15_000` (line 874); the two must move together, and neither is a business policy, just UI freshness |
| CFG-E-034 | `domains/ai/AiControlCenterPage.tsx:874` | `refetchInterval: 15_000` | AI interception queue poll cadence | No | MOVE_TO_ENV (paired with CFG-E-033) |
| CFG-E-035 | `domains/ai/AiControlCenterPage.tsx:883` | `refetchInterval: 60_000` | AI interception history (terminal records) poll cadence | No | KEEP_IN_CODE — history view, low staleness cost |
| CFG-E-036 | `domains/shipments/ShipmentConsolePage.tsx:156` | `refetchInterval: 15_000` | Shipment console poll cadence | No | MOVE_TO_ENV — operationally similar to case/support polling; candidate for one shared constant |
| CFG-E-037 | `features/graph-analyzer/analyzerQueries.ts:27,36` | `refetchInterval: ... ? 1_500 : false` | Graph-analyzer run/status poll cadence while a run is `RUNNING`/`PREPARING` | No | KEEP_IN_CODE — an internal ops tool for schema analysis, not customer-facing; 1.5s is deliberately tight for a short-lived synchronous-feeling operation |
| CFG-E-038 | `domains/returns/panes/casePanel/useDraftEditor.ts:56,262` | `AUTOSAVE_DELAY_MS = 800` | Debounce delay before an edited draft autosaves | No | KEEP_IN_CODE — UX debounce tuning, not a deployment/customer policy |
| CFG-E-039 | `hooks/CapabilityProvider.tsx:25` | `staleTime: 5 * 60 * 1000` | How long the caller's RBAC capability set is cached client-side before refetch | No | MOVE_TO_ENV (tentative) — a security-adjacent staleness window (how quickly a revoked capability is noticed in the UI); worth a deliberate per-deployment choice rather than a hardcoded 5 minutes |
| CFG-E-040 | `main.tsx:38` | `staleTime: 30_000` | Global React Query default staleTime | No | KEEP_IN_CODE — a sane global default; per-query overrides already exist where it matters (CFG-E-031 through 037) |
| CFG-E-041 | `components/RuntimeConfigProvider.tsx:19` | `staleTime: Infinity` | Runtime config snapshot query never auto-refetches | N/A — deliberate ("Infinity" is a documented design choice for a value that only changes via explicit reload) | Not flagged |
| CFG-E-042 | `hooks/useElapsedSeconds.ts:38` | `setInterval(write, 1000)` | 1-second UI tick for elapsed-time displays | No | KEEP_IN_CODE — display-only, not a policy |
| CFG-E-043 | `env.ts:66-70` | `VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED` (default `false`) | Whether the data-source credential-reveal UI is enabled | **Yes — this is the correct pattern.** Build-time `VITE_*` flag, validated at boot, default closed | Not flagged — cited as the one legitimate frontend feature toggle found |
| CFG-E-044 | `vite.config.ts:56-58` | `port: 5173`, `host: "0.0.0.0"`, `strictPort: true` | Dev server bind address/port | No, but dev-only tooling, not shipped | KEEP_IN_CODE — not production config |
| CFG-E-045 | `vite.config.ts:15-30` (`FRONTEND_BACKEND_TARGET`) | Required env var, no literal default (`throw` if absent) | Dev-server proxy target for `/api`, `/data-console/v1`, `/health` | **Yes** — `.env.example:306` documents `FRONTEND_BACKEND_TARGET=http://localhost:8000` as the pattern | Not flagged — correctly required, no hardcoded fallback host |

Frontend polling summary: no dedicated "support queue poll" constant was found under a `domains/support/` path — the support/interception queue polling that exists lives in `AiControlCenterPage.tsx` (CFG-E-034/035) and `CasePanel.tsx`/`ReturnCopilotPage.tsx` (function-based `refetchInterval`, not a bare literal — see `panelRefetchInterval`/`caseRefetchInterval` in `api/casePanel.ts`/`api/cases.ts`, which resolve to CFG-E-031/032).

---

## Task 3 — Configuration test coverage map

### `backend/tests/configuration/` inventory

| Test file | What is asserted (1 line each) |
|---|---|
| `test_agent_configuration_releases.py` | Agent manifest edits go through the proposal/governance path and publish a release with domains |
| `test_ai_credential_configuration.py` | Inline key vs. vault-reference precedence per provider/environment; production refuses `-change-me` dev secrets (`Settings` field validators) |
| `test_canonical_application.py` | `LegacyCompatibilityAdapter.build_canonical_snapshot` behavior (confirmed by Part B as test-only/unused at runtime) |
| `test_canonical_config_api.py` | Secret masking (vault refs not masked, resolved secret-key values masked, case/convention-insensitive, nested structures scrubbed); canonical router is versionless; release lifecycle is the only mutation surface; canonical promotion delegates; every canonical response is scrubbed |
| `test_configuration_health.py` | (not read in detail — file exists, name implies health/readiness of configuration subsystem) |
| `test_copilot_agent_binding.py` | (not read in detail — agent binding to copilot config) |
| `test_epoch_not_visible_before_all_module_commits.py` | An epoch is not visible to reads until every participating module has committed |
| `test_late_restart_required_aborts_all.py` | A late `RESTART_REQUIRED` verdict from one participant aborts the whole activation, not just that module |
| `test_pinned_release_resolves_after_handle_recreation.py` | A pinned release id still resolves correctly after the runtime-configuration handle is recreated |
| `test_reconciler_delegates_to_reconfiguration_coordinator.py` | `ConfigurationReconciler` delegates decision-making to `ReconfigurationCoordinator` rather than reimplementing it |
| `test_reconfiguration_protocol.py` | The multi-module reconfiguration commit protocol itself (2-phase-like commit across participants) |
| `test_release_checksum_is_verified.py` | A release's checksum is verified before it is trusted/served |
| `test_release_transitions_are_single_sourced.py` | Every module reads the same shared transition table (no module hand-rolls its own DRAFT→VALIDATED→RELEASED rules); unknown states permit nothing; RELEASED is terminal; the repository itself enforces the shared table |
| `test_requests_never_observe_mixed_release_during_adoption.py` | An in-flight request never sees config from two different releases mixed together during a promotion/adoption window |
| `test_return_method_requirements_configuration.py` | Return-method requirement rules load/validate from configuration |
| `test_selection_vocabulary.py` | (not read in detail — likely AI route/task selection vocabulary validation) |
| `test_support_ai_gateway_tasks.py` | (not read in detail — support-domain AI gateway task wiring) |
| `test_support_gate_configuration.py` | Support-handoff gate configuration parsing/validation |
| `test_support_ingress_configuration.py` | Support ingress configuration parsing/validation |
| `test_support_resolver_configuration.py` | Support resolver (resolution ladder) configuration parsing/validation |
| `test_support_template_configuration.py` | Template placeholder/formatter/source allowlists refused when unknown; every allowlisted formatter accepted; dunder/method attribute access refused; unresolvable placeholders refused; default-variant-must-exist; duplicate variant/field ids refused; per-record section structure; `production.yaml` carries its three variants |

### Bootstrap graph configuration (`backend_scripts/bootstrap_graph_configuration.py`) — required scenario checklist

| Scenario (from BRIEF) | Covered? | Test |
|---|---|---|
| Empty graph (no active release at all) | **GAP** — every test double in `test_graph_configuration_bootstrap.py` (`_Repository`, `_CarryForwardRepository`) returns an existing active release from `get_active_release()`; none return `None`/raise "no release exists" | — |
| Existing release, reused without AI validation | Yes | `test_if_missing_reuses_active_release_without_ai_validation` |
| AI validation skipped without explicit flag / runs when requested | Yes | `test_ai_validation_is_skipped_without_explicit_flag`, `test_ai_validation_runs_when_explicitly_requested` |
| New key added by packaged file, absent from active release, is adopted | Yes | `test_a_key_the_active_release_predates_is_adopted_from_the_packaged_file` |
| Active release wins for every key it already carries | Yes | `test_the_active_release_still_wins_for_every_key_it_carries` |
| A packaged change inside an existing key is adopted (presumably for keys not yet operator-touched) | Yes | `test_a_packaged_change_inside_a_key_the_release_carries_is_adopted` |
| Operator-edited value survives a conflicting packaged default (customized values preserved) | Yes | `test_an_operator_edit_survives_the_packaged_file_that_disagrees_with_it` |
| State this bootstrap itself generated is not re-overwritten by the file on a later run | Yes | `test_state_this_bootstrap_generated_is_not_overwritten_by_the_file` |
| Publish records which packaged baseline it was built from | Yes | `test_the_publish_records_the_baseline_it_was_built_from` |
| A release with no recorded baseline metadata keeps its values (upgrade-safety) | Yes | `test_a_release_with_no_baseline_keeps_its_values_and_says_which` |
| Operator override path (`adopt_packaged`) for an undecidable release | Yes | `test_adopt_packaged_is_the_operators_way_out_of_an_undecidable_release` |
| Invalid defaults in the packaged file | **GAP** — not found in this file; not found elsewhere in a targeted grep for malformed-default handling specific to the bootstrap script (loader-level invalid-YAML tests exist in `test_catalog_loader.py`/`test_mapping_configuration_loader.py` but those are a different loader, not the bootstrap script's packaged-file merge) | — |
| DB (Neo4j) unavailable during bootstrap | **GAP** — not found in `test_graph_configuration_bootstrap.py`; `test_graph_configuration.py` and `test_worker_runtime_activation.py` test *runtime* activation resilience to a bad connection (e.g. `test_activation_re_resolves_vault_and_a_route_cannot_exist_without_it`, `test_a_failing_poll_does_not_take_the_worker_down`) but not the one-shot bootstrap CLI's behavior when Neo4j is unreachable at bootstrap time | — |

### Repository, API, process-adoption/epoch — broader inventory

| Area | Test file(s) | What is asserted |
|---|---|---|
| Graph repository save/update/version-conflict | `test_configuration_api.py::test_configuration_release_lifecycle_and_revision_conflict` | Full lifecycle plus an expected-head-revision conflict is rejected |
| Graph repository transition table enforcement | `test_release_transitions_are_single_sourced.py::test_the_repository_enforces_the_shared_table` | Repository itself refuses a transition the shared table disallows (not just callers) |
| Release checksum | `test_release_checksum_is_verified.py` | Checksum verified before trust |
| Config API GET/PATCH/promote | `test_configuration_api.py` (4 tests) | Lifecycle + revision conflict; partial agent-behavior edit activates without restart; partial edit rejects an invalid complete configuration; AI prompts/simulation behavior activate from graph |
| Config API authorization | `security/test_guards_match_the_console.py::test_promoting_a_release_needs_the_promote_capability`, `::test_proposing_an_agent_configuration_needs_proposal_write`, `::test_listing_interceptions_needs_the_read_capability`, `::test_reading_a_draft_needs_the_read_capability`, `::test_mutating_a_draft_needs_the_write_capability` | Positive coverage that each config-adjacent mutation is capability-gated |
| Config API — negative/403 path exercised by a caller *without* the role | UNVERIFIED — the guard tests above assert the capability requirement exists structurally; did not verify (time-boxed) whether they also assert an actual 403 response for an unauthorized caller vs. asserting the route-to-capability mapping in isolation | — |
| Secret redaction | `test_canonical_config_api.py` (8 masking tests) | Comprehensive — vault refs untouched, resolved secrets masked, case-insensitive key matching, nested/list structures, non-string values, empty string not treated as secret |
| Process adoption / epoch | `test_process_adoption_reporting.py` (17 tests), `test_worker_runtime_activation.py` (13 tests), `test_epoch_not_visible_before_all_module_commits.py`, `test_late_restart_required_aborts_all.py`, `test_pinned_release_resolves_after_handle_recreation.py`, `test_reconciler_delegates_to_reconfiguration_coordinator.py`, `test_reconfiguration_protocol.py`, `test_requests_never_observe_mixed_release_during_adoption.py` | Very thorough: partial adoption not counted live, stale reports expire, lagging replicas excluded, unexpected process classes reported-not-gating, in-flight turns finish on their starting release, reconciler delegates rather than reimplements, epoch visibility ordering, mixed-release isolation |
| Settings (`configuration/settings.py`) field validation | No dedicated `test_settings*.py` file found anywhere in `backend/tests/`. Partial indirect coverage via `test_ai_credential_configuration.py` (provider-key/vault-reference precedence, production `-change-me` refusal for `field in {google_api_key, ...}`, `test_production_refuses_the_development_reasoning_key`) | **GAP** — no direct unit test for most of the ~140 `Settings` fields inventoried in PART A (path-must-be-absolute validators, `PLATFORM_FRONTEND_CORS_ORIGIN` shape, `PLATFORM_NEO4J_URI` scheme check, numeric range validators like `dependency_connect_timeout_seconds >= probe_timeout_seconds`, etc.) — only the vault/secret-key production-refusal path is exercised |
| Schema-releases API (`/api/schema-releases/*`) | `api/test_schema_release_surface.py` (5 tests) | Live release surfaced in list; first activation planned as a build; preview records nothing; activation returns the committed migration; an unpublished release cannot be planned/activated |
| Support template config | `configuration/test_support_template_configuration.py` (23 tests, see above) | Extensive |

### Frontend `frontend/src` tests for configuration UI

| Component | Test file | What it asserts about load/save/reload |
|---|---|---|
| `ConfigurationPage` | `ConfigurationPage.test.tsx` (15 tests) | Release selectable via a real control; RELEASED recognized as published; promotion options gated by current status (DRAFT→VALIDATED/ARCHIVED, VALIDATED→RELEASED/ARCHIVED, RELEASED→none); promote without head revision vs. requiring it before publishing; backend refusal shown verbatim; promotion hidden without capability; data-sources tab removed; audit reads via canonical route; support-template deep link; integrations reported as "already served" |
| `AgentsSection` | `AgentsSection.test.tsx` (17 tests) + `AgentsSection.a11y.test.tsx` | Read-only rendering without proposal-write access; unsaved-draft retention across agent switch/back-forward with confirmation; nested object/array editing writes only the touched field; JSON-mode edit parity and guard rails (won't leave invalid JSON, won't save non-JSON, split-view requires valid JSON); backend rejection reason surfaced; list-load-failure messaging; adding typed properties/list entries |
| `SupportTemplateSection` | `SupportTemplateSection.test.tsx` (15 tests) + a11y test | Loads active release's template (not invented); "no template yet" state; preview reflects the draft, not published; selector/variant judged against described case shape; gap named as a hold; unparseable-JSON preview refused with reason; PATCH targets `support_template` on `RETURN_PLATFORM` domain of a fresh draft; confirm-before-publish with cancel path; reports published release without over-claiming running-case effects; confirmation state survives a concurrent refetch; backend refusal shown verbatim; preview available without publish capability |
| `RuntimeSchemaEditor` | `RuntimeSchemaEditor.test.tsx` (11 tests) | Names the release being edited; flattens document to searchable paths; sends the whole document (not just rendered values); preserves boolean typing; refuses to publish with no changes; can publish without pointing runtime at result; discard-to-running; refuses unparseable JSON; reports fallback-file state; offers reload on a concurrent external publish |
| `AiControlCenterPage` (config-adjacent tabs) | `AiControlCenterPage.test.tsx` (17 tests) | Interception queue rendering/polling behavior (see CFG-E-033/034/035), capability gating on responder actions — this is the AI operations surface, not the AI *gateway release config* editor; **no direct test found for an AI-gateway-domain release edit/save/reload flow** (only agent/template/schema domains above are covered) |

### GAPS table mapped to the required test-pyramid list

| Layer | Required coverage | Status |
|---|---|---|
| Unit — parsing | Loader/manifest parsing (`test_catalog_loader.py`, `test_mapping_configuration_loader.py`) | Covered, extensively |
| Unit — validation | Support-template, support-gate/ingress/resolver, return-method-requirements config validators | Covered |
| Unit — validation | `Settings` (settings.py) field validators beyond the vault/secret-key `-change-me` path (path-absoluteness, URI schemes, numeric range cross-checks) | **GAP** — no dedicated settings test file |
| Unit — merge/precedence | BOOTSTRAP_ENV → BASELINE → ACTIVE_RELEASE precedence as an end-to-end merge (as opposed to bootstrap's packaged-vs-active adoption rules, which *is* covered) | UNVERIFIED — did not find a test that constructs all three layers together and asserts final precedence order; `test_graph_configuration_bootstrap.py` covers packaged-file vs. active-release only, which is one layer of the claimed three |
| Repository — save/update/missing/version conflict | `test_configuration_release_lifecycle_and_revision_conflict`, `test_the_repository_enforces_the_shared_table` | Covered |
| Bootstrap — empty DB | **GAP** (see table above) |
| Bootstrap — partial DB / existing release | Covered |
| Bootstrap — customized values preserved | Covered |
| Bootstrap — new key added | Covered |
| Bootstrap — invalid defaults | **GAP** |
| Bootstrap — DB unavailable | **GAP** |
| API — GET | Covered (`test_configuration_api.py`, `test_canonical_config_domains.py`) |
| API — UPDATE (PATCH) | Covered |
| API — validation | Covered (`test_partial_edit_rejects_invalid_complete_configuration`) |
| API — authz | Covered structurally (capability-gating tests); explicit 403-for-unauthorized-caller assertion UNVERIFIED |
| API — version conflict | Covered |
| UI — loads persisted | Covered (Agents/SupportTemplate/RuntimeSchemaEditor/ConfigurationPage) |
| UI — validation | Covered (JSON-mode guards, placeholder/formatter allowlists surfaced via backend refusal messages) |
| UI — save | Covered |
| UI — errors | Covered (backend refusal shown verbatim, in 3 of 4 editors) |
| UI — reload persists | Covered for `RuntimeSchemaEditor` (external-publish reload) and `SupportTemplateSection` (confirmation survives refetch); **not found** for `ConfigurationPage`'s own release list or `AgentsSection` |
| UI — AI-gateway release domain editor | **GAP** — `AiControlCenterPage` tests cover AI *operations* (interception queue) but no test exercises loading/editing/saving the `AIGatewayConfiguration` release domain itself through a UI editor (unlike Agents/SupportTemplate/Schema, which each have one) |
| E2E — UI→API→DB→runtime→behaviour | **GAP** — `frontend/tests/canonical-routes.spec.ts` is the only Playwright spec and only checks route mounting/keyboard/a11y/no-horizontal-scroll, not a config change propagating to runtime behavior. `backend/tests/acceptance/test_item_22_the_release_stays_pinned_across_a_promotion.py` is the closest backend acceptance-level test but is scoped to pinning, not a full save→promote→adopt→behavior-changes chain |

---

STATUS: COMPLETE
