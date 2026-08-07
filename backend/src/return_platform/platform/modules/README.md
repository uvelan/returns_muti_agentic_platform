# platform/modules

Module identity, declaration, descriptor bookkeeping, and runtime lifecycle.

## Descriptor bookkeeping (Phase 1A)

- `descriptor.py` — `ModuleDescriptor`: `module_id`, `module_kind`,
  `implementation_id`, `version`, `capabilities`, `configuration_schema`,
  `required_platform_capabilities`, `initialization_dependencies` (module_ids that
  must finish `initialize()` first — see `lifecycle.py::topological_order` below).
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
- `lifecycle.py` — `topological_order(descriptors)`: orders module_ids so every
  module's `initialization_dependencies` precede it (DFS with cycle detection; a
  module depending on itself is a cycle of length one, caught the same way as any
  other cycle; a dependency naming an unregistered module_id raises
  `MissingInitializationDependency`). `initialize_all()` runs in that order and fails
  closed **with cleanup**: if a module raises, that module itself is shut down first
  -- it may have partially-initialized resources even though `initialize()` raised,
  which is why `Initializable.shutdown()`'s contract requires tolerating this -- then
  every module already initialized is shut down in reverse order, before the
  exception propagates, so the caller never holds a partially-initialized module
  list; if cleanup itself also fails, both are reported together in one
  `ExceptionGroup`. `shutdown_all()` is reverse-ordered,
  best-effort — every module gets a shutdown attempt even if an earlier one failed.
  `rollup_health()` is worst-of aggregation over a caller-supplied list of
  `ModuleHealth`. `topological_order`/`initialize_all`/`shutdown_all` depend only on
  the narrow `Initializable` protocol (`initialize`/`shutdown`), not the full
  `ModuleRuntime` surface — test doubles only need to implement those two methods.

`bootstrap/activation.py::activate()` calls `topological_order()` before constructing,
so the module list it produces — and therefore `initialize_all`'s order and
`shutdown_all`'s reverse order — is dependency-correct by construction, not by
caller discipline.

The epoch-keyed reconfiguration mechanism itself (`EpochAdmission`,
`ReconfigurationCoordinator`, `ReplicaStatus`, `FatalReconfigurationError`) lives in
`bootstrap/epoch.py`, not here — it coordinates *multiple* modules plus a
replica-scoped pointer, which is a bootstrap concern, not a module-registry one. See
`bootstrap/README.md`.

## Security constraint

The registry never accepts a Python import path from configuration. `implementation_id`
resolves only against `builtins.py`'s explicit allowlist or a controlled package entry
point — never `importlib.import_module(arbitrary_string)`.
