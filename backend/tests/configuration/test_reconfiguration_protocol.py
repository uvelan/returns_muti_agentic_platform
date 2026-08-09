from unittest.mock import AsyncMock

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.platform.modules.contracts import ReconfigureOutcome


@pytest.mark.asyncio
async def test_reconfiguration_protocol():
    m1 = AsyncMock()
    m2 = AsyncMock()

    m1.prepare_reconfigure.return_value = ReconfigureOutcome.READY
    m2.prepare_reconfigure.return_value = ReconfigureOutcome.READY

    admission = EpochAdmission(SimpleRuntimeEpoch(1, "r1"))
    coordinator = ReconfigurationCoordinator({"m1": m1, "m2": m2}, admission)

    new_epoch = SimpleRuntimeEpoch(2, "r2")
    retired = await coordinator.reconfigure(new_epoch)

    assert retired.epoch == 1
    assert admission.current.epoch == 2

    m1.prepare_reconfigure.assert_called_once_with(new_epoch)
    m2.prepare_reconfigure.assert_called_once_with(new_epoch)
    m1.commit_reconfigure.assert_called_once_with(new_epoch)
    m2.commit_reconfigure.assert_called_once_with(new_epoch)

    m1.abort_reconfigure.assert_not_called()
    m2.abort_reconfigure.assert_not_called()
