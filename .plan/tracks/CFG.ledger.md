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
| CFG-1 | feat/cfg-1-dead-code | 06b43b18 | Sonnet | IN_PROGRESS | — | — | — |
| CFG-2 | feat/cfg-2-config-split | after CFG-1 | Opus spike → Sonnet | NOT_STARTED | — | — | — |
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
