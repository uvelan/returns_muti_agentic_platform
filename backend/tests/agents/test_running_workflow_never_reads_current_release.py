import pytest
from unittest.mock import AsyncMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl
from return_platform.platform.contracts.epoch import RuntimeEpoch

@pytest.mark.asyncio
async def test_running_workflow_never_reads_current_release():
    client = AsyncMock()
    db = AsyncMock()
    client.get_database.return_value = db
    
    releases_coll = AsyncMock()
    releases_coll.find_one.return_value = {"release_id": "r1", "snapshot": {}, "checksum": "c1"}
    db.get_collection.return_value = releases_coll
    
    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)
    
    # Current release updates continuously over time
    for i in range(1, 10):
        handle.set_current(RuntimeEpoch(i, f"r{i}"), AsyncMock(release_id=f"r{i}"))
        
    # The workflow which started on r1 calls pinned("r1") and gets r1 no matter what
    view = await handle.pinned("r1")
    assert view.release_id == "r1"
