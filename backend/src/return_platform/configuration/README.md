# configuration

The canonical runtime configuration model (design doc §3, §13.8). One manifest-driven model
replacing the V1/V2/runtime-config fragmentation, with a release lifecycle and atomic activation.

## Canonical domains

`platform`, `system_store`, `modules`, `agents`, `workflow`, `sources`, `integrations`, `graph`,
`ai`, `features` — each a typed, frozen pydantic model under `domain/`. Together they compose
`domain/release_model.py::RuntimeSnapshot`, the single immutable output of configuration
resolution. Nothing downstream ever reads a domain model directly; everything reads a
`RuntimeSnapshot` through a `ConfigurationView` (`domain/handle.py`).

## Precedence

```
BOOTSTRAP_ENV  →  BASELINE  →  ACTIVE_RELEASE  →  (output) RuntimeSnapshot
```

`application/precedence.py::ConfigurationPrecedenceEvaluator` enforces this. `BOOTSTRAP_ENV` may
only supply an explicit allowlisted set of deployment/bootstrap fields (region, host, port,
log_level, credentials, …) — business configuration must never come from environment
variables, and `ACTIVE_RELEASE` may never override a bootstrap-only key. Secret values never enter
a snapshot; only non-secret references are permitted through.

## Manifest and compatibility translation

`backend/config/manifest.yaml` is authoritative for which YAML files under `backend/config/` are
active configuration — `application/loader.py::ConfigurationLoader` never globs a directory to
discover files. Every manifest entry is validated: no absolute or traversal paths, the file must
exist, and the loaded document's `module_id`/`module_type` must match the manifest key and its
prefix (`agent.*` → `AGENT`, `source.*` → `SOURCE`, …). `schema_version` must be one of
`loader.py::SUPPORTED_MANIFEST_SCHEMA_VERSIONS` — anything else is rejected outright rather than
silently treated as the current version.

`application/compatibility.py::LegacyCompatibilityAdapter` translates the loaded manifest modules
into a `RuntimeSnapshot`. **Translation is fail-closed**: a malformed AGENT/SOURCE/GRAPH/MAPPING/
SYNC/PLATFORM payload raises `ConfigurationValidationError` immediately — it is never logged as a
warning and skipped, because a warning-and-skip would let a module declared in `ModulesConfig`
silently disappear from its actual canonical domain (an agent nobody notices stopped being
routable). `POLICY` entries are preserved only in `ModulesConfig` by design and are never mapped to
`IntegrationsConfig`; `MAPPING`/`SYNC` entries are never treated as `GraphSchemaNode`s. Dynamic
Knowledge schemas are loaded only when declared as `GRAPH` modules in the manifest — directory
globbing under `dynamic_knowledge/` is forbidden, so an unreferenced YAML file there can never
become runtime configuration.

## Semantic validation

`application/validator.py::ConfigurationValidator.validate_snapshot()` runs before a release may
move `DRAFT → VALIDATED`, and raises `ConfigurationValidationError` naming every failure (source
domain, source identifier, referenced identifier, target domain) rather than stopping at the
first. It checks:

- Every declared module dependency's `module_id` exists, is not self-referential, and the
  dependency graph is acyclic (DFS-based cycle detection).
- **Reverse completeness**: every enabled `AGENT`/`SOURCE`/`WORKFLOW`/`GRAPH`/`MAPPING`/`SYNC`
  module has a corresponding entry in its specialized domain config — a module that exists in
  `ModulesConfig` but nowhere else is caught here, not discovered at runtime. `POLICY` is exempt by
  design.
- Agent → manifest module normalization, and agent AI route refs against `ai.routes` →
  `ai.tasks`/`ai.providers`. These checks are fail-closed even when `ai.tasks`/`ai.providers` are
  empty maps — gating on `ai_tasks and task_id not in ai_tasks` would let an unknown `task_id` pass
  whenever the map happened to be empty, which is the opposite of what an empty map should mean.
- Workflow stage IDs are business state names, not agent IDs — only a structured `{"handler":
  {"type": "AGENT", "agent": ...}}` stage triggers agent-ID resolution.
- Graph → source references, but only for a plain string/`None` value (a logical reference into
  `SourcesConfig`); a `dict` value is a Dynamic Knowledge embedded source definition and is not
  cross-referenced.
