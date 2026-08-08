"""FencedMongoWriter re-verifies the lease inside the same transaction as the write it
guards, so a stale holder -- one whose fencing_token has been superseded by a new
acquisition -- is rejected atomically, with no partial write ever landing.

Runs against a real MongoDB replica set: this is exactly the kind of race a hand-rolled
session mock cannot prove (design doc §13.7; see also the concurrent-activation review
that found a fake session hides real transaction bugs)."""

from __future__ import annotations

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.locking import LeaseLost
from return_platform.platform.system_store.mongo import FencedMongoWriter, MongoLeaseStore


@pytest.mark.asyncio
async def test_guarded_write_succeeds_for_the_current_lease_holder(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    await db.get_collection("platform_bootstrap_locks").delete_many({})
    await db.get_collection("platform_fencing_tokens").delete_many({})
    await db.get_collection("fenced_writer_probe").delete_many({})

    lease_store = MongoLeaseStore(client, database="platform")
    writer = FencedMongoWriter(client, database="platform")

    lease = await lease_store.acquire("probe-lock", owner_instance_id="owner-a", ttl_seconds=30.0)

    async def _write(session: object) -> str:
        await db.get_collection("fenced_writer_probe").insert_one(
            {"_id": "written-by-a"}, session=session
        )
        return "ok"

    result = await writer.guarded_write(lease, _write)

    assert result == "ok"
    assert (
        await db.get_collection("fenced_writer_probe").find_one({"_id": "written-by-a"}) is not None
    )


@pytest.mark.asyncio
async def test_guarded_write_rejects_a_lease_whose_token_was_superseded(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    await db.get_collection("platform_bootstrap_locks").delete_many({})
    await db.get_collection("platform_fencing_tokens").delete_many({})
    await db.get_collection("fenced_writer_probe").delete_many({})

    lease_store = MongoLeaseStore(client, database="platform")
    writer = FencedMongoWriter(client, database="platform")

    stale_lease = await lease_store.acquire(
        "probe-lock", owner_instance_id="owner-a", ttl_seconds=30.0
    )

    # Simulate the stale holder pausing past its TTL while a second instance takes over
    # the same lock -- this must mint a strictly greater fencing_token.
    await db.get_collection("platform_bootstrap_locks").update_one(
        {"_id": "probe-lock"}, {"$set": {"expires_at": stale_lease.acquired_at}}
    )
    new_lease = await lease_store.acquire(
        "probe-lock", owner_instance_id="owner-b", ttl_seconds=30.0
    )
    assert new_lease.fencing_token > stale_lease.fencing_token

    async def _write(session: object) -> None:
        await db.get_collection("fenced_writer_probe").insert_one(
            {"_id": "written-by-stale-a"}, session=session
        )

    with pytest.raises(LeaseLost):
        await writer.guarded_write(stale_lease, _write)

    # The rejected write must never have landed -- rejection and the write attempt are
    # one atomic transaction, not "check then maybe write."
    assert (
        await db.get_collection("fenced_writer_probe").find_one({"_id": "written-by-stale-a"})
        is None
    )
