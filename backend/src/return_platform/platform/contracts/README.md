# platform/contracts

Neutral protocols that domain modules structurally satisfy without `platform/*` ever
naming a domain type.

## Why this package exists

`platform/*` must not import `configuration`, `graph`, `agents`, `business`, `ai`, or
`graph_schema_analyzer` (see `tests/platform/test_layering.py`). But a few concepts —
"the configuration this request should read", "the read-consistency this operation
holds" — genuinely need to reach into platform-owned contexts like
`ModuleRuntimeContext` and `AgentExecutionContext`. This package is where that crossing
happens without an import: platform declares the shape it needs, and the domain type
satisfies it structurally.

## Contracts

- `RuntimeConfigurationView` / `RuntimeConfigurationHandle` (`runtime_configuration.py`)
  — satisfied by `configuration.domain.handle.ConfigurationView` /
  `ConfigurationHandle`. The view is immutable, names exactly one release, and is the
  only object with `section()`; the handle resolves views and cannot itself be read.
  This split is what makes configuration pinning structural rather than a convention —
  see the target design, section 7.1 and section 13.2.
- `ConsistencyHandle` / `ConsistencyChanged` (`consistency.py`) — satisfied by
  `graph.lifecycle.handles.GenerationHandle` / `GenerationChanged`. Says nothing about
  graph generations on purpose: an opaque token, `assert_current()`, and `release()`.
- `RuntimeEpoch` (`epoch.py`) — one replica-local generation of runtime state,
  declared here and consumed by `bootstrap/` starting in Phase 1B.
- `Clock` (`clock.py`) and `CorrelationContext` (`correlation.py`) — the smallest
  possible platform-owned utilities; no domain type needs to satisfy these, they are
  just here because every module needs one.

## What does not belong here

Business meaning. `RuntimeConfigurationView.section(key)` returns a raw mapping — the
calling module validates it into its own typed configuration model. This package never
grows a field that only one domain understands.

## Adding a contract

Add it only when a second domain module would otherwise need to import a first one's
type to describe a dependency. A contract used by exactly one consumer belongs in that
consumer's own `ports/`, not here.
