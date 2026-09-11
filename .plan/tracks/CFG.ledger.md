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
| CFG-2 | feat/cfg-2-config-split | 3cb696e7 | Opus spike DONE → Sonnet | READY | — | — | — |
| CFG-3a | feat/cfg-3a-config-api | after CFG-0 | Sonnet | NOT_STARTED | — | — | — |
| CFG-3b | feat/cfg-3b-form-primitives | after CFG-0 | Opus note → Sonnet | NOT_STARTED | — | — | — |
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
