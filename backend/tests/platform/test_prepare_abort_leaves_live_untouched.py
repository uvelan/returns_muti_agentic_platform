"""Focused check: a late RESTART_REQUIRED, or a prepare_reconfigure exception, aborts
EVERY module -- not just the ones that already prepared successfully -- and promotes
nothing (design doc section 13.2).

A module can allocate real resources before it decides to refuse, or before it fails
outright, so the refusing/failing module itself can have cleanup work. abort_reconfigure
must therefore be called on every module for the candidate epoch, including the one
that never returned READY.
"""

from __future__ import annotations

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome


class RecordingModule:
    def __init__(self, outcome: ReconfigureOutcome, *, raises: bool = False) -> None:
        self._outcome = outcome
        self._raises = raises
        self.prepared: list[int] = []
        self.committed: list[int] = []
        self.aborted: list[int] = []

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        self.prepared.append(epoch.epoch)
        if self._raises:
            raise RuntimeError("candidate pool allocation failed")
        return self._outcome

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        self.committed.append(epoch.epoch)

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        self.aborted.append(epoch.epoch)

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        pass


@pytest.mark.asyncio
async def test_late_restart_required_aborts_every_module() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module_a = RecordingModule(ReconfigureOutcome.READY)
    module_b = RecordingModule(ReconfigureOutcome.READY)
    module_c = RecordingModule(ReconfigureOutcome.RESTART_REQUIRED)
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator(
        {"a": module_a, "b": module_b, "c": module_c}, admission
    )

    result = await coordinator.reconfigure(new_epoch)

    assert result is None
    assert module_a.aborted == [2]
    assert module_b.aborted == [2]
    # module_c itself refused -- it may still have allocated candidate resources
    # before deciding to refuse, so it gets an abort call too, not just A and B.
    assert module_c.aborted == [2]
    assert module_a.committed == []
    assert module_b.committed == []
    assert admission.current is old_epoch


@pytest.mark.asyncio
async def test_prepare_exception_aborts_every_module_and_reraises() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module_a = RecordingModule(ReconfigureOutcome.READY)
    module_b = RecordingModule(ReconfigureOutcome.READY)
    module_c = RecordingModule(ReconfigureOutcome.READY, raises=True)
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator(
        {"a": module_a, "b": module_b, "c": module_c}, admission
    )

    with pytest.raises(RuntimeError, match="candidate pool allocation failed"):
        await coordinator.reconfigure(new_epoch)

    assert module_a.aborted == [2]
    assert module_b.aborted == [2]
    assert module_c.aborted == [2]
    assert module_a.committed == []
    assert module_b.committed == []
    assert admission.current is old_epoch
