import pytest
from unittest.mock import AsyncMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl
from return_platform.platform.contracts.epoch import RuntimeEpoch

@pytest.mark.asyncio
async def test_agent_reads_pinned_configuration_after_new_release_activation():
    # Test that a pinned session always resolves its initial release
    client = AsyncMock()
    db = AsyncMock()
    client.get_database.return_value = db
    
    releases_coll = AsyncMock()
    releases_coll.find_one.return_value = {"release_id": "r1", "snapshot": {}, "checksum": "c1"}
    db.get_collection.return_value = releases_coll
    
    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)
    
    # Simulate current is r2
    handle.set_current(RuntimeEpoch(2, "r2"), AsyncMock(release_id="r2"))
    
    # Agent pinned to r1
    view = await handle.pinned("r1")
    assert view.release_id == "r1"
