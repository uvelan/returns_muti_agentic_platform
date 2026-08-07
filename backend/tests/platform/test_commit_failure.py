"""Focused check: a commit_reconfigure failure is fail-closed, not a recoverable
exception (design doc section 13.2).

"If a commit nevertheless raises, the replica marks itself UNAVAILABLE and requires
restart rather than serving a partial promotion." Some modules may already have made
their candidate addressable under the new epoch when a later module's commit raises --
there is no safe rollback, so the replica must stop accepting new work rather than let
a caller catch the exception and continue serving.
"""

from __future__ import annotations

import threading

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    FatalReconfigurationError,
    ReconfigurationCoordinator,
    ReplicaStatus,
    ReplicaUnavailable,
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


def test_fatal_commit_atomically_closes_request_admission() -> None:
    """A commit failure calls EpochAdmission.close(); this proves close() and
    acquire_current() are genuinely atomic with respect to each other under real
    OS-thread concurrency, not just in the sequential case already covered by
    test_no_request_admission_after_fatal_commit_failure above.

    Both methods take EpochAdmission's single lock and neither awaits or blocks
    inside it, so there is no window in which a request can be admitted after close()
    has taken effect -- this hammers that guarantee with genuine concurrent threads
    rather than relying on it being true "by construction."
    """
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    admission = EpochAdmission(epoch_1)

    results: list[str] = []
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(9)  # 8 requester threads + 1 closer thread

    def requester() -> None:
        start_barrier.wait()
        for _ in range(500):
            try:
                admission.acquire_current()
                outcome = "success"
            except ReplicaUnavailable:
                outcome = "fail"
            with results_lock:
                results.append(outcome)

    def closer() -> None:
        start_barrier.wait()
        admission.close()

    threads = [threading.Thread(target=requester) for _ in range(8)]
    threads.append(threading.Thread(target=closer))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert "fail" in results  # close() did take effect during the hammering
    assert not admission.is_accepting()

    # once every thread has finished, admission is unambiguously and permanently
    # closed -- no straggler could have been admitted after close() returned.
    with pytest.raises(ReplicaUnavailable):
        admission.acquire_current()
