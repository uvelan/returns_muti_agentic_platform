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
| **Security** | `PUBLIC` (non-sensitive) · `INTERNAL` (operationally sensitive) · `SECURITY` (a control — changing it changes what the platform will permit) · `SECRET-REF` (holds Vault references, never values) |
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
| `extensions` | `PUBLIC` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `runtime_integrations` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |
| `feature_flags` | `INTERNAL` | `RUNTIME` | Yes | Yes | `PINNED` | Safe |

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
model, task, secret fingerprint, Vault version and configuration checksum.
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
| Vault addresses and tokens | **`SECRET-REF`** | `BOOTSTRAP` | — |
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
