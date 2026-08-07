import pytest
import asyncio
from pymongo import AsyncMongoClient
from return_platform.configuration.application.activation import ActivationService, ActivationConflictError
from return_platform.configuration.domain.release import ReleaseStatus

@pytest.mark.asyncio
async def test_concurrent_activation(mongodb_client: AsyncMongoClient[dict[str, object]]):
    service = ActivationService(mongodb_client)
    await service.initialize_indexes()
    
    # Setup test releases
    db = mongodb_client.get_database("platform")
    releases = db.get_collection("configuration_releases")
    pointer = db.get_collection("configuration_active_pointer")
    
    # clear state
    await releases.delete_many({})
    await pointer.delete_many({})
    
    await releases.insert_one({"release_id": "r1", "status": ReleaseStatus.APPROVED, "checksum": "c1"})
    await releases.insert_one({"release_id": "r2", "status": ReleaseStatus.APPROVED, "checksum": "c2"})
    
    # Activate r1
    await service.activate_release("r1")
    
    doc = await pointer.find_one({"_id": "active"})
    assert doc["release_id"] == "r1"
    assert doc["version"] == 1
    
    # Concurrent activation of r2 and r3 (using r1 as superseded)
    async def try_activate(rid: str):
        try:
            await service.activate_release(rid)
            return True
        except ActivationConflictError:
            return False
            
    await releases.insert_one({"release_id": "r3", "status": ReleaseStatus.APPROVED, "checksum": "c3"})
    
    results = await asyncio.gather(
        try_activate("r2"),
        try_activate("r3")
    )
    
    # Exactly one should succeed
    success_count = sum(1 for r in results if r)
    assert success_count == 1
    
    actives = await releases.count_documents({"status": ReleaseStatus.ACTIVE})
    assert actives == 1
    
    doc = await pointer.find_one({"_id": "active"})
    assert doc["version"] == 2
