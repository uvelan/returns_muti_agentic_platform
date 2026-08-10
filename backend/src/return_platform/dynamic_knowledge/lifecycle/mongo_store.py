"""MongoDB-backed stores for the Platform-Mongo-authoritative half of the
blue/green activation protocol: ActiveRuntimeSnapshot (the one atomic pointer
every request resolves) and RebuildLease (prevents concurrent rebuilds of the
same named snapshot). See graph/generation.py for the record shapes and
graph/generation_writer.py for the Neo4j-side counterpart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import ActiveRuntimeSnapshot, RebuildLease

ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION = "dynamic_graph_active_snapshots"
"""Platform-Mongo collection holding one document per snapshot_name. Named
beside the store so the activation writer and the request-path reader cannot
drift onto different collections."""


class ActiveRuntimeSnapshotStore(Protocol):
    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None: ...

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool: ...


class RebuildLeaseStore(Protocol):
    async def acquire(
        self,
        *,
        snapshot_name: str,
        graph_generation_id: str,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> RebuildLease | None: ...

    async def release(self, *, snapshot_name: str, lease_id: str) -> None: ...


class MongoActiveRuntimeSnapshotStore:
    """One document per snapshot_name (_id = snapshot_name). compare_and_swap
    relies on MongoDB's own single-document write atomicity -- no multi-document
    transaction needed. expected_activation_version=None means "no snapshot
    exists yet for this name" (first activation ever); the swap only succeeds
    if that is still true when the insert runs.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None:
        document = await self._collection.find_one({"_id": snapshot_name})
        if document is None:
            return None
        payload = dict(document)
        payload.pop("_id", None)
        return ActiveRuntimeSnapshot.model_validate(payload)

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool:
        if new_snapshot.snapshot_name != snapshot_name:
            raise ValueError("new_snapshot.snapshot_name must match snapshot_name")
        payload = new_snapshot.model_dump(mode="json")
        payload["_id"] = snapshot_name

        if expected_activation_version is None:
            try:
                await self._collection.insert_one(payload)
                return True
            except DuplicateKeyError:
                return False

        result = await self._collection.replace_one(
            {"_id": snapshot_name, "activation_version": expected_activation_version}, payload
        )
        # `bool(...)`, not the bare comparison: `replace_one` is untyped here,
        # so `matched_count` is `Any` and the comparison is `Any` too -- the
        # declared `-> bool` was asserting something mypy could not see.
        return bool(result.matched_count == 1)


class MongoRebuildLeaseStore:
    """One lease slot per snapshot_name (_id = snapshot_name). acquire() only
    succeeds if no lease exists yet, or the existing one has expired --
    real datetime objects (never stringified) so expiry comparison uses
    MongoDB's native BSON date ordering, not lexical string comparison. An
    active (non-expired) lease held by someone else causes a duplicate-key
    conflict on the upsert, which acquire() reports as "could not acquire"
    (returns None) rather than propagating the raw Mongo error.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def acquire(
        self,
        *,
        snapshot_name: str,
        graph_generation_id: str,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> RebuildLease | None:
        now = datetime.now(UTC)
        lease = RebuildLease(
            lease_id=str(uuid.uuid4()),
            snapshot_name=snapshot_name,
            graph_generation_id=graph_generation_id,
            owner_instance_id=owner_instance_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        payload = lease.model_dump(mode="python")
        payload["_id"] = snapshot_name
        try:
            await self._collection.find_one_and_update(
                {"_id": snapshot_name, "expires_at": {"$lt": now}},
                {"$set": payload},
                upsert=True,
            )
        except DuplicateKeyError:
            return None
        return lease

    async def release(self, *, snapshot_name: str, lease_id: str) -> None:
        await self._collection.delete_one({"_id": snapshot_name, "lease_id": lease_id})
