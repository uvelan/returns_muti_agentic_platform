# Configuration Audit — Returns Multi-Agent Platform

Branch `feat/acc-frontend` at `9b92633c`, audited 2026-09-11 against the running host stack, and re-verified against `refactor/unified-return-platform` at `42b0536b` (section K) (six Docker services, seven Python processes, Vite frontend). Evidence for every claim is in this folder: `PART_A` (Settings/env), `PART_B` + `PART_B1` (manifest, release, precedence, dead keys), `PART_C` + `PART_C_frontend_ui` (APIs and UI), `PART_D` (non-manifest files), `PART_E` (hardcoded values, tests), and the graph snapshots `baseline/`, `after_launch/`, `after_ui_publish/` (release id, head revision, metadata and the three domain payloads at each step).

## A. Executive summary

| Measure | Count | Note |
|---|---:|---|
| Configuration files | 41 | 39 under `backend/config` (16,705 lines), `.env.example` (111 variables), `compose.yaml` |
| Configuration domains | 6 | 3 release domains in Neo4j; `Settings` (env); dynamic-knowledge schema (Mongo); file-only governance (`schema_registry`, `data_assets`, `system_store`) |
| Configuration keys | ~875 | `production.yaml` 27 sections / 317 leaves; `ai_gateway.yaml` 8 top-level / 25 tasks / 285 leaves; `dependency_simulation.yaml` 22 leaves; `Settings` 140 fields; env template 111 |
| DB-backed configs | 7 stores | 3 release domains (Neo4j), graph schema releases (Mongo), AI gateway runtime settings (Mongo), source bindings (Mongo), bay configuration (SQL Server) |
| File-only configs | 5 | `schema_registry.yaml`, `data_assets.yaml`, `platform/system_store.yaml`, seed manifest, manifest modules (structural read only) |
| ENV/Vault configs | 140 | 32 secrets as `SecretStr`; Vault optional, references inert when disabled |
| UI-editable configs | 4 → 30 | before: support template, AI tasks, AI providers, agents (proposal); after: every business section (24) and dependency simulation added |
| Missing UI | 21 → 1 | source bindings still has a complete API client and no screen |
| Placeholder UI | 0 | no mock values, local-only state, no-op buttons or unwired fields found |
| Duplicate configs | 6 groups | one removed (`backend/assets.yaml`), one removed (3 Settings fields shadowed by `ai_gateway.yaml`) |
| Dead configs | 12 groups | 5 env variables and 3 Settings fields removed; the rest reported (see F) |
| Hardcoded config candidates | 45 | 30 backend, 15 frontend (PART_E); recommendations only |
| Unnecessary configs | 9 | see F |
| Bootstrap defects | 4 | 3 fixed here, 1 already fixed upstream |
| Runtime precedence defects | 3 | 2 fixed, 1 operator decision pending |
| Security defects | 0 live | unredacted console router is unmounted dead code |

**What was broken, in one paragraph.** The platform runs from a release in the Neo4j configuration graph, not from files. At every stack start a bootstrap republishes that release from the packaged YAML, and it could only tell an operator's edit apart from a change to the file when the release carried a "packaged baseline" digest. No release on this host had one, because operator publishes through `/api/config` never carried it forward and the bootstrap never recorded one while any key was undecidable, so the deployment was permanently undecidable: new packaged keys never reached the runtime, the bootstrap warned on every start, and the Windows launcher treated that warning as fatal and refused to start the stack at all. Worse, the AI gateway and dependency-simulation domains were never carried forward, so every edit made in the AI Control Center was silently replaced by the file on the next restart (measured: a task cap set to 1234 read 192 one bootstrap later). Nineteen of twenty-four business sections had no UI, and the two editors that existed sent whole documents, which could never delete anything. Release changes wrote no audit record.

**What changed.** The bootstrap now decides per key (and per AI task / per simulated dependency), records a partial baseline for every key it could decide, fills in leaves the release lacks, names only the undecidable keys, and takes a `--adopt-packaged-key <unit>` answer. Releases created through the API carry the baseline forward, store the validated model's canonical dump, refuse unknown domains, and write `CONFIGURATION_*` audit records with the changed paths. The launcher survives a warning. The Configuration screen has a Business tab that edits every section the release carries through the shared editor and a real merge patch. Dead env variables, dead Settings fields and a duplicate file are gone, and both configuration READMEs now describe the path that actually runs.

