"""Focused check: release_epoch is never called while a request still holds that
epoch, and a module cleanup failure never finalizes the epoch as RELEASED on
unverified success (design doc section 13.2).
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


class FlakyReleaseModule:
    """Fails release_epoch a configured number of times before succeeding."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self._fail_times = fail_times
        self.release_attempts = 0
        self.released: list[int] = []

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        return ReconfigureOutcome.READY

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        self.release_attempts += 1
        if self.release_attempts <= self._fail_times:
            raise RuntimeError("pool close failed")
        self.released.append(epoch.epoch)


@pytest.mark.asyncio
async def test_release_epoch_waits_for_the_lease_to_drain() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module = FakeModule()
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator({"module": module}, admission)

    held = admission.acquire_current()  # a request is mid-flight on the old epoch
    await coordinator.reconfigure(new_epoch)

    released_while_held = await coordinator.release_if_drained(old_epoch)
    assert released_while_held is False
    assert module.released == []

    admission.release(held)
    released_after_drain = await coordinator.release_if_drained(old_epoch)
    assert released_after_drain is True
    assert module.released == [1]


@pytest.mark.asyncio
async def test_release_failure_does_not_finalize_epoch() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module_a = FakeModule()
    module_b = FlakyReleaseModule(fail_times=1)
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator({"a": module_a, "b": module_b}, admission)

    await coordinator.reconfigure(new_epoch)

    with pytest.raises(ExceptionGroup):
        await coordinator.release_if_drained(old_epoch)

    # module_a's release succeeded, but the epoch is NOT finalized as RELEASED --
    # module_b's failure keeps it retryable in RELEASING, not silently marked done.
    assert module_a.released == [1]
    assert admission.begin_release(old_epoch) is True  # still RELEASING, never RELEASED


@pytest.mark.asyncio
async def test_release_cleanup_can_be_retried() -> None:
    old_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    new_epoch = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    module_a = FakeModule()
    module_b = FlakyReleaseModule(fail_times=1)  # fails once, then succeeds
    admission = EpochAdmission(old_epoch)
    coordinator = ReconfigurationCoordinator({"a": module_a, "b": module_b}, admission)

    await coordinator.reconfigure(new_epoch)

    with pytest.raises(ExceptionGroup):
        await coordinator.release_if_drained(old_epoch)

    # retry: module_b's second attempt succeeds, and the release now completes
    retried = await coordinator.release_if_drained(old_epoch)
    assert retried is True
    assert module_b.released == [1]
    # module_a is called again on retry even though it already succeeded --
    # release_epoch is documented as must-not-fail / idempotent for exactly this
    assert module_a.released == [1, 1]
