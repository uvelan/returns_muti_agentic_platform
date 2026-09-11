# CFG-6 design — env → release `deployment` section

Spike on `refactor/unified-return-platform @ 660ab5e0` (CFG-0..3b merged). Decision in force: **D-CFG-4**. Read-only
analysis; every claim carries a `file:line` on that tree.

## 0. The finding that changes the shape of this lease

The hot-adopt machinery this lease was scoped to build **already exists and already carries two of these fields**.
`runtime_integrations.py:26` `apply_graph_runtime_configuration` derives `ai_provider_order` (:145) and
`<provider>_{lightweight,standard}_models` (:195-196) from the release's `runtime_integrations` section and returns a
**re-validated** `Settings` (:198 `Settings.model_validate(...)`). `runtime_activation.py:298` calls it on every
5-second poll (`refresh_interval_seconds: float = 5.0`, :184), rebuilds the route table (`build_routes`, :330), swaps
it in place (`current_route_pool.replace_routes`, :344) and reassigns `resources.settings` / `app_state.settings`
(:370-372) inside the activation boundary. So CFG-6 is **not** "build a live-reload path": it is (a) add a
`deployment` section feeding the *same* overlay the remaining fields through it, (b) move the readers that cache a
value at construction behind that rebuild, (c) fix the two env/Mongo duplicates.

**The production gates come for free.** `Settings.validate_relationships` (`settings.py:930`) refuses SIMULATOR
(:936), MANUAL (:938), interception (:946) and SIMULATED dependency modes (:955) in production, and re-runs on every
adoption because the overlay ends in `model_validate`. A release violating a gate raises inside
`RuntimeConfigurationActivator.refresh` **before** anything is published, so the process stays on its last good
release (two-phase `prepare`/`publish`, `runtime_activation.py:104-121`). Rollback needs no data migration either
(§7). The one genuinely new rule is **precedence** between `deployment` and `runtime_integrations`; §4 settles it.

## 1. Reader inventory — request time vs process start

**Already live; nothing to do.** `ai_provider_order` (`routes.py:210`) and the model pools (`routes.py:152-189`,
`_provider_models`) are read at route build, which the activator redoes. `google_thinking_budget` /
`google_response_schema` (`ai/providers/google.py:28-29`) are captured in `GeminiProvider.__init__`, but `_provider()`
(`routes.py:58,67`) constructs one per route on every `build_routes`. Provider availability
(`api/dependencies.py:177-190`) and the four `*_dependency_mode` (`api/dependency_simulator.py:105-108`) are read per
request off `app.state.settings` / `resources.settings`, both reassigned at `runtime_activation.py:370-372`.

**Work.** (a) `workers/integration_outbox.py:134-135,141,157` builds its dispatcher table once in `run()` (:58) from
`support_ticket_mode`, `support_ticket_base_url`, `omc_`/`freight_dependency_mode` → rebuild it as an
`ActivationParticipant`. (b) `operations/feedback_service.py:92` caches `feedback_learning_enabled` in `__init__` →
read `self._settings.feedback_learning_enabled` at call time (one attribute, no wiring). (c)
`operations/orchestrator.py:184` → `return_support/providers/factory.py:20` / `external.py:65,72` cache
`support_ticket_mode`/`base_url` in `ReturnOrchestrator.__init__`; **no production construction site exists in
`backend/src`** (only tests import it), so the requirement is that whichever site wires it passes live
`resources.settings`. (d) health cards read the **Mongo** `providerOrder` (`api/dependencies.py:196`) — repointed in
§5. Not a reader at all: `ai/routing/selection.py` touches no `Settings` field (grep `settings\.` → empty).

## 2. The `deployment` section

New `DeploymentConfiguration` in `configuration/return_configuration.py`, added to `ReturnPlatformConfiguration`
(:1760) **with a default** so a release cut before it still loads — the argument `copilot` (:1806) and
`policy_evaluation` (:1800) already carry. Packaged part `backend/config/returns/deployment.yaml`, listed in
`index.yaml`.

```yaml
deployment:
  ai: {provider_order: [GOOGLE, NVIDIA, SIMULATOR],                  # list, not the env CSV
       model_pools: {GOOGLE: {lightweight: [...], standard: [...]}}, # per provider key
       google: {thinking_budget: 2048, response_schema: false}}
  dependencies: {omc: SIMULATED, parcel: SIMULATED, freight: SIMULATED, lsi: SIMULATED}
  feedback_learning: {enabled: true}
  support_ticket: {mode: INTERNAL, base_url: null}                   # the api key stays env/Vault
```

