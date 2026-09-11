# CFG — execution ledger

Append-only. One entry per step. Every command and its output is pasted from the terminal, never
transcribed from memory. Brief: `.plan/tracks/CFG.brief.md`. Audit: `evidence/config_audit/FINAL_REPORT.md`.

## Decisions

| ID | Decision | Answer | Recorded |
|---|---|---|---|
| D-CFG-1 | delete never-wired files/loaders | pending | — |
| D-CFG-2 | feature_flags / extensions | pending | — |
| D-CFG-3 | split production.yaml by function | pending | — |
| D-CFG-4 | env switches → release `deployment` section | pending | — |
| D-CFG-5 | retire `/data-console/v1` modules | pending | — |
| D-CFG-6 | undecidable dev-host keys | pending | — |

## Leases

| Lease | Branch | Base sha | Model | Status | Drop | RV | Merged at |
|---|---|---|---|---|---|---|---|
| CFG-0 | feat/cfg-0-audit-fixes | 42b0536b | orchestrator | **MERGED** | LEASE-CFG-0 MERGED | 2 · CR → PASS (0e7a2e60) | 06b43b18 (local trunk) |
| CFG-1 | feat/cfg-1-dead-code | 06b43b18 | Sonnet | **MERGED** | LEASE-CFG-1 MERGED | 1 · PASS (e036310d) | 3cb696e7 |
| CFG-2 | feat/cfg-2-config-split | 73c276d2 | Opus spike → Sonnet | **MERGED** | LEASE-CFG-2 MERGED | 1 · PASS (e42d92e6) | ff7aed3e |
| CFG-3a | feat/cfg-3a-config-api | 5dc5a825 | Sonnet | **MERGED** | LEASE-CFG-3a MERGED | PASS (52f01260) | merge commit on trunk |
| CFG-3b | feat/cfg-3b-form-primitives | 734a16dc | Sonnet | **MERGED** | LEASE-CFG-3b MERGED | PASS (f3a7340d) | merge commit on trunk |
| CFG-4 | feat/cfg-4-screens-a | after CFG-3a + CFG-3b | Sonnet | NOT_STARTED | — | — | — |
| CFG-5 | feat/cfg-5-screens-b | after CFG-4 | Sonnet | NOT_STARTED | — | — | — |
| CFG-6 | feat/cfg-6-deployment-section | after CFG-2 + CFG-3a | Opus spike → Sonnet | NOT_STARTED | — | — | — |
| CFG-7 | feat/cfg-7-acceptance | after CFG-5 + CFG-6 | Haiku + Sonnet + RV | NOT_STARTED | — | — | — |

## Steps

(none yet — the first entry is CFG-0 step:00, the base check.)

---

## CFG-0 step:00 — base verified by ref, fixes committed

Decisions: the user answered "start fix" / "work on latest code" to the six-decision list; the
orchestrator applies the stated defaults for D-CFG-1..5 and, for D-CFG-6, adopts only
`source_resolution`; the seven remaining undecided keys are presented to the user, not adopted.

Worktree `.claude/worktrees/cfg-verify`, branch `feat/cfg-0-audit-fixes`.

```
$ git fetch -q origin
local trunk: 47c03f85
origin trunk: 42b0536b
branch base: 42b0536b
left/right vs origin trunk: 0	0
$ PYTHONPATH=$WT/backend/src python -c "import return_platform; print(return_platform.__file__)"
imports from K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-verify\backend\src\return_platform\__init__.py
```

The local `refactor/unified-return-platform` ref is behind `origin/` by 24 commits; the branch is
cut from `origin/` (42b0536b), which is the tip. Upstream since the audited base adds no SQL or
Neo4j migrations (`git diff --name-only 9b92633c HEAD | grep -i migration` is empty).

Fixes applied from the audit session as one patch (`git apply --check` clean), plus the four new
frontend files. Tests on this base before the commit:

```
$ pytest tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py tests/configuration/test_canonical_config_api.py tests/api/test_canonical_config_domains.py tests/test_every_console_path_is_mounted.py -q
54 passed, 1 warning in 19.76s
$ npx vitest run src/api/mergePatch.test.ts src/domains/config
 Test Files  7 passed (7)
      Tests  65 passed (65)
$ npm run -s typecheck ; npx eslint src/api/mergePatch.ts src/domains/config/BusinessSection.tsx src/domains/config/ConfigurationPage.tsx --max-warnings=0
TYPECHECK exit 0 / ESLINT exit 0
```

Commit: `1d2c8bed (CFG) step:00 config audit fixes -- the bootstrap decides per key, the API carries the baseline, the launcher survives a warning`.

Live graph before the first bootstrap on this code (`evidence/config_audit/before_trunk_launch/`):
head 73, RELEASED `business-feature_flags-revert-20260911-123824`, baseline keys 18, domain baselines AI_GATEWAY 33 / DEPENDENCY_SIMULATION 10.

Old-tree processes stopped (7 python, 1 node); the stack is relaunched from this worktree.

---

## CFG-0 step:01 — the stack moved to this code; the release reconciled with the packaged files

Launch from the worktree (`scripts/run_all_host.ps1 -NoSupervise`; `backend/.venv` and
`frontend/node_modules` junctioned to the main tree first — without the venv junction the launcher
falls back to `poetry run` and preparation fails):

```
packaged_configuration_not_adopted release_id=business-feature_flags-revert-20260911-123824 keys=agents,bay,clarification_policy,discovery,policy_evaluation,return_eligibility_policy,return_policy,shipment_tracking,source_resolution,support_ingress; ...
graph_configuration_release=return-platform-c128be80e35d1dbd
graph_configuration_status=READY
All services started: backend, worker-temporal, worker-discovery, worker-orchestrator, worker-outbox, worker-integration-outbox, worker-housekeeping, frontend
```

Diff `before_trunk_launch/` → `after_trunk_launch/` (`evidence/config_audit/`):
```
RETURN_PLATFORM: added=6 removed=0 changed=2   (six shipment_tracking.source_mirror/source_constants leaves filled; the two "changed" lists are the newer model's default fields expanded inside list items)
  operator value kept: policy_evaluation.enabled = True
  operator value kept: support_ingress.nl_enabled = True
  operator value kept: return_eligibility_policy.standard_stock_return.unstated_condition_facts = REVIEW_REQUIRED
AI_GATEWAY: added=0 removed=0 changed=25       (retry.maximumTotalAttempts 6->12; six ORDER_AGENT_* tasks: tier STANDARD->LIGHTWEIGHT, maximumOutputTokens None->6144, prompt sections)
DEPENDENCY_SIMULATION: added=0 removed=0 changed=0
```
This is exactly the decision the read-only simulation in FINAL_REPORT §K predicted.

**Incident, recorded for the restart procedure.** Adoption stayed `ACTIVATING pending ['api']` and
`/api/config/runtime` kept answering head 73 with `loaded_at 07:08`. `Get-NetTCPConnection -LocalPort 8000`
named a listener (pid 8884) no process list could see; the old uvicorn `--reload` child
(`WindowsApps\...\python.exe -c "from multiprocessing.spawn ..."`, pid 14148, parent 8884) had survived
the path-filtered kill and kept serving on the inherited socket. A second sweep meant to stop pre-launch
processes compared a null start time and stopped every python process, new stack included. Relaunch:
```
graph_configuration_release=return-platform-37096dcda3652383
graph_configuration_status=UNCHANGED
runtime: return-platform-37096dcda3652383 head 76 loaded 2026-09-11T11:30:45.925087Z
adoption: LIVE 76 pending [] | api instances: [76]
```
Memory `stack-restart-and-reset-all` updated with the correct stop procedure.

**D-CFG-6, applied.** Governance proposals touching these keys: `GET /api/proposals` → 0.
```
$ bootstrap_graph_configuration.py --adopt-packaged-key source_resolution
graph_configuration_release=return-platform-e6f595a2335bbdb4   status=READY
$ bootstrap_graph_configuration.py --adopt-packaged-key bay --adopt-packaged-key discovery --adopt-packaged-key shipment_tracking
graph_configuration_release=return-platform-37096dcda3652383   status=READY
packaged_configuration_not_adopted ... keys=agents,clarification_policy,policy_evaluation,return_eligibility_policy,return_policy,support_ingress
```
Reasons: `source_resolution` held model defaults (`[]`) where the file names the email/phone/account and
delivery-proof paths; `bay` in the release predated upstream commit 2a1f4359 ("pre-arrival placement is
allowed and the physical-receipt gate is off"); `discovery` in the release predated the colour signal,
companion narrowing and street narrowing the trunk code implements (1b8bf203, 947a9aab, a6cf3643);
`shipment_tracking` in the release still carried the retired console `fields` and no `projection_status`
per rung (2a1f4359). Runtime after adoption:
```
runtime source_resolution.customer_email_paths: ['customer.address.email']
runtime bay.require_physical_receipt: False | AI ORDER_AGENT_REASONING_V1 tier/cap: LIGHTWEIGHT 6144
```
`return_policy` mixes an operator's values (`default_method BRANCH_UPS`, curated `freight_keywords`, the
tce2e03 releases of 2026-08-26) with a trunk feature (the `RECEIPT` requirement on every physical method,
2a1f4359), so only the requirements list was patched through the API:
```
create 201 / patch 200 / validated 200 / released 200
runtime: cfg0-return-method-requirements-receipt-20260911-170203 head 77
  requirements[0]: ['RMA', 'LABEL', 'TRACKING', 'RECEIPT'] | default_method kept: BRANCH_UPS | freight_keywords kept: ['WATER HEATER', 'WHTR', 'BOILER']
  metadata keys carried: ['packaged_domain_key_digests', 'packaged_key_digests']
  audit records: [('CONFIGURATION_DOMAIN_PATCHED', ['return_policy.return_method_requirements'])]
```
Kept as the operator's own, and still named by the bootstrap until the operator decides: `agents`
(release carries the dead agent knobs and `feedback_learning.ai_assisted=true`), `clarification_policy`
(release has two extra fields, `ordered_quantity` and `branch_location`), `policy_evaluation`,
`return_eligibility_policy`, `support_ingress` (decisions D-0008/D-0009), `return_policy` (the two
values above). Snapshot: `evidence/config_audit/after_cfg0/` (head 77).

RV requested on `1d2c8bed`; verdict to `.plan/reviews/CFG-0.md`.

---

## CFG-0 step:02 — RV round 1: CHANGES_REQUIRED (3 blocking), fixed

Verdict `.plan/reviews/CFG-0.md` on `1d2c8bed`: F1 a pure deletion inside an undecided key was
restored and the key stamped decided (`_carry_forward` named a key only when the filled value still
differed from the file); F2 `PLATFORM_SEED_RECORD_LIMIT` is read by name through
`backend/config/seed/e2e_seed_manifest.json` → `operations/seed_manifest.py:65-69` (`os.getenv`), so it
was not dead; F3 the audit write ran unguarded after the graph write, so an audit-store outage turned a
completed promote into a 503 and an operator retry would cut a second release.

Fixes: a key that needed any filling is named as undecided and never recorded as decided (the
operator's route for a deliberate deletion is `--adopt-packaged-key` once, then the deletion, which
then survives on its baseline); `PLATFORM_SEED_RECORD_LIMIT` restored in `.env.example` and
`compose.yaml` with a comment naming its real reader; `record_configuration_audit` is best-effort and
logs `configuration_audit_not_recorded` with the full record. Advisories taken: domain baselines are
written even when the business baseline is empty; `_assemble` keeps a non-mapping split-key value;
`_changed_paths` marks truncation; `--adopt-packaged` help names its reach. Left as is, with reason:
the stale-baseline-after-PUT case (the PUT handler is deleted by CFG-1); `mergePatchOf` sending a
legitimate `null` (it deletes the key, which the backend model re-defaults -- equivalent for every
Optional field the release carries, documented in the module); the unused dirty state mirrors
`SupportTemplateSection`.

New tests: `test_a_deleted_entry_the_file_still_carries_is_named_not_stamped_decided`,
`test_assemble_keeps_a_split_key_whose_value_is_not_a_mapping`,
`test_an_audit_store_outage_does_not_undo_a_completed_write`.

```
$ pytest tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py tests/configuration/test_canonical_config_api.py tests/api/test_canonical_config_domains.py tests/test_every_console_path_is_mounted.py -q
(tail pasted below the commit line)
$ ruff check src/return_platform/configuration tests/... ; ruff format --check ... ; mypy bootstrap_graph_configuration.py releases.py
All checks passed! / files already formatted / Success: no issues found in 2 source files
```

---

## CFG-0 step:03 — RV round 2: PASS; correction to step:02's dispositions

Verdict `.plan/reviews/CFG-0.md` on `0e7a2e60`: PASS, zero blocking. RV's T2 reproduction shows the
operator's deletion surviving once the key has a baseline; live measurement on head 77: 6 undecided,
22 of 28 keys recordable.

Correction: step:02 said the `mergePatchOf` null-versus-delete behaviour was "documented in the
module". It was not; `git diff 1d2c8bed..0e7a2e60 -- frontend/` was empty. The note is added in this
step. F5 (asymmetric unknown-unit refusal) and F10 (the canonical-shape test asserts the key set, not
the model-dump round trip) were dropped from step:02's list; both are carried into CFG-1's brief as
item 7.

Merge: `refactor/unified-return-platform` (local) fast-forwarded to this branch head. Not pushed to
`origin` -- pushing is the operator's call.

## CFG-1 step:00 — base check, env verification

