# platform/system_store

Application-owned structures create themselves safely at startup (design doc §13.6,
§13.7; implementation plan Phase 3; hardened in Slice 3R). `dynamic_knowledge/internal_store/`
remains as-is — its `InternalStoreBootstrapper` and SQL/Neo4j adapters are proven
typed-object bootstrap for those connectors, kept as a reference pattern rather than
migrated further. This package is the canonical-provider (MongoDB) implementation,
driven entirely by `configuration.domain.system_store.SystemStoreConfig.structures`
rather than hardcoded names.

## Logical naming is mandatory

Business code resolves `system_store.collection("ai_interceptions")` for an
unencrypted structure, or `system_store.read_only(...)` / `insert_one(...)` for an
encrypted one — never `db["platform_ai_interceptions"]` directly.
`repository.py::SystemStore` is the only sanctioned path: it resolves a logical name
against the manifest's `structures` block and raises `UnknownStructure` for anything
not declared there. Renaming a physical collection is a manifest change, not a source
change.

## Startup algorithm

```
compute manifest_fingerprint
try:
    acquire FENCED lease (token T) + start heartbeat
    mark bootstrap-state RUNNING (fenced)
    for each configured structure:
        persistent fence check                              <- guarded on token T
        inspect
        if missing: create                                  <- idempotent
        in-memory heartbeat-health check
        create required indexes                              <- idempotent
        persistent fence check again
        re-inspect actual structure/index -> physical_identity
        index drift: FAIL or WARN per configured policy, never auto-repair
        apply pending forward migration                      <- guarded on token T
        record schema version                                <- guarded on token T
    mark bootstrap-state COMPLETE (fenced)
    release lease (always, via finally)
except LeaseUnavailable:
    wait: bounded backoff + jitter, inspect durable bootstrap-state for the exact
    manifest_fingerprint; COMPLETE -> re-validate structures and return without
    migrating; owner lease expired -> retry (may become the new owner); deadline
    elapsed -> SystemStoreBootstrapTimeout
```

`bootstrap.py::SystemStoreBootstrapper.bootstrap()` runs this. It never drops a valid
existing structure. When `auto_bootstrap_missing_structures` (the manifest flag,
threaded through as `auto_bootstrap_missing`) is `False`, a missing structure is a hard
failure (`MissingSystemStoreStructure`) rather than something this bootstrapper creates
on its own.

## Multi-replica contention (Slice 3R.8)

`COMPLETE` means the *entire* manifest finished — every structure, its indexes, and its
migrations — never inferred from partial schema-version progress; a waiter that only
checked schema-version records could mistake an owner's in-progress bootstrap for done
partway through. The durable state lives in `platform_bootstrap_state`,
`_id`-keyed by `compute_manifest_fingerprint(structures)` (a hash of every structure's
logical/physical name, schema version, and declared shape) — a manifest change gets its
own bootstrap run rather than incorrectly reusing a `COMPLETE` record left by a
different set of structures.

The loser of the lease race is never left to fail: it polls the durable state with
bounded exponential backoff and jitter (no tight polling, no thundering herd), and
either observes `COMPLETE` and returns without migrating anything itself, retries
acquisition once the owner's lease expires (crash/takeover — the new owner mints a new
`fencing_token`, so the stale former owner can never authoritatively finalize
`COMPLETE`), or times out with a typed `SystemStoreBootstrapTimeout` if the deadline
elapses first.

**Enforced by** `tests/platform/test_system_store_bootstrap.py`: two contenders racing
(exactly one becomes owner), a waiter succeeding after the winner completes, an
owner-crash takeover where the stale owner's own `mark_complete` attempt is rejected,
and a waiter timing out when the owner never finishes.

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

## Every guarded write is a store-level conditional *mutation*, not a read (Slice 3R.1)

`mongo.py::FencedMongoTransactionGuard.assert_and_lock()` re-verifies the lease **as a
conditional `find_one_and_update`** on `(lock_name, lease_id, fencing_token)` — a write,
not a read whose result is merely trusted — inside the same MongoDB transaction that
performs the protected write. Mongo's write-conflict detection protects the transaction
precisely because the fence check is itself a write. If the fence predicate doesn't
match, the guard raises `LeaseLost` and the transaction rolls back; the protected write
never lands.

A genuine Mongo transient-transaction error (`errorLabels: ["TransientTransactionError"]`
— confirmed empirically to arise when two concurrent transactions race to
`find_one_and_update` the same lock document) retries the *entire* transaction,
including the fence predicate, with bounded exponential backoff and jitter
(`locking.py::bounded_retry_with_jitter`, shared with the Slice 3R.8 bootstrap waiter). A
real `LeaseLost` is never retried — it fails closed on the first attempt. Confirmed
empirically, not assumed: a single *non*-transactional write (e.g. a heartbeat) to a
document held by an open transaction *blocks* behind that transaction's document lock
rather than producing a conflict; only transaction-vs-transaction contention produces the
`TransientTransactionError` this retry path exists for.

For DDL-like operations that cannot join a transaction (`createCollection`,
`createIndex`), `bootstrap.py` calls `FencedMongoTransactionGuard.verify_fence()` — the
same conditional mutation, without a bundled write — both immediately before and
immediately after the operation. If the post-operation check fails, the caller must not
record a schema version or mark anything complete; that's what makes a stale holder's
DDL non-authoritative even though the DDL itself couldn't be fenced directly.

`migrations.py::MongoVersionLedger.record_version()` and the bootstrap-state transitions
in `mongo.py::MongoBootstrapStateStore` all route through the same guard.

