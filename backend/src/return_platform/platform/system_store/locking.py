"""Fenced leasing for system-store bootstrap (design doc §13.7).

A TTL lock with only an owner ID is not sufficient: a migration slower than the TTL lets
a second instance acquire the lock and run the same migration concurrently while the
first instance is still working. `FencedLease` adds a monotonically increasing
`fencing_token` alongside the lease identity, and `FencedLeaseManager` runs a background
heartbeat that renews the lease at a fraction of its TTL. If renewal ever fails — the
lease expired, or another holder's fencing_token has superseded it — the manager raises
`LeaseLost` at the *next* protected operation rather than silently continuing; it does
not retry or attempt to finish whatever the caller was doing under the old lease.

Every guarded write (`platform/system_store/mongo.py`'s `FencedMongoTransactionGuard`) is
itself a conditional mutation on `(lock_name, lease_id, fencing_token)`, so even a
paused-then-resumed stale holder is rejected at the store — `ensure_alive()` closes the
common-case window early, but the store-level guard is what makes staleness impossible
to miss.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FencedLease:
    lock_name: str
    lease_id: str
    owner_instance_id: str
    fencing_token: int
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class LeaseUnavailable(RuntimeError):
    """Raised by `LeaseStore.acquire()` when another holder's lease has not expired."""


class LeaseLost(RuntimeError):
    """Raised when a heartbeat or guarded write finds the lease no longer current --
    either it expired and was reclaimed, or fencing_token was superseded."""


class LeaseStore(Protocol):
    async def acquire(
        self, lock_name: str, *, owner_instance_id: str, ttl_seconds: float
    ) -> FencedLease: ...

    async def heartbeat(self, lease: FencedLease, *, ttl_seconds: float) -> FencedLease: ...

    async def release(self, lease: FencedLease) -> None: ...


class FencedLeaseManager:
    """Acquires a fenced lease and keeps it alive with a background heartbeat.

    Usage::

        async with FencedLeaseManager(store, "system_store_bootstrap", owner_instance_id=iid) as mgr:
            for structure in structures:
                mgr.ensure_alive()
                ... perform structure creation, guarded on mgr.current_lease.fencing_token ...

    `ensure_alive()` must be called before every protected step. It is a fast in-memory
    check, not a store round-trip -- it catches the common case early, but the store-level
    guard on every write is the actual correctness boundary (see module docstring).
    """

    def __init__(
        self,
        store: LeaseStore,
        lock_name: str,
        *,
        owner_instance_id: str,
        ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._lock_name = lock_name
        self._owner_instance_id = owner_instance_id
        self._ttl_seconds = ttl_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds or (ttl_seconds / 3)
        self._lease: FencedLease | None = None
        self._lost: asyncio.Event = asyncio.Event()
        self._lost_reason: BaseException | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def current_lease(self) -> FencedLease:
        if self._lease is None:
            raise RuntimeError("FencedLeaseManager is not active -- use 'async with'")
        return self._lease

    def ensure_alive(self) -> None:
        """Raise LeaseLost immediately if the background heartbeat has already failed."""
        if self._lost.is_set():
            assert self._lost_reason is not None
            raise LeaseLost(str(self._lost_reason)) from self._lost_reason

    async def __aenter__(self) -> FencedLeaseManager:
        self._lease = await self._store.acquire(
            self._lock_name,
            owner_instance_id=self._owner_instance_id,
            ttl_seconds=self._ttl_seconds,
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._lease is not None:
            await self._store.release(self._lease)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval_seconds)
                assert self._lease is not None
                self._lease = await self._store.heartbeat(
                    self._lease, ttl_seconds=self._ttl_seconds
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any failure aborts the lease, by design
            self._lost_reason = exc
            self._lost.set()


async def bounded_retry_with_jitter[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    is_retryable: Callable[[BaseException], bool],
) -> T:
    """Retry `fn` with exponential backoff and full jitter, but only for exceptions
    `is_retryable` accepts -- an infrastructure-contention class (a genuine Mongo
    transient-transaction error, a lock briefly held by another instance), never an
    unknown exception treated as if it were transient. Used for both 3R.1's
    transaction-conflict retry and 3R.8's bootstrap-waiter backoff, so both share one
    reviewed implementation of "no tight polling, no thundering herd, bounded attempts."
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            await asyncio.sleep(random.uniform(0, delay))
