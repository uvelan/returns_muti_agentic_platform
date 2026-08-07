import pytest
import asyncio
from unittest.mock import AsyncMock
from return_platform.bootstrap.epoch import (
    EpochAdmission,
    SimpleRuntimeEpoch,
    EpochAllocator,
    ReconfigurationCoordinator
)
from return_platform.configuration.application.runtime_configuration import (
    RuntimeConfigurationHandleImpl
)
from return_platform.platform.modules.contracts import ReconfigureOutcome

@pytest.mark.asyncio
async def test_requests_never_observe_mixed_release_during_adoption():
    client = AsyncMock()
    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)

    initial_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    admission = EpochAdmission(initial_epoch)

    class MockModule:
        def __init__(self, name):
            self.name = name

        async def prepare_reconfigure(self, epoch):
            return ReconfigureOutcome.READY

        async def commit_reconfigure(self, epoch):
            view = AsyncMock(release_id=epoch.release_id)
            handle.set_current(epoch, view)

        async def abort_reconfigure(self, epoch):
            pass

        async def release_epoch(self, epoch):
            pass

    mod_a = MockModule("ModuleA")
    mod_b = MockModule("ModuleB")

    coordinator = ReconfigurationCoordinator(
        {"mod_a": mod_a, "mod_b": mod_b}, admission
    )

    view1 = AsyncMock(release_id="r1")
    handle.set_current(initial_epoch, view1)

    stop_event = asyncio.Event()
    violations = []
    reads_count = 0

    async def worker():
        nonlocal reads_count
        while not stop_event.is_set():
            lease = admission.acquire_current()
            try:
                view_a = handle.current(lease)
                await asyncio.sleep(0.001)
                view_b = handle.current(lease)

                if view_a.release_id != view_b.release_id:
                    violations.append((view_a.release_id, view_b.release_id))
                reads_count += 1
            finally:
                admission.release(lease)

    workers = [asyncio.create_task(worker()) for _ in range(10)]

    allocator = EpochAllocator(start=1)
    for i in range(2, 6):
        target_epoch = allocator.next(f"r{i}")
        await coordinator.reconfigure(target_epoch)
        await asyncio.sleep(0.01)

    stop_event.set()
    await asyncio.gather(*workers)

    assert reads_count > 0
    assert len(violations) == 0, f"Observed mixed release across modules: {violations}"
