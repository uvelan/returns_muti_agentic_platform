# bootstrap

Startup composition. Knows every module's `module.py` and nothing else about them.

## What's here now (Phase 1B)

The module lifecycle mechanism, buildable and testable before any real module exists:

- `epoch.py` — the epoch-keyed two-phase reconfiguration mechanism (design doc
  section 13.2): `EpochAllocator` mints replica-local epoch numbers, `EpochPointer`
  holds the single "current epoch" reference, `EpochLeaseTracker` counts in-flight
  holders per epoch, and `ReconfigurationCoordinator` runs prepare-all →
  commit-all-with-one-swap, or abort-all if any module refuses.
- `context.py` — assembles whatever the caller provides into something that
  structurally satisfies `ModuleRuntimeContext`. Only wires the fields backed by an
  existing contract (`configuration`, `capabilities`, `clock`, `correlation`); does
  not know how to construct a `SystemStore` or an `AuditSink`, because those don't
  exist yet.
- `capabilities.py` — the native-publication (step 9) and resolution (step 11) passes
  over a module list.
- `activation.py` — construction (step 8) plus `activate()`, the function that runs
  steps 8–11 in order.
- `lifespan.py` — `module_lifespan()`: activation, then ordered `initialize()`, then a
  yield to serve traffic, then reverse-ordered `shutdown()` no matter how serving ends.
- `health.py` — module health collection and worst-of rollup.
- `errors.py` — startup failure classification (`FATAL` always stops; `DEGRADABLE`
  stops only in production).
- `adapters/` — populated starting in Phase 9; see its own README.

**`main.py` does not call any of this yet.** "Introduce the kernel alongside the
existing boot process, migrate nothing yet" — the mechanism above is real and tested,
but nothing has cut the actual application over to it. That cutover happens once
Settings (`bootstrap/settings.py`), the real `RuntimeConfigurationHandle`
(`configuration/`, Phase 2), and `SystemStore`/`AuditSink` (Phase 3) exist to hand it.

## The four-pass activation sequence

```
construct_all()  →  publish_native_capabilities()  →  publish_adapter_capabilities()  →  resolve_all()
     step 8              step 9                            step 10                        step 11
```

A module is constructed before any cross-module capability exists, and resolves its
ports only after **every** publication — including the bootstrap-constructed adapters,
which cannot exist until the modules they wrap have already published. This is what
makes activation order independent of capability dependency order.

## The epoch swap

Per-module `commit_reconfigure()` calls are not enough on their own — they happen in
sequence, so a request admitted mid-sequence could see module A on the new release and
module B on the old one. `ReconfigurationCoordinator.reconfigure()` only calls
`EpochPointer.swap()` once, after **every** module has committed, so adoption becomes
visible atomically in one write. A request that captures its epoch at admission and
keeps it for its whole life never observes a release change mid-flight; old-epoch
resources are released only once `EpochLeaseTracker.is_drained()` is true.

## What Phase 2+ adds

`settings.py` (canonical `BootstrapSettings`), `reconciler.py`
(`ConfigurationReconciler` — watches the active configuration pointer and drives
`ReconfigurationCoordinator` when a release activates), and `routers.py` (mounts each
active module's router). `lifespan.py` is extended to call into these once they exist,
rather than being rewritten.