- Source `connector_type` is non-empty and `access_mode` is read-only if set — the platform never
  configures a writable external source.
- `SystemStore` structures have non-empty, non-duplicate physical names.

## Release lifecycle

```
DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED
```

Held in Neo4j and enforced by `graph_repository.py::RELEASE_TRANSITIONS`, which is the single
table all three enforcers share: both repository implementations and the Data Console router's
early 409 check. `RELEASED` is the live state — `get_active_release` resolves it, and
`ConfigurationSnapshotBuilder` refuses to start production without one.

There is deliberately no transition *out* of `RELEASED`. A release leaves it only by being
superseded when its successor publishes, which the publish transaction does atomically; exposing
`RELEASED → SUPERSEDED` as a promotion would let an operator retire the live configuration with no
replacement.

**Wave D3 retired a second lifecycle.** `application/release_service.py::ReleaseService` and
`application/activation.py::ActivationService` implemented DRAFT → VALIDATED → APPROVED → ACTIVE
→ SUPERSEDED over MongoDB, with checksum verification and a compare-and-swap activation. It was
the better-hardened of the two and no production path ever constructed it — both services existed
only in their own tests, while the runtime read the graph. Keeping both meant two vocabularies
over two databases; see `docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md` for the measurements
behind choosing the graph. `domain/release.py::ReleaseStatus` survives as the *manifest* status
vocabulary and no longer describes any live transition.

## Integrity

The release checksum is frozen and then verified, rather than restamped:

- **DRAFT → VALIDATED** computes the checksum over every domain payload and records it.
  `save_draft_domain` refuses to touch a release past DRAFT, so this is the point contents stop
  changing.
- **VALIDATED → RELEASED** recomputes and *compares*, raising `ConfigurationIntegrityError` on
  mismatch. Before Wave D3 both repositories recomputed and overwrote, so a domain edited between
  validation and publication was adopted rather than caught.
- **Every load of the active release** recomputes and compares again
  (`ConfigurationSnapshotBuilder.build_snapshot`). This is the strongest of the three: it catches
  tampering after publication, and in production — where `allow_baseline_fallback=False` — a
  mismatch means the process refuses to start.

All three go through one `compute_release_checksum`. They were previously three separate
implementations kept consistent by nothing but having been written the same way. The encoding is
length-delimited so that a `(domain_key, payload)` pair cannot collide with a different split of
the same bytes.

pymongo's async driver makes `session.start_transaction()` itself a coroutine — it must be
`await`ed to obtain the context manager (`async with await session.start_transaction():`). A
missing `await` type-checks fine against a permissive session mock but raises `TypeError` against
the real driver, silently preventing every activation from ever running. Any new transactional
adapter in this module must follow the same `await`ed form already used here and in
`workflows/persistence.py`.

**Enforced by** `tests/configuration/test_concurrent_activation.py` — run against the project's own
`mongodb` + `mongodb-rs-init` compose services (a hand-rolled session mock cannot exercise real
transaction rollback/isolation) — asserting exactly one winner, the pointer's `release_id`/
`checksum` match that specific winner, the pointer version advances by exactly one, the loser has
no partial mutation, and the previously-active release is superseded by the winner only.

## Pinned reads

`domain/handle.py::ConfigurationHandle` (`application/runtime_configuration.py`'s
`RuntimeConfigurationHandleImpl`) resolves `ConfigurationView`s and has no read method of its own —
`current(epoch)` for the adopted epoch, `async pinned(release_id)` for a historical release a
running session stays bound to for its whole life. `pinned()` recomputes the checksum of the loaded
historical snapshot and raises `ConfigurationIntegrityError` on mismatch before constructing the
view — a stored release can never be adopted from a tampered or corrupted document just because its
old checksum still matches what was written alongside it.

## What's not here yet

Runtime adoption (the epoch-keyed two-phase reconfiguration protocol driving `bootstrap/reconciler.py`)
is specified in the implementation plan's Phase 2 §6 and the design doc's §13.2; see
`bootstrap/README.md` for the mechanism it drives. `configuration/domain/system_store.py` is the
typed contract only — the system store's own bootstrap, migration, and fenced-locking machinery
lives in `platform/system_store/` (Phase 3).
