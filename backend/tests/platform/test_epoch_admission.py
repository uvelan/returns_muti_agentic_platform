"""Focused check: epoch capture and lease acquisition are one atomic operation, leases
are tracked by unique identity rather than a bare count, the
CURRENT/DRAINING/RELEASING/RELEASED state machine is enforced, and a swap can never
regress the current epoch (design doc section 13.2).

A separate pointer object and lease-tracker object allows a request to read the
current epoch, and before it registers as a holder, a concurrent reconfiguration
swaps past it and releases that epoch's resources out from under it. EpochAdmission
makes "read current" and "register as holder" one atomic operation under one lock, so
that race is structurally impossible: there is no way to even express "read current"
separately from "become a holder of it." begin_swap() additionally fences on the
caller's expected current epoch, so a stale or out-of-order swap is rejected rather
than silently regressing the current epoch.

Holders are tracked by unique lease_id (a set per epoch), not a bare integer count. A
count cannot distinguish "this exact lease was released twice" from "two different
holders each released once" -- releasing the same lease twice would decrement twice,
letting the epoch appear drained while a genuinely different holder is still active.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    EpochStateError,
    SimpleRuntimeEpoch,
    StaleReconfiguration,
)
from return_platform.platform.contracts.epoch import RuntimeEpoch


def test_reader_captured_before_swap_keeps_the_old_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    captured_before = admission.current
    admission.begin_swap(epoch_2, epoch_1)

    assert captured_before is epoch_1
    assert admission.current is epoch_2


@pytest.mark.asyncio
async def test_concurrent_readers_never_observe_a_torn_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)
    observed: list[RuntimeEpoch] = []

    async def reader() -> None:
        for _ in range(50):
            observed.append(admission.current)
            await asyncio.sleep(0)

    async def swapper() -> None:
        await asyncio.sleep(0)
        admission.begin_swap(epoch_2, epoch_1)

    await asyncio.gather(*(reader() for _ in range(5)), swapper())

    assert all(value in (epoch_1, epoch_2) for value in observed)
    assert epoch_2 in observed


def test_capture_and_lease_is_atomic_during_swap() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    old_lease = admission.acquire_current()
    assert old_lease.epoch == epoch_1.epoch

    admission.begin_swap(epoch_2, epoch_1)

    # a new request admitted right after the swap must land on the NEW epoch, never
    # on the one that is now draining
    new_lease = admission.acquire_current()
    assert new_lease.epoch == epoch_2.epoch

    # epoch_1 cannot drain while old_lease is still outstanding
    assert admission.begin_release(epoch_1) is False

    admission.release(old_lease)

    # only once the sole lease has released does epoch_1 actually become releasable
    assert admission.begin_release(epoch_1) is True
    admission.finish_release(epoch_1)

    # releasing epoch_1's lease never touched epoch_2's lease
    admission.release(new_lease)


def test_new_lease_cannot_enter_draining_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    admission.begin_swap(epoch_2, epoch_1)
    acquired = admission.acquire_current()

    assert acquired.epoch == epoch_2.epoch


def test_current_epoch_cannot_be_released() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    admission = EpochAdmission(epoch_1)

    with pytest.raises(EpochStateError):
        admission.begin_release(epoch_1)


def test_duplicate_release_does_not_decrement_another_holder() -> None:
    """The exact scenario a bare per-epoch counter gets wrong: two independent
    requests hold the same epoch; releasing one twice must never be mistaken for
    releasing the other."""
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    lease_a = admission.acquire_current()
    lease_b = admission.acquire_current()
    admission.begin_swap(epoch_2, epoch_1)

    admission.release(lease_a)
    admission.release(lease_a)  # accidental duplicate release of the SAME lease

    # lease_b was never released -- epoch_1 must still be held
    assert admission.begin_release(epoch_1) is False

    admission.release(lease_b)
    assert admission.begin_release(epoch_1) is True


def test_epoch_cannot_release_while_any_unique_lease_remains() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    leases = [admission.acquire_current() for _ in range(5)]
    admission.begin_swap(epoch_2, epoch_1)

    for lease in leases[:-1]:
        admission.release(lease)
        assert admission.begin_release(epoch_1) is False  # at least one lease remains

    admission.release(leases[-1])
    assert admission.begin_release(epoch_1) is True  # every distinct lease is gone


def test_concurrent_lease_release_is_idempotent() -> None:
    """Many threads redundantly release the same set of leases, racing each other and
    themselves. Despite the redundancy, the epoch drains exactly once all distinct
    leases are genuinely released -- never early (a phantom double-release wrongly
    counted as two releases) and never stuck (a genuine release lost to a race).
    """
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    leases = [admission.acquire_current() for _ in range(50)]
    admission.begin_swap(epoch_2, epoch_1)

    def worker() -> None:
        for _ in range(5):
            for lease in leases:
                admission.release(lease)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert admission.begin_release(epoch_1) is True


def test_begin_swap_requires_expected_current_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    admission = EpochAdmission(epoch_1)

    wrong_expectation = SimpleRuntimeEpoch(epoch=99, release_id="r99")

    with pytest.raises(StaleReconfiguration):
        admission.begin_swap(epoch_2, wrong_expectation)

    # the failed swap changed nothing
    assert admission.current is epoch_1


def test_older_reconfiguration_cannot_replace_newer_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    epoch_3 = SimpleRuntimeEpoch(epoch=3, release_id="r3")
    admission = EpochAdmission(epoch_1)

    # a "newer" reconfiguration completes first
    admission.begin_swap(epoch_3, epoch_1)
    assert admission.current is epoch_3

    # an "older" attempt -- still holding its stale view of what was current, and
    # even targeting an epoch number that is not newer than the current one -- must
    # never be allowed to regress the pointer
    with pytest.raises(StaleReconfiguration):
        admission.begin_swap(epoch_2, epoch_1)

    assert admission.current is epoch_3
