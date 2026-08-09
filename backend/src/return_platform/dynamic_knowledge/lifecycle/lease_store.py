"""Outstanding-work tracking for a graph generation, so retirement can wait.

`GenerationReadLease` and `GenerationWriteReservation` (graph/generation.py)
have documented since they were written that "cleanup of a RETIRED generation
waits for every read lease ... to drain or expire". Nothing implemented that:
the orchestrator went ACTIVE -> RETIRED the instant its compare-and-swap
succeeded, which is safe only while nothing pins requests to a generation.
This module is the missing half.

**Why one document per generation.** Both lease classes and the drain flag live
in a single document keyed by `graph_generation_id`, so MongoDB's
single-document write atomicity is the whole concurrency argument -- no
transaction, no replica-set requirement, no read-then-write window. The
alternative (a document per lease plus a separate drain marker) has a real race:
a reader that resolved the snapshot just before the cutover could insert its
lease just after retirement counted outstanding work and found none. Here that
reader's `$push` is filtered on `draining` not being set, so it either lands
before the drain begins and is waited for, or is refused. There is no third
outcome.

**Why acquisition can be refused at all.** Retirement is only ever reached after
the ActiveRuntimeSnapshot CAS, so a refused acquisition means the caller is
holding a snapshot that has already been superseded -- it should re-resolve, not
retry. That is the `REBIND_ON_RESUME` behaviour the plan asks for, surfaced at
the one place that can detect it.

**Why TTLs are load-bearing.** A holder that crashes never releases. Outstanding
work is therefore counted by `expires_at`, not by presence, so a dead holder
stops blocking retirement on its own without anyone reaping it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import (
    GenerationReadLease,
    GenerationWriteReservation,
)


class LeaseClass(StrEnum):
    """The two kinds of outstanding work retirement must wait on.

    Kept distinct rather than collapsed into one counter because they fail
    differently: an outstanding READ means someone would serve stale results
    from a removed generation, an outstanding WRITE means an on-demand sync
    would write into one. Retirement waits on both, but an operator diagnosing
    a stuck drain needs to know which.
    """

    READ = "READ"
    WRITE = "WRITE"


class GenerationLeaseStore(Protocol):
    async def acquire_read_lease(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> GenerationReadLease | None: ...

    async def acquire_write_reservation(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> GenerationWriteReservation | None: ...

    async def release(self, *, graph_generation_id: str, lease_id: str) -> None: ...

    async def begin_drain(self, *, graph_generation_id: str) -> None: ...

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]: ...


class MongoGenerationLeaseStore:
    """One document per generation: `{_id, draining, leases: [...]}`.

    `leases` does not grow without bound -- entries are pulled on release, and
    the only entries that can outlive their holder are bounded by the drain
    wait plus their own TTL, after which the whole document becomes garbage
    alongside the RETIRED generation it describes.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def _acquire(
        self,
        *,
        graph_generation_id: str,
        lease_class: LeaseClass,
        lease_id: str,
        owner_instance_id: str,
        expires_at: datetime,
    ) -> bool:
        entry = {
            "lease_id": lease_id,
            "lease_class": lease_class.value,
            "owner_instance_id": owner_instance_id,
            "expires_at": expires_at,
        }
        try:
            result = await self._collection.update_one(
                {"_id": graph_generation_id, "draining": {"$ne": True}},
                {"$push": {"leases": entry}},
                upsert=True,
            )
        except DuplicateKeyError:
            # The upsert lost to a concurrent write that set `draining`: the
            # filter no longer matches the existing document, so Mongo tried to
            # insert a second one under the same _id. That is a refusal, not an
            # error the caller should see as a fault.
            return False
        return result.matched_count == 1 or result.upserted_id is not None

    async def acquire_read_lease(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> GenerationReadLease | None:
        now = datetime.now(UTC)
        lease = GenerationReadLease(
            lease_id=str(uuid.uuid4()),
            graph_generation_id=graph_generation_id,
            snapshot_activation_version=snapshot_activation_version,
            owner_instance_id=owner_instance_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        acquired = await self._acquire(
            graph_generation_id=graph_generation_id,
            lease_class=LeaseClass.READ,
            lease_id=lease.lease_id,
            owner_instance_id=owner_instance_id,
            expires_at=lease.expires_at,
        )
        return lease if acquired else None

    async def acquire_write_reservation(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> GenerationWriteReservation | None:
        now = datetime.now(UTC)
        reservation = GenerationWriteReservation(
            reservation_id=str(uuid.uuid4()),
            graph_generation_id=graph_generation_id,
            snapshot_activation_version=snapshot_activation_version,
            owner_instance_id=owner_instance_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        acquired = await self._acquire(
            graph_generation_id=graph_generation_id,
            lease_class=LeaseClass.WRITE,
            lease_id=reservation.reservation_id,
            owner_instance_id=owner_instance_id,
            expires_at=reservation.expires_at,
        )
        return reservation if acquired else None

    async def release(self, *, graph_generation_id: str, lease_id: str) -> None:
        await self._collection.update_one(
            {"_id": graph_generation_id}, {"$pull": {"leases": {"lease_id": lease_id}}}
        )

    async def begin_drain(self, *, graph_generation_id: str) -> None:
        """Close the generation to new work. Idempotent, and safe to call for a
        generation that has never had a lease -- the upsert creates the closed
        document so a late acquisition is refused rather than creating it open."""
        await self._collection.update_one(
            {"_id": graph_generation_id},
            {"$set": {"draining": True, "draining_since": datetime.now(UTC)}},
            upsert=True,
        )

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]:
        document = await self._collection.find_one({"_id": graph_generation_id})
        counts = {LeaseClass.READ: 0, LeaseClass.WRITE: 0}
        if document is None:
            return counts
        now = datetime.now(UTC)
        for entry in document.get("leases", ()):
            expires_at = entry.get("expires_at")
            if isinstance(expires_at, datetime):
                # Mongo hands back naive UTC datetimes; comparing those to an
                # aware `now` raises rather than returning False, which would
                # turn a routine drain poll into a crash.
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= now:
                    continue
            try:
                lease_class = LeaseClass(entry.get("lease_class"))
            except ValueError:
                continue
            counts[lease_class] += 1
        return counts
