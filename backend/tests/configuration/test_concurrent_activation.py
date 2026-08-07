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
    
    # Ensure indexes and clear collections for isolation
    await service.initialize_indexes()
    db = client.get_database("return_platform")
    releases = db.get_collection("configuration_releases")
    pointer = db.get_collection("configuration_activation_pointer")
    
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
    
    # 2. Concurrently attempt to activate r2 and r3
    async def try_activate(rid: str) -> bool:
        try:
            await service.activate_release(rid)
            return True
        except ActivationConflictError:
            return False
            
    results = await asyncio.gather(
        try_activate("r2"),
        try_activate("r3")
    )
    
    # 3. Exactly one should succeed
    success_count = sum(1 for r in results if r)
    assert success_count == 1
    
    # 4. Exactly one new active release (r1 was active, but should now be SUPERSEDED)
    actives = await releases.count_documents({"status": ReleaseStatus.ACTIVE})
    assert actives == 1
    
    # 5. The pointer must have advanced by exactly 1 version
    new_pointer = await pointer.find_one({"_id": "active"})
    assert new_pointer is not None
    assert new_pointer["version"] == 2
    
    # 6. Check statuses of r2 and r3
    r2_doc = await releases.find_one({"release_id": "r2"})
    r3_doc = await releases.find_one({"release_id": "r3"})
    
    statuses = {r2_doc["status"], r3_doc["status"]}
    # One must be ACTIVE (the winner), one must be APPROVED (the loser)
    assert statuses == {ReleaseStatus.ACTIVE, ReleaseStatus.APPROVED}
