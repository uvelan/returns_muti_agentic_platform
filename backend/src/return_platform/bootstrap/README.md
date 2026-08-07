# bootstrap

Startup composition. Knows every module's `module.py` and nothing else about them.

## What's here now (Phase 1B)

The module lifecycle mechanism, buildable and testable before any real module exists:

- `epoch.py` — the epoch-keyed two-phase reconfiguration mechanism (design doc
  section 13.2): `EpochAllocator` mints replica-local epoch numbers, `EpochAdmission`
  owns the current-epoch pointer, the `CURRENT`/`DRAINING`/`RELEASED` state machine,
  and the holder counts all behind one lock, and `ReconfigurationCoordinator` runs
  prepare-all → commit-all-with-one-swap, or abort-all (every module, not just the
  ones that already prepared) if any module refuses or raises. A `commit_reconfigure`
  failure raises `FatalReconfigurationError` and marks the replica `UNAVAILABLE` --
  every later `acquire_current()`/`reconfigure()` call is refused from then on.
- `context.py` — assembles whatever the caller provides into something that
  structurally satisfies `ModuleRuntimeContext`. Only wires the fields backed by an
  existing contract (`configuration`, `capabilities`, `clock`, `correlation`); does
  not know how to construct a `SystemStore` or an `AuditSink`, because those don't
  exist yet.
- `capabilities.py` — the native-publication (step 9) and resolution (step 11) passes
  over a module list.
- `activation.py` — construction (step 8) plus `activate()`, which computes
  dependency order via `platform.modules.lifecycle.topological_order()` before
  constructing, so the resulting module list is already in the order
  `initialize_all()`/`shutdown_all()` need.
- `lifespan.py` — `module_lifespan()`: activation, then dependency-ordered
  `initialize()` (unwinding already-initialized modules in reverse if one fails), then
  a yield to serve traffic, then reverse-ordered `shutdown()` no matter how serving
  ends.
- `health.py` — module health collection and worst-of rollup.
- `errors.py` — startup failure classification (`FATAL` always stops; `DEGRADABLE`
  stops only in production).
- `adapters/` — populated starting in Phase 9; see its own README.

**`main.py` calls this now, with zero business modules.** `create_app`'s existing
`lifespan()` constructs an empty `ModuleRegistry`, runs it through `module_lifespan()`,
and wraps the pre-existing `yield` with it — proving the full construct → publish →
resolve → initialize → shutdown sequence executes end to end with zero effect on
existing behavior. The `_NoModulesYetConfigurationHandle` placeholder in `main.py`
satisfies `RuntimeContext`'s required `configuration` field and is never actually
called (there are no modules to call it); it is deleted once Phase 2's real
`ConfigurationHandle` exists to take its place. `SystemStore`/`AuditSink` (Phase 3)
extend `ModuleRuntimeContext` and this assembly the same way.

## The four-pass activation sequence

```
construct_all()  →  publish_native_capabilities()  →  publish_adapter_capabilities()  →  resolve_all()
     step 8              step 9                            step 10                        step 11
```

A module is constructed before any cross-module capability exists, and resolves its
ports only after **every** publication — including the bootstrap-constructed adapters,
which cannot exist until the modules they wrap have already published. This is what
makes activation order independent of capability dependency order.

`construct_all()` receives module_ids already ordered by
`topological_order(descriptors)` — dependency order, not caller order — so a module
whose `initialize()` uses a resolved capability from another module is guaranteed that
module already finished initializing.

## The epoch swap and admission

Per-module `commit_reconfigure()` calls are not enough on their own — they happen in
sequence, so a request admitted mid-sequence could see module A on the new release and
module B on the old one. `ReconfigurationCoordinator.reconfigure()` only swaps the
current epoch once, after **every** module has committed, so adoption becomes visible
atomically in one write.

Capture and lease acquisition are also one atomic operation, not two: `EpochAdmission`
is the single object owning the pointer and the holder counts, behind one lock, with
`acquire_current()` as the only admission entry point. A separate pointer object and
lease-tracker object would let a request read the current epoch, and before it
registers as a holder, let a concurrent reconfiguration swap past it and release that
epoch's resources out from under it -- see `tests/platform/test_epoch_admission.py`,
which includes a real multi-threaded stress test proving the lock prevents lost holder
counts under genuine OS-thread concurrency (e.g. sync route handlers in a thread
pool), not just asyncio-task interleaving.

Old-epoch resources are released only once every holder has released
(`EpochAdmission.try_release()`), and only once the epoch has actually moved to
`DRAINING` -- the currently-serving `CURRENT` epoch can never be released, by
construction.

## What Phase 2+ adds

`settings.py` (canonical `BootstrapSettings`), `reconciler.py`
(`ConfigurationReconciler` — watches the active configuration pointer and drives
`ReconfigurationCoordinator` when a release activates), and `routers.py` (mounts each
active module's router). `lifespan.py` is extended to call into these once they exist,
rather than being rewritten. The zero-module proof in `main.py` is replaced by real
module registration as each business module lands.
