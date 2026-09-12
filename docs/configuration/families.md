# Configuration families

**Current as of 2026-08-14, commit `dcbb7dc`.**

`backend/config/README.md` documents what the packaged YAML files are. It does not
document, per family: security classification, bootstrap-only vs runtime-editable,
hot-change support **stated separately for API and worker processes**, propagation
behaviour, in-flight case behaviour, rollback implications, or how to read back the
adopted release. This document does.

## Read this first

**The database is the runtime source of truth.** Packaged YAML under
`backend/config/` is bootstrap/default input only and is **never rewritten at
runtime**.

**Hot configuration is now true of workers as well as the API.** It previously was
not, and the documentation asserted it anyway. Workers were startup-bound: they
loaded configuration once and never reconciled, so publishing a release changed API
behaviour and left every worker on the old release indefinitely, with no error
anywhere and no way to detect it. That is fixed — all five deployed worker classes
reconcile — and it is now *verifiable* rather than asserted:

```http
GET /api/config/adoption
```

`LIVE` only when every required process class has at least one live instance and
**all** of them report the activated release id **and** head revision. Otherwise
`ACTIVATING`, naming the classes that have not adopted.

**Read that endpoint after every publish.** A release is activated the moment you
promote it. It is *live* when every class is running it, and those are different
facts.

## Legend

| Column | Values |
|---|---|
| **Security** | `PUBLIC` (non-sensitive) · `INTERNAL` (operationally sensitive) · `SECURITY` (a control — changing it changes what the platform will permit) · `SECRET-REF` (holds a credential identity, never a value) |
| **Editable** | `BOOTSTRAP` (startup only) · `RUNTIME` (via a release) · `RESTART` (release-editable, takes effect on restart) |
| **Hot: API** / **Hot: worker** | Whether a published change takes effect without restarting that process class |
| **In-flight cases** | `PINNED` (existing cases keep the old value) · `IMMEDIATE` (applies to work already running) |

---

## `RETURN_PLATFORM`

| Family | Security | Editable | Hot: API | Hot: worker | In-flight cases | Rollback |
|---|---|---|---|---|---|---|
| `schema_version`, `assumption_set_version` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `agents.*` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `discovery.identification_fields` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | **Removing a field** can strip a signal a conversation was mid-clarification on. Add freely; remove during quiet periods |
| `discovery.strong_anchors` | `SECURITY` | `RUNTIME` | Yes | Yes | `PINNED` | Safe. Governs which fields match exactly rather than fuzzily |
| `discovery.progressive.*` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe. Includes `customer_fulltext_index` — repointing needs no deployment |
| `discovery.auto_confirmation_allowed` | **`SECURITY`** | `RUNTIME` | Yes | Yes | `PINNED` | **Rejected at validation in production.** Production discovery cannot allow automatic confirmation |
| `discovery.conversation` | `PUBLIC` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `source_resolution` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `clarification_policy` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `return_policy` | `INTERNAL` | `RUNTIME` | Yes | Yes | **`PINNED`** | A case decided under one policy must not be re-judged under another |
| `workflow` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `support` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `omc` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | `omc.tendered_is_pickup` is **rejected at validation** — a tendered state is not a physical pickup |
| `bay` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe. Bay is best-effort; a bad value degrades a recommendation, never a return |
| `return_case` (timings) | `INTERNAL` | `RUNTIME` | Yes | Yes | **`PINNED`** | See below |
| `business_calendars` | `INTERNAL` | `RUNTIME` | Yes | Yes | **`PINNED`** | See below |
| `integrations` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | `ai_may_fabricate_success` is a **`SECURITY`** field per topic |
| `runtime_integrations` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `deployment.ai.provider_order` | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | **`SIMULATOR`/`MANUAL` rejected at validation in production.** The only source of AI provider order — see "`deployment`: the env→release switches" below |
| `deployment.ai.model_pools` | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Read-only in the UI, and ignored on adoption, for a provider `runtime_integrations` governs |
| `deployment.ai.google` (`thinking_budget`, `response_schema`) | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Safe |
| `deployment.dependencies` | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | **`SIMULATED` rejected at validation in production** |
| `deployment.feedback_learning` | `INTERNAL` | `RUNTIME` | Yes | (b) | `IMMEDIATE` | Safe |
| `deployment.support_ticket` | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | `base_url` required at validation for `INTERNAL_WITH_EXTERNAL_MIRROR`/`EXTERNAL_AUTHORITY` |

