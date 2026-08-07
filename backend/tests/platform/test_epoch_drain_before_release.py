"""Focused check: release_epoch is never called while a request still holds that
epoch (design doc section 13.2).
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


class FakeModule:
    def __init__(self) -> None:
        self.released: list[int] = []

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        return ReconfigureOutcome.READY

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        self.released.append(epoch.epoch)


@pytest.mark.asyncio
async def test_release_epoch_waits_for_the_lease_to_drain() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module = FakeModule()
    pointer = EpochPointer(old_epoch)
    leases = EpochLeaseTracker()
    coordinator = ReconfigurationCoordinator([module], pointer, leases)

    leases.acquire(old_epoch)  # a request is mid-flight on the old epoch
    await coordinator.reconfigure(new_epoch)

    released_while_held = await coordinator.release_if_drained(old_epoch)
    assert released_while_held is False
    assert module.released == []

    leases.release(old_epoch)
    released_after_drain = await coordinator.release_if_drained(old_epoch)
    assert released_after_drain is True
    assert module.released == [1]
