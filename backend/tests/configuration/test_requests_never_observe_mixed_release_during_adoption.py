import asyncio

import pytest

from return_platform.bootstrap.epoch import (
    EpochAdmission,
    EpochAllocator,
    ReconfigurationCoordinator,
    SimpleRuntimeEpoch,
)
from return_platform.platform.modules.contracts import ReconfigureOutcome


@pytest.mark.asyncio
async def test_requests_never_observe_mixed_release_during_adoption():
    initial_epoch = SimpleRuntimeEpoch(epoch=1, release_id="r1")
    admission = EpochAdmission(initial_epoch)

    class MockModuleWithResources:
        def __init__(self, name: str):
            self.name = name
            self.resources_by_epoch = {1: f"{name}_resource_r1"}

        async def prepare_reconfigure(self, epoch):
            return ReconfigureOutcome.READY

        async def commit_reconfigure(self, epoch):
            # Candidate resource for epoch becomes committed/addressable
            self.resources_by_epoch[epoch.epoch] = f"{self.name}_resource_{epoch.release_id}"

        async def abort_reconfigure(self, epoch):
            self.resources_by_epoch.pop(epoch.epoch, None)

        async def release_epoch(self, epoch):
            self.resources_by_epoch.pop(epoch.epoch, None)

        def release_for(self, lease) -> str:
            res = self.resources_by_epoch.get(lease.epoch, "UNKNOWN")
            return res.split("_")[-1]

    mod_a = MockModuleWithResources("ModuleA")
    mod_b = MockModuleWithResources("ModuleB")

    coordinator = ReconfigurationCoordinator({"mod_a": mod_a, "mod_b": mod_b}, admission)

    stop_event = asyncio.Event()
    violations = []
    reads_count = 0

    async def worker():
        nonlocal reads_count
        while not stop_event.is_set():
            lease = admission.acquire_current()
            try:
                rel_a = mod_a.release_for(lease)
                await asyncio.sleep(0.001)
                rel_b = mod_b.release_for(lease)

                if rel_a != rel_b:
                    violations.append((rel_a, rel_b))
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
