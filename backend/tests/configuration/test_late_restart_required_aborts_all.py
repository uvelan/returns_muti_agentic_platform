import pytest
from unittest.mock import AsyncMock
from return_platform.bootstrap.epoch import ReconfigurationCoordinator, EpochAdmission, SimpleRuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome

@pytest.mark.asyncio
async def test_late_restart_required_aborts_all():
    m1 = AsyncMock()
    m2 = AsyncMock()
    
    # m1 prepares fine, m2 refuses
    m1.prepare_reconfigure.return_value = ReconfigureOutcome.READY
    m2.prepare_reconfigure.return_value = ReconfigureOutcome.RESTART_REQUIRED
    
    admission = EpochAdmission(SimpleRuntimeEpoch(1, "r1"))
    coordinator = ReconfigurationCoordinator({"m1": m1, "m2": m2}, admission)
    
    new_epoch = SimpleRuntimeEpoch(2, "r2")
    retired = await coordinator.reconfigure(new_epoch)
    
    assert retired is None
    assert admission.current.epoch == 1
    
    # Both should be aborted, even the one that succeeded preparation or failed
    m1.abort_reconfigure.assert_called_once_with(new_epoch)
    m2.abort_reconfigure.assert_called_once_with(new_epoch)
    
    m1.commit_reconfigure.assert_not_called()
    m2.commit_reconfigure.assert_not_called()
