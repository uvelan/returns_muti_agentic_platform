"""On-demand sync holds a write reservation, and refuses to write to a drain.

Slice 2 gave the read path a lease. The write path was the other half: on-demand
sync writes into the graph, and retirement could not see those writes at all.

Two things make the write case different from the read case, and both are
asserted here:

* **A refused reservation fails the sync.** A refused *read* degrades to
  unleased because serving from a generation on its way out is merely stale. A
  refused *write* means a successor is already ACTIVE and anything written here
  is discarded at retirement -- silently, while the sync reports success.
* **Nothing else catches it.** The Neo4j write fence reads the generation's
  *current* status and passes it as the expected one, so it rejects a status
  change mid-write but accepts a write to a DRAINING generation. Without the
  reservation there is no check at all.
"""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.lifecycle.handle import (
    GenerationDraining,
    GenerationHandleProvider,
)
from return_platform.dynamic_knowledge.lifecycle.lease_store import LeaseClass


class _Resolver:
    async def active_generation(self, schema: object) -> str:
        return "gen-1"


class _LeaseStore:
    def __init__(self, *, draining: bool = False, raise_on_acquire: bool = False) -> None:
        self.draining = draining
        self.raise_on_acquire = raise_on_acquire
        self.held: dict[str, LeaseClass] = {}
        self.released: list[str] = []

    async def acquire_read_lease(self, **kwargs: object) -> object | None:
        raise NotImplementedError

    async def acquire_write_reservation(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> object | None:
        if self.raise_on_acquire:
            raise RuntimeError("mongo unreachable")
        if self.draining:
            return None
        reservation_id = f"reservation-{len(self.held) + 1}"
        self.held[reservation_id] = LeaseClass.WRITE
        return type("_Reservation", (), {"reservation_id": reservation_id})()

    async def release(self, *, graph_generation_id: str, lease_id: str) -> None:
        self.released.append(lease_id)
        self.held.pop(lease_id, None)

    async def begin_drain(self, *, graph_generation_id: str) -> None:
        self.draining = True

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]:
        return {
            LeaseClass.READ: 0,
            LeaseClass.WRITE: sum(1 for c in self.held.values() if c is LeaseClass.WRITE),
        }


@pytest.mark.asyncio
async def test_a_write_is_counted_as_outstanding_while_it_runs() -> None:
    store = _LeaseStore()
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    async with provider.reserve_write("gen-1") as handle:
        assert handle.leased is True
        assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.WRITE] == 1

    assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.WRITE] == 0
    assert store.released == ["reservation-1"]


@pytest.mark.asyncio
async def test_a_failing_write_still_releases_its_reservation() -> None:
    store = _LeaseStore()
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    with pytest.raises(RuntimeError, match="source unreachable"):
        async with provider.reserve_write("gen-1"):
            raise RuntimeError("source unreachable")

    assert store.released == ["reservation-1"]


@pytest.mark.asyncio
async def test_writing_to_a_draining_generation_is_refused() -> None:
    """The whole point of the write side. Without this the write lands in a
    generation that is about to be deleted, and the sync reports success."""
    provider = GenerationHandleProvider(_Resolver(), lease_store=_LeaseStore(draining=True))

    with pytest.raises(GenerationDraining) as caught:
        async with provider.reserve_write("gen-1"):
            pytest.fail("the body must not run against a draining generation")

    assert caught.value.graph_generation_id == "gen-1"


@pytest.mark.asyncio
async def test_a_drain_that_starts_mid_write_does_not_orphan_the_reservation() -> None:
    """Retirement closing the generation while a write is in flight must still
    leave that write counted -- otherwise the drain sees zero and retires."""
    store = _LeaseStore()
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    async with provider.reserve_write("gen-1"):
        await store.begin_drain(graph_generation_id="gen-1")
        assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.WRITE] == 1

    assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.WRITE] == 0


@pytest.mark.asyncio
async def test_a_lease_store_outage_does_not_block_writes() -> None:
    """Unlike a refusal, an outage carries no information about the generation.
    Refusing to write because the bookkeeping is unavailable would take the
    platform down for a cleanup concern."""
    provider = GenerationHandleProvider(_Resolver(), lease_store=_LeaseStore(raise_on_acquire=True))

    async with provider.reserve_write("gen-1") as handle:
        assert handle.leased is False


@pytest.mark.asyncio
async def test_without_a_lease_store_writes_proceed_unreserved() -> None:
    provider = GenerationHandleProvider(_Resolver(), lease_store=None)

    async with provider.reserve_write("gen-1") as handle:
        assert handle.graph_generation_id == "gen-1"
        assert handle.leased is False
