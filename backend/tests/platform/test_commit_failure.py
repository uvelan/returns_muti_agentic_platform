"""Focused check: a commit_reconfigure failure is fail-closed, not a recoverable
exception (design doc section 13.2).

"If a commit nevertheless raises, the replica marks itself UNAVAILABLE and requires
restart rather than serving a partial promotion." Some modules may already have made
their candidate addressable under the new epoch when a later module's commit raises --
there is no safe rollback, so the replica must stop accepting new work rather than let
a caller catch the exception and continue serving.
"""

from __future__ import annotations

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    FatalReconfigurationError,
    ReconfigurationCoordinator,
    ReplicaStatus,
    SimpleRuntimeEpoch,
)
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome


class RecordingModule:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self._fail_commit = fail_commit
        self.committed: list[int] = []

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        return ReconfigureOutcome.READY

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        if self._fail_commit:
            raise RuntimeError("pool promotion failed")
        self.committed.append(epoch.epoch)

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        pass


def _coordinator() -> tuple[ReconfigurationCoordinator, EpochAdmission, RecordingModule]:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    module_a = RecordingModule()
    module_b = RecordingModule(fail_commit=True)
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator({"a": module_a, "b": module_b}, admission)
    return coordinator, admission, module_a


@pytest.mark.asyncio
async def test_commit_failure_marks_replica_unavailable() -> None:
    coordinator, _admission, _module_a = _coordinator()
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")

    with pytest.raises(FatalReconfigurationError):
        await coordinator.reconfigure(new_epoch)

    assert coordinator.status is ReplicaStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_commit_failure_never_swaps_epoch() -> None:
    coordinator, admission, module_a = _coordinator()
    old_epoch = admission.current
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")

    with pytest.raises(FatalReconfigurationError):
        await coordinator.reconfigure(new_epoch)

    # module_a committed before module_b's failure -- the pointer must still not move
    assert module_a.committed == [2]
    assert admission.current is old_epoch


@pytest.mark.asyncio
async def test_no_request_admission_after_fatal_commit_failure() -> None:
    coordinator, _admission, _module_a = _coordinator()
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")

    with pytest.raises(FatalReconfigurationError):
        await coordinator.reconfigure(new_epoch)

    with pytest.raises(FatalReconfigurationError):
        coordinator.acquire_current()

    # a second reconfiguration attempt is also refused -- the replica requires restart
    with pytest.raises(FatalReconfigurationError):
        await coordinator.reconfigure(SimpleRuntimeEpoch(epoch=3, release_id="r3"))
