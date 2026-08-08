"""FencedLeaseManager: background heartbeat renews the lease, and a heartbeat failure
aborts the holder immediately at its next protected operation rather than letting it
carry on with a stale lease (design doc §13.7)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from return_platform.platform.system_store.locking import (
    FencedLease,
    FencedLeaseManager,
    LeaseLost,
)


def _now() -> datetime:
    return datetime.now(UTC)


class _FakeLeaseStore:
    """In-memory LeaseStore whose heartbeat behaviour is scripted per test."""

    def __init__(self) -> None:
        self.heartbeat_calls = 0
        self.released: list[FencedLease] = []
        self.fail_heartbeat_after: int | None = None
        self.heartbeat_called = asyncio.Event()

    async def acquire(
        self, lock_name: str, *, owner_instance_id: str, ttl_seconds: float
    ) -> FencedLease:
        now = _now()
        return FencedLease(
            lock_name=lock_name,
            lease_id="lease-1",
            owner_instance_id=owner_instance_id,
            fencing_token=1,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    async def heartbeat(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease:
        self.heartbeat_calls += 1
        self.heartbeat_called.set()
        if (
            self.fail_heartbeat_after is not None
            and self.heartbeat_calls >= self.fail_heartbeat_after
        ):
            raise LeaseLost("fencing_token superseded")
        now = _now()
        return replace(lease, heartbeat_at=now, expires_at=now + timedelta(seconds=ttl_seconds))

    async def release(self, lease: FencedLease) -> None:
        self.released.append(lease)


@pytest.mark.asyncio
async def test_heartbeat_renews_the_lease_and_keeps_fencing_token_stable() -> None:
    store = _FakeLeaseStore()
    async with FencedLeaseManager(
        store, "lock", owner_instance_id="owner-a", ttl_seconds=0.3, heartbeat_interval_seconds=0.05
    ) as manager:
        original = manager.current_lease
        await asyncio.wait_for(store.heartbeat_called.wait(), timeout=2.0)
        # Give the loop a moment to complete at least one more full cycle.
        await asyncio.sleep(0.15)
        manager.ensure_alive()
        renewed = manager.current_lease

    assert store.heartbeat_calls >= 1
    assert renewed.fencing_token == original.fencing_token
    assert renewed.lease_id == original.lease_id
    assert renewed.heartbeat_at >= original.heartbeat_at


@pytest.mark.asyncio
async def test_heartbeat_failure_aborts_the_holder_at_the_next_protected_operation() -> None:
    store = _FakeLeaseStore()
    store.fail_heartbeat_after = 1

    async with FencedLeaseManager(
        store, "lock", owner_instance_id="owner-a", ttl_seconds=0.3, heartbeat_interval_seconds=0.05
    ) as manager:
        await asyncio.wait_for(store.heartbeat_called.wait(), timeout=2.0)
        # Give the failing heartbeat time to actually record the loss.
        await asyncio.sleep(0.1)
        with pytest.raises(LeaseLost):
            manager.ensure_alive()

    # release() is still called in __aexit__ -- the manager does not skip cleanup
    # just because the lease was already lost.
    assert len(store.released) == 1


@pytest.mark.asyncio
async def test_ensure_alive_is_a_no_op_while_the_lease_is_healthy() -> None:
    store = _FakeLeaseStore()
    async with FencedLeaseManager(
        store, "lock", owner_instance_id="owner-a", ttl_seconds=30.0
    ) as manager:
        manager.ensure_alive()
        manager.ensure_alive()


@pytest.mark.asyncio
async def test_current_lease_raises_outside_the_context_manager() -> None:
    store = _FakeLeaseStore()
    manager = FencedLeaseManager(store, "lock", owner_instance_id="owner-a", ttl_seconds=30.0)
    with pytest.raises(RuntimeError):
        _ = manager.current_lease
