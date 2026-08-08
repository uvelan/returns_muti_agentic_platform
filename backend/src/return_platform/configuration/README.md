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
log_level, Vault URI references, …) — business configuration must never come from environment
variables, and `ACTIVE_RELEASE` may never override a bootstrap-only key. Secret values never enter
a snapshot; only `vault://` URI references are permitted through.

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
DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED
```

`DRAFT`/`VALIDATED`/`APPROVED` (and the `VALIDATED → DRAFT` rejection path) are owned exclusively
by `application/release_service.py::ReleaseService`; `APPROVED → ACTIVE` and `ACTIVE → SUPERSEDED`
are owned exclusively by `application/activation.py::ActivationService` — see
`domain/release.py::RELEASE_SERVICE_TRANSITIONS`. Every transition recomputes the snapshot checksum
(`application/snapshot.py::compute_checksum`) and compares it against the persisted value before
proceeding, so a tampered or corrupted stored snapshot is caught before it can be validated,
approved, or activated. Every transition is a single CAS write on `(release_id, expected_status)`;
a losing concurrent caller sees `modified_count == 0` and raises `InvalidTransitionError` — proven
by `tests/configuration/test_release_lifecycle.py`'s barrier-synchronized concurrent
validate/approve tests, which assert `success_count == 1, failure_count == 1` exactly, not just "at
least one succeeded."

## Atomic activation

`ActivationService.activate_release()` is a single MongoDB transaction (design §13.8): supersede
the current `ACTIVE` release, activate the target (must be `APPROVED`), CAS-update the
`configuration_active_pointer` singleton — all three or none. A partial unique index on
`configuration_releases` where `status = "ACTIVE"` is defence in depth against two `ACTIVE`
releases ever being representable even if the transaction logic changes. Both `ActivationService`
and `ReleaseService` always operate against the fixed `platform` database — independent of the
business `mongo_database` setting — via fixed collection names
(`configuration_releases`, `configuration_active_pointer`); a test or caller that targets a
different database/collection name observes an always-empty collection and proves nothing.

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
