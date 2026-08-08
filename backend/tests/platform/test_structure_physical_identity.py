"""MongoVersionLedger binds a recorded schema version to StructureIdentity
(logical_name + physical_name + physical_identity), not logical_name alone (Slice
3R.4). A collection's UUID (its physical identity) changes across a drop+recreate even
under the same name, and changes entirely when a structure is repointed at a different
physical collection -- in both cases the recorded version must not be inherited by the
new physical object. Runs against a real MongoDB replica set: physical identity is
genuine server-reported metadata (`listCollections`' `info.uuid`), not something a mock
session can fake."""

from __future__ import annotations

import uuid

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.contracts import StructureIdentity
from return_platform.platform.system_store.mongo import MongoLeaseStore, MongoVersionLedger


async def _physical_identity(
    client: AsyncMongoClient[dict[str, object]], physical_name: str
) -> str:
    cursor = await client.get_database("platform").list_collections(filter={"name": physical_name})
    async for doc in cursor:
        info = doc.get("info") or {}
        return bytes(info["uuid"]).hex()
    raise AssertionError(f"collection '{physical_name}' does not exist")


@pytest.mark.asyncio
async def test_physical_rename_does_not_inherit_the_old_version(test_settings: Settings) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    logical_name = f"probe_identity_{uuid.uuid4().hex[:8]}"
    old_physical = f"{logical_name}_old_physical"
    new_physical = f"{logical_name}_new_physical"
    await db.get_collection(old_physical).drop()
    await db.get_collection(new_physical).drop()
    await db.get_collection("platform_schema_versions").delete_many({"_id": logical_name})

    await db.create_collection(old_physical)
    old_identity = StructureIdentity(
        logical_name=logical_name,
        physical_name=old_physical,
        physical_identity=await _physical_identity(client, old_physical),
        structure_fingerprint="fp-1",
    )
    ledger = MongoVersionLedger(client, database="platform")
    lease_store = MongoLeaseStore(client, database="platform")
    lease = await lease_store.acquire(
        f"lock-{logical_name}", owner_instance_id="owner", ttl_seconds=30.0
    )

    assert await ledger.current_version(old_identity) == 0
    await ledger.record_version(old_identity, 3, lease)
    assert await ledger.current_version(old_identity) == 3

    # The manifest now points the same logical_name at a different physical collection.
    await db.create_collection(new_physical)
    new_identity = StructureIdentity(
        logical_name=logical_name,
        physical_name=new_physical,
        physical_identity=await _physical_identity(client, new_physical),
        structure_fingerprint="fp-1",
    )

    assert await ledger.current_version(new_identity) == 0


@pytest.mark.asyncio
async def test_same_name_recreated_collection_does_not_inherit_the_old_version(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    logical_name = f"probe_recreate_{uuid.uuid4().hex[:8]}"
    physical_name = f"{logical_name}_physical"
    await db.get_collection(physical_name).drop()
    await db.get_collection("platform_schema_versions").delete_many({"_id": logical_name})

    await db.create_collection(physical_name)
    first_identity = StructureIdentity(
        logical_name=logical_name,
        physical_name=physical_name,
        physical_identity=await _physical_identity(client, physical_name),
        structure_fingerprint="fp-1",
    )
    ledger = MongoVersionLedger(client, database="platform")
    lease_store = MongoLeaseStore(client, database="platform")
    lease = await lease_store.acquire(
        f"lock-{logical_name}", owner_instance_id="owner", ttl_seconds=30.0
    )

    await ledger.record_version(first_identity, 2, lease)
    assert await ledger.current_version(first_identity) == 2

    # Drop and recreate under the exact same physical name -- a new collection UUID.
    await db.get_collection(physical_name).drop()
    await db.create_collection(physical_name)
    recreated_identity = StructureIdentity(
        logical_name=logical_name,
        physical_name=physical_name,
        physical_identity=await _physical_identity(client, physical_name),
        structure_fingerprint="fp-1",
    )

    assert recreated_identity.physical_identity != first_identity.physical_identity
    assert await ledger.current_version(recreated_identity) == 0
