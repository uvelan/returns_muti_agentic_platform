"""MongoDB-backed stores for the Platform-Mongo-authoritative half of the
blue/green activation protocol: ActiveRuntimeSnapshot (the one atomic pointer
every request resolves), RebuildLease (prevents concurrent rebuilds of the same
named snapshot), and the durable monotonic counter every fencing token is
allocated from. See graph/generation.py for the record shapes and
graph/generation_writer.py for the Neo4j-side counterpart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import (
    FENCING_TOKEN_FLOOR,
    ActiveRuntimeSnapshot,
    RebuildLease,
)

ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION = "dynamic_graph_active_snapshots"
"""Platform-Mongo collection holding one document per snapshot_name. Named
beside the store so the activation writer and the request-path reader cannot
drift onto different collections."""

REBUILD_LEASES_COLLECTION = "dynamic_graph_rebuild_leases"
"""Platform-Mongo collection holding one rebuild-lease slot per snapshot_name.
Named here for the same reason as the snapshot collection: two rebuilders on
different collection names would both believe they held the lease."""

GENERATION_FENCING_TOKENS_COLLECTION = "dynamic_graph_fencing_tokens"
"""Platform-Mongo collection holding one counter document per allocation scope."""


class ActiveRuntimeSnapshotStore(Protocol):
    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None: ...

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool: ...


class FencingTokenAllocator(Protocol):
    async def allocate(self, *, scope: str, floor: int = 0) -> int: ...


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


class MongoFencingTokenAllocator:
    """Strictly increasing fencing tokens from a durable `$inc` counter.

    The one thing this must never do is repeat a value. `find_one_and_update`
    with `$inc` is atomic on a single document, so concurrent allocators
    serialize on the server rather than on any coordination between them, and
    the counter is persisted, so a restarted process cannot re-issue a token an
    earlier incarnation already used. That durability is the entire difference
    between a real fence and the constant `1` this replaced -- an in-process
    counter would reset on deploy and hand a stale writer's token to its
    successor.

    **One scope per named snapshot, not per generation.** Tokens are compared
    across the generations belonging to a snapshot (a claim on the newly
    activated generation must out-rank anything issued for its predecessor), so
    a per-generation counter restarting at the floor would let a claim on N+1
    present a *lower* token than one already issued for N.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def allocate(self, *, scope: str, floor: int = 0) -> int:
        """The next token for `scope`, guaranteed to exceed `floor`.

        The counter lives in Platform Mongo while the token it authorizes is
        written to a marker in Neo4j, so the two can start out of step: a graph
        adopted from outside this counter's history, or a platform database
        restored from a backup older than the graph. Every claim would then lose
        to the marker forever -- fail-closed, but permanently stuck.

        `floor` is how the one place that can observe both raises the counter
        past what the marker already holds. It is deliberately not applied on
        the ordinary claim path: catching up to a token someone else is holding
        is indistinguishable from a stale writer overtaking its successor, which
        is the exact thing this fence exists to stop. Only adoption -- which has
        just established that the generation is unowned and serving -- may do it.

        Two writes rather than one: MongoDB rejects `$max` and `$inc` on the same
        field in a single update. Both are idempotent and the `$inc` still
        serializes concurrent allocators, so the result is strictly increasing
        either way.
        """
        if floor > FENCING_TOKEN_FLOOR:
            await self._collection.update_one(
                {"_id": scope},
                {"$max": {"next_token": floor - FENCING_TOKEN_FLOOR}},
                upsert=True,
            )
        document = await self._collection.find_one_and_update(
            {"_id": scope},
            {"$inc": {"next_token": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        # upsert + return_document=AFTER always returns a document.
        counter = int(document["next_token"])
        # Offset rather than seeded, so a counter document created by an older
        # build (or by hand) still yields a token above the legacy constant --
        # seeding only fixes documents that do not exist yet.
        return FENCING_TOKEN_FLOOR + counter


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
