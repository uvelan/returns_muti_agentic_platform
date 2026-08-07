import pytest
import asyncio
from unittest.mock import AsyncMock
from return_platform.bootstrap.epoch import ReconfigurationCoordinator, EpochAdmission, RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome

@pytest.mark.asyncio
async def test_epoch_not_visible_before_all_module_commits():
    # Ensures the new epoch isn't visible on EpochAdmission until after all commits
    m1 = AsyncMock()
    m2 = AsyncMock()
    
    m1.prepare_reconfigure.return_value = ReconfigureOutcome.READY
    m2.prepare_reconfigure.return_value = ReconfigureOutcome.READY
    
    admission = EpochAdmission(RuntimeEpoch(1, "r1"))
    coordinator = ReconfigurationCoordinator({"m1": m1, "m2": m2}, admission)
    
    new_epoch = RuntimeEpoch(2, "r2")
    
    # We want to capture admission.current state *during* the commit phase
    async def mock_commit_m2(*args, **kwargs):
        assert admission.current.epoch == 1
        
    m2.commit_reconfigure.side_effect = mock_commit_m2
    
    await coordinator.reconfigure(new_epoch)
    
    # After reconfiguration is fully done, epoch is swapped
    assert admission.current.epoch == 2
