"""The lease store's concurrency claim, against real Mongo.

`MongoGenerationLeaseStore` argues that keeping the drain flag and the lease
list in one document makes single-document write atomicity the entire
concurrency story -- no transaction, no read-then-write window. That claim is
about MongoDB's behaviour, so a double cannot test it: an in-memory dict is
atomic for free and would pass no matter how the filter was written.

The property that matters is that an acquisition racing a drain has exactly two
outcomes and no third: either it lands before the drain and is therefore waited
for, or it is refused. What must never happen is a lease that is granted *and*
not counted -- that is the reader who gets its generation deleted mid-query.

Deliberately avoids the shared `test_settings` fixture, which demands API keys
nothing here exercises.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    LeaseClass,
    MongoGenerationLeaseStore,
)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def collection() -> AsyncIterator[object]:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    name = f"generation_leases_t_{uuid.uuid4().hex[:12]}"
    database = client.get_database("platform")
    try:
        yield database.get_collection(name)
    finally:
        await database.drop_collection(name)
        await client.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_a_lease_can_be_acquired_and_is_counted(collection: object) -> None:
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"

    lease = await store.acquire_read_lease(
        graph_generation_id=generation_id,
        snapshot_activation_version=1,
        owner_instance_id="reader-1",
        ttl_seconds=300,
    )

    assert lease is not None
    assert await store.outstanding(graph_generation_id=generation_id) == {
        LeaseClass.READ: 1,
        LeaseClass.WRITE: 0,
    }


@pytest.mark.asyncio(loop_scope="module")
async def test_reads_and_writes_are_counted_separately(collection: object) -> None:
    """An operator diagnosing a stuck drain needs to know which class is
    holding it -- a stalled reader and a stalled on-demand write are different
    problems with different owners."""
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"

    await store.acquire_read_lease(
        graph_generation_id=generation_id,
        snapshot_activation_version=1,
        owner_instance_id="reader-1",
        ttl_seconds=300,
    )
    await store.acquire_write_reservation(
        graph_generation_id=generation_id,
        snapshot_activation_version=1,
        owner_instance_id="writer-1",
        ttl_seconds=300,
    )

    assert await store.outstanding(graph_generation_id=generation_id) == {
        LeaseClass.READ: 1,
        LeaseClass.WRITE: 1,
    }


@pytest.mark.asyncio(loop_scope="module")
async def test_release_stops_the_lease_being_counted(collection: object) -> None:
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"

    lease = await store.acquire_read_lease(
        graph_generation_id=generation_id,
        snapshot_activation_version=1,
        owner_instance_id="reader-1",
        ttl_seconds=300,
    )
    assert lease is not None
    await store.release(graph_generation_id=generation_id, lease_id=lease.lease_id)

    assert not any((await store.outstanding(graph_generation_id=generation_id)).values())


@pytest.mark.asyncio(loop_scope="module")
async def test_an_expired_lease_is_not_counted(collection: object) -> None:
    """Written directly rather than by sleeping out a TTL: the point is that
    expiry is evaluated against `expires_at`, and Mongo stores these as naive
    UTC datetimes, which would raise on comparison if read back carelessly."""
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    await collection.insert_one(  # type: ignore[attr-defined]
        {
            "_id": generation_id,
            "leases": [
                {
                    "lease_id": "dead-holder",
                    "lease_class": LeaseClass.READ.value,
                    "owner_instance_id": "crashed-reader",
                    "expires_at": datetime.now(UTC) - timedelta(seconds=1),
                }
            ],
        }
    )

    assert not any((await store.outstanding(graph_generation_id=generation_id)).values())


@pytest.mark.asyncio(loop_scope="module")
async def test_a_lease_cannot_be_acquired_once_the_drain_has_begun(collection: object) -> None:
    """The refusal is what makes the drain terminate. Without it a steady
    arrival of readers would keep the outstanding count above zero forever and
    the generation could never be retired."""
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"
    await store.begin_drain(graph_generation_id=generation_id)

    refused = await store.acquire_read_lease(
        graph_generation_id=generation_id,
        snapshot_activation_version=1,
        owner_instance_id="late-reader",
        ttl_seconds=300,
    )

    assert refused is None
    assert not any((await store.outstanding(graph_generation_id=generation_id)).values())


@pytest.mark.asyncio(loop_scope="module")
async def test_drain_closes_a_generation_that_never_held_a_lease(collection: object) -> None:
    """begin_drain upserts, so retiring a quiet generation still leaves the
    document closed -- otherwise a straggler would create it open and acquire a
    lease on something already being retired."""
    store = MongoGenerationLeaseStore(collection)
    generation_id = f"gen-{uuid.uuid4().hex[:8]}"

    await store.begin_drain(graph_generation_id=generation_id)

    assert (
        await store.acquire_write_reservation(
            graph_generation_id=generation_id,
            snapshot_activation_version=1,
            owner_instance_id="straggler",
            ttl_seconds=300,
        )
        is None
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_a_lease_racing_the_drain_is_either_granted_and_counted_or_refused(
    collection: object,
) -> None:
    """The actual race, run for real. Every granted lease must also be visible
    to `outstanding` at the moment the drain starts counting -- a lease that is
    granted but uncounted is the reader whose generation gets deleted under it.
    """
    store = MongoGenerationLeaseStore(collection)

    for _ in range(25):
        generation_id = f"gen-{uuid.uuid4().hex[:8]}"

        async def _acquire(generation_id: str = generation_id) -> object | None:
            return await store.acquire_read_lease(
                graph_generation_id=generation_id,
                snapshot_activation_version=1,
                owner_instance_id="racing-reader",
                ttl_seconds=300,
            )

        async def _drain(generation_id: str = generation_id) -> None:
            await store.begin_drain(graph_generation_id=generation_id)

        lease, _ = await asyncio.gather(_acquire(), _drain())
        counted = (await store.outstanding(graph_generation_id=generation_id))[LeaseClass.READ]

        if lease is None:
            assert counted == 0, "a refused lease must not be recorded"
        else:
            assert counted == 1, (
                "a granted lease was not counted; the drain would have retired "
                "this generation out from under its holder"
            )
