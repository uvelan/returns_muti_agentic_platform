import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient

from return_platform.configuration.application.activation import (
    ActivationConflictError,
    ActivationService,
)
from return_platform.configuration.application.snapshot import compute_checksum
from return_platform.configuration.domain.agents import AgentConfigNode, AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.modules import ModuleConfigNode, ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release import ReleaseStatus
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.workflow import WorkflowConfig
from return_platform.configuration.settings import Settings


def _snapshot(release_id: str) -> RuntimeSnapshot:
    """A real, valid snapshot per release -- activate_release() now re-verifies the
    checksum before activating (Slice 3R.7), so a synthetic release document without
    a real snapshot/checksum pair would fail closed rather than exercise the CAS race
    this test is actually about."""
    return RuntimeSnapshot(
        platform=PlatformConfig(),
        system_store=SystemStoreConfig(),
        modules=ModulesConfig(
            modules={
                "agent.order_discovery": ModuleConfigNode(
                    module_id="agent.order_discovery", module_type="AGENT"
                )
            }
        ),
        agents=AgentsConfig(agents={"order_discovery": AgentConfigNode()}),
        workflow=WorkflowConfig(workflow={}),
        sources=SourcesConfig(sources={}),
        integrations=IntegrationsConfig(integrations={}),
        graph=GraphConfig(),
        ai=AiConfig(),
        features=FeaturesConfig(flags={release_id: True}),
    )


@pytest_asyncio.fixture
async def activation_collections(
    test_settings: Settings,
) -> AsyncIterator[tuple[ActivationService, Any, Any]]:
    """Clean before *and* after.

    This test used to clean only before inserting, so every run left `r1`, `r2`,
    `r3` and an active pointer behind in the shared dev database. They were
    still there months later, and were briefly mistaken for real configuration
    data while measuring the D3 lifecycle decision -- a test that leaves rows an
    operator might inspect is a test that can be misread as production state.

    Cleaning at both ends rather than only at the end: a previous run that died
    mid-test still has to be recoverable from.
    """
    client = AsyncMongoClient(test_settings.mongo_dsn.get_secret_value())
    service = ActivationService(client)
    # `ActivationService` always operates against the "platform" database (see
    # its `__init__`) regardless of the business `mongo_database` setting -- the
    # test must target the same database and collection names or it silently
    # observes an empty collection.
    await service.initialize_indexes()
    db = client.get_database("platform")
    releases = db.get_collection("configuration_releases")
    pointer = db.get_collection("configuration_active_pointer")

    await releases.delete_many({})
    await pointer.delete_many({})
    try:
        yield service, releases, pointer
    finally:
        await releases.delete_many({})
        await pointer.delete_many({})
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_activation(
    activation_collections: tuple[ActivationService, Any, Any],
) -> None:
    """Proves that concurrent activation attempts are strictly serialized.

    Exactly one activation must succeed. The others must receive an
    ActivationConflictError. The pointer version must advance exactly once.
    The loser remains in APPROVED status.
    """
    service, releases, pointer = activation_collections

    # Create 3 approved releases, each with a real snapshot/checksum pair.
    await releases.insert_many(
        [
            {
                "release_id": rid,
                "status": ReleaseStatus.APPROVED,
                "snapshot": _snapshot(rid).model_dump(),
                "checksum": compute_checksum(_snapshot(rid)),
            }
            for rid in ("r1", "r2", "r3")
        ]
    )

    # 1. Activate r1 sequentially to establish a baseline
    await service.activate_release("r1")

    base_pointer = await pointer.find_one({"_id": "active"})
    assert base_pointer is not None
    assert base_pointer["release_id"] == "r1"
    assert base_pointer["version"] == 1

    # 2. Concurrently attempt to activate r2 and r3, both released from a
    # shared barrier so the two transactions genuinely race rather than
    # happening to run sequentially inside the event loop.
    barrier = asyncio.Barrier(2)

    async def try_activate(rid: str) -> str | None:
        await barrier.wait()
        try:
            await service.activate_release(rid)
            return rid
        except ActivationConflictError:
            return None

    results = await asyncio.gather(
        try_activate("r2"),
        try_activate("r3"),
    )

    # 3. Exactly one should succeed
    winners = [rid for rid in results if rid is not None]
    assert len(winners) == 1
    winner_id = winners[0]
    loser_id = "r3" if winner_id == "r2" else "r2"

    # 4. Exactly one new active release (r1 was active, but should now be SUPERSEDED)
    actives = await releases.count_documents({"status": ReleaseStatus.ACTIVE})
    assert actives == 1

    # 5. The pointer must have advanced by exactly 1 version, and it must
    # point at the actual winner -- not just at "some" release.
    new_pointer = await pointer.find_one({"_id": "active"})
    assert new_pointer is not None
    assert new_pointer["version"] == 2
    assert new_pointer["release_id"] == winner_id

    # 6. The winner is ACTIVE, its checksum matches the pointer's, and it was
    # activated -- the loser is untouched APPROVED with no partial mutation
    # from its aborted transaction (no activated_at, no status drift).
    winner_doc = await releases.find_one({"release_id": winner_id})
    loser_doc = await releases.find_one({"release_id": loser_id})

    assert winner_doc["status"] == ReleaseStatus.ACTIVE
    assert winner_doc["activated_at"] is not None
    assert new_pointer["checksum"] == winner_doc["checksum"]

    assert loser_doc["status"] == ReleaseStatus.APPROVED
    assert loser_doc.get("activated_at") is None
    assert loser_doc.get("superseded_by") is None

    # 7. r1 was superseded by the winner only -- never by the loser.
    r1_doc = await releases.find_one({"release_id": "r1"})
    assert r1_doc["status"] == ReleaseStatus.SUPERSEDED
    assert r1_doc["superseded_by"] == winner_id
