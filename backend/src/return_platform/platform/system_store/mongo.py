"""MongoDB implementations: the canonical `SystemStoreAdapter`, the fenced `LeaseStore`,
and `FencedMongoTransactionGuard` -- a single reusable primitive that fences every
protected write against a `FencedLease` with a conditional *mutation* inside the same
transaction that performs the write, not a read whose result is trusted.

Lock acquisition is a single atomic `find_one_and_update` with `upsert=True` filtered on
`expires_at < now`: if no unexpired lock document exists, the operation either updates the
expired one or inserts a fresh one (both are our own write, by MongoDB's per-document
atomicity); if an unexpired lock exists, the filter does not match and the upsert's insert
path collides on the `_id` unique index, raising `DuplicateKeyError` -- mapped to
`LeaseUnavailable`. There is no window between "check" and "acquire": either path is one
atomic operation.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar, cast

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError, PyMongoError

from return_platform.platform.system_store.contracts import (
    BootstrapState,
    BootstrapStatus,
    CompatibilityStatus,
    IndexDefinition,
    IndexDriftReport,
    IndexEnsureResult,
    StructureDefinition,
    StructureIdentity,
    StructureInspection,
)
from return_platform.platform.system_store.locking import (
    FencedLease,
    LeaseLost,
    LeaseUnavailable,
    bounded_retry_with_jitter,
)

_T = TypeVar("_T")


def _now() -> datetime:
    return datetime.now(UTC)


def _is_mongo_transient_transaction_error(exc: BaseException) -> bool:
    """MongoDB's documented signal that a whole transaction should be retried from the
    top (re-running the fence predicate), not treated as a fatal fencing failure -- e.g.
    a legitimate heartbeat write racing the same lock document mid-transaction."""
    return isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError")


class StructurePhysicalIdentityUnavailable(RuntimeError):
    """A collection exists but the server did not report a discoverable physical
    identity (collection UUID). Should not happen on MongoDB 3.6+; fails closed rather
    than silently trusting an unidentifiable physical structure (Slice 3R.4)."""


class MongoLeaseStore:
    """`LeaseStore` backed by a single `platform_bootstrap_locks` collection, `_id`-keyed
    by lock name, with the fencing token minted from a `platform_fencing_tokens`
    counter."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._db = client.get_database(database)
        self._locks = self._db.get_collection("platform_bootstrap_locks")
        self._tokens = self._db.get_collection("platform_fencing_tokens")

    async def acquire(
        self, lock_name: str, *, owner_instance_id: str, ttl_seconds: float
    ) -> FencedLease:
        token_doc = await self._tokens.find_one_and_update(
            {"_id": lock_name},
            {"$inc": {"next_token": 1}, "$set": {"scope": lock_name}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert token_doc is not None  # upsert + return_document=AFTER always returns a document
        fencing_token = int(cast(int, token_doc["next_token"]))
        lease_id = str(uuid.uuid4())
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            await self._locks.find_one_and_update(
                {"_id": lock_name, "expires_at": {"$lt": now}},
                {
                    "$set": {
                        "lock_name": lock_name,
                        "lease_id": lease_id,
                        "owner_instance_id": owner_instance_id,
                        "fencing_token": fencing_token,
                        "acquired_at": now,
                        "heartbeat_at": now,
                        "expires_at": expires_at,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise LeaseUnavailable(
                f"Lock '{lock_name}' is held by another instance and has not expired"
            ) from exc
        return FencedLease(
            lock_name=lock_name,
            lease_id=lease_id,
            owner_instance_id=owner_instance_id,
            fencing_token=fencing_token,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=expires_at,
        )

    async def heartbeat(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease:
        now = _now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        result = await self._locks.find_one_and_update(
            {
                "_id": lease.lock_name,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            },
            {"$set": {"heartbeat_at": now, "expires_at": expires_at}},
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise LeaseLost(
                f"Lease '{lease.lease_id}' for lock '{lease.lock_name}' is no longer current "
                f"(expired or fencing_token superseded)"
            )
        return FencedLease(
            lock_name=lease.lock_name,
            lease_id=lease.lease_id,
            owner_instance_id=lease.owner_instance_id,
            fencing_token=lease.fencing_token,
            acquired_at=lease.acquired_at,
            heartbeat_at=now,
            expires_at=expires_at,
        )

    async def release(self, lease: FencedLease) -> None:
        # Best-effort and idempotent: if the lock already moved on (expired and
        # reclaimed, or fencing_token superseded), there is nothing of ours left to
        # release, and that is not an error.
        await self._locks.delete_one(
            {
                "_id": lease.lock_name,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            }
        )

    async def read(self, lock_name: str) -> Mapping[str, Any] | None:
        """Read-only peek at a lock's current state (owner, heartbeat, expiry) -- used
        by a bootstrap waiter to decide whether to keep waiting or attempt takeover.
        Never used to prove fencing; only `FencedMongoTransactionGuard` and the
        conditional writes above do that."""
        return await self._locks.find_one({"_id": lock_name})


class MongoStructureGateway(Protocol):
    async def describe_collection(self, name: str) -> Mapping[str, Any] | None: ...
    async def create_collection(self, name: str) -> None: ...
    async def get_index(self, collection: str, index_name: str) -> Mapping[str, Any] | None: ...
    async def create_index(
        self,
        collection: str,
        *,
        index_name: str,
        fields: tuple[str, ...],
        unique: bool,
        partial_filter_expression: Mapping[str, Any] | None = None,
        expire_after_seconds: int | None = None,
    ) -> None: ...


class PymongoStructureGateway:
    """`MongoStructureGateway` backed by a real `AsyncDatabase`."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._db = client.get_database(database)

    async def describe_collection(self, name: str) -> Mapping[str, Any] | None:
        cursor = await self._db.list_collections(filter={"name": name})
        async for doc in cursor:
            return doc
        return None

    async def create_collection(self, name: str) -> None:
        await self._db.create_collection(name)

    async def get_index(self, collection: str, index_name: str) -> Mapping[str, Any] | None:
        cursor = await self._db.get_collection(collection).list_indexes()
        async for index in cursor:
            if index.get("name") == index_name:
                return index
        return None

    async def create_index(
        self,
        collection: str,
        *,
        index_name: str,
        fields: tuple[str, ...],
        unique: bool,
        partial_filter_expression: Mapping[str, Any] | None = None,
        expire_after_seconds: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"name": index_name, "unique": unique}
        if partial_filter_expression:
            kwargs["partialFilterExpression"] = dict(partial_filter_expression)
        if expire_after_seconds is not None:
            kwargs["expireAfterSeconds"] = expire_after_seconds
        await self._db.get_collection(collection).create_index(
            [(field, 1) for field in fields], **kwargs
        )


class MongoSystemStoreAdapter:
    """`SystemStoreAdapter` for the canonical MongoDB provider. Mongo collections are
    schemaless, so inspection only concerns existence (plus physical identity for
    Slice 3R.4) -- there is no field-level compatibility check the way
    `dynamic_knowledge.internal_store`'s SQL/Neo4j adapters need for typed objects."""

    def __init__(self, gateway: MongoStructureGateway) -> None:
        self._gateway = gateway

    async def inspect_structure(self, definition: StructureDefinition) -> StructureInspection:
        described = await self._gateway.describe_collection(definition.physical_name)
        if described is None:
            return StructureInspection(
                logical_name=definition.logical_name, status=CompatibilityStatus.MISSING
            )
        return StructureInspection(
            logical_name=definition.logical_name,
            status=CompatibilityStatus.PRESENT,
            physical_identity=self._extract_identity(definition.physical_name, described),
        )

    @staticmethod
    def _extract_identity(physical_name: str, described: Mapping[str, Any]) -> str:
        info = described.get("info") or {}
        raw_uuid = info.get("uuid")
        if raw_uuid is None:
            raise StructurePhysicalIdentityUnavailable(
                f"Collection '{physical_name}' exists but the server did not report a "
                f"collection UUID; cannot verify physical identity"
            )
        return bytes(raw_uuid).hex()

    async def create_structure(self, definition: StructureDefinition) -> None:
        await self._gateway.create_collection(definition.physical_name)

    async def ensure_indexes(self, definition: StructureDefinition) -> IndexEnsureResult:
        created: list[str] = []
        drifted: list[IndexDriftReport] = []
        for index_spec in definition.indexes:
            declared = IndexDefinition.from_declared(index_spec)
            observed_raw = await self._gateway.get_index(definition.physical_name, declared.name)
            if observed_raw is None:
                await self._gateway.create_index(
                    definition.physical_name,
                    index_name=declared.name,
                    fields=tuple(field for field, _ in declared.keys),
                    unique=declared.unique,
                    partial_filter_expression=declared.partial_filter_expression,
                    expire_after_seconds=declared.expire_after_seconds,
                )
                created.append(declared.name)
                continue
            observed = IndexDefinition.from_observed(observed_raw)
            if not declared.matches(observed):
                drifted.append(
                    IndexDriftReport(logical_name=definition.logical_name, index_name=declared.name)
                )
        return IndexEnsureResult(created=tuple(created), drifted=tuple(drifted))


class FencedMongoTransactionGuard:
    """Fences a write against a `FencedLease` with a conditional *mutation* --
    `find_one_and_update` on `(lock_name, lease_id, fencing_token)` -- inside the same
    MongoDB transaction that performs the protected write. Mongo's write-conflict
    detection protects the transaction because the fence itself is a write, not a read
    whose result the caller merely trusts.

    A genuine Mongo transient-transaction error (e.g. the lease's own heartbeat writing
    the same lock document concurrently) retries the *entire* transaction -- including
    re-running the fence predicate -- with bounded exponential backoff and jitter.
    `LeaseLost` (the fence predicate genuinely not matching) is never retried; it fails
    closed immediately.
    """

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        *,
        database: str = "platform",
        max_attempts: int = 5,
        base_delay_seconds: float = 0.05,
        max_delay_seconds: float = 1.0,
    ) -> None:
        self._client = client
        self._locks = client.get_database(database).get_collection("platform_bootstrap_locks")
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds

    @staticmethod
    def _fence_filter(lease: FencedLease) -> Mapping[str, Any]:
        return {
            "_id": lease.lock_name,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
        }

    @staticmethod
    def _fence_mutation() -> Mapping[str, Any]:
        return {
            "$inc": {"transaction_guard_revision": 1},
            "$set": {"last_confirmed_at": _now()},
        }

    async def assert_and_lock(
        self,
        lease: FencedLease,
        write_fn: Callable[[AsyncClientSession], Awaitable[_T]],
    ) -> _T:
        async def _attempt() -> _T:
            async with self._client.start_session() as session:
                async with await session.start_transaction():
                    result = await self._locks.find_one_and_update(
                        self._fence_filter(lease), self._fence_mutation(), session=session
                    )
                    if result is None:
                        raise LeaseLost(
                            f"Lease '{lease.lease_id}' for lock '{lease.lock_name}' was "
                            f"superseded before the guarded write could run"
                        )
                    return await write_fn(session)

        return await bounded_retry_with_jitter(
            _attempt,
            max_attempts=self._max_attempts,
            base_delay_seconds=self._base_delay_seconds,
            max_delay_seconds=self._max_delay_seconds,
            is_retryable=_is_mongo_transient_transaction_error,
        )

    async def verify_fence(self, lease: FencedLease) -> None:
        """Single conditional-mutation fence check, without a bundled protected write --
        for non-transactional DDL-like operations (Slice 3R.2) that cannot join a
        transaction the way an ordinary document write can. Called before and after the
        operation: the "after" check is what proves a stale holder never gets to have its
        DDL treated as authoritative -- if it fails, the caller must not record a schema
        version or mark anything complete."""
        result = await self._locks.find_one_and_update(
            self._fence_filter(lease), self._fence_mutation()
        )
        if result is None:
            raise LeaseLost(
                f"Lease '{lease.lease_id}' for lock '{lease.lock_name}' is no longer current"
            )


class MongoVersionLedger:
    """`VersionLedger` backed by `platform_schema_versions`, `_id`-keyed by logical
    structure name, with the recorded version bound to `StructureIdentity` (Slice 3R.4):
    a stored `physical_name`/`physical_identity` mismatch means the physical structure
    was replaced, and the recorded version can never be inherited by the replacement.
    Writes go through `FencedMongoTransactionGuard` so a stale holder's version write is
    rejected atomically, in the same transaction as the fencing check."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._versions = client.get_database(database).get_collection("platform_schema_versions")
        self._guard = FencedMongoTransactionGuard(client, database=database)

    async def current_version(self, identity: StructureIdentity) -> int:
        doc = await self._versions.find_one({"_id": identity.logical_name})
        if doc is None:
            return 0
        if (
            doc.get("physical_name") != identity.physical_name
            or doc.get("physical_identity") != identity.physical_identity
        ):
            # Physical replacement detected (rename, or drop+recreate under the same
            # name) -- the recorded version belongs to a different physical object.
            return 0
        return int(cast(int, doc["applied_version"]))

    async def record_version(
        self, identity: StructureIdentity, version: int, lease: FencedLease
    ) -> None:
        async def _write(session: AsyncClientSession) -> None:
            await self._versions.update_one(
                {"_id": identity.logical_name},
                {
                    "$set": {
                        "logical_name": identity.logical_name,
                        "physical_name": identity.physical_name,
                        "physical_identity": identity.physical_identity,
                        "structure_fingerprint": identity.structure_fingerprint,
                        "applied_version": version,
                        "applied_at": _now(),
                    }
                },
                upsert=True,
                session=session,
            )

        await self._guard.assert_and_lock(lease, _write)


class MongoBootstrapStateStore:
    """Durable, fenced whole-manifest bootstrap state (Slice 3R.8), `_id`-keyed by
    `manifest_fingerprint`, in `platform_bootstrap_state`. Every transition after the
    initial RUNNING write goes through `FencedMongoTransactionGuard`, so a stale owner
    (fencing_token superseded while it slept) can never authoritatively finalize
    COMPLETE or FAILED -- `assert_and_lock` rejects the write atomically."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._state = client.get_database(database).get_collection("platform_bootstrap_state")
        self._guard = FencedMongoTransactionGuard(client, database=database)

    async def read(self, manifest_fingerprint: str) -> BootstrapState | None:
        doc = await self._state.find_one({"_id": manifest_fingerprint})
        if doc is None:
            return None
        return BootstrapState.model_validate({k: v for k, v in doc.items() if k != "_id"})

    async def mark_running(self, manifest_fingerprint: str, lease: FencedLease) -> None:
        async def _write(session: AsyncClientSession) -> None:
            await self._state.update_one(
                {"_id": manifest_fingerprint},
                {
                    "$set": {
                        "manifest_fingerprint": manifest_fingerprint,
                        "status": BootstrapStatus.RUNNING.value,
                        "owner_instance_id": lease.owner_instance_id,
                        "lease_id": lease.lease_id,
                        "fencing_token": lease.fencing_token,
                        "started_at": _now(),
                    },
                    "$unset": {"completed_at": "", "failure_code": "", "failure_at": ""},
                },
                upsert=True,
                session=session,
            )

        await self._guard.assert_and_lock(lease, _write)

    async def mark_complete(self, manifest_fingerprint: str, lease: FencedLease) -> None:
        async def _write(session: AsyncClientSession) -> None:
            result = await self._state.update_one(
                {
                    "_id": manifest_fingerprint,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                },
                {"$set": {"status": BootstrapStatus.COMPLETE.value, "completed_at": _now()}},
                session=session,
            )
            if result.matched_count == 0:
                raise LeaseLost(
                    f"Bootstrap state for '{manifest_fingerprint}' was superseded before "
                    f"COMPLETE could be recorded"
                )

        await self._guard.assert_and_lock(lease, _write)

    async def mark_failed(
        self, manifest_fingerprint: str, lease: FencedLease, failure_code: str
    ) -> None:
        async def _write(session: AsyncClientSession) -> None:
            result = await self._state.update_one(
                {
                    "_id": manifest_fingerprint,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                },
                {
                    "$set": {
                        "status": BootstrapStatus.FAILED.value,
                        "failure_code": failure_code,
                        "failure_at": _now(),
                    }
                },
                session=session,
            )
            if result.matched_count == 0:
                raise LeaseLost(
                    f"Bootstrap state for '{manifest_fingerprint}' was superseded before "
                    f"FAILED could be recorded"
                )

        await self._guard.assert_and_lock(lease, _write)
