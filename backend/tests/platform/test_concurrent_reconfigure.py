"""Focused check: concurrent reconfigure() calls on the same coordinator never
interleave their prepare/commit phases (design doc section 13.2).

prepare/commit each span multiple `await` points. Without serialization, two attempts
targeting different epochs could interleave their commits and have the OLDER one's
swap land last, regressing the current epoch. The deterministic regression-prevention
guarantee itself is proven at the EpochAdmission level in
test_epoch_admission.py::test_older_reconfiguration_cannot_replace_newer_epoch; this
test proves the coordinator's asyncio.Lock is what stops the two attempts from ever
running their prepare/commit phases concurrently in the first place.
"""

from __future__ import annotations

import asyncio

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.modules.contracts import ReconfigureOutcome


class RecordingModule:
    """Yields control mid-prepare and mid-commit so a lock-free coordinator would have
    a real chance to interleave two concurrent reconfigure() calls."""

    def __init__(self, log: list[str], name: str) -> None:
        self._log = log
        self._name = name

    async def prepare_reconfigure(self, epoch: RuntimeEpoch) -> ReconfigureOutcome:
        self._log.append(f"prepare:{self._name}:{epoch.epoch}:start")
        await asyncio.sleep(0)
        self._log.append(f"prepare:{self._name}:{epoch.epoch}:end")
        return ReconfigureOutcome.READY

    async def commit_reconfigure(self, epoch: RuntimeEpoch) -> None:
        self._log.append(f"commit:{self._name}:{epoch.epoch}:start")
        await asyncio.sleep(0)
        self._log.append(f"commit:{self._name}:{epoch.epoch}:end")

    async def abort_reconfigure(self, epoch: RuntimeEpoch) -> None:
        pass

    async def release_epoch(self, epoch: RuntimeEpoch) -> None:
        pass


@pytest.mark.asyncio
async def test_concurrent_reconfigurations_are_serialized() -> None:
    epoch_1 = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    epoch_2 = SimpleRuntimeEpoch(epoch=2, release_id="r2")
    epoch_3 = SimpleRuntimeEpoch(epoch=3, release_id="r3")
    log: list[str] = []
    module = RecordingModule(log, "a")
    admission = EpochAdmission(epoch_1)
    coordinator = ReconfigurationCoordinator({"a": module}, admission)

    results = await asyncio.gather(
        coordinator.reconfigure(epoch_2),
        coordinator.reconfigure(epoch_3),
        return_exceptions=True,
    )

    # regardless of which order the lock let the two attempts run in, one attempt's
    # prepare/commit phase always runs to completion (its "start" immediately followed
    # by its own "end") before the other's phase begins -- never interleaved.
    assert len(log) % 2 == 0
    for index in range(0, len(log), 2):
        start_key = log[index].rsplit(":", 1)[0]
        end_key = log[index + 1].rsplit(":", 1)[0]
        assert start_key == end_key, f"interleaved reconfiguration phases: {log}"

    # at least one attempt succeeded (whichever ran first always does; the second
    # either also succeeds, if its target is still newer than the epoch the first one
    # produced, or is correctly rejected as StaleReconfiguration rather than being
    # allowed to regress the pointer).
    successes = [result for result in results if not isinstance(result, BaseException)]
    assert len(successes) >= 1
    assert admission.current.epoch in (2, 3)
