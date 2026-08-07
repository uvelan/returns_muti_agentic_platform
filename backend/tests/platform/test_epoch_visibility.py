"""Focused check: concurrent readers during an epoch swap observe exactly one epoch,
never a torn value (design doc section 13.2).
"""

from __future__ import annotations

import asyncio

import pytest

from return_platform.bootstrap.epoch import EpochPointer, SimpleRuntimeEpoch
from return_platform.platform.contracts.epoch import RuntimeEpoch


@pytest.mark.asyncio
async def test_reader_captured_before_swap_keeps_the_old_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    pointer = EpochPointer(epoch_1)

    captured_before = pointer.current
    pointer.swap(epoch_2)

    assert captured_before is epoch_1
    assert pointer.current is epoch_2


@pytest.mark.asyncio
async def test_concurrent_readers_never_observe_a_torn_epoch() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    pointer = EpochPointer(epoch_1)
    observed: list[RuntimeEpoch] = []

    async def reader() -> None:
        for _ in range(50):
            observed.append(pointer.current)
            await asyncio.sleep(0)

    async def swapper() -> None:
        await asyncio.sleep(0)
        pointer.swap(epoch_2)

    await asyncio.gather(*(reader() for _ in range(5)), swapper())

    assert all(value in (epoch_1, epoch_2) for value in observed)
    assert epoch_2 in observed
