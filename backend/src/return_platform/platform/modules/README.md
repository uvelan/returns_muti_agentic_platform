# platform/modules

Module identity, declaration, and descriptor bookkeeping.

## What's here now (Phase 1A)

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

`ModuleRegistry` deliberately does not construct anything yet, and does not know what
a running module looks like — it has nothing to prove before `ModuleRuntime` exists.

## What Phase 1B adds

`contracts.py` — `ModuleRuntime` (the lifecycle every module implements:
`initialize`, `publish_capabilities`, `resolve_capabilities`, the four-method
epoch-keyed reconfiguration protocol, `health`, `shutdown`), `ModuleRuntimeContext`
(platform services + the capability registry, no module-specific field), and
`ModuleFactory` (`descriptor` + `create(context, config) -> ModuleRuntime`). Once those
exist, `ModuleRegistry` grows a `construct()` step that calls a factory and a `health()`
aggregation step — extending this package's registry, not replacing it.

`lifecycle.py` and `bootstrap/` add the four-pass activation sequence: construct every
module, publish native capabilities, let `bootstrap/adapters/` publish consumer-shaped
bindings, then let every module resolve its ports. See the target design, section 2.1 and
section 13.1, and the implementation plan's Phase 1B.

## Security constraint

The registry never accepts a Python import path from configuration. `implementation_id`
resolves only against `builtins.py`'s explicit allowlist or a controlled package entry
point — never `importlib.import_module(arbitrary_string)`.
