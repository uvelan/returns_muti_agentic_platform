# CFG-6 — env → release: the `deployment` section, hot-adopted, gates intact

Base: the RV-approved head of CFG-5 (must carry CFG-2's split loader, CFG-3a's config API v2 and
CFG-4's `TypedSectionScreen`; if CFG-5 has not merged, base on CFG-4's approved head and skip nothing
— §6 is the only item that depends on CFG-4). Branch `feat/cfg-6-deployment-section`. Worktree
`.claude/worktrees/cfg-6` (venv and `node_modules` junctioned, `.env` copied, `PYTHONPATH` pinned and
`python -c "import return_platform; print(return_platform.__file__)"` pasted in step:00). Decisions in
force: **D-CFG-4**. **Read `.plan/tracks/CFG-6.design.md` first — it carries the `file:line` evidence
for every item below and settles the two decisions (precedence in §4, Mongo in §5) this lease must
not re-open.**

Owns: `backend/src/return_platform/configuration/return_configuration.py` (the new
`DeploymentConfiguration` and its field on `ReturnPlatformConfiguration`, nothing else),
`configuration/deployment_settings.py` (new), `configuration/runtime_integrations.py` (one call
site), `configuration/application/packaged_adoption.py`, `configuration/runtime_loader.py`,
`configuration/cli/bootstrap_graph_configuration.py`, `configuration/api/releases.py`,
`backend/config/returns/deployment.yaml` + `index.yaml`, `operations/feedback_service.py` (one
attribute), `operations/models.py` (the AI-gateway settings models), `operations/repository.py`
(`get_ai_settings`/`update_ai_settings`), `api/ai_gateway.py`, `api/dependencies.py` (the two
`providerOrder` reads), `workers/integration_outbox.py`, `frontend/src/domains/config/
DeploymentSection*.tsx`, `frontend/src/domains/domainScreens.ts`, `ConfigurationPage.tsx`,
`.env.example`, `compose.yaml`, the matching tests, the three OpenAPI copies +
`frontend/src/api/generated/return-platform.d.ts` via the project's generator.
Must not touch: `settings.py` (its validators are the backstop and stay **exactly** as they are —
the lease succeeds by feeding them, not by editing them), `ai/routing/routes.py`,
`ai/providers/*`, `runtime_activation.py`, any other config screen, the live databases, launchers.

Budget: 450k tokens; stop rule: write `evidence/orchestration/drops/LEASE-CFG-6/drop.json` PARTIAL at
360k and report.

## Scope

1. **`DeploymentConfiguration`** in `configuration/return_configuration.py`, added to
   `ReturnPlatformConfiguration` **with a default** (design §2 for the shape and the precedent).
   Packaged `backend/config/returns/deployment.yaml` listed in `backend/config/returns/index.yaml`.
   Model validators carry only the environment-*independent* rules (provider names, uniqueness,
   `NONE`, `base_url` required for the two external support modes).
2. **`configuration/deployment_settings.py`**: `apply_deployment_configuration(settings,
   configuration) -> Settings` mapping the section onto `ai_provider_order`, the ten
   `*_{lightweight,standard}_models`, `google_thinking_budget`, `google_response_schema`, the four
   `*_dependency_mode`, `feedback_learning_enabled`, `support_ticket_mode`, `support_ticket_base_url`.
   Called from inside `apply_graph_runtime_configuration` immediately before its final
   `model_validate` (`runtime_integrations.py:196-198`) so there is one place a release becomes
   `Settings` and one re-validation. **Precedence is design §4 and is not negotiable in this lease:**
   `runtime_integrations` keeps *availability* for every provider it enables, `deployment` always
   owns *order* and is the only source of SIMULATOR/MANUAL.
3. **Production gates.** New `validate_deployment_for_environment(section, environment)` (no
   SIMULATOR/MANUAL in `provider_order`, no `SIMULATED` dependency mode in production), called from
   `POST /api/config/validate/{domain_key}`, `/publish` and `/adopt-packaged` with
   `app.state.settings.environment` → **422 with a pydantic-shaped path**
   (`deployment.dependencies.omc`), and from `configuration/runtime_loader.py` at startup so a
   release promoted into production is refused at start with a named reason.
   `Settings.validate_relationships` is untouched.
