import pytest
import asyncio
from pymongo import AsyncMongoClient

from return_platform.configuration.application.activation import ActivationService, ActivationConflictError
from return_platform.configuration.domain.release import ReleaseStatus
from return_platform.configuration.settings import Settings

@pytest.mark.asyncio
async def test_concurrent_activation(test_settings: Settings) -> None:
    """Proves that concurrent activation attempts are strictly serialized.
    
    Exactly one activation must succeed. The others must receive an
    ActivationConflictError. The pointer version must advance exactly once.
    The loser remains in APPROVED status.
    """
    client = AsyncMongoClient(test_settings.mongo_dsn.get_secret_value())
    service = ActivationService(client)
    
    # Ensure indexes and clear collections for isolation.
    # ActivationService always operates against the "platform" database
    # (see ActivationService.__init__) regardless of the business
    # mongo_database setting -- the test must target the same database
    # and collection names or it silently observes an empty collection.
    await service.initialize_indexes()
    db = client.get_database("platform")
    releases = db.get_collection("configuration_releases")
    pointer = db.get_collection("configuration_active_pointer")
    
    await releases.delete_many({})
    await pointer.delete_many({})
    
    # Create 3 approved releases
    await releases.insert_many([
        {"release_id": "r1", "status": ReleaseStatus.APPROVED, "checksum": "c1"},
        {"release_id": "r2", "status": ReleaseStatus.APPROVED, "checksum": "c2"},
        {"release_id": "r3", "status": ReleaseStatus.APPROVED, "checksum": "c3"},
    ])
    
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
