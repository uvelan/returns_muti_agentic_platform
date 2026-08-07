import pytest
from unittest.mock import AsyncMock, MagicMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl

@pytest.mark.asyncio
async def test_pinned_release_resolves_after_handle_recreation():
    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db
    
    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(return_value={"release_id": "r1", "snapshot": {}, "checksum": "c1"})
    db.get_collection.return_value = releases_coll
    
    def load_snapshot_fn(snap):
        return snap
        
    handle1 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)
    handle2 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)
    
    assert not handle2._views
    
    view = await handle2.pinned("r1")
    assert view.release_id == "r1"
    assert view.checksum == "c1"
    assert "r1" in handle2._views