4. **Bootstrap seeding.** `deployment_payload_from_settings(settings)` overlays onto packaged
   `deployment.yaml` only the fields in `settings.model_fields_set`; injected at
   `bootstrap_graph_configuration.py:237` and the API's `_packaged_domain_payloads`
   (`releases.py:999`). The API must use `app.state.packaged_deployment_defaults`, snapshotted from
   the **bootstrap** Settings before `apply_graph_runtime_configuration` runs (`runtime_loader.py:101`,
   `main.py:531`) — seeding from `app.state.settings` is circular and must be asserted against.
5. **Carry-forward granularity.** Add `RETURN_PLATFORM_DOMAIN_KEY: ("deployment",)` to
   `CARRY_FORWARD_SPLIT_KEYS` and route the RETURN_PLATFORM branch of
   `adopt_packaged_configuration` through the existing `_units`/`_assemble`, giving units
   `deployment.ai`, `deployment.dependencies`, `deployment.feedback_learning`,
   `deployment.support_ticket` — valid `--adopt-packaged-key` and `POST /adopt-packaged` values.
6. **Readers behind the rebuild** (design §1): (a) `workers/integration_outbox.py` builds its
   dispatcher table as an `ActivationParticipant` (`prepare` constructs, `publish` assigns — nothing
   fallible in `publish`); (b) `operations/feedback_service.py:92` reads
   `self._settings.feedback_learning_enabled` at call time; (c) a test asserting
   `ReturnOrchestrator` is constructed with the live `resources.settings` object, so a future wiring
   site cannot regress it. No change to `routes.py` or the providers — they already rebuild.
7. **Mongo duplicate.** Remove `providerOrder` from `AIGatewaySettingsUpdate`/`AIGatewaySettingsView`
   (`operations/models.py`), from `update_ai_settings` and from `get_ai_settings`' seeding and
   `["NONE"]` migration branch (`operations/repository.py:2117-2170`); `PUT /api/ai/settings` takes
   `interceptMode` + `expectedVersion` only and its production SIMULATOR check moves to item 3.
   `api/dependencies.py:196` and `api/ai_gateway.py:364` read `settings.ai_provider_order`.
   **`interceptMode` stays in Mongo** — the reasons are design §5; do not move it.
8. **`/config/deployment` screen** on CFG-4's `TypedSectionScreen`: `OrderedList` for provider order,
   `TagListInput` per provider × tier for pools, `EnumSelect` for the four dependency modes and
   `support_ticket.mode` with production-gated options **rendered disabled with the reason on the
   option, not hidden**, `Toggle` for `feedback_learning.enabled` and `response_schema`,
   `NumberField` for `thinking_budget`; `DiffPreview` + `PublishBar` as on every typed screen.
   Pools for providers governed by `runtime_integrations` render read-only with "governed by the AI
   Control Center provider bindings". `environment` from `/api/config/runtime` (add the field if
   absent). Registered in `domainScreens.ts` + `ConfigurationPage.tsx`; MSW handlers.
9. **Docs and defaults.** `.env.example` and `compose.yaml` keep every migrated line under a block
   comment naming it a bootstrap default whose authority now lives in the release at
   `/config/deployment`. Update `backend/config/README.md` and `docs/configuration/`.
10. **Carried, RV CFG-3a F5.** `adopt_packaged_release` and `publish_release` roll back on **any**
    refusal, not only `ReleasePromotionError`: extend the guarded block through `set_release_metadata`
    and `record_configuration_audit`, and add `except BaseException: await
    _archive_draft_on_refusal(...); raise` after the typed clause (`releases.py:848`, :1032-1064).
11. Regenerate OpenAPI (all three copies + the `.d.ts`) and run `scripts/check_openapi_drift.py`.

## Acceptance

