import pytest
import asyncio
from unittest.mock import AsyncMock
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.bootstrap.epoch import EpochAdmission

@pytest.mark.asyncio
async def test_requests_never_observe_mixed_release_during_adoption():
    client = AsyncMock()
    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)
    
    epoch1 = RuntimeEpoch(1, "r1")
    epoch2 = RuntimeEpoch(2, "r2")
    
    view1 = AsyncMock(release_id="r1")
    view2 = AsyncMock(release_id="r2")
    
    handle.set_current(epoch1, view1)
    handle.set_current(epoch2, view2)
    
    admission = EpochAdmission(epoch1)
    
    # Request acquires lease on epoch1
    lease1 = admission.acquire_current()
    
    # Epoch swaps to epoch2
    admission.begin_swap(epoch2, epoch1)
    
    # New request acquires lease on epoch2
    lease2 = admission.acquire_current()
    
    # The first request must still observe r1
    req1_view = handle.current(lease1.epoch)
    assert req1_view.release_id == "r1"
    
    # The second request observes r2
    req2_view = handle.current(lease2.epoch)
    assert req2_view.release_id == "r2"