## B. Configuration architecture (as it actually runs)

```
.env / compose.yaml ──► Settings (pydantic, PLATFORM_*, extra="ignore")
                              │ hosts, credentials, provider order, model pools, mode switches
                              ▼
backend/config/returns/production.yaml ─┐
backend/config/ai_gateway.yaml          ├─► bootstrap_graph_configuration.py (every stack start)
backend/config/dependency_simulation.yaml┘      per-key / per-unit carry-forward against the
                                                release's packaged baseline (metadata)
                                                      │ publishes or leaves UNCHANGED
                                                      ▼
                             Neo4j configuration graph: ConfigurationRelease (RELEASED) ──► ConfigurationHead.revision
                             domains RETURN_PLATFORM · AI_GATEWAY · DEPENDENCY_SIMULATION
                                                      │
              runtime_activation ─► runtime_loader ─► ConfigurationSnapshotBuilder (checksum verified)
                                                      │ every process adopts within ~5 s (no restart);
                                                      │ GET /api/config/adoption shows LIVE / ACTIVATING
                                                      ▼
                             domain services (agents, policies, workflow, support, shipments, AI gateway)
                                                      ▲
              /api/config/releases  POST (clone active, carry baseline) ─► PATCH domain (validated, canonical)
              ─► promote VALIDATED ─► promote RELEASED (expected_head_revision) ─► audit records
                                                      ▲
              Configuration screen: Agents (proposal → approvals), Support Template, Business (new), Releases
              AI Control Center: Configuration (tasks), Providers & Models
              Graph Schema Analyzer: schema releases (Mongo, separate lifecycle)

NOT on the runtime path: manifest.yaml + agents/ policies/ workflows/ sync/ sources/ mappings/ graph/
(loader.py → compatibility.py → RuntimeSnapshot is test-only; agents/*.yaml are read structurally by the Agents editing API only).
```

Precedence, verified from code and runtime: **Neo4j RELEASED release > packaged file (only via bootstrap carry-forward, or as development fallback when no release exists) > model defaults.** `Settings` never overrides a release domain. There is no other cache: each process holds the adopted snapshot and refreshes on a 5-second poll against the head revision (`runtime_activation.py`), which the adoption endpoint reports per process class.

## C. Complete config matrix (condensed; row-level detail in the PART files)

