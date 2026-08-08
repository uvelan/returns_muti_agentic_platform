# platform/system_store

Application-owned structures create themselves safely at startup (design doc §13.6,
§13.7; implementation plan Phase 3). `dynamic_knowledge/internal_store/` remains as-is —
its `InternalStoreBootstrapper` and SQL/Neo4j adapters are proven typed-object bootstrap
for those connectors, kept as a reference pattern rather than migrated further. This
package is the canonical-provider (MongoDB) implementation, driven entirely by
`configuration.domain.system_store.SystemStoreConfig.structures` rather than hardcoded
names.

## Logical naming is mandatory

Business code resolves `system_store.collection("ai_interceptions")` — never
`db["platform_ai_interceptions"]` directly. `repository.py::SystemStore` is the only
sanctioned path: it resolves a logical name against the manifest's `structures` block
and raises `UnknownStructure` for anything not declared there. Renaming a physical
collection is a manifest change, not a source change.

## Startup algorithm

```
acquire FENCED lease (token T) + start heartbeat
for each configured structure:
    inspect
    if missing: create; create required indexes        <- guarded on token T
    if present: reuse
    apply pending forward migration                    <- guarded on token T
    record schema version                               <- guarded on token T
release lease (always, via finally)
```

`bootstrap.py::SystemStoreBootstrapper.bootstrap()` runs this for every declared
structure inside one `FencedLeaseManager` session. It never drops a valid existing
structure. When `auto_bootstrap_missing_structures` (the manifest flag, threaded through
as `auto_bootstrap_missing`) is `False`, a missing structure is a hard failure
(`MissingSystemStoreStructure`) rather than something this bootstrapper creates on its
own — verified by `tests/platform/test_system_store_bootstrap.py`, along with the
Phase 3 gate's live-stack requirement that a second bootstrap against the same Mongo
instance creates nothing and reuses every structure.

## Fenced leasing (design §13.7)

A TTL lock with only an owner ID is not enough: a migration slower than the TTL lets a
second instance acquire the lock and run the same migration concurrently while the first
is still working. `locking.py::FencedLease` carries a monotonically increasing
`fencing_token` (minted from a `platform_fencing_tokens` counter document) alongside its
identity, and `FencedLeaseManager` runs a background heartbeat that renews the lease at a
fraction of its TTL.

**Abort on heartbeat failure.** If renewal ever fails — the lease expired, or another
holder's fencing_token has superseded it — the manager does not retry or try to finish
whatever it was doing. It raises `LeaseLost` at the caller's *next* call to
`ensure_alive()`, which every protected step in `bootstrap.py` and `MigrationRunner`
calls before proceeding. `ensure_alive()` is a fast in-memory check, not a store
round-trip — it catches the common case early, but it is not the correctness boundary by
itself (see below).

`mongo.py::MongoLeaseStore.acquire()` is a single atomic `find_one_and_update` with
`upsert=True`, filtered on `expires_at < now`: if no unexpired lock exists, the operation
either updates the expired document or inserts a fresh one — both are our own write, by
MongoDB's per-document atomicity. If an unexpired lock does exist, the filter doesn't
match and the upsert's insert path collides on the `_id` unique index, raising
`DuplicateKeyError`, mapped to `LeaseUnavailable`. There is no window between "check" and
"acquire."

## Every guarded write is a store-level guard, not just an in-memory check

`mongo.py::FencedMongoWriter.guarded_write()` re-verifies the lease **inside the same
MongoDB transaction** that performs the write: it reads the current `platform_bootstrap_locks`
document for the lock name, and if its `lease_id`/`fencing_token` no longer match the
caller's lease, raises `LeaseLost` and the transaction rolls back — the write never
lands. This is what makes a paused-then-resumed stale holder (a GC pause or scheduling
delay after `ensure_alive()` passed but before the token was superseded) safe: the
in-memory check narrows the common case, but the transaction is what makes staleness
impossible to miss. `migrations.py::MongoVersionLedger.record_version()` uses the same
writer. `pymongo`'s async driver makes `session.start_transaction()` itself a coroutine
(`async with await session.start_transaction():`) — the same gotcha fixed in
`configuration/application/activation.py`; this module was written with that already in
mind.

**Enforced by** `tests/platform/test_fenced_writes_reject_stale_token.py`, run against a
real MongoDB replica set (a hand-rolled session mock cannot exercise real transaction
rollback/isolation — see the concurrent-activation review that found exactly that gap in
`configuration/application/activation.py`'s own tests).

## Migrations are forward-only and must be idempotent

`migrations.py::MigrationRunner.apply_pending()` applies every migration whose
`target_version` is strictly between the recorded version and the requested target, in
ascending order, checking `ensure_alive()` before each one. The runner cannot distinguish
"a migration never ran" from "it ran but the process crashed before `record_version()`
committed" — both look identical on the next attempt (recorded version unchanged) — so a
re-run always calls `apply()` again. Every `Migration.apply()` must therefore be either
transaction-wrapped or independently idempotent; the runner provides ordering and fencing,
not exactly-once execution.

**Enforced by** `tests/platform/test_migration_idempotence.py`, including a
crash-before-recording scenario proving a re-run reapplies without duplicating the
migration's effect.

## Encryption refusal (design §13.6)

A structure declared `encrypted: true` in the manifest can exist starting Phase 9, when a
real KMS-backed `platform.secrets.envelope.EnvelopeEncryptor` lands. Today,
`encryption.py::EncryptionGuard` only enforces the shape: `SystemStore.insert_one()`
refuses (`PlaintextWriteRejected`) any document for an encrypted structure that doesn't
already carry the envelope marker key — callers must encrypt first and pass the
resulting envelope document through. No plaintext write to an encrypted structure can
land, even before a real encryptor exists to make the refusal moot.

## Drift and destructive repair

`fail_closed_on_drift` and destructive-repair avoidance apply to providers whose
structures have field-level shape (the existing SQL/Neo4j `internal_store` adapters).
MongoDB collections are schemaless at this layer — `MongoSystemStoreAdapter.inspect_structure()`
only distinguishes `MISSING` from `PRESENT` — so there is no field-level drift to detect
for the canonical provider today. `SystemStoreAdapter` remains a port specifically so a
schema-validating provider can be added later without touching `SystemStoreBootstrapper`
or `SystemStore`.

## What's not here yet

Nothing wires this into `main.py`'s actual startup yet — like Phase 1B's zero-module
proof, the mechanism is built and tested standalone, ready for the first real consumer
(the manifest already declares `conversations`, `ai_traces`, `ai_interceptions`,
`graph_schema_drafts`, `graph_generations`, and `audit`, none of which are read or written
by business code yet).