### `deployment`: the env→release switches (D-CFG-4, CFG-6)

Eight business switches that used to live only in `.env`/compose now live in
the release, at `deployment`, hot-adopted like everything else in this table:
AI provider order, the per-provider model pools, the two GOOGLE extras
(`thinking_budget`, `response_schema`), the four dependency-simulation modes,
feedback learning, and the support-ticket mode/base URL. Edited at
`/config/deployment`; every option production refuses to run renders
**disabled with the reason on the option**, not hidden.

**Precedence with `runtime_integrations`.** `runtime_integrations` (the AI
Control Center's provider bindings) still governs *availability* — credentials
and the model pool — for every provider it enables. `deployment.ai.provider_order`
is the only source of *order*, and the only way to name `SIMULATOR`/`MANUAL`:
a provider `runtime_integrations` enables but `deployment.ai.provider_order`
does not name gets zero routes, exactly as an uncredentialed provider does.
Enabling a new provider in the AI Control Center is therefore two steps, not
one: enable it there, then add it to the order at `/config/deployment`.

**The env stays the bootstrap default.** A value actually set in `.env`/compose
(not a pydantic default nobody configured) becomes the packaged `deployment.yaml`
default the FIRST release publish carries forward. Once a release exists, the
env is no longer read for these eight switches — the release is authoritative,
env and all — until a `git revert` drops the `deployment` key from the model,
at which point the next bootstrap run republishes without it and env is
authoritative again (`_drop_retired_keys`; no data migration).

**The production gate runs on every `RETURN_PLATFORM` publish, not only one
that touches `deployment` (CFG-6 A11).** `_enforce_deployment_gate`
(`configuration/api/releases.py:438-455`) reads the *whole* merged
`deployment` payload — the patch's own value where the publish changed it,
otherwise whatever the resulting document already carries — and calls
`validate_deployment_for_environment` against it unconditionally, for both
`POST /api/config/publish` and `POST /api/config/adopt-packaged`. A publish
that edits `return_policy` and never mentions `deployment` at all is refused
with a 422 at `deployment.ai.provider_order` (say) if the document it would
produce still names `SIMULATOR` there in production, exactly as a publish
that changed that field directly would be. The same unconditional check runs
again at process startup for every worker resolving the active release
(`main.py:555-565`, `configuration/runtime_loader.py:118-128`) and remains
the backstop even if the publish-time gate were ever bypassed
(`Settings.validate_relationships`, `settings.py:936-955`). Practically: a
release that already violates the gate cannot be published forward by an
edit to an unrelated section — the offending `deployment` value has to be
fixed in the same publish, or the production gate is *why* an operator
cannot "just change the return window" on a release production was already
refusing to run.

(b) `deployment.feedback_learning.enabled` reads live off a `SettingsSource`
(`operations/feedback_service.py`) at call time — `resources.settings.<field>`,
never a captured `Settings` value, because `RuntimeConfigurationActivator.refresh`
*replaces* `resources.settings` with a new instance on every adoption rather
than mutating it in place (RV round 1 F1 on this lease found the service had
gotten this backwards: it held the `Settings` object itself and never saw a
later change). `test_rebinding_resources_settings_changes_the_services_next_read`
(`tests/operations/test_feedback_learning_settings_source.py`) proves the fix:
rebinding a `SettingsSource`'s `.settings` changes the service's answer on its
very next read, no reconstruction needed.

That said, `FeedbackLearningService`/`ReturnOrchestrator` (the class that
constructs it) currently has **no production construction site** in
`backend/src` — only tests instantiate it, and its one construction site
(`operations/orchestrator.py`) passes a `SettingsSnapshot` (a `SettingsSource`
that never changes — the honest answer for a caller with no live resources
container), not a live one. So this hot-adopt does not yet reach production
traffic; a future wiring site must construct `ReturnOrchestrator` with the
process's real, live resources container and thread it through to
`FeedbackLearningService` in place of `SettingsSnapshot`.
`test_no_module_constructs_the_guarded_classes_with_a_bare_settings_name` and
`test_the_one_feedback_learning_service_call_site_wraps_its_settings_source`
(same file, AST-based) pin that requirement: any call site — the current one
or a future one — that passes a bare `settings` name instead of a wrapped or
live source fails the build.

### Validation rules that fail closed

A release is refused, not warned about, when:

- any of the five required agents is missing — `order_discovery`,
  `return_workflow`, `return_fulfillment`, `bay_assignment`, `feedback_learning`;
- `discovery.auto_confirmation_allowed` is true in production;
- `omc.tendered_is_pickup` is true;
- identification `field_id` or `intent_key` values collide;
- a date-bound identification field is `multiple`;
- a `FULLTEXT` search declares `narrow_with`;
- a business calendar declares no working periods.

These fail at **release validation, before publication** — not at request time in
front of an associate.

### Timings and calendars: why `PINNED` matters here

`support_response_wait_seconds` and `reminder_interval_seconds` are
**business-calendar durations**. Eight hours means eight *working* hours.

A workflow reads its timings **once at start** and keeps them for its lifetime. An
in-flight return must not have its deadline moved underneath it, so a change
applies to new cases only. Changing a calendar does **not** retroactively move a
running case's deadline.

`bay_wait_seconds` is deliberately **not** a business duration. It bounds dead time
on the critical path while an associate waits; stretching it across a weekend would
leave a live conversation hanging.

**An empty `business_calendars` is not a silent Mon–Fri.**
`resolve_business_deadline` falls back to wall clock **and says so on the case** —
the behaviour that was there before, now visible rather than assumed. A calendar
declaring every day whole restores wall-clock behaviour explicitly, which is what a
24/7 operation should configure.

`return_case` and `business_calendars` are both defaulted so a release predating
them still loads.

---

## `AI_GATEWAY`

| Family | Security | Editable | Hot: API | Hot: worker | In-flight | Rollback |
|---|---|---|---|---|---|---|
| Task system prompts, `promptVersion` | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` (per request) | Safe |
| Provider allowlists | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Narrowing is safe; widening admits a provider to platform data |
| Key references | **`SECRET-REF`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | References only. **Never values** |
| Model bindings, task routes, priorities | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Requires a valid receipt per active route |
| Token limits | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Lowering may truncate |
| Retry, rate limits, circuit thresholds | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Safe |
| Deterministic fallback selection | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | This is what a caller gets on `REJECT` or exhaustion |
| Interception policy | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Default `ALLOW_ALL` — an explicit, greppable choice rather than an omission |
| Safety and redaction policy | **`SECURITY`** | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Weakening changes what leaves the platform |

`IMMEDIATE` rather than `PINNED` throughout, and deliberately: an AI request is a
single short-lived operation, not a long-running case, so pinning would mean a
tightened safety policy did not apply to conversations already open.

**The route pool is rebuilt at the same activation boundary as the snapshot.** A
pool rebuilt at a different moment would route on one release's providers with
another's limits.

A route is usable only after live validation produced a receipt bound to provider,
model, task, secret fingerprint and configuration checksum.
Publication is refused while any active route lacks one.

**Production and staging fail closed when `AI_GATEWAY` is absent.**

---

## `DEPENDENCY_SIMULATION`

| Family | Security | Editable | Hot: API | Hot: worker | In-flight | Rollback |
|---|---|---|---|---|---|---|
| Simulation contracts, operation sequences | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Safe |
| Narrative behaviour, provider order, timeouts | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Safe |
| Pricing assumptions | `INTERNAL` | `RUNTIME` | Yes | Yes | `IMMEDIATE` | Safe |

**Simulation is forbidden in production and the setting fails closed.** The literal
guard is in `configuration/settings.py`: *"External dependency simulation is
forbidden in production."*

Production and staging still fail closed when the **domain** is absent from the
release, which reads as contradictory and is not: the domain must be present and
valid so the platform can prove simulation is *off*, rather than inferring it from
an absent block.

---

## Infrastructure and deployment — not editable here

| Family | Security | Editable | Why |
|---|---|---|---|
| Data-source **endpoints** | **`SECURITY`** | **`RESTART`, fail closed** | A running process holds live client pools against the old endpoint. Swapping under them leaves half the process talking to each |
| `PLATFORM_DATA_SOURCE_ALLOWED_HOSTS` | **`SECURITY`** | `BOOTSTRAP` (env) | Governs which endpoints may be configured at all. Runtime-editable would make the allowlist self-amending |
| SQL pool sizing (`sqlserver_pool_*`) | `INTERNAL` | `BOOTSTRAP` (env) | Per-process resource ceiling |
| `PLATFORM_GRAPH_SYNC_BATCH_SIZE` | `INTERNAL` | `BOOTSTRAP` (env) | Default 250, range 1–5,000 |
| `PLATFORM_SEED_RECORD_LIMIT` | `INTERNAL` | `BOOTSTRAP` (env) | A hard upper bound the Seed Data UI cannot exceed |
| Credentials (`PLATFORM_*_PASSWORD`, `*_DSN`, `*_API_KEYS`) | **`SECRET-REF`** | `BOOTSTRAP` (env) | Read from the process environment; never held in graph configuration |
| DB schema, graph migrations, deployment wiring | `INTERNAL` | Version-controlled | **Infrastructure contracts, not agent behaviour.** Checksum-tracked in `ConfigurationMigration` nodes; a modified migration file is rejected after application |

---

## Propagation, concretely

```text
publish  → validated, immutable, checksum'd, requires expected head revision
   │
   ├─ activating process: validate → build snapshot → atomic epoch swap → report
   │
   └─ other processes: notice the head revision (~5s) → same sequence
   │
   ▼
GET /api/config/adoption
   ACTIVATING  → at least one required class has not adopted
   LIVE        → every required class reports this release id AND head revision
```

The six required classes: `api`, `return-workflow-worker`,
`order-discovery-worker`, `return-orchestrator`, `outbox-publisher`,
`integration-outbox-worker`.

**No request observes two modules on two different releases.** A single
replica-scoped epoch swap is what makes that true, and every request holds a
uniquely-identified `EpochLease` rather than a bare count, so releasing one
request's lease cannot be mistaken for releasing another's.

## Rollback

Forward-only: **promote an earlier release.** Published releases are immutable and
are never edited back.

Before rolling back, check the family table above for `PINNED`. Rolling back does
not retroactively change cases created under the newer release; they keep their
pinned snapshot until they complete.

Rolling back a family marked `SECURITY` restores the older control. Confirm that is
what you want — a rollback to fix an unrelated field also reverts every security
field in the same release.

## First-time bootstrap

```bash
./scripts/prepare_runtime_configuration.sh
```

Publishes and validates the initial graph configuration when no active release
exists, and is invoked by every host launcher before the backend starts. Releases
created before the multi-domain migration may contain only `RETURN_PLATFORM`;
publish a complete three-domain release before starting upgraded processes.

## Related

- [`../architecture/configuration-adoption.md`](../architecture/configuration-adoption.md)
- [`../optimization/configuration-caching.md`](../optimization/configuration-caching.md)
- [`../screens/configuration.md`](../screens/configuration.md)
- `backend/config/README.md` — what each packaged YAML file is
