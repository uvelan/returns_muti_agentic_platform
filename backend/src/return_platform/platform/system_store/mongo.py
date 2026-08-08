"""MongoDB implementations: the canonical `SystemStoreAdapter`, the fenced `LeaseStore`,
and a transaction-guarded writer for migration and version-ledger writes.

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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, cast

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from return_platform.platform.system_store.contracts import (
    CompatibilityStatus,
    StructureDefinition,
    StructureInspection,
)
from return_platform.platform.system_store.locking import FencedLease, LeaseLost, LeaseUnavailable

_T = TypeVar("_T")


def _now() -> datetime:
    return datetime.now(UTC)


class MongoLeaseStore:
    """`LeaseStore` backed by a single `bootstrap_locks` collection, `_id`-keyed by
    lock name, with the fencing token minted from a `bootstrap_lock_tokens` counter."""

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


class MongoStructureGateway(Protocol):
    async def collection_exists(self, name: str) -> bool: ...
    async def create_collection(self, name: str) -> None: ...
    async def index_exists(self, collection: str, index_name: str) -> bool: ...
    async def create_index(
        self, collection: str, *, index_name: str, fields: tuple[str, ...], unique: bool
    ) -> None: ...


class PymongoStructureGateway:
    """`MongoStructureGateway` backed by a real `AsyncDatabase`."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._db = client.get_database(database)

    async def collection_exists(self, name: str) -> bool:
        names = await self._db.list_collection_names(filter={"name": name})
        return name in names

    async def create_collection(self, name: str) -> None:
        await self._db.create_collection(name)

    async def index_exists(self, collection: str, index_name: str) -> bool:
        cursor = await self._db.get_collection(collection).list_indexes()
        async for index in cursor:
            if index.get("name") == index_name:
                return True
        return False

    async def create_index(
        self, collection: str, *, index_name: str, fields: tuple[str, ...], unique: bool
    ) -> None:
        await self._db.get_collection(collection).create_index(
            [(field, 1) for field in fields],
            name=index_name,
            unique=unique,
        )


class MongoSystemStoreAdapter:
    """`SystemStoreAdapter` for the canonical MongoDB provider. Mongo collections are
    schemaless, so inspection only concerns existence -- there is no field-level
    compatibility check the way `dynamic_knowledge.internal_store`'s SQL/Neo4j adapters
    need for typed objects."""

    def __init__(self, gateway: MongoStructureGateway) -> None:
        self._gateway = gateway

    async def inspect_structure(self, definition: StructureDefinition) -> StructureInspection:
        exists = await self._gateway.collection_exists(definition.physical_name)
        return StructureInspection(
            logical_name=definition.logical_name,
            status=CompatibilityStatus.PRESENT if exists else CompatibilityStatus.MISSING,
        )

    async def create_structure(self, definition: StructureDefinition) -> None:
        await self._gateway.create_collection(definition.physical_name)

    async def ensure_indexes(self, definition: StructureDefinition) -> tuple[str, ...]:
        created: list[str] = []
        for index in definition.indexes:
            name = str(index["name"])
            if await self._gateway.index_exists(definition.physical_name, name):
                continue
            await self._gateway.create_index(
                definition.physical_name,
                index_name=name,
                fields=tuple(str(item) for item in index["fields"]),
                unique=bool(index.get("unique", False)),
            )
            created.append(name)
        return tuple(created)


class FencedMongoWriter:
    """Guards a write against a `FencedLease` by re-verifying the lease inside the same
    MongoDB transaction that performs the write -- so a paused-then-resumed stale holder
    (fencing_token superseded while it slept) is rejected at the store, atomically with
    the write it would otherwise have made, not merely by an earlier in-memory check."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._client = client
        self._locks = client.get_database(database).get_collection("platform_bootstrap_locks")

    async def guarded_write(
        self,
        lease: FencedLease,
        write_fn: Callable[[AsyncClientSession], Awaitable[_T]],
    ) -> _T:
        async with self._client.start_session() as session:
            async with await session.start_transaction():
                current = await self._locks.find_one({"_id": lease.lock_name}, session=session)
                if (
                    current is None
                    or current.get("lease_id") != lease.lease_id
                    or current.get("fencing_token") != lease.fencing_token
                ):
                    raise LeaseLost(
                        f"Lease '{lease.lease_id}' for lock '{lease.lock_name}' was superseded "
                        f"before the guarded write could run"
                    )
                return await write_fn(session)


class MongoVersionLedger:
    """`VersionLedger` backed by `platform_schema_versions`, `_id`-keyed by logical
    structure name. Writes go through `FencedMongoWriter` so a stale holder's version
    write is rejected atomically, in the same transaction as the fencing check."""

    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], *, database: str = "platform"
    ) -> None:
        self._versions = client.get_database(database).get_collection("platform_schema_versions")
        self._writer = FencedMongoWriter(client, database=database)

    async def current_version(self, logical_name: str) -> int:
        doc = await self._versions.find_one({"_id": logical_name})
        return int(cast(int, doc["applied_version"])) if doc else 0

    async def record_version(self, logical_name: str, version: int, lease: FencedLease) -> None:
        async def _write(session: AsyncClientSession) -> None:
            await self._versions.update_one(
                {"_id": logical_name},
                {
                    "$set": {
                        "logical_name": logical_name,
                        "applied_version": version,
                        "applied_at": _now(),
                    }
                },
                upsert=True,
                session=session,
            )

        await self._writer.guarded_write(lease, _write)