| Config | File | DB | Bootstrap | Runtime | API | UI | Editable | Effective | Problem | Action |
|---|---|---|---|---|---|---|---|---|---|---|
| RETURN_PLATFORM: discovery, source_resolution, clarification_policy, selection_vocabulary | production.yaml | Neo4j domain | carry-forward per key | release | GET/PATCH/promote | Business tab (new) | yes | release | `source_resolution.*_paths` are `[]` in the live release, populated in the file, undecidable | operator decides with `--adopt-packaged-key source_resolution` |
| RETURN_PLATFORM: return_policy, return_eligibility_policy, policy_evaluation | production.yaml | Neo4j | per key | release | same | Business tab (new) | yes | release | policy_evaluation/support_ingress differ from file by operator decision D-0008/D-0009; kept | none |
| RETURN_PLATFORM: workflow, return_case, business_calendars, housekeeping | production.yaml | Neo4j | per key | release | same | Business tab (new) | yes | release | `workflow.stages` parsed, real stage order is `DEFAULT_STAGE_SEQUENCE` in code (PART_B1) | keep; document |
| RETURN_PLATFORM: support, support_gate, support_ingress, support_resolver, context_assembly | production.yaml | Neo4j | per key | release | same | Business tab (new) | yes | release | — | none |
| RETURN_PLATFORM: support_template | production.yaml | Neo4j | per key | release | same + preview | Support Template tab | yes | release | whole-document patch could not delete a variant | mergePatch available; tab unchanged |
| RETURN_PLATFORM: shipment_tracking, bay, omc | production.yaml | Neo4j | per key | release | same | Business tab (new) | yes | release | — | none |
| RETURN_PLATFORM: integrations, feature_flags, extensions, copilot | production.yaml | Neo4j | per key | release | same | Business tab (new) | yes | release | `feature_flags` (4 flags) and `extensions` (5 leaves) have **no reader** | decide: wire or remove (F) |
| RETURN_PLATFORM: agents | production.yaml | Neo4j | per key | release | PUT /api/agents → proposal | Agents tab | proposal | release | six agent knobs read by nothing (documented in class docstring) | keep; the tab edits the live block |
| RETURN_PLATFORM: runtime_integrations | generated by bootstrap + UI | Neo4j | preserved (digest differs) | release | PATCH | AI Control Center Providers | yes | release | — | none |
| AI_GATEWAY: tasks (25), circuitBreaker, retry, rateLimits, providerLimits, modelContexts | ai_gateway.yaml | Neo4j | **was: file always wins → now per unit** | release | PATCH | AI Control Center Configuration | yes | release | edits lost at restart (P1, fixed) | fixed |
| DEPENDENCY_SIMULATION | dependency_simulation.yaml | Neo4j | **was: file always wins → now per unit** | release | PATCH | Business tab (new) | yes | release | no UI; required-valid for every promote | fixed |
| Settings (140) | .env / compose | none | n/a | process start | `/data-console/v1/settings` view (unmounted) | none | env only | env | 8 business/operational values live in env (ai_provider_order, model pools, dependency modes, feedback_learning_enabled, support_ticket_mode, google_thinking_budget) | recommend DB-managed (F) |
| Dynamic-knowledge schema | active-schema.return-order.yaml | Mongo `graph_schema_releases` | seeded once if none active; never overwrites | Mongo release, file fallback | schema-releases API (baseChecksum) | Graph Schema Analyzer | yes | Mongo | file edits after first seed reach nothing; `reseed_schema_release.py` is the manual path | document (PART_D) |
| Source bindings | schema declared | Mongo `SourceBindingStore` | none needed | Mongo | PUT/DELETE, capability-gated | **none** | API only | Mongo | complete client, no screen | build screen (P2) |
| AI gateway runtime settings (interceptMode, providerOrder) | none | Mongo `OperationalRepository` | default doc | Mongo | PUT with expectedVersion | Interceptions area | yes | Mongo | duplicates `PLATFORM_AI_PROVIDER_ORDER` meaning | see F |
| Bay configuration | SQL migration seeds 6 bays for WH-CHENNAI-01 | SQL `platform.bay_configuration` | manual `seed_warehouse_bay_configuration.py` | SQL | assignment routes only | none | no | SQL | generated orders have zero eligible bays unless script run | fixed upstream (411e2904 on refactor branch) |
| schema_registry.yaml, data_assets.yaml | file | none | n/a | file at start | read-only | none | no | file | FILE_ONLY by design | keep |
| platform/system_store.yaml | file | none | n/a | file at start | none | none | no | file | `migration_mode`, `migration_lock_required` silently ignored | fix or remove keys (P2) |
| manifest.yaml + 19 modules | file | none | n/a | **not on runtime path** | /api/agents reads agents/*.yaml | Agents tab | proposal | none | README claimed authoritative | README corrected |
| policies/*.yaml, live_validation/*.yaml, internal_manifests/*.yaml, data_platform/*.yaml, reasoning.yaml | file | none | n/a | never loaded | none | none | no | none | dead or test-only | decide (F) |
| Frontend constants | 7 literals | none | n/a | build | none | none | no | code | poll intervals, page size | keep in code |

## D. DB bootstrap matrix

| Config | DB missing | Current behaviour (after fixes) | Expected behaviour | Safe write allowed? | Fix |
|---|---|---|---|---|---|
| Neo4j configuration graph absent | no `ConfigurationRelease` | `apply_neo4j_migrations.py` creates constraints/indexes; bootstrap publishes the packaged files as `return-platform-<checksum>` with a full baseline | same | yes — platform-owned graph | none |
| Release absent (fresh graph) | no RELEASED node | published from files, `graph_configuration_status=READY` | same | yes | none |
| Release present, published by bootstrap | baseline recorded | per-key decision; UNCHANGED when identical, baseline refreshed | same | yes | none |
| Release present, published by an operator (API/UI) | **was: no baseline → every key undecidable forever; AI/DS domains overwritten from file** | draft clones baseline; bootstrap decides per key/unit; operator values kept; new leaves adopted; undecided keys named | same | yes | **fixed**: `releases.py` create carries metadata; CLI partial baseline, `_fill_absent_leaves`, multi-domain carry |
| Key added to packaged file | **was: never reached a deployment that had a release** | adopted (absent leaf) | same | yes | fixed |
| Key changed in packaged file, release unedited, baseline known | adopted | adopted | yes | none (existing) |
| Key changed in packaged file, release unedited, no baseline | **was: silently dropped with a warning nobody could act on per key** | release wins; key named; `--adopt-packaged-key` per unit | operator decision | yes | fixed |
| Release no longer validates against a newer model | falls back to packaged file for that domain, `logger.error`, operator values dropped loudly | same | yes | none (existing; now per domain) |
| Windows launcher and any stderr warning | **was: NativeCommandError aborted startup** | preparation scripts run with Continue; exit code decides | same | n/a | **fixed** `run_all_host.ps1` |
| Mongo graph-schema release absent | seeded from `active-schema.return-order.yaml` once | same | yes (platform Mongo) | none |
| SQL bay configuration | 6 seed rows; generated warehouses unmatched | fixed upstream by reset_all backfill | yes (platform SQL) | merge refactor branch |
| External stores (`return_source` Mongo, SQL source tables) | read-only | never written by configuration code; `SourcesConfig.access_mode` must be read-only, enforced by validator | same | **no** | none |

## E. UI matrix

| Domain | Sections | Before | After | Missing | Duplicate | Proposed next |
|---|---:|---|---|---:|---:|---|
| Order discovery | 4 | none | Business tab | 0 | 0 | typed form for `discovery.identification_fields` |
| Return policy | 3 | none | Business tab | 0 | 0 | typed form for method derivation |
| Workflow and case timing | 4 | none | Business tab | 0 | 0 | — |
| Support | 6 | Support Template tab (1 of 6) | + Business tab (5) | 0 | 0 | one Support page with 6 tabs |
| Fulfilment and warehouse | 3 | none | Business tab | 0 | 0 | shipment ladder editor |
| Integrations and features | 4 | Integrations read-only | Business tab | 0 | 1 (Integrations tab shows read-only what Providers edits) | fold read-only tab into Business |
| Agents | 1 | Agents tab (proposal) | unchanged | 0 | 1 (manifest `agents/*.yaml` vs `production.yaml agents:`) | retire manifest copy |
| AI gateway | 8 (+25 tasks) | AI Control Center | unchanged | 0 | 0 | — |
| Dependency simulation | 1 domain | none | Business tab | 0 | 0 | — |
| Graph schema | 1 artifact | Analyzer (proposed + runtime editors) | unchanged | 0 | relationship between the two editors unverified | clarify in UI |
| Source bindings | 1 | none | none | **1** | 0 | screen under Data Sources & Sync |
| Release governance | — | Overview/Releases/Runtime/Audit | Audit now shows `CONFIGURATION_*` | 0 | 0 | — |

Verified loops (open page → current DB value → change → save → DB changed → reload → runtime): AI Control Center task cap (head 65 → 66, `/api/ai/tasks` served the new cap, adoption LIVE, no restart) and the new Business tab feature flag (head 71 → 72, audit `changedPaths: feature_flags.copilot_operations_console`, baseline metadata carried, adoption LIVE). Both edits were reverted afterwards (heads 67 and 73), and the AI task cap was restored through the new `--adopt-packaged-key AI_GATEWAY/tasks.RETURN_STATUS_SUMMARY_V1` flag (head 71).

## F. Unnecessary, dead and misplaced configuration

| Config | Classification | Evidence | Action |
|---|---|---|---|
| `PLATFORM_AI_VALIDATION_RECEIPT_TTL_HOURS`, `PLATFORM_AI_TRANSIENT_COOLDOWN_INITIAL_SECONDS`, `PLATFORM_AI_TRANSIENT_COOLDOWN_MAX_SECONDS`, `PLATFORM_AI_MAX_RECOVERY_PROBES_PER_REQUEST`, `PLATFORM_SEED_RECORD_LIMIT` | REMOVE | no Settings field, `extra="ignore"` swallows them (settings.py:56) | **removed** from `.env.example` / `compose.yaml` |
| `Settings.ai_max_attempts_per_provider`, `ai_max_concurrency`, `ai_prompt_version` | REMOVE | zero readers; superseded by `ai_gateway.yaml` `retry`/`providerLimits`/per-task `promptVersion` | **removed** with env/compose lines |
| `backend/assets.yaml` | REMOVE | byte-identical to `config/data_assets.yaml`, unreferenced | **removed** |
| `production.yaml feature_flags` (4 flags) | DEPRECATED → REMOVE or wire | parsed into `FeatureFlagsConfiguration`, no reader anywhere in backend or frontend (PART_B1) | product decision; do not edit through the new tab expecting effect |
| `production.yaml extensions` (5 leaves) | DEPRECATED | only its own cross-validator reads it | same |
| `agents.*.{circuit_breaker_failure_threshold, task_queue, state_namespace, prompt_ref, policy_ref, human_confirmation_required}` | MOVE_TO_CODE / REMOVE | docstring says "read by nothing"; queues come from `Settings.return_workflow_task_queue` | remove from model when old releases no longer need parse-compatibility |
| `platform/system_store.yaml migration_mode`, `migration_lock_required` | REMOVE or wire | live loader `_SystemStoreConfigPayload` ignores them (`extra="ignore"`) | P2 |
| `policies/*.yaml` (4), `live_validation/data_assets.sampling.yaml`, `internal_manifests/*.yaml` (4 × 603 lines), `data_platform/*.yaml` (4), `reasoning.yaml` + `load_reasoning_configuration` | DEPRECATED (declared design, never wired) | README lists policies/live_validation as intended-but-unwired; the others have no loader or test-only loaders | keep or delete as a product decision; nothing runs them |
| `manifest.yaml` modules other than `platform.system_store` | DEPRECATED (structural read only) | `compatibility.py` path test-only; `agents/*.yaml` differ in names and fields from the live `agents:` block | retire the manifest copy of agents, or repoint the Agents tab; README corrected |
| `Settings.seed_version` default `e2e-v1` vs template `e2e-v2` | MERGE | settings.py:329 vs `.env.example`/compose | align default |
| `PLATFORM_AI_PROVIDER_ORDER`, model pool lists, `PLATFORM_*_DEPENDENCY_MODE`, `PLATFORM_FEEDBACK_LEARNING_ENABLED`, `PLATFORM_SUPPORT_TICKET_MODE`, `PLATFORM_GOOGLE_THINKING_BUDGET` | MOVE_TO_DB (recommendation) | business/operational values that change without a deploy, currently need a process restart; `providerOrder` already duplicated in Mongo AI gateway settings | design item; keep production gates (no SIMULATED/MANUAL in production) |
| Frontend poll intervals, page size 25, autosave delay | KEEP (code) | operational constants with no per-deployment variance | none |

## G. Defects

| ID | Sev | Status | Files | Root cause → fix |
|---|---|---|---|---|
| CFG-DEF-01 | P0 | **fixed** | `scripts/run_all_host.ps1` | PowerShell 5.1 + `$ErrorActionPreference="Stop"` turns captured native stderr into a terminating error; the bootstrap's warning (exit 0) aborted the stack. Preparation scripts now run with Continue; exit code decides. Observed: launcher exit 1 on this host before, exit 0 after. |
| CFG-DEF-02 | P1 | **fixed** | `configuration/cli/bootstrap_graph_configuration.py` | No baseline could be recorded while any key was undecidable, so none ever was ("at most once" became "every start"); new packaged keys never reached a deployment. Now: partial baseline per decided key, absent leaves filled (`_fill_absent_leaves`), `--adopt-packaged-key`. Live: 22 leaves adopted, 0 operator values changed (head 64 → 65). |
| CFG-DEF-03 | P1 | **fixed** | `configuration/api/releases.py` (create) | Drafts cloned from the active release dropped `packaged_key_digests`; every UI publish reset the deployment to undecidable. Draft now carries the metadata; verified on release `business-feature_flags-…` (both metadata keys present). |
| CFG-DEF-04 | P1 | **fixed** | bootstrap CLI | `AI_GATEWAY` and `DEPENDENCY_SIMULATION` were always republished from the files: every AI Control Center edit lost at restart. Measured 1234 → 192. Now carried per unit (`tasks.<id>`, `dependencies.<name>`) with `packaged_domain_key_digests`; measured 1234 → 1234, status UNCHANGED. |
| CFG-DEF-05 | P1 | **fixed** | `configuration/api/releases.py` (PATCH/PUT) | Stored the raw merged dict, not the validated model's dump, so the bootstrap's equality check failed on non-canonical shapes and republished with a head bump on every start (prior finding F-0084). Canonical dump stored; unknown domain keys refused (404). |
| CFG-DEF-06 | P1 | **fixed** | `frontend/src/domains/config/BusinessSection.tsx`, `api/mergePatch.ts` | 19 of 24 business sections and the dependency-simulation domain had no UI. Business tab edits every section the release carries with the shared editor and an RFC 7396 merge patch (deletions become `null`). Verified end to end. |
| CFG-DEF-07 | P2 | **fixed** | `configuration/api/releases.py` | Release create/patch/promote wrote no audit record; `GET /api/config/audit` now shows `CONFIGURATION_RELEASE_CREATED/DOMAIN_PATCHED/RELEASE_PROMOTED` with actor, target, `changedPaths`, head revision, checksum. |
| CFG-DEF-08 | P2 | **fixed** (docs) | `backend/config/README.md`, `configuration/README.md` | Documentation claimed the manifest path was authoritative; it is test-only. Both READMEs now state the live path and carry-forward rules. |
| CFG-DEF-09 | P2 | **fixed** | `.env.example`, `compose.yaml`, `settings.py` | 5 documented env variables read by nothing; 3 Settings fields read by nothing. Removed. |
| CFG-DEF-10 | P2 | **fixed** | `backend/assets.yaml` | Orphaned byte-identical duplicate. Removed. |
| CFG-DEF-11 | P1 | operator decision | live release, key `source_resolution` (also 9 others) | Release carries model defaults (`[]`) where the file has values (email/phone/account paths, delivery-proof paths, colour path); undecidable by construction. Decide with `--adopt-packaged-key source_resolution` after confirming no operator set them empty on purpose. The other undecided keys: agents, bay, clarification_policy, discovery, policy_evaluation, return_eligibility_policy, return_policy, shipment_tracking, support_ingress (policy_evaluation and support_ingress are known operator decisions and must stay). |
| CFG-DEF-12 | P1 | fixed upstream | `scripts/linux/reset_all.sh`, bay seeding | Generated orders got zero eligible bays unless `seed_warehouse_bay_configuration.py` was run by hand; commit `411e2904` on `refactor/unified-return-platform` chains the backfill. Merge that branch. |
| CFG-DEF-13 | P2 | open | `frontend/src/api/sourceBindings.ts` | Complete, mocked, contract-tested client with no screen. |
| CFG-DEF-14 | P2 | open | `platform/system_store/manifest_loader.py` | `migration_mode`, `migration_lock_required` silently ignored. |
| CFG-DEF-15 | P3 | open | `configuration/api/releases.py` (PATCH) | No optimistic lock on a draft domain patch; last write wins within a draft. Low exposure: every UI publish creates its own draft and RELEASED promotion is compare-and-set on the head revision. Add `expected_version` using the domain node's existing `version`. |
| CFG-DEF-16 | P3 | open | `configuration/api/releases.py`, `router.py` | Create/patch use `require_write_roles` (7 roles) where promote uses a capability; add `CONFIG_RELEASE_WRITE`. |
| CFG-DEF-17 | P3 | open (dead code) | `/data-console/v1/*` routers | Unmounted since Wave F1; console responses lack redaction and the PUT full-replace remains in the module. Delete in Wave F5 as planned. |
| CFG-DEF-18 | P3 | open | frontend mocks | No MSW handlers for `/api/agents`, `/api/schema-releases/*`, replay/compare; `dev:mock` 404s there. |
| CFG-DEF-19 | P3 | open | `Settings.seed_version` | Default drift `e2e-v1` vs `e2e-v2`. |
| CFG-DEF-20 | P3 | note | live graph | 4 stale DRAFT releases and 1 VALIDATED from August; harmless, archive for hygiene. |

## H. Tests added or changed

| Area | Test | Asserts |
|---|---|---|
| Bootstrap | `test_a_release_with_no_baseline_keeps_its_values_and_says_which` (updated) | partial baseline recorded for every decided key, not the undecided one |
| Bootstrap | `test_a_leaf_the_release_lacks_is_filled_even_inside_an_undecidable_key` | absent agent block adopted, edited leaf kept, key still named |
| Bootstrap | `test_a_partial_baseline_decides_its_keys_and_leaves_the_rest_undecided` | second run adopts on decided keys, keeps undecided |
| Bootstrap | `test_adopt_packaged_key_takes_the_file_for_one_key_only`, `…refuses_a_key_the_file_does_not_have` | per-key adoption, unknown key rejected before any write |
| Bootstrap | `test_an_operators_ai_task_edit_survives_the_next_start`, `…dependency_simulation_edit_survives…`, `…unedited_task_is_adopted_beside_an_edited_one`, `…takes_the_file_for_one_ai_task`, `…ai_domain_matches_the_file_is_unchanged` | multi-domain carry-forward, per-task granularity, UNCHANGED path intact |
| API | `test_a_patched_domain_is_stored_in_the_shape_the_bootstrap_compares` | canonical dump stored |
| API | `test_a_domain_the_platform_does_not_read_is_refused_not_stored` | unknown domain → 404, nothing stored |
| API | `test_a_draft_cloned_from_the_active_release_carries_its_packaged_baseline` | metadata carried |
| API | `test_every_release_change_leaves_an_audit_record` | three actions, actor, target, `changedPaths` |
| Frontend | `mergePatch.test.ts` (6) | RFC 7396 semantics incl. `null` deletion |
| Frontend | `BusinessSection.test.tsx` (6) | offered sections = release's own; patch under section key; deletion as null; DEPENDENCY_SIMULATION whole; read-only without capability |

Results: bootstrap 20/20, configuration API 8/8, related backend suites 351 passed, frontend config-domain suites 39 passed, ruff/mypy/eslint/tsc clean on changed files. Full suites at the end of the session (audited branch): backend 4,068 passed, 4 skipped, 1 failed (`test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item`, an `AttributeError` on a workflow test double at `return_case_workflow.py:2247`, code this session did not touch); frontend 877 passed, 2 failed (`registry.test.ts`, which expects eight domains where the branch declares nine; neither file was changed here, last touched 2026-08-30). Both failures are pre-existing on `feat/acc-frontend`.

## I. Runtime evidence timeline

| Head | Release | By | What it proves |
|---:|---|---|---|
| 64 | `run0001-d0009-…-20260902-1108` | dev-operator | baseline: no metadata; file has 22 leaves the release lacks; 27 undecidable diffs |
| — | launcher | — | before fix: exit 1 on the bootstrap warning (NativeCommandError); bootstrap alone: exit 0, UNCHANGED |
| 65 | `return-platform-cc56121100dcb3df` | bootstrap | after fix: +22 leaves, 0 removed, 0 changed; partial baseline recorded; all 7 process classes LIVE |
| 66 | `ai-tasks-20260911-121412` | UI | AI Control Center edit → graph → runtime within seconds; metadata empty (defect 03 observed) |
| 67 | `audit-revert-…` | API | revert; metadata still empty (chain had lost it) |
| 68 → 69 | demo edit, bootstrap | API, bootstrap | **defect 04 reproduced**: cap 1234 → 192 after one bootstrap run |
| 70 | `audit-demo2-…` | API | after fixes: metadata carried; bootstrap → UNCHANGED, cap stays 1234 |
| 71 | `…-r71` | bootstrap `--adopt-packaged-key AI_GATEWAY/tasks.RETURN_STATUS_SUMMARY_V1` | per-unit adoption restores 192 |
| 72 | `business-feature_flags-…` | new Business tab | flag flipped, audit `changedPaths` recorded, both baseline keys carried, LIVE |
| 73 | `business-feature_flags-revert-…` | API | environment restored |

## J. Follow-ups for the operator

1. Decide the ten undecidable `RETURN_PLATFORM` keys, one at a time: `backend/.venv/Scripts/python.exe backend/scripts/bootstrap_graph_configuration.py --adopt-packaged-key source_resolution` is the likely first, since the release holds model defaults there and the copilot cannot search by email, phone or account path until it is taken. Leave `policy_evaluation` and `support_ingress` as they are.
2. Merge `refactor/unified-return-platform` for the bay backfill and the newer discovery rules; the bootstrap and API changes here merge cleanly (one overlapping review file).
3. Decide the fate of `feature_flags`, `extensions`, the manifest agent copies and the never-wired files listed in F.
4. Build the source-bindings screen and typed forms for the two or three sections operators change most.
5. Move the eight env-held business switches into a release domain when the next configuration wave is planned.

## K. Verification against `refactor/unified-return-platform` @ `42b0536b` (2026-09-10)

Done in a worktree of the latest trunk with this session's fixes applied as a patch. Nothing was published to the live graph; the bootstrap decision was simulated read-only with the patched code and the live release.

| Check | Result |
|---|---|
| Files the fixes touch changed upstream? | No: bootstrap CLI, releases API, launcher, Configuration page have zero upstream commits; `settings.py` gained one field (`google_response_schema`), no overlap |
| Fixes apply on `42b0536b` | `git apply --check` clean; 12 modified, 1 deleted, 4 new files |
| Backend tests there | 54 passed (bootstrap 20, configuration API 8, canonical config API, canonical domains, mounted paths), imports confirmed from the worktree |
| Frontend tests there | 65 passed across the config domain and merge-patch suites; `tsc` and `eslint` clean |
| Structural findings still hold | reasoning loader 0 callers; `backend/assets.yaml` still an identical duplicate; `internal_manifests` 0 references; `policies/` 0 loaders; `feature_flags` 0 readers; 3 dead Settings fields with 0 readers; 5 dead env variables; console routers still unmounted |
| Pre-existing failures | both are registered in `scripts/ci/known_test_failures.json` upstream (`test_a_rejected_return_still_opens_no_work_item`, the two `registry.test.ts` cases) |
| Inventory deltas | `production.yaml` 27 sections, 323 leaves (+6: `shipment_tracking.source_mirror`, `source_constants`), 6 changed leaves (identification fields, method requirements, bay receipt rules, ladder); `ai_gateway.yaml` six order-agent tasks moved to LIGHTWEIGHT with `maximumOutputTokens: 6144`, `retry.maximumTotalAttempts` 12; `Settings` 144 fields; the discovery rules `narrow_with`, `searches_only_with`, `known_values` live in `return_configuration.py` / `production.yaml`, not the schema file |
| Live release vs latest files | RETURN_PLATFORM 29 diffs (the same 10 undecided keys, plus 6 new leaves the carry-forward fills); AI_GATEWAY 19 diffs; DEPENDENCY_SIMULATION 0 |
| Simulated bootstrap decision on latest files | AI_GATEWAY adopts `retry` and the six `tasks.ORDER_AGENT_*` units (baselines recorded, unedited); RETURN_PLATFORM keeps the same 10 undecided keys and fills the new shipment-tracking leaves; DEPENDENCY_SIMULATION unchanged; operator values (`policy_evaluation`, `support_ingress`, D-0009) untouched |

Conclusion: the audit's findings, the fixes and the plan stand on the latest trunk. CFG-0 should be cut from `42b0536b` (the verification worktree at `.claude/worktrees/cfg-verify` already holds the applied patch), which also brings the bay-seeding fix (CFG-DEF-12) in for free. Part D's remark that the colour and companion-search rules "do not exist" and Part E's "6144 not found" were true on the audited branch only.