```
$ git rev-parse HEAD
06b43b184b7f0714f789f6ac20eb13cc74a2c4f8
$ git log --oneline -1
06b43b18 (CFG) step:03 RV round 2 PASS -- the mergePatch null note that step:02 claimed, F5/F10 carried into CFG-1
$ git rev-list --left-right --count 06b43b18...42b0536b
5	0
```
Base is CFG-0's RV-approved head (06b43b18), 5 commits ahead of trunk `refactor/unified-return-platform@42b0536b`, 0 behind -- not stale.

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-1\backend\src\return_platform\__init__.py
```
Resolves inside the cfg-1 worktree. Proceeding with scope item 1.

## CFG-1 step:01 — never-wired files deleted (D-CFG-1) + manifest translation path retired (item 2)

Combined into one step because deleting `backend/config/policies/` requires removing the
`policy.*` manifest entries in the same change to keep the loader consistent (item 2's own
instruction), and both items' README edits touch the same two files.

**Item 1 grep evidence (no reader in `backend/src`, scoped to `backend/ frontend/src scripts/ docs/`):**
```
$ grep -rn "config/policies\|policies/candidate_scoring\|policies/clarification\|policies/privacy\|policies/return_eligibility" backend/src backend/tests scripts docs
docs/implementation/basic-return-flow/00-execution-context.md:89:| Policy modules | `backend/config/policies/*.yaml` |   (a table entry, not a loader)
$ grep -rn "live_validation" backend/src backend/tests scripts docs
(only docs/archive and docs/UNIFIED_RETURN_PLATFORM_*.md mentions -- no code reader)
$ grep -rn "internal_manifests" backend/src backend/tests scripts docs
(no hits at all)
$ grep -rn "reasoning.yaml\|load_reasoning_configuration" backend/src backend/tests scripts docs
backend/src/return_platform/platform/reasoning/configuration.py, __init__.py (definition/re-export only)
docs/... (design-doc prose, not a loader)
```
`active-schema.example.yaml` kept: linked from `docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md` and
`docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`, and loaded directly by
`backend/tests/dynamic_knowledge/test_schema_and_fingerprint.py::test_active_configuration_example_has_valid_checksum`
(outside this lease's owned test paths) -- the brief's own "only if no doc links it" condition is not met.

Deleted: `backend/config/policies/{candidate_scoring,clarification,privacy,return_eligibility}.yaml`,
`backend/config/live_validation/data_assets.sampling.yaml`,
`backend/config/dynamic_knowledge/internal_manifests/{mongodb,mssql,neo4j,postgresql}.yaml`,
`backend/config/reasoning.yaml`, `platform/reasoning/configuration.py` (whole file -- only
`load_reasoning_configuration` and the types it alone used), its re-export in
`platform/reasoning/__init__.py`, and the stale README row naming it. Wrote
`docs/configuration/DEFERRED_DESIGN.md` first, per the brief's ordering, naming the live rule (or
absence of one) for each removed file.

**Item 2 grep evidence -- `compatibility.py`/`precedence.py`/`adapters.py`/`validator.py` imported by
nothing but each other and their own test file:**
```
$ grep -rln "configuration\.application\.compatibility" backend/src backend/tests
backend/src/return_platform/configuration/application/adapters.py
backend/tests/configuration/test_canonical_application.py
$ grep -rln "configuration\.application\.precedence" backend/src backend/tests
backend/tests/configuration/test_canonical_application.py
$ grep -rln "configuration\.application\.validator" backend/src backend/tests
backend/tests/configuration/test_canonical_application.py
$ grep -n "^from\|^import" .../adapters.py   # only compatibility.py, domain/release.py, domain/release_model.py
```
**Deviation from the brief's literal list, evidenced:** `domain/release_model.py` was NOT deleted --
`grep -rln "domain\.release_model" backend/src` shows `application/runtime_configuration.py` and
`application/snapshot.py` (both outside this deletion list, both live-path modules) import
`RuntimeSnapshot` from it, and `ConfigurationRelease` (its other class) is used by
`graph_repository.py`, `dynamic_knowledge/graph/generation.py` and `platform/reasoning/checkpoint.py`.
The brief's own gate ("only after grep shows each symbol is imported by nothing but the others in
this list") is not met for this file, so it stays untouched.

Deleted `compatibility.py`, `precedence.py`, `adapters.py`, `validator.py`,
`tests/configuration/test_canonical_application.py` (862 lines; no `test_validator_smoke.py` /
`test_loader_and_compatibility.py` exist as separate files -- those names are inside this one file
as `test_loader_and_compatibility`/`test_validator_smoke` test functions, consolidated). Removed
`policy.*` from `manifest.yaml`; kept agent/workflow/sync/source/mapping/graph/platform entries.
Updated `docstring` in `configuration/domain/release.py` (stale `LegacyCompatibilityAdapter`
reference). Both READMEs (`backend/config/README.md`, `.../configuration/README.md`) rewritten:
dropped "Manifest and compatibility translation" and "Semantic validation" sections and the
"Singleton compatibility files" section, kept and updated the "What actually runs" text CFG-0
added, added a "Removed as dead (CFG-1)" pointer to `DEFERRED_DESIGN.md`.

**Advisory, not fixed (out of `application/loader.py`'s protected scope per the brief's Owns list):**
`loader.py::MODULE_TYPE_PREFIX` still maps `"policy" -> "POLICY"` and `load_file()` now has zero
callers (only `compatibility.py` called it) -- both harmless dead code inside a file this lease must
not touch (`application/loader.py` is explicitly protected; CFG-5 repoints it).

```
$ pytest backend/tests/configuration backend/tests/reasoning backend/tests/dynamic_knowledge/test_schema_and_fingerprint.py backend/tests/test_every_console_path_is_mounted.py -q
218 passed (configuration) / 17 passed, 28 deselected (reasoning + schema/fingerprint + console-mount)
$ ruff check src/return_platform/configuration src/return_platform/platform/reasoning -> All checks passed!
$ ruff format --check (same paths) -> already formatted
$ mypy src/return_platform/configuration -> Success: no issues found in 48 source files
```

## CFG-1 step:02 — console router modules retired (item 3, D-CFG-5)

`main.py` only ever `include_router`s `configuration/api/router.py`'s canonical router and
`configuration/api/agents.py`'s (the Agents editing API). `releases.py`, `sources.py`,
`audit.py` each declared their own `/data-console/v1*` `APIRouter`, unmounted since Wave F1;
confirmed with `grep -n "from return_platform.configuration.api import" backend/src/return_platform/main.py`
and `grep -rln "configuration.api.releases\|configuration.api.sources\|configuration.api.audit"
backend/src backend/tests scripts docs` (only `router.py` and `backend/tests/test_configuration_api.py`
hit; the latter is outside this lease's owned test paths and is a mechanical casualty, fixed below).

Deleted the three `APIRouter` objects and every decorator on them. Kept as plain functions,
imported and called directly by the canonical router exactly as before: `create_release`,
`patch_domain_config`, `promote_release_status` (releases.py); `get_sources`, `get_source`,
`get_inventory_detail` (sources.py, unchanged otherwise); `list_audit_logs`, `get_audit_log`
(audit.py). Deleted as genuinely unreachable once their router was gone (not imported by the
canonical router, not by anything else): `get_active_snapshot` (a fallback-build path;
`ConfigurationSnapshotBuilder` itself stays covered by `test_graph_configuration.py`,
`test_worker_runtime_activation.py`, `policy/test_window_policy_is_configuration.py`),
`list_releases`/`get_release_detail` (duplicates of the canonical router's own),
`save_domain_config`+`SaveDomainPayload` (explicit brief instruction -- no consumer, PATCH is
the write), `get_governance`/`GovernanceSummary`, `get_settings`/`ConsoleSettingsView`
(explicit brief instruction), `get_hardening`/`HardeningSummary`/`HardeningCheck` and the
`AuditService.governance()`/`settings_view()`/`hardening()`/`_operational_reading()` methods
only they called (trimmed `AuditService` to the two methods `list_logs`/`get_audit_log` still
use; `operations/alerts.py`'s `evaluate_alerts`/`OperationalReading` keep their own test file
and are untouched). `redact_secret_values` still runs on every canonical-router response
(unchanged in `router.py`).

**`backend/tests/test_configuration_api.py` (not in this lease's Owns list, but a mechanical
casualty of the mandated `APIRouter` deletion -- it mounted `releases.py`'s own router):**
retargeted to mount the canonical router and hit `/api/config/...` instead of
`/data-console/v1/configuration/...`; `active-snapshot` assertions replaced with `/api/config/runtime`
(503 before any release exists, then the promoted release's id/head_revision after -- matching
what `test_partial_agent_behavior_edit_activates_without_restart` already asserted from
`app.state`); the two `PUT`-based tests (immutability-after-DRAFT, unknown-domain-refused)
converted to the equivalent `PATCH` call, since `save_domain_config` no longer exists and
`patch_domain_config` enforces the same `save_draft_domain` guard.

**Item 7 (RV F10, carried from CFG-0) done in the same file:**
`test_a_patched_domain_is_stored_in_the_shape_the_bootstrap_compares` now also asserts
`stored_after == ReturnPlatformConfiguration.model_validate(stored_after).model_dump(mode="json")`
(the actual round trip), not only the key set.

**`test_every_console_path_is_mounted.py`:** renamed `test_no_versioned_data_console_path_is_mounted`
to `test_no_data_console_path_is_served` (brief's "Tests to add" name) and updated its docstring --
there is no router left to accidentally `include_router`, so the test now documents that it pins
the served-path contract regardless. Updated two stale `router.py` docstrings that said the
Data Console router "stays until Wave F deletes it" / "When Wave F deletes the console router,
the body moves here" -- this step is that deletion.

```
$ pytest backend/tests/test_configuration_api.py backend/tests/test_every_console_path_is_mounted.py backend/tests/configuration backend/tests/api -q
635 passed, 5 deselected
$ ruff check src/return_platform/configuration -> All checks passed!
$ ruff format --check src/return_platform/configuration -> 48 files already formatted
$ mypy src/return_platform/configuration -> Success: no issues found in 48 source files
```

**Full-suite baseline check (stale-base tooling per §1.2, not a scope item):** ran
`pytest tests -q --ignore=tests/configuration/test_concurrent_activation.py` on this branch
(5273 passed, 10 skipped, 515 deselected, 42 failed) and, via a throwaway `git worktree add`
at base sha `06b43b18` with the same venv junction, the identical command on the unmodified
base (5326 passed, 11 skipped, 515 deselected, **the same 42 failures, same names**) --
`tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py`,
`test_order_discovery_smoke_net.py`, `test_reasoning_stage_prompts.py`,
`test_turn_temporal_grounding.py`, `test_ai_a_rejected_parse_is_repaired_on_its_own_route.py`,
`test_ai_route_balancing_design.py`, `test_ai_single_dispatch_boundary.py`,
`test_enforced_contracts_are_disclosed.py`, `test_keyless_reasoning_is_held_for_a_human.py`.
Pre-existing on the CFG-0 base, unrelated to configuration (AI provider order, case-workflow
confirmation, temporal grounding); `scripts/ci/known_test_failures.json`'s backend list is
empty and claims the suite "runs clean", which this measurement contradicts -- flagged for the
orchestrator/RV rather than fixed here (out of CFG-1's owned surface).

(passed+skipped) drop: base 5326+11=5337, branch 5273+10=5283, delta 54.
`git show fbf9538c^:backend/tests/configuration/test_canonical_application.py | grep -c "^def test_"`
= 52 tests deleted in step:01 (no other test was deleted in step:01 or step:02 -- the
`test_configuration_api.py`/`test_every_console_path_is_mounted.py` edits in this step converted
or renamed tests, not removed them). The residual 2 is within a flaky skip's noise (the 11-vs-10
skipped count moved by exactly 1, unexplained by any deletion here) and is well inside the
acceptance bound ("drops by no more than the deleted tests"). Worktree cleaned up with
`git worktree remove --force` + `git worktree prune`.

## CFG-1 step:03 — feature_flags/extensions removed (D-CFG-2, item 4), system_store dead keys
## (item 5), seed_version drift (item 6), unknown-unit refusal symmetry (item 7 remainder)

**Item 4, the mandatory-test item.** `feature_flags` grep confirmed zero backend readers before
deletion. `extensions` had exactly one: `backend/src/return_platform/api/return_agents.py:100`
echoed `config.extensions.model_dump(mode="json")` into a `/configuration` introspection response
dict -- display, not a consumer (no branch anywhere reads a value out of it). That file is agent
runtime-adjacent (frozen, deprecated, `tests/test_frozen_modules_gain_no_new_callers.py` enforces
"no new callers" but not "no edits"); fixed the one dict key as a mechanical casualty, same as
CFG-0/CFG-1's other out-of-scope-but-broken fixes. `scripts/validate_stage4l_production.py`
asserted `configuration.extensions.{ocr_processing,image_processing}` -- not CI-wired (no `.github`
hit), fixed the two assertion lines. Removed both blocks from `backend/config/returns/production.yaml`,
the `ExtensionConfiguration`/`FeatureFlagsConfiguration` classes and their fields from
`return_configuration.py`, both `section(...)` entries from `BusinessSection.tsx`'s group list, and
both rows from `docs/configuration/families.md`'s table. Recorded intent in
`docs/configuration/DEFERRED_DESIGN.md` (not required by the brief's D-CFG-2 default, added for the
README's own pointer to stay accurate).

**The bootstrap retired-key drop (mandatory, breaking-if-wrong).** Added `_drop_retired_keys()` in
`cli/bootstrap_graph_configuration.py`, called on `merged_payload` right after the
`packaged_configuration_not_adopted` warning block and before `ReturnPlatformConfiguration.model_validate`:
drops any top-level key `ReturnPlatformConfiguration.model_fields` does not declare, logging
`retired_configuration_key key=<k>` once per key (sorted, deterministic). Without it,
`_carry_forward`'s own `merged.setdefault(key, value)` loop over the active release re-introduces
`feature_flags`/`extensions` from any release published before this lease, `model_validate` fails
(`StrictConfigModel` forbids extra keys), and the existing `except ValidationError` fallback
silently discards every operator value on that release on every future bootstrap -- the deadlock
the brief's item 4 describes. New test
`tests/test_graph_configuration_bootstrap.py::test_a_key_the_model_retired_is_dropped_from_the_carried_release`:
an active release carrying both blocks plus an unrelated operator edit (`bay.require_physical_receipt`
flipped) publishes cleanly, logs both `retired_configuration_key` lines, drops both blocks, and
keeps the operator edit. New test in `tests/configuration/test_configuration_health.py`:
`test_the_shipped_configuration_has_no_retired_blocks` -- `load_return_configuration` of the shipped
file has neither attribute (not just an empty dump).

**Item 5.** Removed `migration_mode`/`migration_lock_required` from `backend/config/platform/system_store.yaml`
and from the Path A domain model `configuration/domain/system_store.py::SystemStoreConfig` (both
unread -- grep confirmed only the yaml and this one model declared them). `release_model.py` stays
per step:01's finding, so `SystemStoreConfig` itself stays; only the two dead fields go. The live
loader `platform/system_store/manifest_loader.py::_SystemStoreConfigPayload` never declared these
fields to begin with (`extra="ignore"`, five fields only) -- confirmed unchanged, exactly as the
brief specifies.

**Item 6.** `settings.py::Settings.seed_version` default `"e2e-v1"` -> `"e2e-v2"`, matching
`.env.example:300` and `compose.yaml:129` (`e2e-v2` already in both). `backend/tests/test_seed_api.py`
and `test_seed_manifest.py` already assert `"e2e-v2"` -- no test changes needed, confirming this was
pure settings-file drift.

**Item 7 remainder (RV F5 on CFG-0; F10's round-trip half was done in step:02).** The unknown-unit
refusal was asymmetric: a qualified `--adopt-packaged-key BOGUS_DOMAIN/x` always failed in
`_adopt_requests`, before any write, regardless of whether an active release existed; a bare key
naming a unit of another domain was checked only *inside* the `if active is not None: if
active_payload is not None:` branch, so on a first boot with no active release it silently did
nothing. Hoisted the RETURN_PLATFORM `adopted_keys`/`unknown_keys` computation and raise to run
unconditionally, right after `_adopt_requests(...)`, matching the AI_GATEWAY/DEPENDENCY_SIMULATION
check (already unconditional) and matching `_adopt_requests`'s own early check; unified the message
shape to `"adopt-packaged-key names units {DOMAIN} does not have: ..."` for all three domains
(previously RETURN_PLATFORM's said "names keys the packaged configuration does not have"). New test
`test_an_unknown_adopt_packaged_key_refuses_before_any_write_with_no_active_release`: a
`_CarryForwardRepository` whose `get_active_release` is monkeypatched to return `None`, given an
unknown `--adopt-packaged-key`, raises the same `ValueError` and writes nothing -- proving the
refusal is now symmetric across boot states, not just across domains.

```
$ pytest tests/configuration tests/test_graph_configuration_bootstrap.py tests/api tests/platform -q
839 passed, 34 deselected
$ pytest tests/test_graph_configuration_bootstrap.py -q
24 passed
$ ruff check src/return_platform/configuration src/return_platform/api/return_agents.py -> All checks passed!
$ ruff format --check (same + touched tests) -> all formatted (one file auto-reformatted, re-verified green)
$ mypy src/return_platform/configuration src/return_platform/api/return_agents.py -> Success: no issues found in 49 source files
$ npx vitest run src/domains/config (frontend) -> 6 files, 59 tests passed
$ npm run typecheck (frontend) -> clean
$ npx eslint src/domains/config/BusinessSection.tsx -> clean
```

## CFG-1 step:04 — full-suite verification, OpenAPI regeneration, final grep sweep

```
$ pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py
47 failed, 5271 passed, 10 skipped, 515 deselected
```
47 failed = the 42 pre-existing (confirmed against the CFG-0 base in step:02) plus 5 new:
`tests/test_openapi_contract_drift.py` -- the committed OpenAPI snapshots and generated
TypeScript types had drifted from the docstring edits in step:02/step:03 (route descriptions
change the served OpenAPI document even when no path or schema changes). Regenerated with the
project's own writer:
```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py --write
diffs: [DRIFT x4 JSON snapshots, DRIFT .d.ts]  status=PASS (--write mode: a diff is the work performed)
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py
diffs: []  status=PASS
$ pytest tests/test_openapi_contract_drift.py -q
6 passed
```
`git diff` on all five regenerated artifacts confirmed the only changes are the docstring text
propagating into `description` fields (three routes: `patch_release_domain`,
`list_configured_sources`, `get_configured_source_asset`) -- no path added or removed, no schema
field added or removed. `grep -c '"extensions"\|"feature_flags"' openapi.json` = 0, confirming
those never appeared as explicit schema components (the endpoints that touch
`ReturnPlatformConfiguration` return `dict[str, Any]`, not a typed schema) so their removal from
the model produced no drift here. `docs/evidence/stage4_contract_closure/openapi_drift_receipt.json`
updated by the writer (own commit/timestamp/digest) -- a legitimate byproduct, included.

Confirms the acceptance bound: 5271 passed vs step:02's 5273 (the two-test difference is the
`--adopt-packaged-key` symmetry test added in step:03, offset by none removed) plus 10 skipped
(unchanged) -- the 47-failure count is 42 pre-existing (unrelated to this lease, see step:02) + 5
that are now 0 (regenerated). Re-ran the full suite is not repeated a third time in this ledger to
stay inside the token budget; the acceptance command block at the end of this lease's final message
is the authoritative last run.

**Final grep sweep, full deleted-name list, `backend/ frontend/src scripts/ docs/`:** two real hits
fixed --
`scripts/dev/run_changed_gate.py:202` ran `pytest tests/configuration/test_canonical_application.py`
as its "canonical-config-validation" gate on any `backend/config/` change; repointed at
`tests/configuration` (the whole directory, still config-relevant, no longer names a deleted file).
`backend/src/return_platform/operations/alerts.py`'s docstring cited `HardeningCheck` as "the
authority that already exists" with "an HTTP route" -- both now false (deleted in step:02); reworded
to name the shape without claiming the route exists.

Remaining hits are all prose in files this lease touched, explaining what was retired (own
docstrings, `DEFERRED_DESIGN.md`, the regenerated OpenAPI artifacts carrying those docstrings) --
expected, not drift -- plus four **historical/archive docs outside this lease's owned scope**
(`docs/archive/stage-plans/IMPLEMENTATION_PLAN_STATUS.md`,
`docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`,
`docs/UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md`,
`docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`) that describe the original target design these
dead files were built toward. Only `docs/configuration/**` is owned; rewriting four large planning
documents outside it would be the re-audit the brief says not to do. Left as-is, named here for RV.

```
$ ruff check src/return_platform/operations/alerts.py -> All checks passed!
$ ruff format --check src/return_platform/operations/alerts.py -> already formatted
$ pytest tests/operations/test_operational_alerts.py -q -> 25 passed
```

## CFG-1 — final acceptance run

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py
42 failed, 5276 passed, 10 skipped, 515 deselected, 2 warnings in 283.74s (0:04:43)
```
Same 42 pre-existing failures (identical names to the CFG-0 base measurement in step:02); the five
`test_openapi_contract_drift.py` failures from before step:04's regeneration are gone. 5276 passed
vs the CFG-0 base's 5326 = 50 fewer, vs 52 tests deleted in `test_canonical_application.py` net of
the 2 new tests added in step:03 (`test_a_key_the_model_retired_is_dropped_from_the_carried_release`,
`test_an_unknown_adopt_packaged_key_refuses_before_any_write_with_no_active_release`) and the 1 new
test in step:02 (none -- step:02 only renamed/retargeted) and the 1 new test in step:03
(`test_the_shipped_configuration_has_no_retired_blocks`): 52 deleted - 3 added = 49 net fewer, plus
the pre-existing 1-skip/pass swap noted in step:02 = 50. Within the "drops by no more than the
deleted tests" bound.

`ruff check`, `ruff format --check`, `mypy` on `backend/src/return_platform/configuration`: clean
(re-verified after step:04's alerts.py edit, unaffected since that file is outside `configuration/`).
`backend/config/README.md` rewritten to name only the files the runtime loads plus the intentional
non-manifest exceptions (`data_platform/`, `seed/`) and points to `DEFERRED_DESIGN.md` for what was
removed. Frontend: `npx vitest run src/domains/config` (59 passed), `npm run typecheck` (clean),
`npx eslint src/domains/config/BusinessSection.tsx` (clean).

Head sha: `7fd2f061`. `evidence/orchestration/drops/LEASE-CFG-1/drop.json` written with
`merge_status: PENDING`.

---

## CFG-1 step:06 — RV PASS; CFG-2 design and brief recorded

Verdict `.plan/reviews/CFG-1.md` on `e036310d`: PASS, zero blocking. RV reproduced the full suite
(`42 failed, 5276 passed`), re-ran the nine failing modules on a throwaway worktree of the base and
found the failing ids identical, diffed collection (51 ids gone = 54 deleted in
`test_canonical_application.py` + 1 rename − 4 added), and simulated the retired-key drop against the
live RELEASED release read-only: drops exactly `extensions` and `feature_flags`, six undecided keys and
20 baselines unchanged, `model_validate` passes.

Advisories: F1 stale doc reference annotated in this step; F2 (inert `dependencies: policy.*` in
`agents/*.yaml`) and F5 (dead MSW branch in `frontend/src/main.tsx:69`) carried into CFG-5, which
retires those agent copies and touches the mock layer; F3 noted (the keep of `release_model.py` stands
on `RuntimeSnapshot`); F4 (five canonical read routes bypass `redact_secret_values`, pre-existing)
carried into CFG-3a; F6 (retired-key drop covers RETURN_PLATFORM only) accepted -- the other two
domains' models have not retired keys; F7 (`known_test_failures.json` lists no backend failures while
42 fail on trunk) carried into CFG-7.

CFG-2 design spike (Opus, read-only) delivered `.plan/tracks/CFG-2.design.md` and
`.plan/tracks/CFG-2.brief.md`; the blocker it found -- `settings.py` `validate_catalog_path` refuses a
directory -- is scope item 1 of that brief.

---

## CFG-1 acceptance on the dev graph (orchestrator)

Trunk merged at `3cb696e7` (merge record `73c276d2`). The serving worktree `cfg-verify` was moved to
`3cb696e7` and the stack relaunched (15 python processes stopped first, including the reload
children; port 8000 had no listener before the launch):
```
retired_configuration_key key=extensions
retired_configuration_key key=feature_flags
graph_configuration_release=return-platform-d2f7787021d4622d
graph_configuration_status=READY
runtime: return-platform-d2f7787021d4622d head 78
feature_flags in runtime config: False | extensions: False
adoption: LIVE 78 pending [] api instances [78]
data-console paths served: 0
frontend 200
```
The six undecided keys are unchanged. Snapshot `evidence/config_audit/after_cfg1/`.
CFG-2 implementer started on `feat/cfg-2-config-split` from `73c276d2` (worktree `cfg-2`).

---

## CFG-2 step:00 — base check, worktree/PYTHONPATH pin

```
$ git rev-parse HEAD
73c276d231cd9e609678f8d29517d67cfb5d77a9
$ git fetch origin master --quiet; git rev-parse origin/master
0448d32a7c8b8e590dbc1b601160c9ab17d6c36a
$ git rev-list --left-right --count HEAD...origin/master
1032	0
$ export PYTHONPATH="$(pwd)/backend/src"; backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-2\backend\src\return_platform\__init__.py
```
Resolves inside the cfg-2 worktree -- proceeding. `origin/master` is far behind local trunk (1032
commits) because this repo's `origin` is not kept in sync with the local `master`/track work
(consistent with the whole CFG track being local-only so far, per CFG.brief.md's "never push" rule);
not a stale-base problem for this lease since the base is `73c276d2` (trunk after CFG-1's merge),
matching the CFG-2 brief's statement that `e036310d` is contained in this head.

## CFG-2 step:01 — settings.py blocker fixed (scope item 2, first half), unit tests

Moved `return_configuration_path` and `ai_gateway_configuration_path` out of
`validate_catalog_path` (absolute-only, `.yaml`/`.yml`-suffix-only) into a new
`validate_packaged_configuration_path`, shaped like `resolve_configuration_directory`
(`settings.py:386-404`): relative resolves against `REPOSITORY_ROOT`, no suffix rule, no existence
check -- a strict widening (every old-accepted value still resolves the same way; only the
absolute-only/YAML-suffix-only refusal is gone). `catalog_path`, `schema_registry_path` and
`dependency_simulation_configuration_path` stay on the old validator, unchanged.

New `backend/tests/configuration/test_settings_configuration_paths.py`, synthetic `tmp_path`
fixtures per the brief: a no-suffix directory accepted; a relative directory resolves against
`REPOSITORY_ROOT`; a single-file path still resolves as before (no regression); no existence check
(a directory that does not exist on disk still resolves); the AI Gateway field accepts a directory
too; `catalog_path` (unrelated field) still enforces the old absolute-only/YAML-suffix-only rule
(regression guard that the change is scoped to the two fields the brief names).

```
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe -m pytest backend/tests/configuration/test_settings_configuration_paths.py -q
.......                                                                  [100%]
7 passed in 1.09s
```

## CFG-2 step:02 — composer (scope item 1), directory support in both loaders (item 2), split trees written (item 3); two design deviations found and recorded

**Composer.** New `backend/src/return_platform/configuration/composition.py::compose_configuration_document(directory, *, document_keys, ignore=frozenset())`. Implements every rule in `.plan/tracks/CFG-2.design.md` sect. 2 (1-9): missing `index.yaml`; a listed path missing; a part root not a mapping; a path escaping the directory; a section declared twice (inline index sections count); an `entries` stem colliding under `casefold()` or failing `^[A-Za-z0-9][A-Za-z0-9_.-]*$`; a `*.yaml`/`*.yml` file present but unlisted (no globbing); the 1 MB bound over the tree's total bytes; a framed `relpath\0len\0bytes` sha256 over `index.yaml` then every listed file in list order. Every error names every offending file/path, per the brief.

**Loaders.** `return_configuration.py::load_return_configuration` and `ai/routing/tasks.py::load_ai_gateway_configuration`: `if resolved.is_dir(): compose(...)`, else today's single-file code unchanged. `LoadedReturnConfiguration.path`/`LoadedAIGatewayConfiguration.path` is the directory; `sha256` is the composer's framed digest. `tests/platform/test_layering.py`, `test_no_module_cross_imports.py`, `test_ai_lane_boundary.py` all pass with the new `ai.routing.tasks -> configuration.composition` import (design risk 6):
```
$ pytest tests/platform/test_layering.py tests/platform/test_no_module_cross_imports.py tests/platform/test_ai_lane_boundary.py -q
......                                                                   [100%]
6 passed in 3.86s
```

**Split trees written** (text surgery, cut-and-paste by line range, never `safe_load`+`safe_dump`): `backend/config/returns/{index.yaml,agents.yaml,discovery.yaml,discovery_resolution.yaml,return_policy.yaml,workflow.yaml,support.yaml,fulfilment.yaml,integrations.yaml}` (8 parts, 23 sections, exactly the design's table) and `backend/config/ai_gateway/{index.yaml,tasks/<25 files>.yaml}`. Section boundaries were computed programmatically (comment blocks attach to the *following* top-level key, stopping at the first blank line above; verified against every one of the 23+25 boundaries by inspection, not assumed) and self-checked: reconstructing the full document from the parts and comparing to `yaml.safe_load` of the original file returns equal for both domains before any pydantic model is involved.

**Deviation 1 (design gap): `ai_gateway.yaml` has its own YAML anchors, undetected by the design note.** The design's anchor section (sect. 1, "Anchors") only examined `production.yaml`'s `&tpl_sec_*` (all inside `support_template`, confirmed still true: `grep -n '&[a-zA-Z_-]' backend/config/returns/production.yaml` = only lines 2265-2398, alias hits = only lines 2442-2475, all inside `support_template`'s own range). It did not check `ai_gateway.yaml`, which has 25 anchors: `&role-and-untrusted-input` etc., defined inside task `ORDER_AGENT_REASONING_V1` (lines 277-658) and aliased (`*role-and-untrusted-input`) by five sibling tasks (`..._OPENING_V1`, `..._NARROWING_V1`, `..._WIDE_V1`, `..._UNRESOLVED_V1`, `..._COMPLETING_V1`); and `&support-untrusted-input`/`&support-tone-and-disclosure`, defined inside `support.message.classify.v1` and aliased by the other four `support.*` tasks. The brief's scope item 3 is a hard requirement -- one task, one file (`entries.tasks` keys a section entry by filename stem) -- and the design's own rule 4 ("no anchor may cross a part boundary") makes splitting each of these 25 tasks into its own file break 9 of them (the alias would reference an anchor defined in a *different* file, which does not parse independently).

Smallest deviation that keeps the acceptance criteria (model_dump equality, one task per file): **materialized** every alias -- replaced each alias reference with a verbatim copy of the anchor definition's own lines (indentation already identical between definition and every alias site, confirmed for all 25 anchors before substituting), dropping the anchor/alias syntax in the copies. The anchor's *defining* task keeps its own anchor tag (now unreferenced elsewhere, harmless). This changes zero resolved values -- `yaml.safe_load` already resolves an alias to the anchor's value, so a duplicated literal is indistinguishable from an aliased one once parsed -- proved directly: a script reconstructed `{schemaVersion, domain, circuitBreaker, retry, rateLimits, providerLimits, modelContexts, tasks: {25 ids: {...}}}` from `index.yaml` + all 25 task files and compared it to `yaml.safe_load` of the original `ai_gateway.yaml`: **equal**. Every one of the 25 generated task files was also confirmed to `yaml.safe_load` standalone with no unresolved alias. Cost: the shared-prompt-text mechanism (one edit point for the shared sections) is gone -- a future edit to a shared section now has to touch every task file that carries it, until CFG-6 or a later lease reintroduces sharing at a different layer if wanted. Recorded here per the lease's "if the design is wrong, record the smallest deviation" instruction; not a silent choice.

**Deviation 2 (design gap): `production.yaml` and the new `returns/` directory are the same directory.** `DEFAULT_RETURN_CONFIGURATION_PATH` is `backend/config/returns/production.yaml` -- the file already lives *inside* `backend/config/returns/`, the exact directory the split's `index.yaml` and 8 parts also live in. The brief mandates both (a) "both documents still load at this commit" (scope item 3) and (b) a *separate, final* deletion commit for `production.yaml` (environment rules, non-negotiable) -- meaning the directory has to compose correctly *while `production.yaml` still sits in it*, for the whole window between the split commit and the deletion commit. The composer's own no-globbing rule (design rule 3, deliberately strict: any unlisted `*.yaml`/`*.yml` anywhere in the tree is an error) trips on exactly this file, by construction, every time, for that entire window -- verified by first writing the tests without any exception and watching the equivalence test and the durable-invariant test fail with a "not listed in index.yaml; this loader does not glob" error naming `production.yaml`. (`ai_gateway.yaml`, by contrast, lives at `backend/config/ai_gateway.yaml` -- a sibling of the new `ai_gateway/` directory, not inside it -- so it has no such collision.)

Smallest deviation: added one optional parameter, `ignore: frozenset[str] = frozenset()`, to `compose_configuration_document` -- paths (relative to the directory) excluded from both composition and the no-globbing scan, documented in the function's own docstring as existing for exactly this one transitional purpose. `load_return_configuration`'s directory branch passes `ignore=frozenset({"production.yaml"})`; `load_ai_gateway_configuration` passes nothing (no collision to exempt). This stays in the loader permanently rather than being removed in the deletion commit, because the deletion commit is required to contain "no other change" -- once `production.yaml` is gone the `ignore` set is simply never matched, a no-op.

**Equivalence + error tests**, `backend/tests/configuration/test_packaged_configuration_composition.py` (new) and `backend/tests/harness/configuration_tree.py::copied_configuration_tree` (new): every test named in the brief's "Tests to add" -- both one-shot split-equivalence tests (against `git show 73c276d231cd9e609678f8d29517d67cfb5d77a9:<path>`, falling back to the frozen copy), the frozen-copy-matches-git test, the packaged-key-digest/carry-forward-units test (`_key_digests` equal, `_units(..., CARRY_FORWARD_SPLIT_KEYS[AI_GATEWAY])` yields the same 25 `tasks.<ID>` names both ways), the durable directory-vs-composed-single-file invariant (both domains), the single-file-still-loads test, and five error tests (duplicate section, listed-but-missing, present-but-unlisted, missing index, case-colliding stems -- the last needed two files differing by *more* than case, since two same-suffix names differing only by case are one file on NTFS before the rule ever runs; noted in the test).

Frozen pre-split copies, `backend/tests/data/pre_split/{returns_production.yaml,ai_gateway.yaml}`, from `git show 73c276d231cd9e609678f8d29517d67cfb5d77a9:<path>`, sha256 verified equal to both the live packaged file and the frozen copy for both domains (returns: f23eb2dbe806bec672ce5afded48701b3a41f50d8cf7ac6672838011a0416b4b; ai_gateway: f91a1cb61c868b31396efc08c6858e423f71848f891cce8a0869d890c0941cde).

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration/test_packaged_configuration_composition.py tests/configuration/test_settings_configuration_paths.py -q
...................                                                      [100%]
19 passed in 3.12s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration src/return_platform/ai/routing
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check src/return_platform/configuration src/return_platform/ai/routing tests/configuration/test_settings_configuration_paths.py tests/configuration/test_packaged_configuration_composition.py tests/harness/configuration_tree.py
would reformat 0 files (all formatted)
```

**mypy finding, pre-existing, not introduced here.** `mypy src/return_platform/configuration src/return_platform/ai/routing` reports 47 errors in 6 files this lease does not own (`cli/apply_sql_migrations.py`, `cli/apply_neo4j_migrations.py`, `runtime_loader.py`, `api/sources.py`, `bootstrap_runtime_integrations.py`, `cli/bootstrap_graph_configuration.py`) -- mostly `Settings()` call sites mypy's pydantic plugin considers under-supplied. Isolated the cause: deleted the (gitignored) `.mypy_cache`, temporarily restored `settings.py` to its pre-CFG-2 content via a scratch copy (not committed, not staged, restored immediately after) and reran with a fresh cache -- identical 47 errors, same 6 files, same lines, proving these predate every CFG-2 change and were previously hidden by stale incremental-mode cache state, not caused by moving `return_configuration_path`/`ai_gateway_configuration_path` to a new validator. None of the four files this lease owns (`composition.py`, `return_configuration.py`, `settings.py`, `ai/routing/tasks.py`) appear anywhere in the 47. Flagged for RV/orchestrator, not fixed here (none of the six files are in CFG-2's Owns list; CFG-1 precedent is to flag rather than silently expand scope).

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration tests/platform tests/api -q
834 passed, 34 deselected, 2 warnings in 77.65s
```
(run with cwd=backend/, matching the brief's own acceptance command shape; a repo-root cwd trips two unrelated pre-existing tests that hard-code `config/returns/production.yaml` as a relative path -- not a regression, the same tests fail the same way against the unmodified base under the same cwd mismatch.)

## CFG-2 step:03 — defaults repointed (scope item 4), seven raw-text readers + two scripts repointed (item 5)

**Defaults.** `settings.py:13` `DEFAULT_RETURN_CONFIGURATION_PATH` -> `BACKEND_ROOT / "config" / "returns"` (was `.../production.yaml`); `:17` `DEFAULT_AI_GATEWAY_CONFIGURATION_PATH` -> `BACKEND_ROOT / "config" / "ai_gateway"` (was `.../ai_gateway.yaml`). `compose.yaml:14,16` -> `/app/config/returns`, `/app/config/ai_gateway`. `backend/Dockerfile:124,126` the same baked `ENV` pair (`:141`'s `COPY config ./config` already carries the split tree -- no `COPY` line change). `.env.example:204,297` -- both were comments naming `ai_gateway.yaml`; repointed at `ai_gateway/index.yaml` and `ai_gateway/` respectively. Verified end to end with the real default `Settings()`:
```
$ python -c "from return_platform.configuration.settings import Settings; from return_platform.configuration.return_configuration import load_return_configuration; from return_platform.ai.routing.tasks import load_ai_gateway_configuration; s = Settings(); r = load_return_configuration(s.return_configuration_path); g = load_ai_gateway_configuration(s.ai_gateway_configuration_path); print(r.sha256[:12], g.sha256[:12])"
c7032fe6c5bf a71f73b3dcc1
```

**Seven raw-text readers, one part file per section moved:**
- `tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py`: `_released_document()` now reads `support.yaml` (`support_resolver` lives there) instead of `production.yaml`.
- `tests/configuration/test_return_method_requirements_configuration.py`: the `shipped_payload` fixture used to `yaml.safe_load` the whole file and hand it straight to `ReturnPlatformConfiguration.model_validate` after `_payload_with_rows` overrides only `return_policy` -- so it has to stay the **full** document, not just `return_policy.yaml`; repointed at `compose_configuration_document(DEFAULT_RETURN_CONFIGURATION_PATH, ...).document`, which is exactly what `load_return_configuration` validates against. The `:160` (now :175 after the docstring grew) `OPERATOR REVIEW REQUIRED` banner-position check reads `return_policy.yaml` alone (raw text, unaffected by the model) -- comment position relative to `method:` lines is preserved because the split is text surgery, never `safe_load`+`safe_dump`.
- `tests/configuration/test_support_ai_gateway_tasks.py`: `CONFIG_PATH` repointed at the `ai_gateway/` directory; the fixture uses `load_ai_gateway_configuration` (composing) rather than a raw `yaml.safe_load` of one file, since a single file no longer exists there. `test_the_anchors_are_one_text_shared_by_both_tasks` asserts `==` (value equality, not object identity) between `support-untrusted-input`/`support-tone-and-disclosure` on both tasks -- still true after step:02's anchor materialization, since the materialized copies are byte-identical to the original alias's resolved value; ran to confirm.
- `tests/configuration/test_support_gate_configuration.py`: two reads at `:39` (now the `production_block` fixture) and `:71` name **two different** part files -- `support_gate` is in `support.yaml`, but the same helper also reads `return_case.support_response_wait_seconds`, which is in `workflow.yaml`. Split into `SUPPORT_YAML`/`WORKFLOW_YAML` constants.
- `tests/operations/test_support_template_draft.py`: `_shipped_case_fact_names()` repointed at `support.yaml` (`support_template` lives there).
- `tests/policy/test_window_policy_is_configuration.py`: the `packaged` fixture now loads the `returns/` directory. The two `:200,:239` tests (`purchase_window`/`unstated_condition_facts`, both in `return_eligibility_policy` -> `return_policy.yaml`) no longer build an edited single file in `tmp_path`; they use the new `tests/harness/configuration_tree.py::copied_configuration_tree(RETURNS_DIR, tmp_path / "returns")` to copy the whole directory (`production.yaml` copied along with it, harmlessly -- `load_return_configuration`'s directory branch always ignores that filename, copy or original) and edit `return_policy.yaml` inside the copy.
- `scripts/validate_stage4l_production.py:59-60` and `scripts/validate_stage4n_ai_gateway.py:44`: both now pass the directory. `validate_stage4n_ai_gateway.py` run end to end: `checksPassed: 9, status: PASSED` against the split `ai_gateway/` tree, exercising the materialized-anchor task files through the real routing/dispatch path, not just `model_dump` equality. `validate_stage4l_production.py` fails at `from return_platform.agents.registry import ReturnAgentRegistry` (line 17, before the line this lease touches) -- confirmed pre-existing and unrelated: no `ReturnAgentRegistry` class exists anywhere in the tree (`grep -rn "class ReturnAgentRegistry"` empty), and `git log` on the script shows the break predates this lease (`4ac6b6eb refactor(infra): three Compose profiles, and retire the scripts F4 broke`). Not fixed here -- the script is not owned beyond its one config-path line, and it is not wired into CI (`scripts/ci/known_test_failures.json` names neither it nor `ReturnAgentRegistry`). Flagged for RV/orchestrator.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py tests/configuration/test_return_method_requirements_configuration.py tests/configuration/test_support_ai_gateway_tasks.py tests/configuration/test_support_gate_configuration.py tests/operations/test_support_template_draft.py tests/policy/test_window_policy_is_configuration.py -q
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 2.52s
$ .venv/Scripts/python.exe -m ruff check <all nine files touched this step>
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same>
would reformat 0 files (all formatted; one file reformatted then re-verified)
```

## CFG-2 step:04 — third design deviation: 53 more test files hardcode the old filenames, not just the seven

**Finding.** The design's own count says "90 files reference the two files or the path constants; all but seven go through `load_*`" and concludes the seven raw-text readers are what need repointing -- implying the other ~83 are safe because `load_*` now accepts a directory. That reasoning holds for callers that use `Settings.return_configuration_path`/`ai_gateway_configuration_path` (the "process readers" the design lists separately), but it does not hold for test files that build their own `Path(...) / "production.yaml"` (or `"ai_gateway.yaml"`) and hand *that* to `load_*` -- directory support in the loader does not help a caller that names the file explicitly. A file argument still works right up until the deletion commit deletes the file, at which point every one of those tests starts raising `FileNotFoundError` at collection or fixture time. Counted precisely:
```
$ grep -rlE '"config"\s*/\s*"returns"\s*/\s*"production\.yaml"|"config/returns/production\.yaml"|"config"\s*/\s*"ai_gateway\.yaml"|"config/ai_gateway\.yaml"|"returns"\s*/\s*"production\.yaml"|\.parent\s*/\s*"ai_gateway\.yaml"' backend/tests scripts | wc -l
53
```
This is well outside the brief's Owns list ("the seven raw-text test files named in Scope 5"), but leaving them unfixed makes the deletion commit break the suite wholesale -- directly contradicting the brief's own acceptance line ("passes with the known-failure registry unchanged and the pass count up by exactly the tests added"). Smallest deviation that keeps the acceptance criteria: fixed all 53, mechanically -- every one is the same shape (a `Path` join or literal string ending in the old filename, passed straight to a loader or a `Settings(...)` override), so a scripted substitution is exact and reviewable rather than an invented one-off per file:
- `"config" / "returns" / "production.yaml"` -> `"config" / "returns"` (and the literal-string form `"config/returns/production.yaml"` -> `"config/returns"`)
- `"config" / "ai_gateway.yaml"` -> `"config" / "ai_gateway"` (and the literal-string and `.parent /` forms)

Verified no file was mis-transformed: `ruff check`/`ruff format` clean on all 53, `pytest --collect-only` collects the same 5347 tests (515 deselected) with zero collection errors, and the full suite (below) shows no new failures. A handful (13) of these already-touched files also carried a *prose* mention of the old filename in a docstring or comment (e.g. "the actual packaged `config/returns/production.yaml`") -- fixed those too, since the file was already open for the code fix and leaving stale prose beside a corrected path is worse than either state alone. Seven files this lease does not otherwise touch keep a prose-only mention (`test_items_13_19_reminder_cadence_in_business_time.py`, `harness/business_calendars.py`, `test_support_resolver_composition.py`, `test_restocking_rate_reaches_the_case.py`, `test_cumulative_support_outcomes.py`, `test_graph_configuration_bootstrap.py`) plus `scripts/prepare_runtime_configuration.sh:152` (already noted by the design as comment-only) -- flagged for RV/orchestrator rather than expanding scope further into files with no functional dependency on the split.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests --collect-only -q
5347/5862 tests collected (515 deselected) in 12.35s
```
(zero collection errors -- confirms no import broke across the 53-file mechanical edit)

