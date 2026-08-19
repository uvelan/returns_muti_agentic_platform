"""The one place a request learns which graph generation serves it.

Phase 12's rule is "no code below handle acquisition resolves 'current
generation' independently". Two things follow from that, and this module exists
to make both true at once rather than leaving them to each caller's discipline:

* **Resolution happens exactly once per request.** A request that re-resolved
  mid-flight could read half its data from one generation and half from its
  successor -- the blue/green cutover would become visible as inconsistency
  rather than as an atomic swap.
* **Resolution and lease acquisition are the same step.** Slice 1 built the
  drain that retirement waits on, but a drain only means something if the
  readers it is protecting are actually counted. Resolving without leasing
  gives a request that believes it is pinned to a generation the retirement
  path is free to remove.

Binding them into one async context manager is what makes "resolved but not
leased" unrepresentable: there is no API that returns a generation id without
also having recorded the claim, and no way to hold the claim past the `async
with` that took it.

**The lease is best-effort, deliberately.** `acquire` yields a handle even when
no lease store is configured, and even when the store refuses. A refusal means
the generation began draining between resolution and acquisition, which is
precisely the case the plan answers with `REBIND_ON_RESUME` -- the caller should
re-resolve, not fail -- and until the rebind path exists, degrading to the
unleased behaviour that shipped before this module is strictly better than
failing a request that would have worked. `leased` says which happened, so
nothing has to infer it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from return_platform.dynamic_knowledge.lifecycle.lease_store import GenerationLeaseStore
from return_platform.dynamic_knowledge.lifecycle.mongo_store import ActiveRuntimeSnapshotStore
from return_platform.dynamic_knowledge.schema import ActiveSchema

_LOGGER = logging.getLogger(__name__)

# Long enough to outlive a full reasoning turn including model latency, short
# enough that a crashed request stops blocking a retirement within minutes
# rather than hours. Retirement's own drain wait is bounded independently, so
# this is not the only backstop.
DEFAULT_READ_LEASE_TTL_SECONDS = 900

#: The snapshot name the Order Discovery graph activates under. One named
#: snapshot per servable graph; there is only one today.
DEFAULT_SNAPSHOT_NAME = "ORDER_DISCOVERY"


class GenerationResolver(Protocol):
    """The existing `MongoGraphStateProvider.active_generation` shape."""

    async def active_generation(self, schema: ActiveSchema) -> str: ...


class GenerationDraining(RuntimeError):
    """A write was attempted against a generation that is being retired.

    Retryable: a successor generation is already ACTIVE, so re-resolving and
    writing there is expected to succeed. The caller should not treat this as a
    source or graph fault.
    """

    def __init__(self, graph_generation_id: str) -> None:
        self.graph_generation_id = graph_generation_id
        super().__init__(
            f"generation {graph_generation_id!r} is draining and no longer accepts writes"
        )


class GenerationResolutionError(RuntimeError):
    """The active generation could not be determined, so no read may proceed.

    Raised when the `ActiveRuntimeSnapshot` read itself fails -- a Mongo
    outage, a timeout, an unresolved credential. Deliberately *not* degraded to
    the legacy resolver: after the first cutover that resolver names a drained,
    RETIRED generation, so degrading would answer every order search with zero
    results. A dependency failure reported as "no such order" is worse than one
    reported as a failure, because only the first looks like an answer.

    Retryable in the same sense `GenerationDraining` is -- the fault is in
    reaching the snapshot, not in the graph or the source.
    """


@dataclass(frozen=True, slots=True)
class GenerationHandle:
    """One request's pinned view of which generation serves it.

    `leased` is not decoration. An unleased handle is one the retirement path
    cannot see, so a generation it names may be retired while the request is
    still using it. Callers that need the guarantee -- as opposed to callers
    that merely need an id -- have to be able to tell the difference.
    """

    graph_generation_id: str
    leased: bool
    lease_id: str | None = None


class GenerationHandleProvider:
    """Resolves and leases as one step. The only sanctioned way to learn the
    current generation."""

    def __init__(
        self,
        resolver: GenerationResolver,
        *,
        lease_store: GenerationLeaseStore | None = None,
        owner_instance_id: str = "unknown",
        read_lease_ttl_seconds: int = DEFAULT_READ_LEASE_TTL_SECONDS,
        snapshot_store: ActiveRuntimeSnapshotStore | None = None,
        snapshot_name: str = DEFAULT_SNAPSHOT_NAME,
    ) -> None:
        self._resolver = resolver
        self._lease_store = lease_store
        self._owner_instance_id = owner_instance_id
        self._read_lease_ttl_seconds = read_lease_ttl_seconds
        self._snapshot_store = snapshot_store
        self._snapshot_name = snapshot_name

    @asynccontextmanager
    async def acquire_read(self, schema: ActiveSchema) -> AsyncIterator[GenerationHandle]:
        """Pin a generation for the duration of the block, and release on exit.

        Release is in a `finally` so a raising body still gives the lease back:
        an exception that leaked a lease would block the next retirement for the
        full TTL, turning a transient request failure into an operational one.
        """
        graph_generation_id, activation_version = await self._resolve(schema)
        lease_id = await self._try_acquire(
            graph_generation_id, snapshot_activation_version=activation_version
        )
        try:
            yield GenerationHandle(
                graph_generation_id=graph_generation_id,
                leased=lease_id is not None,
                lease_id=lease_id,
            )
        finally:
            if lease_id is not None and self._lease_store is not None:
                try:
                    await self._lease_store.release(
                        graph_generation_id=graph_generation_id, lease_id=lease_id
                    )
                except Exception:
                    # The TTL is the backstop. Failing the request on a release
                    # error would convert a cleanup problem into a user-visible
                    # one for no gain.
                    _LOGGER.exception(
                        "Could not release read lease %s on generation %s; "
                        "it will expire on its TTL",
                        lease_id,
                        graph_generation_id,
                    )

    @asynccontextmanager
    async def acquire_read_pinned(
        self, graph_generation_id: str
    ) -> AsyncIterator[GenerationHandle]:
        """Lease a generation the caller has already chosen, skipping resolution.

        For `STRICT_PINNING`: a conversation resuming onto the generation it
        started on must lease *that* one, not whatever is current, or the lease
        protects the wrong generation entirely -- the pinned one would still be
        free to retire while the request read it.

        This is the one sanctioned exception to "resolution and leasing are the
        same step", and it is not a loophole: the id comes from a prior
        `acquire_read`, persisted in the conversation's own checkpoint. It never
        re-derives "current" from anywhere.

        A refusal degrades to unleased, as with `acquire_read` -- the generation
        is draining, and a strict pin cannot survive a retirement no matter what
        this returns. `leased` reports it.
        """
        lease_id = await self._try_acquire(graph_generation_id)
        try:
            yield GenerationHandle(
                graph_generation_id=graph_generation_id,
                leased=lease_id is not None,
                lease_id=lease_id,
            )
        finally:
            if lease_id is not None and self._lease_store is not None:
                try:
                    await self._lease_store.release(
                        graph_generation_id=graph_generation_id, lease_id=lease_id
                    )
                except Exception:
                    _LOGGER.exception(
                        "Could not release read lease %s on generation %s; "
                        "it will expire on its TTL",
                        lease_id,
                        graph_generation_id,
                    )

    @asynccontextmanager
    async def reserve_write(self, graph_generation_id: str) -> AsyncIterator[GenerationHandle]:
        """Claim a write reservation on an *already resolved* generation.

        Not a resolution step -- on-demand sync inherits the generation its
        calling turn pinned, so this only records the claim. Retirement counts
        READ and WRITE separately because they fail differently: an outstanding
        read means stale results, an outstanding write means data written into a
        generation that is about to disappear.

        **Refusal fails, unlike the read path.** A refused read degrades to
        unleased because serving a request from a generation still on its way
        out is merely stale. A refused write means the generation is draining
        and a successor is already ACTIVE, so anything written here is
        discarded at retirement -- silently, and with the sync reporting
        success. `GenerationDraining` is retryable precisely so the caller
        re-resolves and writes to the live generation instead.

        Nothing else catches this. The Neo4j write fence
        (`compile_generation_fence`) reads the generation's *current* status and
        passes it as the expected one, so it rejects a status change mid-write
        but happily accepts a write to a DRAINING generation.

        Store absence or a store outage still degrades to unreserved: refusing
        to write because the bookkeeping is unavailable would take the platform
        down for a cleanup concern.
        """
        reservation_id: str | None = None
        if self._lease_store is not None:
            try:
                reservation = await self._lease_store.acquire_write_reservation(
                    graph_generation_id=graph_generation_id,
                    snapshot_activation_version=0,
                    owner_instance_id=self._owner_instance_id,
                    ttl_seconds=self._read_lease_ttl_seconds,
                )
            except Exception:
                _LOGGER.exception(
                    "Could not acquire a write reservation on generation %s; proceeding unreserved",
                    graph_generation_id,
                )
            else:
                if reservation is None:
                    raise GenerationDraining(graph_generation_id)
                reservation_id = reservation.reservation_id
        try:
            yield GenerationHandle(
                graph_generation_id=graph_generation_id,
                leased=reservation_id is not None,
                lease_id=reservation_id,
            )
        finally:
            if reservation_id is not None and self._lease_store is not None:
                try:
                    await self._lease_store.release(
                        graph_generation_id=graph_generation_id, lease_id=reservation_id
                    )
                except Exception:
                    _LOGGER.exception(
                        "Could not release write reservation %s on generation %s; "
                        "it will expire on its TTL",
                        reservation_id,
                        graph_generation_id,
                    )

    async def _resolve(self, schema: ActiveSchema) -> tuple[str, int]:
        """Which generation serves this request, and at what activation version.

        Two notions of "current" exist in this codebase.
        `MongoGraphStateProvider.active_generation` reads the
        `dynamic_graph_generations` collection and predates the blue/green
        protocol; `ActiveRuntimeSnapshot` is the design's "one atomic pointer
        every request resolves". **The snapshot wins where one exists** -- it is
        the pointer the activation compare-and-swap moves, so resolving anything
        else would let a request read a generation the cutover has already
        replaced.

        **The snapshot is the live path, not a future one.** A rebuild activates
        by compare-and-swapping this exact document (`orchestrator.py`'s
        `build_and_activate` and `adopt_existing_generation` are its only two
        writers), so once any rebuild has run, every request resolves through
        the branch above and the legacy resolver is never consulted. An earlier
        version of this docstring claimed the fallback was "still the state of
        production today"; that stopped being true at the first cutover, and the
        stale claim reads as though the dead collection below were the live
        mechanism.

        **Nothing in this repository writes `dynamic_graph_generations`.** The
        collection is a read-only inheritance from the pre-blue/green design, so
        `active_generation` cannot find a row and always returns
        `LEGACY_GENERATION_ID`. That is *correct* before the first cutover --
        `GraphSyncService._ensure_generation_marker` creates a marker under
        exactly that id, so writer and reader agree -- and it is why the
        fallback is kept rather than deleted.

        After a cutover it is no longer correct: `legacy-live` has been drained
        and RETIRED, and resolving it would serve an empty generation. Reaching
        this line at all therefore means either a genuinely pre-cutover
        deployment or a snapshot read that failed, and the two are not
        distinguishable here -- which is why taking the fallback is logged
        rather than silent. See the caller's comment on the read-failure branch.

        Activation version 0 means "resolved without a snapshot" and is never a
        real version -- `ActiveRuntimeSnapshot.activation_version` is `ge=1`.
        """
        if self._snapshot_store is not None:
            try:
                snapshot = await self._snapshot_store.read(snapshot_name=self._snapshot_name)
            except Exception as exc:
                # FAIL CLOSED. This used to degrade to the legacy resolver, on
                # the reasoning that a Mongo blip should not take order search
                # down. That was right while `legacy-live` held the live data:
                # degrading meant *slightly stale results*.
                #
                # The first cutover inverted it. `legacy-live` is drained and
                # RETIRED, so degrading now resolves an empty generation and the
                # answer is "no orders found" -- which an associate cannot tell
                # apart from "this order does not exist". A read failure would
                # therefore present as a confident, wrong negative, and send them
                # off to re-key an order number that was correct all along.
                #
                # This is not hypothetical, and not a production-only concern.
                # On 2026-08-15 this dev stack ran for two hours with a Mongo DSN
                # pointing at a host that did not exist, so every Mongo read
                # raised ServerSelectionTimeoutError. Any snapshot read in that
                # window took this branch -- with 5,978 nodes sitting in Neo4j
                # and search answering nothing.
                #
                # An error says "something is broken, retry". A zero-result says
                # "the order is not real". The second is worse precisely because
                # it is actionable, in the wrong direction. So: raise, and let
                # the caller surface a dependency failure honestly.
                _LOGGER.exception(
                    "Could not read ActiveRuntimeSnapshot %r; failing the request rather than "
                    "resolving a generation no activation published",
                    self._snapshot_name,
                )
                raise GenerationResolutionError(
                    f"could not resolve the active graph generation: reading "
                    f"ActiveRuntimeSnapshot {self._snapshot_name!r} failed"
                ) from exc
            else:
                if snapshot is not None:
                    return snapshot.graph_generation_id, snapshot.activation_version
        graph_generation_id = await self._resolver.active_generation(schema)
        # Loud, because reaching here after the first cutover means a request is
        # about to be served from a generation no activation ever published.
        # Silently returning `legacy-live` is how "the graph has 6,000 nodes and
        # search finds nothing" becomes an unexplainable report.
        _LOGGER.warning(
            "Resolved generation %s from the legacy resolver rather than ActiveRuntimeSnapshot %r. "
            "Nothing writes `dynamic_graph_generations`, so this is correct only on a deployment "
            "that has never completed a rebuild; after a cutover it names a retired generation.",
            graph_generation_id,
            self._snapshot_name,
        )
        return graph_generation_id, 0

    async def _try_acquire(
        self, graph_generation_id: str, *, snapshot_activation_version: int = 0
    ) -> str | None:
        if self._lease_store is None:
            return None
        try:
            lease = await self._lease_store.acquire_read_lease(
                graph_generation_id=graph_generation_id,
                snapshot_activation_version=snapshot_activation_version,
                owner_instance_id=self._owner_instance_id,
                ttl_seconds=self._read_lease_ttl_seconds,
            )
        except Exception:
            _LOGGER.exception(
                "Could not acquire a read lease on generation %s; proceeding unleased",
                graph_generation_id,
            )
            return None
        if lease is None:
            _LOGGER.info(
                "Generation %s is draining; proceeding unleased pending REBIND_ON_RESUME",
                graph_generation_id,
            )
            return None
        return lease.lease_id