**Gates, in three layers.** (1) `DeploymentConfiguration` model validators carry the environment-*independent* rules
lifted from `settings.py:831` (`validate_provider_order`: allowed names, uniqueness, `NONE`) and `:956` (`base_url`
required when mode is `INTERNAL_WITH_EXTERNAL_MIRROR`/`EXTERNAL_AUTHORITY`). (2) New
`validate_deployment_for_environment(section, environment)` carries the production rules (no SIMULATOR/MANUAL in
`provider_order`, no `SIMULATED` mode), called from the publish boundary (`POST /api/config/validate/{domain_key}`,
`/publish`, `/adopt-packaged`) with `app.state.settings.environment` so an operator gets a **422 with a path**
(`deployment.dependencies.omc`) rather than a release no process can adopt, and from `runtime_loader.py` at startup so
a release promoted from staging into production is refused at start with a named reason instead of at the first poll.
(3) `Settings.validate_relationships` stays the backstop — 1-2 are usability, 3 is the mechanism.

## 3. Bootstrap, in `packaged_adoption.py` terms

Env stays the **bootstrap default** by being injected into the *packaged* payload, the way `runtime_integrations`
already is (CFG.brief §3.2, "bootstrap-generated part kept"). New `deployment_payload_from_settings(settings) -> dict`
overlays onto packaged `deployment.yaml` only the fields in `settings.model_fields_set` — values a deployment actually
set in `.env`/compose, not pydantic defaults — injected at the two places the packaged payload is assembled:
`cli/bootstrap_graph_configuration.py:237` (before the `adopt_packaged_configuration` call at :262) and the API's
`_packaged_domain_payloads(request)` (`configuration/api/releases.py:999`). **The API side must not seed from
`app.state.settings`** — after CFG-6 that object is already release-derived, so the "packaged default" would be the
release's own value and every key would read as decided. The env-only payload is computed once at process start,
before `apply_graph_runtime_configuration` runs (`runtime_loader.py:101`, `main.py:531`), and stashed as
`app.state.packaged_deployment_defaults`.

`_carry_forward` (`packaged_adoption.py:189`) then gives the behaviour with **no new rule**:

| Situation | `_carry_forward` branch | Result |
|---|---|---|
| First publish (no `deployment` in the active release) | `key not in active_payload` → `merged[key] = value` (:230) | env/packaged value adopted, **baseline recorded** (:241) |
| Later start, operator never touched it, env changed | `key in known`, `active_digests[key] == known[key]` (:233) | new env value adopted — env is a default that moves |
| Later start, operator changed it at `/config/deployment` | `key in known`, digest moved | **release value kept**, env ignored |
| Release predates baselines and both differ | `_fill_absent_leaves` + `unadopted` (:236-239) | release wins, key listed in `undecided`, operator resolves via `POST /adopt-packaged` |

**Granularity.** `RETURN_PLATFORM` carries forward per top-level key (`adopt_packaged_configuration`, :381-386) with
no split, so one `deployment` key would let an edited provider order freeze an env change to dependency modes. Fix:
add `RETURN_PLATFORM_DOMAIN_KEY: ("deployment",)` to `CARRY_FORWARD_SPLIT_KEYS` (:88) and route the RETURN_PLATFORM
branch through the existing `_units`/`_assemble` (:95,:107), giving units `deployment.ai`, `deployment.dependencies`,
`deployment.feedback_learning`, `deployment.support_ticket`. Backwards compatible — `_units` splits only the named key
and no existing release carries `deployment`, so no recorded baseline changes meaning — and the same unit names become
valid `--adopt-packaged-key` / `POST /adopt-packaged` values.

## 4. The AI route pool: the join, and precedence

`build_routes` (`routes.py:203`) is the join: `provider_order × credentials × tier × models`, credentials from
`resolved_*_api_keys` (:135-145, **env/Vault, unchanged**) and models from `_provider_models` (:152). Hot-adopt is
`runtime_activation.py:330-347` and needs no new trigger; `replace_routes` mutates the live pool only after every
participant has prepared successfully.