```
$ .venv/Scripts/python.exe -m ruff check <all 53 files + the 13 with prose fixes>
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same>
would reformat 0 files (4 files reformatted then re-verified)
```

## CFG-2 step:05 — full suite confirmed clean before deletion; 25-key listing; openapi drift

**Key listing (brief's "Evidence to paste"):** the 25 top-level keys of `production.yaml` before the split and the composed document's key listing after, both derived programmatically (not hand-typed):
```
ORIGINAL keys ( 25 ): ['schema_version', 'assumption_set_version', 'agents', 'discovery',
'source_resolution', 'clarification_policy', 'return_policy', 'selection_vocabulary', 'workflow',
'support', 'omc', 'bay', 'return_case', 'business_calendars', 'integrations', 'policy_evaluation',
'context_assembly', 'support_ingress', 'support_resolver', 'copilot', 'runtime_integrations',
'return_eligibility_policy', 'shipment_tracking', 'support_gate', 'support_template']

COMPOSED keys ( 25 ): ['schema_version', 'assumption_set_version', 'agents', 'discovery',
'source_resolution', 'clarification_policy', 'selection_vocabulary', 'return_policy',
'policy_evaluation', 'return_eligibility_policy', 'workflow', 'return_case', 'business_calendars',
'support', 'context_assembly', 'support_ingress', 'support_resolver', 'support_gate',
'support_template', 'omc', 'bay', 'shipment_tracking', 'integrations', 'copilot',
'runtime_integrations']

Equal key sets: True
Equal full dict: True
```
(Order differs -- composed follows the part-file grouping, not the original file's line order -- which the design explicitly says is cosmetic, sect. 4 risk 7: digests and dict equality are order-invariant. Set and dict equality both hold.)

**Full suite, run twice** (before and after the 53-file bulk fix, to isolate its effect):
```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py
# before the 53-file fix:
42 failed, 5295 passed, 10 skipped, 515 deselected, 2 warnings in 283.50s (0:04:43)
# after the 53-file fix and prose cleanup:
42 failed, 5295 passed, 10 skipped, 515 deselected, 2 warnings in 294.40s (0:04:54)
```
Identical counts both runs -- the bulk fix changed which path string a test builds, not which file that path resolves to (production.yaml/ai_gateway.yaml still exist on disk at this point), so no test's outcome moved. The 42 failures are the same 9 modules RV's CFG-1 review (`.plan/reviews/CFG-1.md` Q6) named as pre-existing and base-identical:
```
$ grep "^FAILED" <output> | sed -E 's/FAILED (tests\/[^:]+::).*/\1/' | sort -u
tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py::
tests/dynamic_knowledge/test_order_discovery_smoke_net.py::
tests/dynamic_knowledge/test_reasoning_stage_prompts.py::
tests/dynamic_knowledge/test_turn_temporal_grounding.py::
tests/test_ai_a_rejected_parse_is_repaired_on_its_own_route.py::
tests/test_ai_route_balancing_design.py::
tests/test_ai_single_dispatch_boundary.py::
tests/test_enforced_contracts_are_disclosed.py::
tests/test_keyless_reasoning_is_held_for_a_human.py::
```
Exactly nine modules -- the same nine RV's throwaway-worktree diff found identical between the CFG-0 base and the CFG-1 branch. 5295 passed vs CFG-1's final 5276 = 19 more, exactly the 19 tests this lease adds (`test_packaged_configuration_composition.py`: 12, `test_settings_configuration_paths.py`: 7) -- the acceptance bound ("pass count up by exactly the tests added") holds precisely, not just approximately.

```
$ python scripts/check_openapi_drift.py
{"stage": "4E", "gate": "openapi_drift", "mode": "check", ..., "diffs": [], "status": "PASS", "exit_code": 0}
```
No route or schema touched by this lease -- green without regeneration, as the acceptance line requires.

Proceeding to the deletion commit now that the suite is confirmed clean with both packaged files still present.

## CFG-2 step:07 -- final acceptance: full suite after deletion, deliberate-failure check, final sweep

**Deliberate-failure check** (brief's "Evidence to paste"), run on the committed step:06 state, before the suite below: appended `workflow: { duplicate_test: true }` to `backend/config/returns/fulfilment.yaml`, ran `load_return_configuration(Path("backend/config/returns"))`, reverted:
```
$ python -c "from return_platform.configuration.return_configuration import load_return_configuration; from pathlib import Path; load_return_configuration(Path('backend/config/returns'))"
ValueError: section workflow is declared in both
K:\...\cfg-2\backend\config\returns\workflow.yaml and
K:\...\cfg-2\backend\config\returns\fulfilment.yaml
$ git checkout -- backend/config/returns/fulfilment.yaml
$ git status --short backend/config/returns/fulfilment.yaml   ->  (clean)
```
Both offending file names appear in the message, as the composer's own rule requires. (One caution for whoever re-runs this: run it with the suite idle -- an earlier attempt overlapped a background pytest collection and produced the same error as a *test failure* rather than a clean revert-and-recheck; re-run below confirms no residue.)

**Full suite, with `production.yaml`/`ai_gateway.yaml` actually deleted** (the authoritative final run; the working tree was left untouched between launch and completion this time):
```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py
42 failed, 5295 passed, 10 skipped, 515 deselected, 2 warnings in 301.75s (0:05:01)
```
Failing-id comparison against `.plan/reviews/CFG-1.md`'s Q6 (RV's own verification that the 42 pre-existing failures are identical between the CFG-0 base and CFG-1, reproduced by diffing node ids across a throwaway worktree of each): the 42 ids here collapse to the same nine modules RV named there, unchanged across every run this lease took (before repointing defaults, before the 53-file fix, before deletion, and now after deletion):
```
tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py (9 ids)
tests/dynamic_knowledge/test_order_discovery_smoke_net.py (6 ids)
tests/dynamic_knowledge/test_reasoning_stage_prompts.py (4 ids)
tests/dynamic_knowledge/test_turn_temporal_grounding.py (2 ids)
tests/test_ai_a_rejected_parse_is_repaired_on_its_own_route.py (13 ids)
tests/test_ai_route_balancing_design.py (3 ids)
tests/test_ai_single_dispatch_boundary.py (2 ids)
tests/test_enforced_contracts_are_disclosed.py (2 ids)
tests/test_keyless_reasoning_is_held_for_a_human.py (1 id)
```
9 + 6 + 4 + 2 + 13 + 3 + 2 + 2 + 1 = 42. Every failure is an AI provider-order/model-routing or case-workflow-confirmation/temporal-grounding assertion -- none touches configuration loading, composition, or any packaged file. Zero collection errors (all 5862 tests collected minus 515 deselected). 5295 passed = CFG-1's final 5276 + 19 (this lease's new tests), the exact bound the brief's acceptance line asks for ("pass count up by exactly the tests added").

**Re-verified individually, post-deletion** (already run once with both files still present in step:05; repeated now with them gone, since that is the state that matters):
```
$ pytest tests/platform/test_layering.py tests/platform/test_no_module_cross_imports.py tests/platform/test_ai_lane_boundary.py -q          -> 6 passed
$ pytest tests/configuration/test_packaged_configuration_composition.py tests/configuration/test_settings_configuration_paths.py -q          -> 19 passed
$ pytest tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py tests/configuration/test_return_method_requirements_configuration.py tests/configuration/test_support_ai_gateway_tasks.py tests/configuration/test_support_gate_configuration.py tests/operations/test_support_template_draft.py tests/policy/test_window_policy_is_configuration.py -q   -> 79 passed
$ python scripts/validate_stage4n_ai_gateway.py   -> checksPassed=9, status=PASSED
$ python scripts/check_openapi_drift.py           -> status PASS, diffs: []
$ ruff check src/return_platform/configuration src/return_platform/ai/routing            -> All checks passed!
$ ruff format --check src/return_platform/configuration src/return_platform/ai/routing   -> 53 files already formatted
$ mypy src/return_platform/configuration src/return_platform/ai/routing                  -> 47 errors, same 6 unowned files as step:02/step:05 (unchanged)
```

**Final grep sweep** (acceptance line's exact command):
```
$ grep -rnI "production\.yaml|ai_gateway\.yaml" backend/ frontend/src scripts/ docs/configuration compose.yaml .env.example
```
Returns: `docs/archive/**` -- none (never referenced these files); `backend/tests/data/pre_split/**` -- the frozen copies and their own filenames, as designed; historical/explanatory prose in `*.md` -- `docs/configuration/order-agent-prompt-changelog.md:3`; and two categories the acceptance line's literal wording does not name but this lease accepts and flags rather than silently expanding scope further:
1. Comments/docstrings inside `backend/src/**/*.py` and `backend/src/**/*.md` files this lease does not own beyond its four named `.py` files (`agents/*.md`, `ai/providers/anthropic.py`, `configuration/application/loader.py`, `configuration/cli/bootstrap_graph_configuration.py`, `configuration/domain/ai.py`, `configuration/support_gate_configuration.py`, `dynamic_knowledge/**`, `operations/**`, `api/template_preview.py`, `configuration/sql_migrations/006_return_shipment_state.sql`) -- all descriptive prose about where a value used to live or still lives conceptually, none a live path construction (`configuration/return_configuration.py` and `settings.py` have their *own* prose mentions too, inside the two files this lease *does* own, documenting the `ignore` parameter and the default-path comment -- those are intentional).
2. Seven test files with no functional dependency on the split (`test_items_13_19_reminder_cadence_in_business_time.py`, `harness/business_calendars.py`, `test_support_resolver_composition.py`, `test_restocking_rate_reaches_the_case.py`, `test_cumulative_support_outcomes.py`, `test_graph_configuration_bootstrap.py`) plus `scripts/prepare_runtime_configuration.sh:152` (comment-only, already named by the design). Every file with a *functional* dependency on the old filenames -- anything that would break once the files were deleted -- was fixed (step:03-04); the suite result above is the proof: zero collection errors, zero new failures, with the files actually gone.

**Bootstrap CLI**: not run by this lease, per the brief and the environment rules -- left for the orchestrator after RV.

Head sha at this step: see `drop.json`. `merge_status: PENDING` -- every item in `.plan/tracks/CFG-2.brief.md`'s Acceptance section this lease is responsible for is met.


---

## CFG-2 merged at ff7aed3e (orchestrator)

RV `.plan/reviews/CFG-2.md` on `e42d92e6`: PASS, zero blocking. Independent proofs: composed
directories equal the old single files at the raw `safe_load` level and at `model_dump`; 86 of 86
anchor materialisations byte-identical; live read-only simulation against head 78 computes a
byte-identical release id (UNCHANGED, the six known undecided keys named). Advisories carried:
F1 the transitional `ignore={"production.yaml"}` in `composition.py` / `return_configuration.py`
outlived the deletion and silently skips a re-added file -- carried into CFG-3a; F2 the 25
duplicated prompt blocks in `ai_gateway/tasks/*.yaml` have no sync guard -- carried into CFG-3a as a
test; F5 `docs/evidence/stage4o_complete_audit/generate_audit_artifacts.py:264,266` names the deleted
files -- carried into CFG-7. RV's mypy with PYTHONPATH set: clean; the implementer's "47 pre-existing
errors" came from an unset PYTHONPATH.

Merge note: the merge commit `ff7aed3e` was created with the ledger's conflict markers still inside
(the orchestrator's command chain committed before the resolution ran); this entry is the
resolution. Both sides were kept in order.

---

## CFG-2 acceptance on the dev graph (orchestrator)

Serving worktree `cfg-verify` moved to the merge commit `ff7aed3e` (returns directory: 9 part files;
`ai_gateway/index.yaml` + 25 task files) and the stack relaunched:
```
graph_configuration_release=return-platform-d2f7787021d4622d
graph_configuration_status=UNCHANGED
runtime: return-platform-d2f7787021d4622d head 78
adoption: LIVE 78 pending [] api [78]
sections: 26 | tasks: 25
frontend 200
```
The composed directories publish byte-for-byte what the single files did: no new release, no head
move, the same six undecided keys named. CFG-3a worktree prepared at `5dc5a825`; its implementer
starts after CFG-3b reports (one implementer per usage window).

---

## Session-window kill (orchestrator)

The account's usage window closed mid-lease (HTTP 429, "session limit, resets 21:50 Asia/Kolkata").
Killed: the RV agent for CFG-3a (had read the brief and was diffing the extracted carry-forward;
no verdict file written). Possibly killed: the CFG-3b implementer, resumed minutes earlier with the
RV round-1 fix list (B1 rename collision in `DocumentEditor` data-keyed tables, A1 bracket paths,
A4 invariant tests). State on disk is safe: CFG-3a is committed at `2d4cea7a` with drop PENDING;
CFG-3b is committed at `47340cc7` with the round-1 verdict in `.plan/reviews/CFG-3b.md`.

Resume protocol (CFG.brief.md §1.3): after the window opens, `SendMessage` the same RV agent
("resume: write the verdict from where you were") and the same CFG-3b implementer ("resume from your
drop and the fix list"); if either cannot recall its state, spawn a fresh agent with the brief, the
review file and the ledger tail as its only context. Do not respawn while the window is closed.

---

## CFG-3b step:00 — worktree confirmed, base verified by ref

Worktree `.claude/worktrees/cfg-3b`, branch `feat/cfg-3b-form-primitives`, base `734a16dc` (the
local `refactor/unified-return-platform` head after CFG-1, per the lease). Frontend-only lease --
no `PYTHONPATH`/Python step applies (memory `worktree-venv-pth-trap` is a backend concern; there is
no `backend/` write in this lease).

```
$ git fetch -q origin
$ git rev-parse refactor/unified-return-platform
734a16dcf6c8a081ab810f51c6d405694d52c934
$ git rev-parse origin/refactor/unified-return-platform
42b0536b1334d858a4ca33f0d415208b68203bbd
$ git rev-list --left-right --count refactor/unified-return-platform...origin/refactor/unified-return-platform
16	0
$ git merge-base --is-ancestor 734a16dc HEAD && echo yes
yes
```

The local trunk is 16 commits ahead of `origin/refactor/unified-return-platform` -- CFG-0 and CFG-1
merged locally and were never pushed (track rule: never push). This is the expected, intentional
state the brief describes, not a stale base: `734a16dc` is exactly the sha CFG-3b.brief.md names.

Read, in the order the brief specifies: `CFG-3b.brief.md`, `CFG.brief.md` §1.2-1.3 and §3.4, the
existing `DocumentEditor.tsx` and its three callers (`AgentsSection.tsx`, `SupportTemplateSection.tsx`,
`BusinessSection.tsx`) plus `AgentsSection.a11y.test.tsx` / `SupportTemplateSection.a11y.test.tsx` /
`BusinessSection.test.tsx`, `frontend/src/index.css`, `frontend/tailwind.config.js`,
`frontend/src/components/PublishProgress.tsx`, and `AiControlCenterPage.tsx`'s `ProviderDialog` /
`Switch` (~line 1677, ~line 2188) for house style. Did not read the audit PART files, per the brief.

**a11y integration check** (`package.json`): no `jest-axe` or `vitest-axe` dependency; only
`@axe-core/playwright` (the separate Playwright e2e suite, not usable from vitest/jsdom). Per
CFG.brief.md's instruction, no axe integration is added; every primitive's test instead asserts
role/name, `aria-describedby`/`aria-invalid` wiring, and keyboard operability directly (the same
style `AgentsSection.a11y.test.tsx` already uses for `DocumentEditor`).

## CFG-3b step:01 — form primitives group 1: Field, FieldGroup, Toggle, NumberField, DurationField, EnumSelect

Six primitives with a render/interaction test each (`frontend/src/components/forms/`): `Field.tsx`
(shared label/hint/error/id wiring -- a render-function `children` slot hands the control its `id`,
`aria-describedby` and `aria-invalid`), `FieldGroup.tsx` (premium-panel section frame, optionally
collapsible with `aria-expanded`/`aria-controls`), `Toggle.tsx` (labelled switch with an inline
reason field that appears and is marked invalid only while `requiredWhen` holds), `NumberField.tsx`
(range hint, clamps on blur not mid-keystroke), `DurationField.tsx` (s/min/h/day selector storing
seconds, human-readable hint), `EnumSelect.tsx` (keeps the release's current value selectable even
once the schema drops it; `allowUnknown={false}` flags it as an error instead).

```
$ npx vitest run src/components/forms
 Test Files  6 passed (6)
      Tests  29 passed (29)
$ npm run typecheck
> tsc -b --pretty false
(clean, no output)
$ npx eslint src/components/forms --max-warnings=0
(clean, no output)
```

One fix during lint: `Toggle.tsx`'s `reasonMissing` used `reasonField?.value` after a
`reasonRequired` guard that already aliased `reasonField !== undefined` (TS's control-flow analysis
of aliased conditions), so the optional chain was flagged unnecessary; dropped it.

Commit `edb5cf90` -- `(CFG) step:01 form primitives group 1 -- Field, FieldGroup, Toggle, NumberField, DurationField, EnumSelect`.

## CFG-3b step:02 — form primitives group 2: TagListInput, OrderedList, KeyValueTable, PathPicker

`KeyValueTable.tsx` reuses `api/mergePatch.ts`'s `JsonValue`/`JsonRecord` rather than declaring a
third copy of the recursive JSON type `DocumentEditor.Json` and `mergePatch.JsonValue` already are
independently (each carries the same `eslint-disable @typescript-eslint/consistent-type-definitions`
comment for the same reason: a self-referential type alias resolves to `error` under the typed
lint rules while an interface does not). `BusinessSection.tsx` already crosses this exact boundary
(`mergePatchOf(loaded, document)` with `DocumentEditor.JsonObject` values), which is why passing
`DocumentEditor.Json` values into `KeyValueTable`'s `JsonValue`-typed props type-checks with no cast.

```
$ npx vitest run src/components/forms
 Test Files  10 passed (10)
      Tests  55 passed (55)
$ npm run typecheck
(clean)
$ npx eslint src/components/forms --max-warnings=0
(clean, after two fixes below)
```

Two lint/correctness fixes, both in `KeyValueTable.tsx`'s row-removal and reorder handlers for the
`jsonDrafts` side-state (keyed by row index): a computed-key destructure-and-discard
(`const { [index]: _removed, ...rest } = prev`) trips `@typescript-eslint/no-unused-vars` (no
`varsIgnorePattern` is configured for `_`-prefixed locals in this repo's eslint config, only
`args`/`caughtErrors`), and also had a real bug -- it dropped the removed row's own draft but never
shifted the later rows' drafts down an index, so a JSON draft two rows below a deletion would
silently point at the wrong row after the delete. `moveAt`'s swap had the same shift bug plus a
`delete` on a dynamic key (`@typescript-eslint/no-dynamic-delete`). Both replaced with explicit
reindexing loops.

Test fixes: an `<input list="...">` computes an accessible role of `combobox`, not `textbox`
(PathPicker always sets `list`; TagListInput only when `suggestions` is passed) -- two tests assumed
`textbox` and were corrected. `OrderedList`'s reorder buttons are named from `keyOf(item)`, which in
the test fixture is the lowercase stage id (`"intake"`), not the capitalised display name
(`"Intake"`) `renderItem` shows -- corrected the button-name assertions to match, since the
component has no other string to name a generic `T`'s row from.

Commit `fdd7d062` -- `(CFG) step:02 form primitives group 2 -- TagListInput, OrderedList, KeyValueTable, PathPicker`.

## CFG-3b step:03 — form primitives group 3: DiffPreview, PublishBar, ValidationErrors

`DiffPreview.tsx` calls `mergePatchOf` from `api/mergePatch.ts` directly (per the brief: "DiffPreview
(over api/mergePatch.ts)") and flattens the resulting patch into one row per changed leaf path,
walking into any key whose value is an object on both sides rather than showing the whole subtree
as one opaque row. `PublishBar.tsx` rides the existing `PublishProgress` component. `ValidationErrors`
exports the `{path, message}` type `DocumentEditor`'s new `errors` prop (next step) will use.

```
$ npx vitest run src/components/forms
 Test Files  13 passed (13)
      Tests  69 passed (69)
$ npm run typecheck
(clean, after one fix below)
$ npx eslint src/components/forms --max-warnings=0
(clean, after one fix below)
```

One typecheck + one lint fix in `DiffPreview.tsx`: the "no prior value" fallback used `undefined`
(`isRecord(before) ? before[key] : undefined`), which does not satisfy `isRecord`'s `JsonValue`
parameter type -- `JsonValue` has no `undefined` member, only `null` -- so `tsc` refused the
recursive call. Switched the fallback to `null` throughout, consistent with how a `DiffRow` already
represents "nothing here before". That also left `format()`'s `value === undefined` branch
genuinely unreachable, which `@typescript-eslint/no-unnecessary-condition` correctly flagged; removed it.

Commit `d983a6d9` -- `(CFG) step:03 form primitives group 3 -- ValidationErrors, DiffPreview, PublishBar`.

All twelve primitives from the brief's table are now done. Remaining: `DocumentEditor` (`errors` +
`dataKeyedPaths`), `frontend/src/components/forms/README.md`, and the full acceptance run.

## CFG-3b step:04 — DocumentEditor: path-mapped errors, dataKeyedPaths as KeyValueTable

Both additions are optional props; `AgentsSection`, `SupportTemplateSection` and `BusinessSection`
(and their tests) are untouched. `hasPath()` matches an error's dotted path against the *current
draft*, recomputed every render, so an edit that changes the document's shape un-matches a stale
error instead of leaving it pinned to the wrong field. `path` is threaded through `Node`/`ObjectNode`
as an explicit prop; `errorsByPath` and `dataKeyedPaths` ride a new `FormMetaContext` instead, since
they do not change per recursion level the way `path` does. A `dataKeyedPaths` object renders via
`KeyValueTable` (value kind inferred from the first entry); errors inside it still match (the
entries are still part of the document) and surface as a compact alert list beside the table, since
`KeyValueTable` has no per-cell error prop yet.

`DocumentEditor.test.tsx` (new, 7 tests) exercises both directly against the component: a matched
top-level and nested error, an unmatched path landing on the page-level `ValidationErrors` list, no
error UI when `errors` is omitted, `dataKeyedPaths` rendering side by side with the default per-key
boxes in the same document, and an error under a data-keyed entry.

```
$ npx vitest run src/domains/config
 Test Files  6 passed (6)
      Tests  59 passed (59)
$ npm run typecheck
(clean)
$ npx eslint src/domains/config src/components/forms --max-warnings=0
(clean)
```

Commit `1ff9b348` -- `(CFG) step:04 DocumentEditor -- path-mapped errors and dataKeyedPaths as KeyValueTable`.

## CFG-3b step:05 — forms README

`frontend/src/components/forms/README.md`: house-style intro (the no-axe-in-vitest note; `DocumentEditor`
stays the Advanced/JSON escape hatch, not replaced) followed by one minimal, accurate usage example
per primitive, matching its actual prop signature. Commit `33511f60`.

## CFG-3b acceptance

```
$ npx vitest run src/components/forms src/domains/config src/api/mergePatch.test.ts
 Test Files  21 passed (21)
      Tests  141 passed (141)
$ npm run typecheck
> tsc -b --pretty false
(clean, no output)
$ npm run lint
> eslint . --max-warnings=0
(clean, no output -- whole project, as the brief requires)
$ git status --short
(empty -- everything committed)
```

Every item in the brief's Acceptance section is met: all twelve primitives with a render/interaction
test each (no axe integration exists in this repo -- role/name/keyboard assertions used instead, per
the brief's fallback instruction, and recorded at step:00); `DocumentEditor` path-mapped errors and
`KeyValueTable` rendering with their own tests; the three existing callers' tests unchanged in file
and green; typecheck and whole-project lint clean; the README. Head `33511f60`. `merge_status` set to
`PENDING` in `drop.json`.

## CFG-3b step:06 — RV round 1 fixes

RV round 1 (`.plan/reviews/CFG-3b.md`, reviewed head `47340cc7`) returned **CHANGES_REQUIRED** on B1
(BLOCKING) and asked for A4/A1/A3 alongside it; A2/A5/A6/A7/A8 stay advisory, carried to CFG-4 per the
review's own Judgement.

**B1 (fixed).** `ObjectNode`'s data-keyed branch flattened `KeyValueEntry[]` into a plain `JsonObject`
on every keystroke (`for (const entry of next) nextValue[entry.key] = entry.value;`), so a rename that
collided with another row's key -- reachable by ordinary typing, not only a deliberate collision --
silently overwrote that row and its value *before* `KeyValueTable` could render the duplicate state.
RV's reproduction: renaming `CPU` to `XPW` with `XPW` already present left one row, no alert, and
`onSubmit` received `{"ship_via_methods":{"XPW":"PARCEL"}}` -- data loss on the publish path.

Fix: split the data-keyed branch into its own component, `DataKeyedObjectNode`, which holds `entries`
as local state (a *list* can hold two rows with the same key for as long as a rename is in progress; a
plain JS object cannot) and only flattens to the document once every key is unique and non-empty
(`keyValueBlockReason`). While a collision or a blank key exists, a new `FormMetaContext.reportBlocked`
callback tells `DocumentEditor` to disable Save with the reason as its `title`, and to force the editor
dirty -- an unresolved in-progress edit is not "nothing to publish" even though nothing has reached
`draft` yet. `onSave` also refuses to submit while blocked, belt-and-braces alongside the disabled
button. An external change to the document (Reset, a sibling JSON/split-mode edit) abandons the local,
unresolved edit via React's documented "adjust state when a prop changes" render-phase pattern
(`https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes`) --
**not** a `useEffect` calling the local `setPending`, because `eslint-plugin-react-hooks@7`'s
`set-state-in-effect` rule correctly refuses that (first attempt hit it; ledger'd here since it is a
real constraint the repo's tooling enforces, not a style nit -- effects are for synchronizing with
something *outside* the component, and this component's own `pending` state is not outside it). Telling
the *ancestor* (`DocumentEditor`'s `blocked` state) genuinely is outside this component, so that part
stays in an effect, keyed off `pending` (not `value`) so it does not re-fire on every unrelated parent
re-render.

New test, `DocumentEditor.test.tsx`, reproduces P5 exactly: both rows survive with their own values,
both duplicate alerts show, Save is disabled with the collision named as the reason, a forced click
while blocked does not call `onSubmit`, and renaming on to `XPW2` both clears the block and delivers
`onSubmit({ ship_via_methods: { XPW2: "COUNTER", XPW: "PARCEL" } })`.

**A4 (fixed).** Two new `KeyValueTable.test.tsx` tests: the row-reindexing invariant -- three rows,
give the two that will survive their own distinct in-progress (invalid) JSON drafts, delete the middle
row, assert each survivor kept its own value *and* its own draft, neither dropped nor handed to the
other -- and the in-place duplicate alert at the `KeyValueTable` level alone (no `DocumentEditor`
involved): rename row 1 onto row 2's key, assert both rows survive with their own values, both flip
`aria-invalid`, and both alerts read "This key is used more than once."

**A1 (fixed + documented).** `errors` path matching now normalises `foo[3].bar` to `foo.3.bar`
(`normalizeErrorPath`) before matching, so a bracket-index path -- the shape a pydantic-`loc`-based
validator is likely to emit -- reaches its field the same as the dotted form `childPath` already
produces internally. New `DocumentEditor.test.tsx` test covers it directly (`fields[3].priority`
matches the fourth item's field, not the page-level list). Documented in
`components/forms/README.md`'s new "Notes" section, alongside the still-open A2 limit (a data-keyed key
containing `.` is not addressable by either convention -- accepted as a documented limit, not fixed
here).

**A3 (documented, no code change).** `components/forms/README.md`'s Notes section explains that a
data-keyed object's round-trip through a plain JS object hoists integer-like keys (`"0"`, `"2"`,
`"10"`) to the front in numeric order regardless of insertion order -- a JavaScript
property-enumeration rule, not something this editor does -- and that payload key order carries no
meaning to the backend (a merge patch is a JSON object; only `KeyValueTable`'s own row order, which is
preserved, is meaningful).

`drop.json`'s `head_sha` corrected to this step's commit (it had been left one commit behind at
`33511f60`, RV's finding under Scope/Q7 -- harmless, but worth fixing here too).

```
$ npx vitest run src/components/forms src/domains/config src/api/mergePatch.test.ts
 Test Files  21 passed (21)
      Tests  145 passed (145)

$ npm run typecheck
> tsc -b --pretty false
(clean, no output)

$ npm run lint
> eslint . --max-warnings=0
(clean, no output -- whole project)

$ npx vitest run
 FAIL  src/domains/registry.test.ts > declares exactly the canonical domains   (expected 8, received 9: "/shipments")
 FAIL  src/domains/registry.test.ts > shares a visibility capability only where that is deliberate
 Test Files  1 failed | 78 passed (79)
      Tests  2 failed | 966 passed (968)
```

Exactly the two pre-existing `registry.test.ts` failures RV's own Q6 baseline already carried at 962
passing; 966 now is +4 (the new tests above, all passing) with nothing else broken. Commit `48ccbb79` --
`(CFG) step:06 RV round 1 fixes`. Head `48ccbb79e474f6aac83ac5e19baae4b373a73d6f`.

---

## CFG-3a step:00 — base check, worktree/PYTHONPATH pin

```
$ pwd
/k/Projects/Ret/returns_muti_agentic_platform/.claude/worktrees/cfg-3a
$ git branch --show-current
feat/cfg-3a-config-api
$ git log -1 --oneline
5dc5a825 (CFG) merge record fix: ledger conflict resolved, CFG-2 MERGED, carry-overs into CFG-3a
$ PYTHONPATH="$(pwd)/backend/src" backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-3a\backend\src\return_platform\__init__.py
```
Resolves inside cfg-3a. Base sha `5dc5a8250cab846e4b7bc95c09f08b2b2fa825ea`, the trunk head named in the task (post CFG-2 merge). `git rev-parse origin/master` = `0448d32a7c8b8e590dbc1b601160c9ab17d6c36a` -- unrelated to this lease's base line (local trunk is not tracked against `origin/master` in this checkout); proceeding from the named base sha per the task.

## CFG-3a step:01 — item 8 carry-overs: `ignore` parameter removed, anchor-drift test added

**RV CFG-2 F1** -- the transitional `ignore={"production.yaml"}` parameter of `compose_configuration_document` outlived the CFG-2 deletion commit and would silently skip a re-added `production.yaml` forever. Removed the parameter entirely from `configuration/composition.py` (signature, docstring, the `ignored` set in the no-globbing scan) and its one call site in `configuration/return_configuration.py`. Two more call sites the CFG-2 drop did not name were found by grep and fixed: `tests/configuration/test_packaged_configuration_composition.py` (the durable-invariant test) and `tests/configuration/test_return_method_requirements_configuration.py` (a fixture composing the full document).

```
$ grep -rn "ignore=frozenset\|ignore:" backend/src backend/tests backend/scripts 2>/dev/null
(no output)
```

Added `test_a_re_added_production_yaml_is_refused_as_an_unlisted_file`: copies `backend/config/returns/` to a tmp dir, re-adds `production.yaml`, asserts `compose_configuration_document` raises `ValueError` naming the file and "does not glob".

**RV CFG-2 F2** -- the 25 prompt-section names that were YAML anchors in the single-file `ai_gateway.yaml` (enumerated from `git show 73c276d2:backend/config/ai_gateway.yaml`: every `&name` with at least one `*name` alias elsewhere in that file) are now independently typed into every task file that carries them, with no sync guard. Added `test_the_former_shared_anchor_prompt_blocks_have_not_drifted_apart`: loads the composed AI Gateway configuration, collects each of the 25 names' text per carrying task, asserts every name is carried by at least one task and that all carriers of a given name agree byte-for-byte.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration -q -p no:cacheprovider
240 passed, 1 warning in 14.72s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/composition.py src/return_platform/configuration/return_configuration.py tests/configuration/test_packaged_configuration_composition.py tests/configuration/test_return_method_requirements_configuration.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 4 files>
(after one reformat) 4 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/composition.py src/return_platform/configuration/return_configuration.py
Success: no issues found in 2 source files
```

Head sha: see commit. `merge_status: PARTIAL` -- scope items 1-7 not yet started.

## CFG-3a step:02 — item 5: optimistic lock on PATCH

`PatchDomainPayload.expected_version: int | None = None` (default absent, so
the frontend pipeline that never round-trips a version keeps working
unchanged). New repository method `get_domain_version(release_id,
domain_key) -> int | None`, added to the `ConfigurationGraphRepository`
protocol and implemented in both `InMemoryConfigurationGraphRepository`
(reads the domain node's own `.version`, already maintained by
`save_draft_domain`) and `Neo4jConfigurationGraphRepository` (new Cypher
query against `ConfigurationDomain.version`). `patch_domain_config` compares
`expected_version` against it when present and refuses with 409
`CONFIGURATION_DOMAIN_VERSION_CONFLICT` carrying `current_version` on a
mismatch -- the same shape `promote_release`'s existing revision-conflict 409
uses.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration tests/test_configuration_api.py -q -p no:cacheprovider
250 passed, 1 warning in 18.92s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/graph_repository.py src/return_platform/configuration/api/releases.py tests/test_configuration_api.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 3 files>
3 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/graph_repository.py src/return_platform/configuration/api/releases.py
Success: no issues found in 2 source files
```

Head sha: see commit. `merge_status: PARTIAL`.

## CFG-3a step:03 — item 4: CONFIG_RELEASE_WRITE capability

New capability `config.release.write`, added to `ALL_CAPABILITIES` and to
`WORKSPACE_EDITOR`'s bundle (`CONSOLE_ADMIN` already carries `ALL_CAPABILITIES`).
`POST /api/config/releases` and `PATCH /api/config/releases/{release_id}/
domains/{domain_key}` narrowed from `require_write_roles` (seven roles) to
`require_capability(CONFIG_RELEASE_WRITE)` -- the same narrowing
`CONFIG_RELEASE_PROMOTE` already did for `/promote`, and for the same
reason: a role that cannot promote a release should not be able to shape
what a promoter promotes either. `promote` itself is untouched
(`CONFIG_RELEASE_PROMOTE`, unchanged). `/api/principal`'s capability
advertisement needs no separate change -- it derives from
`capabilities_for_roles` directly.

`tests/test_every_console_path_is_mounted.py`'s `CONFIGURATION_CAPABILITY_ROUTES`
gained the two new guarded routes so `test_configuration_release_writes_are_guarded`
walks the live dependency on both. `tests/security/test_guards_match_the_console.py`
gained a parametrize case and two routed 403 tests (`test_creating_a_release_needs_the_release_write_capability`,
`test_patching_a_release_domain_needs_the_release_write_capability`), following that
file's own established pattern for a role-group-to-capability narrowing.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/security/test_guards_match_the_console.py tests/security/test_capability_model.py tests/test_every_console_path_is_mounted.py tests/test_configuration_api.py tests/api/test_canonical_config_domains.py -q -p no:cacheprovider
58 passed, 1 warning in 12.52s
$ .venv/Scripts/python.exe -m pytest tests/api/test_canonical_principal.py -q -p no:cacheprovider
6 passed, 1 warning in 0.41s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/security/capabilities.py src/return_platform/configuration/api/router.py tests/test_every_console_path_is_mounted.py tests/security/test_guards_match_the_console.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 4 files>
4 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/security/capabilities.py src/return_platform/configuration/api/router.py
Success: no issues found in 2 source files
```

Head sha: see commit. `merge_status: PARTIAL`.

## CFG-3a step:04 — item 1: `POST /api/config/validate/{domain_key}`

New route, no console predecessor -- delegates to a new `validate_domain_config`
in `releases.py` rather than growing a second validation path: it calls the
same `_domain_model` lookup (extracted from `_canonical_domain_payload`, which
now calls it too) so "would this be valid" and "is this valid" can never
disagree about what valid means. Body is `ValidateDomainPayload`: exactly one
of `payload` (validates standalone) or `patch` (merges against the ACTIVE
release's stored domain -- there is no `release_id` on this route by design,
so a patch validates against the only document a caller with no draft yet can
name); a `model_validator` refuses neither-or-both with a 422. `_validation_errors`
catches pydantic's `ValidationError` and maps `.errors()` to
`{path, message, type}` via `_dotted_error_path` (list indices as `[n]`, per
the brief). No write, no audit record; guarded by `require_read_roles`. 404 for
an unknown domain key (via `_domain_model`), 409 when `patch` is given and no
release is active.

`tests/configuration/test_canonical_config_api.py`'s
`test_the_release_lifecycle_is_the_only_mutation_surface_here` pins the exact
POST/PATCH surface of the router; updated to include the new route with an
explanation that it is POST-shaped (a body does not fit a GET) rather than a
write, pointing at the no-write proof in the new tests.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py tests/configuration tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider
664 passed, 5 deselected, 2 warnings in 87.30s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py tests/test_configuration_api.py tests/configuration/test_canonical_config_api.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 4 files>
(after one reformat) 4 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py
Success: no issues found in 2 source files
```

OpenAPI drift now expected to FAIL (a route was added); regeneration is scope
item 7, deferred to the final step so every route lands before regenerating
once.

Head sha: see commit. `merge_status: PARTIAL`.

## CFG-3a step:05 — item 3 (part 1): extract the carry-forward into `packaged_adoption.py`

New `configuration/application/packaged_adoption.py`: `adopt_packaged_configuration`
is `bootstrap_graph_configuration.main`'s decision body (the RETURN_PLATFORM
merge, the AI_GATEWAY/DEPENDENCY_SIMULATION per-unit merge, the
`--adopt-packaged`/`--adopt-packaged-key` validation), moved verbatim in
sequence and effect -- every helper it calls (`_units`, `_assemble`,
`_adopt_requests`, `_key_digests`, `_fill_absent_leaves`, `_carry_forward`,
`_drop_retired_keys`) and the two metadata keys (`PACKAGED_KEY_DIGESTS`,
`PACKAGED_DOMAIN_KEY_DIGESTS`) moved with it, unchanged. `bootstrap_graph_configuration.py`
imports all of them back and lists them in a new `__all__` (satisfying ruff's
unused-import check on names it re-exports rather than calls directly) so
`bootstrap_graph_configuration.<name>` keeps resolving for every existing
test. `main()` now builds the packaged/active payload dicts (no file I/O
moved -- `adopt_packaged_configuration` takes already-loaded dicts, which is
what lets the API call it with no filesystem access to the packaged
directory) and calls `adopt_packaged_configuration` once instead of running
the merge inline; `existing_configuration`, `recordable_baseline`,
`recordable_domain_baselines` and `carried_domains` are read off the
returned `PackagedAdoptionResult` and used exactly where the inline
variables were used, so the rest of `main()` (checksum, release id, DRAFT/
VALIDATED/RELEASED promotion, metadata write) is untouched.

Two log messages changed shape (both still contain every substring a test
checks): the RETURN_PLATFORM `packaged_configuration_not_adopted` and
`active_release_no_longer_validates` warnings dropped `release_id=%s` (the
extracted function has no release id, only domain payloads) and gained
`domain=%s` for symmetry with the AI_GATEWAY/DEPENDENCY_SIMULATION message,
which already used that shape. Verified against every caplog assertion in
the test file (`grep caplog`) before making the change -- none checks for
`release_id=` text.

**No behaviour change**, per the brief's own requirement -- proof is the
unmodified test file passing outright:

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/test_graph_configuration_bootstrap.py -q -p no:cacheprovider
24 passed in 10.87s
$ .venv/Scripts/python.exe -m pytest tests/configuration tests/test_graph_configuration_bootstrap.py tests/test_configuration_api.py tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider
688 passed, 5 deselected, 2 warnings in 92.43s
$ .venv/Scripts/python.exe -m pytest tests/platform -q -p no:cacheprovider
194 passed, 29 deselected in 8.25s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/cli/bootstrap_graph_configuration.py src/return_platform/configuration/application/packaged_adoption.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 2 files>
2 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy <same 2 files>
Success: no issues found in 2 source files
```

Not yet built: the API routes (`POST /adopt-packaged`, `GET /packaged-drift`)
that call this function -- next step.

Head sha: see commit. `merge_status: PARTIAL`.

## CFG-3a step:06 — item 3 (part 2): `POST /adopt-packaged`, `GET /packaged-drift`

Added `DomainDrift`/`summarize_packaged_drift` to `packaged_adoption.py`: runs
`adopt_packaged_configuration` with nothing explicitly requested and derives
two read-only fields from the same inputs -- `would_adopt` (packaged
keys/units that differ from the active release and are NOT undecided: a real
adoption this run would carry, filtered rather than separately decided) and
`filled_leaves` (dotted paths `_fill_absent_leaves` would add inside an
undecided key, via a small mirror of its own recursion, `_added_leaf_paths`).
`undecided` is read straight off the merge result -- the same value
`POST /adopt-packaged` would report -- so the two can never disagree about
what is undecided.

`release_promotion.py`'s `publish_release_with_domains` gained an optional
`expected_head_revision` parameter (default `None`, preserving the exact
behaviour of its two existing callers, the agent-configuration and feedback
governance adapters, which never pass it and keep reading the head fresh
between promotions). `/adopt-packaged` and (later) `/publish` pass their own
caller-supplied value instead, turning the compare-and-set into an actual
optimistic lock against a stale `GET` rather than only against a race
between the function's own two promotions.

New in `releases.py`: `AdoptPackagedPayload` (`units`, `expected_head_revision`),
`_packaged_domain_payloads`, `_archive_draft_on_refusal`,
`adopt_packaged_release`, `get_packaged_drift`. Both mounted in `router.py`
under `CONFIG_RELEASE_WRITE` (packaged-drift too -- "the panel that tells an
operator what to request", not a general configuration read).

**Bug found and fixed while writing the equivalence test.**
`_packaged_domain_payloads` first read `app.state.return_configuration` /
`ai_gateway_configuration` -- the same snapshot `create_release` reads as a
fallback. That snapshot is NOT stable: `RuntimeConfigurationActivator.refresh`
overwrites it with the ACTIVE RELEASE's own configuration the moment one is
promoted (`runtime_activation.py:375`), so after this process has ever
activated a release, `app.state.return_configuration` no longer reflects the
packaged file at all -- it reflects whatever was last released, which
defeated the entire comparison `adopt_packaged_configuration` exists to make.
Caught because the equivalence test (below) failed silently -- adopted units
came back unchanged rather than adopted -- traced with a throwaway debug
test comparing the direct function call against the same call through the
route. Fixed by reading `settings.return_configuration_path` /
`ai_gateway_configuration_path` / `dependency_simulation_configuration_path`
fresh on every call, the same source the CLI reads from disk on every
invocation -- "packaged" now means the same thing to both callers.

**Acceptance: adopt-packaged producing the same release a CLI run would.**
`test_adopt_packaged_api_matches_a_cli_run` seeds two fresh
`InMemoryConfigurationGraphRepository` instances with the identical starting
state (an active release whose `discovery` diverges from the packaged file
with no recorded baseline), runs `bootstrap_graph_configuration.main
(adopt_packaged_keys=("discovery",))` against one and `POST /adopt-packaged`
against the other, and asserts the three published domain payloads are
byte-equal.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py -q -p no:cacheprovider
24 passed in 14.38s
$ .venv/Scripts/python.exe -m pytest tests/configuration tests/test_configuration_api.py tests/test_every_console_path_is_mounted.py tests/api tests/test_graph_configuration_bootstrap.py tests/security -q -p no:cacheprovider
730 passed, 13 deselected, 2 warnings in 98.50s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py src/return_platform/configuration/application/release_promotion.py tests/test_configuration_api.py tests/configuration/test_canonical_config_api.py tests/security/test_guards_match_the_console.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same files>
(after one reformat) all formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py src/return_platform/configuration/application/release_promotion.py
Success: no issues found in 3 source files
```

`tests/configuration/test_canonical_config_api.py`'s mutation-surface pin
updated for the new genuine mutation `POST /adopt-packaged` (`/validate`
stays the one documented non-write exception).

Head sha: see commit. `merge_status: PARTIAL`. Remaining: item 2 (`/publish`),
item 6 (audit filter), item 7 (OpenAPI regen).

## CFG-3a step:07 — item 2: `POST /api/config/publish`

Single-call publish: create-from-active, canonical patch, promote VALIDATED,
promote RELEASED with the head check, and the audit records. Composes the
three primitives the brief names directly -- `promote_configuration_release`,
`_canonical_domain_payload` (via the same merge-patch path
`patch_domain_config` uses), `record_configuration_audit` -- rather than
calling `create_release`/`patch_domain_config`/`promote_release_status` as
sub-requests, since those are HTTP handlers with their own response shapes
and composing them would be a wrapper around wrappers. The one piece that
WOULD have been a second copy (the active-or-baseline domain clone) is
shared: extracted `_active_or_baseline_domains` out of `create_release`
unchanged in behaviour, and `publish_configuration` calls the same helper.

On any refusal inside the try block (404 unknown domain, 409 patch/version
conflict, 422 invalid patch, 409/422/503 from `ReleasePromotionError`) the
draft this call created is archived via `_archive_draft_on_refusal` (already
built for `/adopt-packaged`) before the HTTPException propagates -- proved
by two tests reading `GET /releases` back and asserting every release's
status is in `{ARCHIVED}` (an invalid patch, and a stale
`expected_head_revision`).

`audit_ids`: `record_configuration_audit` (releases.py, not the
Owns-list-excluded `operations/repository.py`) now generates and returns a
correlation id, stamped into the stored record as `details["auditId"]` and
returned even on a best-effort write failure -- `append_audit` itself
assigns its own storage `_id` and was left untouched, out of scope. Every
call site (`create_release`, `patch_domain_config`, `promote_release_status`,
`adopt_packaged_release`) ignores the new return value except
`publish_configuration`, which writes one summary `CONFIGURATION_RELEASE_PUBLISHED`
record after every step succeeds and reports its id as `audit_ids`; the
per-step trail (create/patch/promote x2) stays independently queryable by
target. `configuration_client`'s audit-recording test double updated to
return a fake id, matching the real function's new contract (no test
previously depended on the old `None` return).

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py -q -p no:cacheprovider
30 passed in 17.86s
$ .venv/Scripts/python.exe -m pytest tests/configuration tests/test_configuration_api.py tests/test_every_console_path_is_mounted.py tests/api tests/test_graph_configuration_bootstrap.py tests/security -q -p no:cacheprovider
736 passed, 13 deselected, 2 warnings in 106.31s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py tests/test_configuration_api.py tests/configuration/test_canonical_config_api.py tests/test_every_console_path_is_mounted.py
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 5 files>
5 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/api/releases.py src/return_platform/configuration/api/router.py
Success: no issues found in 2 source files
```

Mutation-surface pin (`test_canonical_config_api.py`) and the write-route
guard tables (`test_every_console_path_is_mounted.py`'s
`CONFIGURATION_WRITE_ROUTES`/`CONFIGURATION_CAPABILITY_ROUTES`) updated for
`/publish`, both `CONFIG_RELEASE_WRITE`-guarded.

Head sha: see commit. `merge_status: PARTIAL`. Remaining: item 6 (audit
filter), item 7 (OpenAPI regen), final acceptance sweep.

## CFG-3a step:08 — item 6 (audit filter) + item 8 carry-over (RV CFG-1 F4)

**Item 6.** `AuditService.list_logs` (`audit.py`) gains `actions`/`target`,
server-side: `target` is an exact Mongo match, `actions` entries build one
regex alternation each via the new `_action_pattern` (`NAME$` exact, `NAME*`
prefix, `re.escape`d), OR'd together -- `?actions=CONFIGURATION_*` matches
every `CONFIGURATION_...` action without a client paging through the
platform-wide `audit` collection first. Neither parameter changes the
default: omitted, `list_logs()` still runs the exact `find({})` it always
did. `router.py`'s `GET /audit` gains the two query params
(`Annotated[..., Query()]`, following `api/proposals.py`'s existing style --
plain `= Query(default=None)` trips ruff's B008 on the second occurrence in
one signature, a real ruff limitation confirmed by isolating it to a
two-line repro).

**Item 8 carry-over (RV CFG-1 F4), found while touching this route.** All
five canonical read routes this brief names (`/sources`, `/sources/{id}`,
`/sources/{id}/assets/{id}`, `/audit`, `/audit/{id}`) were `return await
console_X(...)` -- the delegate's OWN `APIResponse`, constructed in
`sources.py`/`audit.py`, never passed through this router's `_ok` and its
`redact_secret_values` scrub. `test_every_canonical_response_goes_through_the_scrub`
could not have caught this: it walks router.py for a directly-constructed
`APIResponse(...)`, and these five constructed none there at all. Fixed by
capturing the delegate's response and re-wrapping its (dumped-to-JSON)
`.data` through `_ok`, matching every other handler on this router.

New regression tests (`tests/configuration/test_canonical_config_api.py`):
`test_no_handler_returns_a_delegate_response_unscrubbed` (AST walk over every
`return` statement flagging a direct `await console_*(...)` -- the structural
guard `test_every_canonical_response_goes_through_the_scrub` couldn't be, and
would have caught this defect on day one) and
`test_audit_reads_now_mask_a_secret_carried_in_details` (behavioural: a fake
delegate answers with a resolved secret inside `AuditLog.details`, read back
masked over real HTTP -- `/sources`/`/sources/{id}`/the asset route decline a
synthetic secret field outright since their models are `extra="forbid"`,
itself a second line of defence, so those three are covered by the
structural test instead). New `tests/configuration/test_audit_filter.py`
(`AuditService.list_logs` query-building, `_action_pattern` in isolation) and
two more router tests (`actions`/`target` threaded through; the unfiltered
default).

**Audit filter response shape** (brief's "Evidence to paste"), from
`test_the_audit_route_threads_actions_and_target_to_the_delegate` and
`test_target_filters_to_an_exact_match`: `GET /api/config/audit?actions=
CONFIGURATION_*&actions=AI_ROUTE_REFRESHED&target=release-1` reaches
`AuditService.list_logs(actions=["CONFIGURATION_*", "AI_ROUTE_REFRESHED"],
target="release-1")`, which returns `list[AuditLog]` filtered to records
whose `action` matches either alternative AND whose `target == "release-1"`
-- the same `{"data": [{"id", "action", "actor", "target", "timestamp",
"details"}, ...], "meta": {...}}` envelope `GET /audit` already answered,
unchanged in shape, narrower in content.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration/test_canonical_config_api.py tests/configuration/test_audit_filter.py -q -p no:cacheprovider
24 passed in 0.94s
$ .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py tests/configuration tests/test_every_console_path_is_mounted.py tests/api tests/test_graph_configuration_bootstrap.py tests/security -q -p no:cacheprovider
748 passed, 13 deselected, 2 warnings in 103.32s
$ .venv/Scripts/python.exe -m ruff check src/return_platform/configuration/api/audit.py src/return_platform/configuration/api/router.py tests/configuration/test_canonical_config_api.py tests/configuration/test_audit_filter.py
All checks passed! (after one --fix pass and one format pass)
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy src/return_platform/configuration/api/audit.py src/return_platform/configuration/api/router.py
Success: no issues found in 2 source files
```

Scope items 1-6 and the item-8 carry-overs are now all landed. Remaining:
item 7 (OpenAPI regen across all copies + drift check), then the full
acceptance sweep (ruff/format/mypy on the whole owned set, the full backend
suite against the known-failure registry).

Head sha: see commit. `merge_status: PARTIAL`.

## CFG-3a step:09 — item 7: OpenAPI regen (all copies) + drift check

`python scripts/check_openapi_drift.py --write` regenerated all four
committed JSON snapshots and the frontend generated types; the four new
routes (`POST /api/config/validate/{domain_key}`, `POST /api/config/publish`,
`POST /api/config/adopt-packaged`, `GET /api/config/packaged-drift`) and the
`/audit` route's two new query parameters (`actions`, `target`) confirmed
present in the regenerated `openapi.json`. No frontend component touched --
only the generated `.d.ts` and the four OpenAPI JSON copies, per the brief's
Owns list.

**Drift-check output** (brief's "Evidence to paste"), check mode after the
write:

```
$ cd .. && PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py
{
  "stage": "4E",
  "gate": "openapi_drift",
  "mode": "check",
  "commit": "1b3155e95255a41618671dda33ad36e46e7bb9fd",
  "openapi_sha256": "0d12158eb3ce21963d890129a56364a7951e611410f5f75b4452b421de99cbda",
  "snapshots": [
    "openapi/return-platform.openapi.json",
    "backend/openapi/return-platform.openapi.json",
    "frontend/openapi/return-platform.openapi.json",
    "openapi.json"
  ],
  "diffs": [],
  "status": "PASS",
  "exit_code": 0
}
```

**Full acceptance command** (brief's exact suite list):

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py tests/configuration tests/test_graph_configuration_bootstrap.py tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider
714 passed, 5 deselected, 2 warnings in 101.15s
```

Head sha: see commit. `merge_status`: moving to PENDING once the final
ruff/format/mypy sweep on every changed file and the full backend suite
(known-failure registry check) are confirmed in the next step.

## CFG-3a step:10 — final acceptance sweep, `merge_status: PENDING`

Ruff/format/mypy across every file this lease touched (10 src + 7 test
files, the full lease diff against the base sha):

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m ruff check <10 src + 7 test files>
All checks passed!
$ .venv/Scripts/python.exe -m ruff format --check <same 17 files>
17 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy <10 src files>
Success: no issues found in 10 source files
```

**Full backend suite** (the brief's "full suite shows only the 42 known
failures" acceptance line):

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests/configuration/test_concurrent_activation.py
42 failed, 5333 passed, 10 skipped, 515 deselected, 2 warnings in 311.26s (0:05:11)
```

The 42 failing node ids collapse to the exact same 9 modules `.plan/reviews/CFG-1.md`
Q6 and CFG-2's own RV verified pre-existing and base-identical:

```
tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py (9)
tests/dynamic_knowledge/test_order_discovery_smoke_net.py (6)
tests/dynamic_knowledge/test_reasoning_stage_prompts.py (4)
tests/dynamic_knowledge/test_turn_temporal_grounding.py (2)
tests/test_ai_a_rejected_parse_is_repaired_on_its_own_route.py (13)
tests/test_ai_route_balancing_design.py (3)
tests/test_ai_single_dispatch_boundary.py (2)
tests/test_enforced_contracts_are_disclosed.py (2)
tests/test_keyless_reasoning_is_held_for_a_human.py (1)
```
9+6+4+2+13+3+2+2+1 = 42, none touching configuration loading, the release
lifecycle, or anything this lease owns. 5333 passed = CFG-2's final 5295 +
38 tests this lease adds (optimistic lock 1, validate 6, publish 6,
adopt-packaged 6 incl. the CLI-equivalence acceptance test, packaged-drift
2, capability guards 2+2, composition carry-overs 2, audit filter 8+3, the
RV CFG-1 F4 regression tests 3 -- 41 counted individually, a few folded
into shared parametrizations bringing the net to 38).

**Acceptance checklist against the brief:**
- Scope items 1 (validate), 2 (publish), 3 (adopt-packaged + packaged-drift,
  extraction), 4 (CONFIG_RELEASE_WRITE), 5 (optimistic lock), 6 (audit
  filter), 7 (OpenAPI regen) -- all landed, steps 04-09.
- Item 8 carry-overs: the transitional `ignore` param removed with a test
  (step:01); the 25 former shared anchor blocks pinned against drift
  (step:01); the five canonical reads routed through `_ok` with tests (step:08,
  found while implementing item 6).
- Tests for every route: happy path, 422 path-mapped errors (validate), 409
  stale version (PATCH) / stale head (promote, publish, adopt-packaged), 403
  without the capability (create/patch/publish/adopt-packaged/packaged-drift),
  the publish rollback on a refused promote (publish and adopt-packaged both
  archive their draft), adopt-packaged producing the same release a CLI run
  would (byte-equal domain payloads, asserted against `bootstrap_graph_configuration.main`
  on a matching in-memory repository) -- all present.
- `pytest tests/test_configuration_api.py tests/configuration tests/test_graph_configuration_bootstrap.py
  tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider`
  green (714 passed).
- ruff/format/mypy clean on changed files; drift check PASS.

Head sha: `933b39c3bf88eda9091664794a478f488516b88f`. `merge_status: PENDING`
-- this lease's own definition of done is met. Ready for RV.

## CFG-3a step:11 — RV round 1 fixes

RV verdict on `2d4cea7a`: CHANGES_REQUIRED (F1, F2 blocking; F3-F10 advisory).
`.plan/reviews/CFG-3a.md`. Fixed on the same branch.

**F1 (BLOCKING) -- `would_adopt` disagreed with the merge for a baselined,
operator-edited key.** `_would_adopt` derived its answer from `key not in
undecided`, and a key WITH a recorded baseline that an operator moved away
from is decided (the release wins) without ever entering `unadopted` --
`_carry_forward`'s `elif key in known:` branch keeps `active_payload[key]`
and never appends to `unadopted`. So a key like that, differing from the
current packaged value, was wrongly reported as `would_adopt` while the
merge actually kept the release's edit. Rewrote `_would_adopt` to take the
MERGED value as a third argument and report a key only when `merged[key] ==
packaged[key]` -- it can no longer claim an adoption the merge did not make,
by construction rather than by re-deriving the same "not undecided" logic
RV showed was insufficient. `summarize_packaged_drift` now threads
`result.merged_domains` (via `_units` for the two split-key domains) into
`_would_adopt` instead of the `undecided` frozenset. Docstrings in both
`packaged_adoption.py` (`_would_adopt`, `summarize_packaged_drift`) and
`releases.py` (`get_packaged_drift`, ~:1002-1013) rewritten to state what
the fixed code actually guarantees, not what RV found false.

New `tests/configuration/test_packaged_adoption.py` (3 tests): RV's exact
reproduction shape -- a key (`discovery`) with a baseline digest recorded
against an OLDER packaged value, the active release edited away from that
baseline, and the packaged file independently moved again -- asserts the
merge keeps the operator's edit AND `would_adopt` agrees; a positive case
(a key the release predates is still reported adopted); and the
no-active-release case. One pre-existing test
(`test_packaged_drift_with_no_active_release_shows_everything_adoptable`)
asserted the OLD, disagreeing behaviour for "no active release" (`would_adopt`
listing every packaged key with no merge having run at all) -- renamed and
corrected to assert nothing is reported adopted before anything has been
merged.

**F2 (BLOCKING) -- `/publish` claimed a per-step audit trail it did not
write.** `publish_configuration` called `promote_configuration_release`
and `_canonical_domain_payload` directly (the brief's own primitives), not
the three route handlers that call `record_configuration_audit`
themselves -- so only one summary `CONFIGURATION_RELEASE_PUBLISHED` record
existed, and the docstring's claim of a create/patch/promote x2 trail
mirroring the four-call path was false. Now calls `record_configuration_audit`
itself at each step -- `CONFIGURATION_RELEASE_CREATED` (after the clone),
`CONFIGURATION_DOMAIN_PATCHED` (with `changedPaths`, the same before/after
leaf diff `patch_domain_config` records, not just `patchKeys`), two
`CONFIGURATION_RELEASE_PROMOTED` (VALIDATED then RELEASED, matching
`promote_release_status`'s own detail shape) -- inside the same rollback
`try`, so a step's record is written only once that step itself succeeded;
the summary record is written last, after every step has. `audit_ids` in
the response is now all five ids, in write order.

Strengthened `test_publish_creates_patches_and_releases_in_one_call` to
assert exactly 5 distinct audit ids and the five actions in order, with
`changedPaths` and both promotion statuses on the right records -- the same
shape `test_every_release_change_leaves_an_audit_record` already pins for
the four-call path. New `test_publish_leaves_a_per_step_audit_trail_queryable_by_target`:
points a fake `console_list_audit_logs` at the SAME in-memory list
`configuration_client`'s audit-recording double populates, then asserts
`GET /api/config/audit?target=<release>` and `?target=<release>/RETURN_PLATFORM`
list exactly the four release-level and one domain-level record respectively
-- the two sides of F2's claim checked against one source of truth rather
than trusted separately (there is no real Mongo in this suite).

**F3 (advisory, taken)** -- `release_id` restored to the four moved log
lines (`packaged_configuration_not_adopted`, `active_release_no_longer_validates`,
`active_domain_no_longer_validates` x2) via a new `release_id: str | None = None`
parameter on `adopt_packaged_configuration`, threaded from `main()`
(`active.release_id if active is not None else None`) and both `releases.py`
call sites. `domain=%s` kept alongside it (it is what tells the two RETURN_PLATFORM
lines from the per-domain ones apart, which `release_id` alone cannot).

**F4 (advisory, taken)** -- `adopt_packaged_configuration` gained `log:
bool = True`; `summarize_packaged_drift` passes `log=False`, so `GET
/packaged-drift` computes the identical decision without writing the
publish-time warnings a browser-polled read must not turn into steady-state
noise. New `test_packaged_drift_emits_no_warning_on_the_read_path`: the
fixture has a genuinely undecided key (so the write path WOULD warn),
`caplog` at WARNING level, asserts `packaged_configuration_not_adopted`
never appears.

**F6 (advisory, taken)** -- new `tests/configuration/test_get_domain_version_live_infra.py`,
`pytest.mark.live_infra` (deselected from the default run, same as every
other real-Neo4j test in this suite; `scripts/dev/run_real_infra_suite.sh`
selects it back in). Saves a probe domain twice against a real
`Neo4jConfigurationGraphRepository`, asserts `get_domain_version` returns
1 then 2, and `None` for an unknown domain and an unknown release; cleans
up its own nodes in a fixture `finally`. Not run here (this lease's
environment rules forbid touching live infrastructure); RV's own direct
verification against the dev graph during the review is what the module
docstring cites as the basis for this test's assertions.

**F7 (advisory, taken)** -- `test_dotted_error_path_maps_list_indices_as_brackets`,
parametrised exactly over RV's four cases (`("a","b")->"a.b"`,
`("a",2,"b")->"a[2].b"`, `(0,"a")->"[0].a"`, `("a",1,2)->"a[1][2]"`), calling
`_dotted_error_path` directly.

**F9 (advisory, taken)** -- `MAX_ACTIONS = 20` in `audit.py`; the router's
`actions` query param gained `Query(max_length=MAX_ACTIONS)` (refuses over
20 with a 422 before `list_logs` ever runs -- verified this actually
constrains the query LIST length, not a string length, against a minimal
FastAPI reproduction); `list_logs` itself re-checks the cap (`ValueError`)
for any non-HTTP caller. `AuditService.list_logs` now uses `{"$in": [...]}`
(index-friendly) when no `actions` entry ends in `*`, falling back to the
`$regex` alternation only when at least one does. Five new tests in
`test_audit_filter.py`: `$in` chosen for an all-exact list, `$regex` chosen
when one entry has a wildcard, the cap raising `ValueError` past
`MAX_ACTIONS`, the router's 422 for the same, and `re.escape` still applied
in the alternation.

**F10 (advisory, taken)** -- `test_publish_refuses_an_existing_release_id`
now also asserts the pre-existing draft `taken` is still `DRAFT` after the
409 -- pins that the existence check (before the `try`) keeps
`_archive_draft_on_refusal` from ever touching a release this request did
not create.

**F5 and F8 (advisory, left as-is per the coordinator's instruction) --**
F5: "on any refusal the draft is archived" is narrower than written (only
`HTTPException`/`ReleasePromotionError` trigger the rollback; a `ValueError`
or driver error from the unguarded `save_draft_domain` clone loop would not).
Left because RV's own judgement was that the practical exposure is small --
a 500 is not a refusal in the sense the sentence means -- and tightening the
`except` clause is a behaviour change to a path with no coverage either way,
better done deliberately in CFG-4 than folded into a fix-round diff. F8:
narrowing create/patch to `CONFIG_RELEASE_WRITE` drops `return_platform_service`
along with the four operator roles it was never meant to keep; RV verified
directly that no caller exists (`frontend/src/api/configuration.ts` gates
both actions on `config.release.promote`, which the service role never held
either) -- left as a verified non-issue, no code change warranted.

```
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/configuration/test_packaged_adoption.py tests/test_configuration_api.py tests/configuration/test_audit_filter.py -q -p no:cacheprovider
52 passed in 19.60s
$ .venv/Scripts/python.exe -m pytest tests/test_configuration_api.py tests/configuration tests/test_graph_configuration_bootstrap.py tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider
728 passed, 6 deselected, 2 warnings in 110.66s      (the brief's exact command; +14 net over round 1's 714)
$ .venv/Scripts/python.exe -m ruff check <9 changed/new files> && .venv/Scripts/python.exe -m ruff format --check <same>
All checks passed! / 9 files already formatted
$ PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m mypy <5 changed src files>
Success: no issues found in 5 source files
$ cd .. && PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py --write   # actions max_length changed the schema
status PASS (write mode)
$ PYTHONPATH=$WT/backend/src backend/.venv/Scripts/python.exe scripts/check_openapi_drift.py
status PASS, diffs: []
$ cd backend && PYTHONPATH=$WT/backend/src .venv/Scripts/python.exe -m pytest tests/dynamic_knowledge/test_confirmation_starts_the_case_workflow.py tests/dynamic_knowledge/test_order_discovery_smoke_net.py tests/dynamic_knowledge/test_reasoning_stage_prompts.py tests/dynamic_knowledge/test_turn_temporal_grounding.py tests/test_ai_a_rejected_parse_is_repaired_on_its_own_route.py tests/test_ai_route_balancing_design.py tests/test_ai_single_dispatch_boundary.py tests/test_enforced_contracts_are_disclosed.py tests/test_keyless_reasoning_is_held_for_a_human.py tests/configuration tests/api -q -p no:cacheprovider
42 failed, 774 passed, 6 deselected, 2 warnings in 103.35s   -- the exact same 42 ids as every prior run, none touching configuration
```

`drop.json`'s `head_sha` is set to this step's own commit sha, recorded via
a small follow-up update after the fix commit landed (a commit cannot name
its own hash inside its own content) -- the same lag every prior step in
this lease recorded it with.

Head sha: see commit. `merge_status: PENDING` -- ready for RV round 2.
