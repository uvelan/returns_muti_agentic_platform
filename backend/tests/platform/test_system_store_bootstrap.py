"""SystemStoreBootstrapper against a real MongoDB replica set (implementation plan
Phase 3 gate: "verify against the real Mongo instance that a second startup reuses
structures and creates nothing"; Slice 3R.8: multi-replica bootstrap contention).

Also proves auto_bootstrap_missing_structures=False fails closed rather than silently
creating what it was told not to."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.system_store.bootstrap import (
    MissingSystemStoreStructure,
    SystemStoreBootstrapper,
    SystemStoreBootstrapTimeout,
)
from return_platform.platform.system_store.contracts import (
    BootstrapStatus,
    StructureDefinition,
    compute_manifest_fingerprint,
)
from return_platform.platform.system_store.locking import LeaseLost
from return_platform.platform.system_store.migrations import MigrationRunner
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoBootstrapStateStore,
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


async def _clean(
    client: AsyncMongoClient[dict[str, object]], structures: list[StructureDefinition]
) -> None:
    db = client.get_database("platform")
    for definition in structures:
        await db.get_collection(definition.physical_name).drop()
    await db.get_collection("platform_bootstrap_locks").delete_many(
        {"_id": "system_store_bootstrap"}
    )
    await db.get_collection("platform_bootstrap_state").delete_many(
        {"manifest_fingerprint": compute_manifest_fingerprint(structures)}
    )


def _make_bootstrapper(
    client: AsyncMongoClient[dict[str, object]], owner: str, **kwargs: object
) -> SystemStoreBootstrapper:
    return SystemStoreBootstrapper(
        lease_store=MongoLeaseStore(client, database="platform"),
        adapter=MongoSystemStoreAdapter(PymongoStructureGateway(client, database="platform")),
        migration_runner=MigrationRunner(MongoVersionLedger(client, database="platform")),
        bootstrap_state=MongoBootstrapStateStore(client, database="platform"),
        guard=FencedMongoTransactionGuard(client, database="platform"),
        owner_instance_id=owner,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_second_bootstrap_reuses_structures_and_creates_nothing(
    test_settings: Settings,
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    prefix = f"bootstrap_probe_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)
    db = client.get_database("platform")

    first = await _make_bootstrapper(client, "instance-1").bootstrap(structures)
    assert set(first.created_structures) == {"probe_conversations", "probe_audit"}
    assert "conversation_id_unique" in first.created_indexes

    second = await _make_bootstrapper(client, "instance-2").bootstrap(structures)
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
    prefix = f"bootstrap_probe_disabled_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)
    db = client.get_database("platform")

    bootstrapper = _make_bootstrapper(client, "instance-1")

    with pytest.raises(MissingSystemStoreStructure):
        await bootstrapper.bootstrap(structures, auto_bootstrap_missing=False)

    for definition in structures:
        names = await db.list_collection_names(filter={"name": definition.physical_name})
        assert definition.physical_name not in names


@pytest.mark.asyncio
async def test_two_bootstrap_contenders_exactly_one_active_owner(test_settings: Settings) -> None:
    """Two replicas racing to bootstrap the same manifest: exactly one acquires the
    lease and mutates anything (creates every structure); the other loses the race at
    lease acquisition, waits, observes the winner's COMPLETE, and creates nothing
    itself. No artificial delay is needed to force this: `MongoLeaseStore.acquire()`'s
    atomic CAS already guarantees only one caller ever becomes the owner -- the loser is
    diverted to the waiter path immediately, before it would ever touch the adapter."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    prefix = f"bootstrap_race_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)
    db = client.get_database("platform")

    results = await asyncio.gather(
        _make_bootstrapper(client, "instance-1").bootstrap(structures),
        _make_bootstrapper(client, "instance-2").bootstrap(structures),
    )

    created_counts = [len(report.created_structures) for report in results]
    assert sorted(created_counts) == [0, 2]

    for definition in structures:
        names = await db.list_collection_names(filter={"name": definition.physical_name})
        assert definition.physical_name in names

    state = await MongoBootstrapStateStore(client, database="platform").read(
        compute_manifest_fingerprint(structures)
    )
    assert state is not None
    assert state.status is BootstrapStatus.COMPLETE


