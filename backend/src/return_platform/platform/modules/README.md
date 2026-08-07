# platform/modules

Module identity, declaration, descriptor bookkeeping, and runtime lifecycle.

## Descriptor bookkeeping (Phase 1A)

- `descriptor.py` — `ModuleDescriptor`: `module_id`, `module_kind`,
  `implementation_id`, `version`, `capabilities`, `configuration_schema`,
  `required_platform_capabilities`.
- `registry.py` — `ModuleRegistry`: registers descriptors, rejects a duplicate
  `module_id` or a duplicate `implementation_id`, resolves a module_id back to its
  descriptor and factory, and validates that every capability a descriptor requires
  has been published somewhere in a `CapabilityRegistry`.
- `builtins.py` — the allowlist a module's `implementation_id` resolves against.
  Configuration never supplies a Python import path; a concrete implementation
  registers itself here (or via a controlled package entry point) at import time.
- `exceptions.py` — `ModuleNotRegistered`, `DuplicateImplementation`,
  `CapabilityUnsatisfied`, `ModuleInitFailed`.

## Runtime lifecycle (Phase 1B)

- `contracts.py` — `ModuleRuntime` (the lifecycle every module implements:
  `initialize`, `publish_capabilities`, `resolve_capabilities`, the four-method
  epoch-keyed reconfiguration protocol, `health`, `shutdown`, `router`),
  `ModuleRuntimeContext` (platform services plus the capability registry, no
  module-specific field — see its docstring for which fields exist yet), `ModuleHealth`
  / `HealthStatus`, `ReconfigureOutcome`, and `ModuleFactory` (`descriptor` +
  `create(context, config) -> ModuleRuntime`).
- `registry.py` gained `construct(module_id, context, config) -> ModuleRuntime`,
  extending the same class Phase 1A built rather than replacing it.
- `lifecycle.py` — `initialize_all()` (ordered, fails closed), `shutdown_all()`
  (reverse-ordered, best-effort — every module gets a shutdown attempt even if an
  earlier one failed, errors collected into one `ExceptionGroup`), and
  `rollup_health()` (worst-of aggregation over a caller-supplied list of
  `ModuleHealth`). Modules do not currently declare dependency edges on each other, so
  there is no dependency sort here — only the order the caller supplies. Add a real
  sort if `ModuleDescriptor` ever grows a declared dependency list; don't invent one
  speculatively.

The epoch-keyed reconfiguration mechanism itself (`EpochPointer`, `EpochLeaseTracker`,
`ReconfigurationCoordinator`) lives in `bootstrap/epoch.py`, not here — it coordinates
*multiple* modules plus a replica-scoped pointer, which is a bootstrap concern, not a
module-registry one. See `bootstrap/README.md`.

## Security constraint

The registry never accepts a Python import path from configuration. `implementation_id`
resolves only against `builtins.py`'s explicit allowlist or a controlled package entry
point — never `importlib.import_module(arbitrary_string)`.
