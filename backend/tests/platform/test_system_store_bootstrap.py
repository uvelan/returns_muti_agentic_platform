"""SystemStoreBootstrapper against a real MongoDB replica set (implementation plan
Phase 3 gate: "verify against the real Mongo instance that a second startup reuses
structures and creates nothing"). Also proves auto_bootstrap_missing_structures=False
fails closed rather than silently creating what it was told not to."""

from __future__ import annotations

import uuid

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.bootstrap import (
    MissingSystemStoreStructure,
    SystemStoreBootstrapper,
)
from return_platform.platform.system_store.contracts import StructureDefinition
from return_platform.platform.system_store.migrations import MigrationRunner
from return_platform.platform.system_store.mongo import (
    MongoLeaseStore,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
)


def _structures(physical_prefix: str) -> list[StructureDefinition]:
    return [
        StructureDefinition(
            logical_name="probe_conversations",
            physical_name=f"{physical_prefix}_conversations",
            schema_version=1,
            indexes=(
                {"name": "conversation_id_unique", "fields": ["conversation_id"], "unique": True},
            ),
        ),
        StructureDefinition(
            logical_name="probe_audit",
            physical_name=f"{physical_prefix}_audit",
            schema_version=1,
        ),
    ]


@pytest.mark.asyncio
async def test_second_bootstrap_reuses_structures_and_creates_nothing(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    prefix = f"bootstrap_probe_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)

    for definition in structures:
        await db.get_collection(definition.physical_name).drop()
    await db.get_collection("platform_bootstrap_locks").delete_many(
        {"_id": "system_store_bootstrap"}
    )

    def _make_bootstrapper(owner: str) -> SystemStoreBootstrapper:
        return SystemStoreBootstrapper(
            lease_store=MongoLeaseStore(client, database="platform"),
            adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
            migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
            owner_instance_id=owner,
        )

    first = await _make_bootstrapper("instance-1").bootstrap(structures)
    assert set(first.created_structures) == {"probe_conversations", "probe_audit"}
    assert "conversation_id_unique" in first.created_indexes

    second = await _make_bootstrapper("instance-2").bootstrap(structures)
    assert second.created_structures == ()
    assert second.created_indexes == ()
    assert set(second.existing_structures) == {"probe_conversations", "probe_audit"}

    for definition in structures:
        assert await db.get_collection(definition.physical_name).find_one() is None
        names = await db.list_collection_names(filter={"name": definition.physical_name})
        assert definition.physical_name in names


@pytest.mark.asyncio
async def test_missing_structure_fails_closed_when_auto_bootstrap_disabled(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    db = client.get_database("platform")
    prefix = f"bootstrap_probe_disabled_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    for definition in structures:
        await db.get_collection(definition.physical_name).drop()
    await db.get_collection("platform_bootstrap_locks").delete_many(
        {"_id": "system_store_bootstrap"}
    )

    bootstrapper = SystemStoreBootstrapper(
        lease_store=MongoLeaseStore(client, database="platform"),
        adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
        migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
        owner_instance_id="instance-1",
    )

    with pytest.raises(MissingSystemStoreStructure):
        await bootstrapper.bootstrap(structures, auto_bootstrap_missing=False)

    # Refusing to create must not have left anything behind (no half-created structure).
    for definition in structures:
        names = await db.list_collection_names(filter={"name": definition.physical_name})
        assert definition.physical_name not in names