Precedence with `runtime_integrations`, which sets the same two fields for the providers it governs and whose values
are receipt-backed (`required_validation_receipts`, `runtime_integrations.py:201`). This is audit DUP-001 ("precedence
is Settings-driven, availability is ai_gateway-driven") made explicit rather than left to whoever wrote last:
`runtime_integrations` runs first, blanket wipe (`:38-70`) included; **availability** stays its answer for every
provider it enables, and `deployment.ai.model_pools` fills in only providers it does not govern; **order** is always
`deployment.ai.provider_order`, which reorders and filters the enabled set and is the only source of
`SIMULATOR`/`MANUAL` (`runtime_integrations` cannot express them). A provider named in the order but not credentialed
already contributes zero routes (`_provider_credentials` → `()`, `routes.py:148`), so no new failure mode.
`google.thinking_budget`/`response_schema` are `deployment`'s alone.

The overlay is a new `configuration/deployment_settings.py::apply_deployment_configuration(settings, configuration) ->
Settings`, called from inside `apply_graph_runtime_configuration` just before its final `model_validate`
(`runtime_integrations.py:196-198`) — exactly **one** place where a release becomes `Settings`, and one re-validation.

## 5. Mongo `ai_settings`: retire `providerOrder`, keep `interceptMode`

The document (`operations/repository.py:2117-2170`) is seeded from `settings.ai_provider_order` (:2118,:2125) then
edited independently through `PUT /api/ai/settings` (`api/ai_gateway.py:193-207`) — the duplicate the audit named. It
**routes nothing**: `build_routes` never reads it. Its only consumers are health-card enumeration
(`api/dependencies.py:196`) and resume force-provider selection (`api/ai_gateway.py:364`).

**Retire `providerOrder`:** both consumers read `settings.ai_provider_order` (post-CFG-6: the release).
`AIGatewaySettingsUpdate.providerOrder` (`operations/models.py:571`) goes; `PUT /settings` takes `interceptMode` +
`expectedVersion` only, and its production SIMULATOR check (`ai_gateway.py:201`) moves to
`validate_deployment_for_environment`. A stored document keeps the field harmlessly; `get_ai_settings`' `["NONE"]`
migration branch (:2135) is deleted with it. **Keep `interceptMode` in Mongo:** a per-instant operator toggle with its
own optimistic lock, read at request time (`ai/gateway/service.py:112`, `interception_policy.py:179`). Moving it into
the release would put a publish + audit + 5-second adoption in front of "intercept the next request" — the one control
whose whole value is that it takes effect *now*. Production refuses interception outright (`settings.py:946`), so
nothing is lost by leaving it out of the release.

## 6. `/config/deployment`

`frontend/src/domains/config/DeploymentSection.tsx` on CFG-4's `TypedSectionScreen` chrome, with CFG-3b primitives
that all exist (`frontend/src/components/forms/`): provider order `OrderedList`; pools `TagListInput` per provider ×
tier; modes and `support_ticket.mode` `EnumSelect`; `feedback_learning.enabled` and `response_schema` `Toggle`;
`thinking_budget` `NumberField`; `DiffPreview` + `PublishBar` as on every typed screen. Registered in
`domainScreens.ts` and `ConfigurationPage.tsx` (:108-114) under the existing `config.runtime.read`/write capabilities.
Production-gated options render **disabled with the reason on the option**, not hidden — an operator must be able to
see that SIMULATOR exists and why it is unavailable; `environment` comes from the `/api/config/runtime` payload (add
the field if absent; one line, OpenAPI regenerated). Pools for providers governed by `runtime_integrations` render
read-only with "governed by the AI Control Center provider bindings" (§4) — the only way that precedence is visible to
an operator.

## 7. Migration and rollback

`.env.example` (:52,54,59,163,219-294,323-326) and `compose.yaml` (:69,70,75,95,106-123) keep every line, prefixed
with a block comment: *bootstrap default only; the authoritative value lives in the release (`deployment`) — edit it
at `/config/deployment`*. Deleting them is wrong; they are what a first publish seeds from. **A deployment that never
publishes** cannot occur through the launchers (the bootstrap publishes on every start), and where the graph is
unreachable in development the snapshot builder falls back to the packaged baseline (`runtime_loader.py:92`,
`allow_baseline_fallback`) — packaged `deployment.yaml` overlaid with env, exactly today's behaviour. **Revert** is
`git revert` plus one bootstrap run: `_drop_retired_keys` (`packaged_adoption.py:247`) removes the now-undeclared
`deployment` key with a `retired_configuration_key` warning and env is authoritative again — no data migration, no
manual Neo4j edit. Worth its own test; it is the property that makes this lease safe to land.

## 8. Carried: RV CFG-3a F5

`adopt_packaged_release` (`configuration/api/releases.py:1032-1045`) rolls back only on `ReleasePromotionError`. A
Neo4j fault, a `ValidationError` inside `publish_release_with_domains`, a failed `set_release_metadata` (:1056) or
`record_configuration_audit` (:1064), or task cancellation all leave an orphaned DRAFT/VALIDATED node — the exact
thing the handler's own docstring promises against ("On refusal the release this call created is archived, not left
behind", :986). The same hole is at `/publish` (:848). Fix: extend the guarded block through the metadata and audit
writes and add `except BaseException: await _archive_draft_on_refusal(...); raise` after the typed clause. Safe to
widen — `_archive_draft_on_refusal` (:931) archives only a release still in DRAFT/VALIDATED, so a post-RELEASED
failure archives nothing.

## 9. Risks

Precedence (§4) must be asserted by a test, not a comment, or the next reader "fixes" the overlay order. `_units` on
`RETURN_PLATFORM` (§3) touches the carry-forward path every other section depends on: guard it with an equality test
against the pre-change merge for a release without `deployment`. The worker dispatcher table (§1a) is the one reader
with real wiring work; if it slips, dependency-mode changes stay restart-scoped and the screen must *say so*.
