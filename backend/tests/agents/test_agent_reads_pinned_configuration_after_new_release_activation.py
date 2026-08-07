import pytest
from unittest.mock import AsyncMock, MagicMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl
from return_platform.bootstrap.epoch import SimpleRuntimeEpoch

@pytest.mark.asyncio
async def test_agent_reads_pinned_configuration_after_new_release_activation():
    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db
    
    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(return_value={"release_id": "r1", "snapshot": {}, "checksum": "c1"})
    db.get_collection.return_value = releases_coll
    
    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)
    
    handle.set_current(SimpleRuntimeEpoch(2, "r2"), MagicMock(release_id="r2"))
    
    view = await handle.pinned("r1")
    assert view.release_id == "r1"
