import pytest
from unittest.mock import AsyncMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl

@pytest.mark.asyncio
async def test_pinned_release_resolves_after_handle_recreation():
    client = AsyncMock()
    db = AsyncMock()
    client.get_database.return_value = db
    
    releases_coll = AsyncMock()
    releases_coll.find_one.return_value = {"release_id": "r1", "snapshot": {}, "checksum": "c1"}
    db.get_collection.return_value = releases_coll
    
    def load_snapshot_fn(snap):
        return snap
        
    # First handle (simulating before restart)
    handle1 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)
    
    # Simulate restart
    handle2 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)
    
    # Even though _views is empty, pinned() should fetch from db
    assert not handle2._views
    
    view = await handle2.pinned("r1")
    assert view.release_id == "r1"
    assert view.checksum == "c1"
    assert "r1" in handle2._views
