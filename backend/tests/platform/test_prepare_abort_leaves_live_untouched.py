"""Focused check: a late RESTART_REQUIRED aborts every earlier module and promotes
nothing (design doc section 13.2).
"""

from __future__ import annotations

import pytest

from return_platform.bootstrap.epoch import (
    EpochLeaseTracker,
    EpochPointer,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome


class RecordingModule:
    def __init__(self, outcome: ReconfigureOutcome) -> None:
        self._outcome = outcome
        self.prepared: list[int] = []
        self.committed: list[int] = []
        self.aborted: list[int] = []

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        self.prepared.append(epoch.epoch)
        return self._outcome

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        self.committed.append(epoch.epoch)

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        self.aborted.append(epoch.epoch)

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        pass


@pytest.mark.asyncio
async def test_late_restart_required_aborts_every_earlier_module() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module_a = RecordingModule(ReconfigureOutcome.READY)
    module_b = RecordingModule(ReconfigureOutcome.READY)
    module_c = RecordingModule(ReconfigureOutcome.RESTART_REQUIRED)
    pointer = EpochPointer(old_epoch)
    coordinator = ReconfigurationCoordinator(
        [module_a, module_b, module_c], pointer, EpochLeaseTracker()
    )

    result = await coordinator.reconfigure(new_epoch)

    assert result is None
    assert module_a.aborted == [2]
    assert module_b.aborted == [2]
    assert module_c.aborted == []  # never prepared successfully; nothing to abort
    assert module_a.committed == []
    assert module_b.committed == []
    assert pointer.current is old_epoch