- **Live proof, provider order without a restart** (the lease's headline claim). Against the running
  dev stack, in one ledger step: (i) record the API process start time / pid and
  `GET /api/config/runtime` showing the head revision and the current
  `deployment.ai.provider_order`; (ii) `POST /api/config/publish` a patch that reorders
  `deployment.ai.provider_order` with `expected_head_revision`; (iii) poll `GET /api/config/adoption`
  until the API and worker processes report the new release — it must land inside ~10 s and with **no
  restart**; (iv) re-read `GET /api/config/runtime` and the health-card endpoint served by
  `api/dependencies.py` showing the new order; (v) re-record the process start time / pid, unchanged.
  All five outputs pasted verbatim. Never restart the stack inside this proof; never touch
  `scripts/reset_all.*`.
- Production gates hold from the release exactly as from env: a release carrying
  `deployment.dependencies.omc: SIMULATED` or `SIMULATOR` in `provider_order` is refused at publish
  with a 422 naming the path when `environment=production`, refused at startup by
  `runtime_loader`, and — with both bypassed — refused by `Settings.validate_relationships` inside
  `RuntimeConfigurationActivator.refresh`, leaving the process on its previous release.
- `pytest backend/tests` at or above the base pass count; `ruff`, `mypy` clean; OpenAPI drift green;
  `vitest` and the axe check green for the new screen.
- `grep -rn "providerOrder" backend/src frontend/src` returns only `interceptMode`-adjacent history
  and the dependency-simulation `ai.providerOrder` (an unrelated field on
  `dependency_simulation/configuration.py:26`) — no AI-gateway settings duplicate remains.

## Tests to add

1. `test_deployment_section_overlays_settings` — a release payload's `deployment` produces the
   expected `Settings` fields through `apply_graph_runtime_configuration`.
2. `test_deployment_precedence_over_runtime_integrations` — with an enabled `runtime_integrations`
   provider **and** a conflicting `deployment` block: availability from `runtime_integrations`, order
   from `deployment`, SIMULATOR present only because `deployment` names it. The design §4 rule,
   asserted rather than commented.
3. `test_route_table_rebuilds_on_deployment_change` — drive `RuntimeConfigurationActivator.refresh`
   with a reordered release; the pool's route order changes and the pool object identity is preserved
   (`replace_routes`, not a new pool).
4. `test_production_refuses_simulated_dependency_mode_from_release` and
   `..._simulator_in_release_provider_order` — 422 at publish, RuntimeError at startup, and the
   activator leaving the process on its prior snapshot.
5. `test_deployment_first_publish_seeds_from_env` / `..._later_start_keeps_operator_value` /
   `..._later_start_adopts_changed_env` — the three `_carry_forward` rows of design §3, on the
   in-memory repository.
6. `test_adopt_packaged_seeds_from_bootstrap_settings_not_active_settings` — the circularity guard
   for item 4.
7. `test_return_platform_carry_forward_unchanged_without_deployment` — merged payload and recorded
   baselines byte-identical to the pre-change function for a release with no `deployment` key.
8. `test_deployment_key_is_dropped_after_revert` — a release carrying `deployment` against a
   `ReturnPlatformConfiguration` without the field is republished by `_drop_retired_keys`, proving
   the rollback path.
9. `test_ai_settings_has_no_provider_order` — `PUT /api/ai/settings` rejects the field, and the
   health cards enumerate `settings.ai_provider_order`.
10. `test_outbox_dispatchers_rebuild_on_dependency_mode_change` — the worker participant swaps the
    dispatcher for `omc.return.create` when the release flips `deployment.dependencies.omc`.
11. `test_adopt_packaged_rolls_back_on_any_refusal` and the same for `/publish` — a repository that
    raises a non-`ReleasePromotionError` from `set_release_metadata` leaves no DRAFT/VALIDATED node.
12. Frontend: `DeploymentSection.test.tsx` (load → reorder → DiffPreview → publish) and
    `DeploymentSection.a11y.test.tsx` (axe, keyboard operation of `OrderedList`), plus a disabled
    production option rendering its reason.

## Evidence to paste

- step:00: `git rev-parse HEAD`, the local/origin trunk left-right count, and
  `python -c "import return_platform; print(return_platform.__file__)"` from the worktree.
- The five outputs of the live provider-order proof above, in order, unedited.
- `pytest backend/tests -q` tail (base count vs new count), `ruff check`, `mypy`.
- `python scripts/check_openapi_drift.py` output.
- `GET /api/config/packaged-drift` before and after the first publish that carries `deployment`,
  showing the four `deployment.*` units moving from absent to decided with a recorded baseline.
- `npm run test -- DeploymentSection` and the axe output.
- The `grep -rn "providerOrder" backend/src frontend/src` result named in Acceptance.