**Enforced by** `tests/platform/test_fenced_writes_reject_stale_token.py`: rejection of a
stale lease's write, a genuine `LeaseLost` never being retried, and the transient-vs-fatal
retry classification proven by driving the real `pymongo.errors.OperationFailure`/
`TransientTransactionError` label mechanism directly (reproducing the live two-transaction
race turned out to be highly timing-sensitive — see the test module's docstring for what
was tried and confirmed).

## Migrations are strict forward-only and must be idempotent (Slice 3R.3)

`migrations.py::MigrationPathValidator` is the single owner of every forward-only
invariant: `current > target` is a hard failure (`MigrationDowngradeUnsupported`) —
never a silent no-op; `current == target` is a no-op; `current < target` requires an
*exact*, contiguous path with no gaps and no duplicate `target_version`s
(`MigrationPathInvalid` otherwise). An earlier cut of `MigrationRunner.apply_pending`
applied every migration with `target_version > current` unconditionally, which silently
accepted a path with a gap (e.g. current=1, target=4, available=[2, 4], applying just
those two and reporting success despite skipping 3) — the validator exists specifically
to make that class of defect structurally impossible. After applying, the runner
verifies the ledger's recorded version actually equals the target — a defensive check
against the runner or ledger silently under-applying.

The runner still cannot distinguish "a migration never ran" from "it ran but the process
crashed before `record_version()` committed" — both look identical on the next attempt —
so a re-run always calls `apply()` again. Every `Migration.apply()` must therefore be
either transaction-wrapped or independently idempotent.

**Enforced by** `tests/platform/test_migration_idempotence.py`: downgrade rejection,
gap/duplicate rejection, ascending application order, a crash-before-recording scenario
proving safe re-application, and a defensive final-version-mismatch check.

## Schema-version identity follows the physical structure, not just its name (Slice 3R.4)

`migrations.py::VersionLedger` and `mongo.py::MongoVersionLedger` bind a recorded
version to `contracts.py::StructureIdentity` — `logical_name` + `physical_name` +
`physical_identity` (MongoDB's collection UUID, from `listCollections`' `info.uuid`) +
`structure_fingerprint` (a hash of the declared shape) — not `logical_name` alone.
Logical name alone is insufficient because a manifest can repoint a logical name at a
different physical collection; logical + physical name alone is *also* insufficient
because a collection can be dropped and recreated under the identical name, getting a
new UUID. If the stored `physical_name`/`physical_identity` don't match the structure
being inspected, `current_version()` reports `0` — the replacement is treated as a fresh
structure, never as "already migrated."

**Enforced by** `tests/platform/test_structure_physical_identity.py`, against a real
MongoDB replica set (physical identity is genuine server-reported metadata, not
something a mock session can fake).

## Canonical index drift detection (Slice 3R.5)

`contracts.py::IndexDefinition` compares the full canonical shape a declared index can
express today — ordered key/direction pairs, `unique`, `partial_filter_expression` — not
just the index name. `MongoSystemStoreAdapter.ensure_indexes()` fetches the full
observed index spec (previously only its name was read) and compares it via
`IndexDefinition.matches()`. A missing index is created; an exact match is silently
reused; a same-name-but-different-definition index is reported as
`contracts.py::IndexDriftReport` and `bootstrap.py` raises `IndexDriftDetected` when
`fail_closed_on_drift` is set — never auto-repaired. TTL/sparse/collation are not
compared because `StructureDefinition.indexes` doesn't declare them yet; adding that
support means extending the typed model first, not silently comparing backend-only
defaults.

**Enforced by** `tests/platform/test_index_drift.py`, against a real MongoDB replica set.

## Encrypted structures: a restricted read facade, not a raw handle (Slice 3R.6)

A structure declared `encrypted: true` in the manifest can exist starting Phase 9, when
a real KMS-backed `platform.secrets.envelope.EnvelopeEncryptor` lands. `SystemStore`
already enforces the boundary: `collection()` raises
`EncryptedStructureRequiresGuardedAccess` for an encrypted structure rather than handing
out a raw collection — renaming the method to `read_only()` while still returning the
raw PyMongo object would not have been sufficient, since the raw object still exposes
every mutation method to any caller that bypasses static typing.  `read_only()` instead
returns `repository.py::_ReadOnlyCollectionView`, a genuine wrapper implementing only
`find_one`/`find`/`count_documents`/`aggregate` — `insert_one` and friends do not exist
on it at all; calling one raises `AttributeError`, not a policy violation caught later.

`encryption.py::EncryptionGuard.check_document()` validates the envelope's *shape*, not
merely that an `_envelope` key is present: `ciphertext`, `key_ref`, `algorithm`, and
`version` must all be present under the envelope (matching
`platform.secrets.envelope.EnvelopePayload`'s fields), and any top-level document field
that isn't the envelope, `_id`, or an explicitly declared metadata field
(`insert_one(..., allowed_metadata_fields=...)`) is rejected —
`{"_envelope": {...well-formed...}, "password": "plaintext"}` does not pass just because
the envelope itself is well-formed.

**Enforced by** `tests/platform/test_system_store_repository.py`: the raw-handle refusal,
the read-only wrapper's missing mutation methods, malformed-envelope rejection, and
unauthorized-plaintext-field rejection alongside a valid envelope.

## What's not here yet

Nothing wires this into `main.py`'s actual startup yet — like Phase 1B's zero-module
proof, the mechanism is built and gate-verified standalone, ready for the first real
consumer (the manifest already declares `conversations`, `ai_traces`,
`ai_interceptions`, `graph_schema_drafts`, `graph_generations`, and `audit`, none of
which are read or written by business code yet).