@pytest.mark.asyncio
async def test_waiter_succeeds_after_winner_completes(test_settings: Settings) -> None:
    """A waiter that never acquires the lease at all -- the winner finishes first --
    observes COMPLETE and returns without running any migration itself."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    prefix = f"bootstrap_waiter_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)

    await _make_bootstrapper(client, "instance-1").bootstrap(structures)

    waiter_report = await _make_bootstrapper(
        client, "instance-2", waiter_deadline_seconds=10.0
    ).bootstrap(structures)

    assert waiter_report.created_structures == ()
    assert set(waiter_report.existing_structures) == {"probe_conversations", "probe_audit"}


@pytest.mark.asyncio
async def test_owner_crash_allows_takeover_and_stale_owner_cannot_finalize(
    test_settings: Settings,
) -> None:
    """If the owner's lease expires mid-bootstrap (crash), a waiter takes over, resumes
    idempotently, and finishes. The original (now-stale) owner's lease has been
    superseded, so if it somehow tried to resume, its fenced writes would be rejected --
    proven directly by attempting its own mark_complete after takeover."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    prefix = f"bootstrap_crash_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)
    db = client.get_database("platform")

    lease_store = MongoLeaseStore(client, database="platform")
    stale_lease = await lease_store.acquire(
        "system_store_bootstrap", owner_instance_id="crashed-owner", ttl_seconds=30.0
    )
    bootstrap_state = MongoBootstrapStateStore(client, database="platform")
    manifest_fingerprint = compute_manifest_fingerprint(structures)
    await bootstrap_state.mark_running(manifest_fingerprint, stale_lease)

    # Simulate the crash: the owner's lease expires without ever reaching COMPLETE.
    await db.get_collection("platform_bootstrap_locks").update_one(
        {"_id": "system_store_bootstrap"}, {"$set": {"expires_at": stale_lease.acquired_at}}
    )

    takeover_report = await _make_bootstrapper(client, "instance-2").bootstrap(structures)
    assert set(takeover_report.created_structures) == {"probe_conversations", "probe_audit"}

    state_after = await bootstrap_state.read(manifest_fingerprint)
    assert state_after is not None
    assert state_after.status is BootstrapStatus.COMPLETE
    assert state_after.owner_instance_id == "instance-2"

    with pytest.raises(LeaseLost):
        await bootstrap_state.mark_complete(manifest_fingerprint, stale_lease)


@pytest.mark.asyncio
async def test_waiter_times_out_if_owner_never_completes(test_settings: Settings) -> None:
    """A waiter never treats partial schema-version progress as COMPLETE, and gives up
    with a typed timeout rather than waiting forever if the owner's lease is still held
    and genuinely never finishes."""
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    prefix = f"bootstrap_timeout_{uuid.uuid4().hex[:8]}"
    structures = _structures(prefix)
    await _clean(client, structures)

    lease_store = MongoLeaseStore(client, database="platform")
    active_lease = await lease_store.acquire(
        "system_store_bootstrap", owner_instance_id="slow-owner", ttl_seconds=30.0
    )
    bootstrap_state = MongoBootstrapStateStore(client, database="platform")
    await bootstrap_state.mark_running(compute_manifest_fingerprint(structures), active_lease)
    # Never marks RUNNING -> COMPLETE, and the lease is still held (not expired).

    with pytest.raises(SystemStoreBootstrapTimeout):
        await _make_bootstrapper(
            client,
            "instance-2",
            waiter_deadline_seconds=0.5,
            waiter_base_delay_seconds=0.05,
            waiter_max_delay_seconds=0.1,
        ).bootstrap(structures)
