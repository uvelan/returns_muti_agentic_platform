"""Orchestrates one full blue/green rebuild-and-activate cycle: acquire a
rebuild lease, build a new generation via full_sync (BUILDING), catch up on
what changed since the build started (CATCHING_UP), validate, activate
(Neo4j ACTIVE + Mongo ActiveRuntimeSnapshot compare-and-swap), then retire
the previous generation. See the source-to-graph alignment plan's
"Activation protocol" for the full state machine this implements.

Retirement drains. The previous generation goes ACTIVE -> DRAINING -> RETIRED,
and the DRAINING step waits for every GenerationReadLease and
GenerationWriteReservation naming it to be released or to pass its TTL (see
lifecycle/lease_store.py). Requests that resolved ActiveRuntimeSnapshot just
before the cutover are still reading the old generation, and on-demand sync may
still be writing to it; removing it out from under them is what this avoids. The
request path takes those claims for real -- `order_agent/coordinator.py` leases
per turn and `on_demand_sync/coordinator.py` reserves per write, both through
`lifecycle/handle.py`.

Validation is real too. `_validate` runs schema-derived checks (see
`graph/validation.py`) between VALIDATING and READY_FOR_ACTIVATION, and raising
there is what implements the Wave C gate's "validation failure keeps N active":
the candidate is marked FAILED and the compare-and-swap never runs, so the
currently-active generation is untouched.

A failed build no longer leaks a candidate. Any exception between creating the
generation and a successful activation marks it FAILED, so a dead rebuild's
candidate does not sit in BUILDING indefinitely looking like work in progress.

Fencing tokens are allocated, not assumed. This module used to open every
rebuild with `fencing_token = 1`, matching the constant `GraphSyncService`
passed for every write -- so every generation in the system carried the same
token and the fence in `compile_generation_fence` could not distinguish an owner
from a stale one. Tokens now come from a durable monotonic allocator
(`mongo_store.MongoFencingTokenAllocator`), which is what makes both fences real:
Neo4j's exact-match check on the marker, and `MongoSyncCheckpointStore`'s `$lte`
refusal to let a lower token rewind a live cursor.

`adopt_existing_generation` at the bottom is the path off `LEGACY_GENERATION_ID`.
A deployment whose graph predates this protocol has data under one always-ACTIVE
generation and no snapshot; adopting it publishes that generation as activation
version 1 so the next rebuild has a predecessor to cut over from, drain and
retire. Without it the first rebuild would activate beside the live graph rather
than replacing it, orphaning every node already there.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import (
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GenerationLeaseStore,
    LeaseClass,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ActiveRuntimeSnapshotStore,
    FencingTokenAllocator,
    RebuildLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.neo4j_validator import GenerationValidator
from return_platform.dynamic_knowledge.schema import ActiveSchema

_LOGGER = logging.getLogger(__name__)

# Terminal states, plus the one state a *candidate* can never be rolled back
# from: once a generation is ACTIVE and referenced by the snapshot, marking it
# FAILED would strand live traffic on a generation flagged as broken.
_NOT_ROLLBACK_ELIGIBLE = frozenset(
    {GraphGenerationStatus.ACTIVE, GraphGenerationStatus.FAILED, GraphGenerationStatus.RETIRED}
)


class ActivationError(RuntimeError):
    """The activation protocol could not complete; .stage says where it failed."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class RebuildSyncCoordinator(Protocol):
    """The subset of GenericSyncCoordinator this orchestrator drives."""

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str | None = None,
    ) -> tuple[int, int]: ...


class GenerationLifecycleOrchestrator:
    def __init__(
        self,
        *,
        snapshot_store: ActiveRuntimeSnapshotStore,
        lease_store: RebuildLeaseStore,
        generation_writer: Neo4jGenerationWriter,
        sync_coordinator: RebuildSyncCoordinator,
        fencing_tokens: FencingTokenAllocator,
        owner_instance_id: str,
        lease_ttl_seconds: int = 3600,
        generation_lease_store: GenerationLeaseStore | None = None,
        drain_timeout_seconds: float = 120.0,
        drain_poll_seconds: float = 1.0,
        validator: GenerationValidator | None = None,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._lease_store = lease_store
        self._generation_writer = generation_writer
        self._sync_coordinator = sync_coordinator
        # Required, not optional. A default would be a constant, and a constant
        # fencing token is exactly the defect this replaced.
        self._fencing_tokens = fencing_tokens
        self._owner_instance_id = owner_instance_id
        self._lease_ttl_seconds = lease_ttl_seconds
        # Optional so existing construction sites keep working; when absent the
        # generation is still moved through DRAINING (the state is what other
        # components key off) but there is no outstanding work to wait for.
        self._generation_lease_store = generation_lease_store
        self._drain_timeout_seconds = drain_timeout_seconds
        self._drain_poll_seconds = drain_poll_seconds
        # Optional so existing construction sites keep working, but absence is
        # logged loudly at activation rather than passing quietly.
        self._validator = validator

    async def build_and_activate(
        self,
        *,
        schema: ActiveSchema,
        snapshot_name: str,
        configuration_release_id: str,
        search_index_release_id: str = "none",
    ) -> ActiveRuntimeSnapshot:
        graph_generation_id = str(uuid.uuid4())
        # Allocated before the lease so a rebuild that loses the lease race has
        # still consumed its token: reusing an allocated-but-unused token would
        # mean two builds could hold the same one.
        fencing_token = await self._fencing_tokens.allocate(scope=snapshot_name)

        lease = await self._lease_store.acquire(
            snapshot_name=snapshot_name,
            graph_generation_id=graph_generation_id,
            owner_instance_id=self._owner_instance_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        if lease is None:
            raise ActivationError(
                f"a rebuild is already in progress for snapshot {snapshot_name!r}",
                stage="ACQUIRE_REBUILD_LEASE",
            )

        try:
            await self._generation_writer.create_generation(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                status=GraphGenerationStatus.PREPARING,
            )
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.PREPARING,
                GraphGenerationStatus.BUILDING,
                stage="BUILD",
            )
            await self._sync_coordinator.full_sync(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=GraphGenerationStatus.BUILDING,
                sync_run_id=f"rebuild-{graph_generation_id}-build",
            )

            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.BUILDING,
                GraphGenerationStatus.CATCHING_UP,
                stage="CATCH_UP",
            )
            # One catch-up replay pass, capturing whatever changed at the
            # sources since the BUILDING scan's watermarks were captured.
            # Re-running full_sync is safe: every write is MERGE-based.
            await self._sync_coordinator.full_sync(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=GraphGenerationStatus.CATCHING_UP,
                sync_run_id=f"rebuild-{graph_generation_id}-catchup",
            )

            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.CATCHING_UP,
                GraphGenerationStatus.VALIDATING,
                stage="VALIDATE",
            )
            await self._validate(schema=schema, graph_generation_id=graph_generation_id)
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.VALIDATING,
                GraphGenerationStatus.READY_FOR_ACTIVATION,
                stage="VALIDATE",
            )
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.READY_FOR_ACTIVATION,
                GraphGenerationStatus.ACTIVE,
                stage="ACTIVATE_GRAPH",
            )

            previous = await self._snapshot_store.read(snapshot_name=snapshot_name)
            new_snapshot = ActiveRuntimeSnapshot(
                snapshot_name=snapshot_name,
                configuration_release_id=configuration_release_id,
                schema_fingerprint=schema.configuration_checksum,
                graph_generation_id=graph_generation_id,
                search_index_release_id=search_index_release_id,
                activation_id=str(uuid.uuid4()),
                activation_version=(previous.activation_version + 1) if previous is not None else 1,
                activated_at=datetime.now(UTC),
            )
            swapped = await self._snapshot_store.compare_and_swap(
                snapshot_name=snapshot_name,
                expected_activation_version=previous.activation_version
                if previous is not None
                else None,
                new_snapshot=new_snapshot,
            )
            if not swapped:
                # Per the plan: on CAS failure the candidate reverts (here:
                # FAILED, since a fresh rebuild always starts a fresh
                # generation rather than resuming this one) and the old
                # snapshot remains authoritative -- never left ACTIVE and
                # unreferenced.
                await self._transition(
                    graph_generation_id,
                    fencing_token,
                    GraphGenerationStatus.ACTIVE,
                    GraphGenerationStatus.FAILED,
                    stage="ACTIVATE_SNAPSHOT_CAS",
                )
                raise ActivationError(
                    f"ActiveRuntimeSnapshot for {snapshot_name!r} changed concurrently during "
                    "activation; this candidate generation has been marked FAILED",
                    stage="ACTIVATE_SNAPSHOT_CAS",
                )

            if previous is not None:
                await self._retire(previous.graph_generation_id)
            return new_snapshot
        except BaseException:
            # Roll the candidate back to FAILED so a dead rebuild does not leave
            # it parked in BUILDING/CATCHING_UP/VALIDATING, where the next
            # operator to inspect it sees an in-progress build that will never
            # finish. Best-effort by construction: the original failure is what
            # the caller needs, so a rollback that itself fails is logged and
            # swallowed rather than replacing it.
            await self._mark_failed(graph_generation_id)
            raise
        finally:
            await self._lease_store.release(snapshot_name=snapshot_name, lease_id=lease.lease_id)

    async def _validate(self, *, schema: ActiveSchema, graph_generation_id: str) -> None:
        """Deep validation, between VALIDATING and READY_FOR_ACTIVATION.

        Raising here is the mechanism behind the Wave C gate's "validation
        failure keeps N active": the exception propagates to
        `build_and_activate`'s handler, which marks the candidate FAILED and
        never reaches the ActiveRuntimeSnapshot compare-and-swap, so the
        currently-active generation is untouched.

        A validator that is not configured is *not* a silent pass -- it is
        logged, because "we validated and it was fine" and "we did not
        validate" must not look the same in an incident.
        """
        if self._validator is None:
            _LOGGER.warning(
                "No generation validator configured; activating %s without deep validation",
                graph_generation_id,
            )
            return
        report = await self._validator.validate(
            schema=schema, graph_generation_id=graph_generation_id
        )
        if not report.passed:
            raise ActivationError(report.summary(), stage="VALIDATE")

    async def _mark_failed(self, graph_generation_id: str) -> None:
        try:
            status = await self._generation_writer.get_status(
                graph_generation_id=graph_generation_id
            )
            if status is None or status[0] in _NOT_ROLLBACK_ELIGIBLE:
                return
            current_status, fencing_token = status
            await self._generation_writer.transition(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_status=current_status,
                new_status=GraphGenerationStatus.FAILED,
            )
        except Exception:
            _LOGGER.exception(
                "Could not roll back generation %s to FAILED; it may need manual cleanup",
                graph_generation_id,
            )

    async def _transition(
        self,
        graph_generation_id: str,
        fencing_token: int,
        expected: GraphGenerationStatus,
        new: GraphGenerationStatus,
        *,
        stage: str,
    ) -> None:
        try:
            await self._generation_writer.transition(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_status=expected,
                new_status=new,
            )
        except Exception as error:
            raise ActivationError(str(error), stage=stage) from error

    async def _retire(self, graph_generation_id: str) -> None:
        """ACTIVE -> DRAINING -> (wait for outstanding work) -> RETIRED.

        Ordering matters: the lease store is closed to new work *before* the
        Neo4j status changes. Doing it the other way round leaves a window in
        which the generation reads as DRAINING while the lease store would
        still hand out a lease on it, and that lease would never be waited for.

        Never raises. This runs after the compare-and-swap has already made the
        successor authoritative, so the activation has succeeded whatever
        happens here; failing it now would report a false negative for a
        cutover that is complete and serving. A generation left in DRAINING is
        safe -- unreachable, just not yet cleaned up -- so a stuck drain is
        logged for an operator rather than escalated.
        """
        status = await self._generation_writer.get_status(graph_generation_id=graph_generation_id)
        if status is None or status[0] in {
            GraphGenerationStatus.RETIRED,
            GraphGenerationStatus.FAILED,
        }:
            return
        current_status, fencing_token = status

        if self._generation_lease_store is not None:
            await self._generation_lease_store.begin_drain(graph_generation_id=graph_generation_id)

        if current_status is not GraphGenerationStatus.DRAINING:
            try:
                await self._generation_writer.transition(
                    graph_generation_id=graph_generation_id,
                    fencing_token=fencing_token,
                    expected_status=current_status,
                    new_status=GraphGenerationStatus.DRAINING,
                )
            except Exception:
                _LOGGER.exception(
                    "Could not move generation %s to DRAINING; leaving it as-is",
                    graph_generation_id,
                )
                return

        if not await self._await_drain(graph_generation_id):
            return

        try:
            await self._generation_writer.transition(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_status=GraphGenerationStatus.DRAINING,
                new_status=GraphGenerationStatus.RETIRED,
            )
        except Exception:
            _LOGGER.exception(
                "Generation %s drained but could not be marked RETIRED", graph_generation_id
            )

    async def _await_drain(self, graph_generation_id: str) -> bool:
        """True once no unexpired lease or reservation names this generation.

        Bounded: a holder that crashed will never release, so the wait relies on
        lease TTLs rather than on cooperative release, and gives up entirely
        after `drain_timeout_seconds` so a misbehaving holder cannot pin the
        retirement step forever.
        """
        if self._generation_lease_store is None:
            return True
        deadline = asyncio.get_running_loop().time() + self._drain_timeout_seconds
        while True:
            outstanding = await self._generation_lease_store.outstanding(
                graph_generation_id=graph_generation_id
            )
            if not any(outstanding.values()):
                return True
            if asyncio.get_running_loop().time() >= deadline:
                _LOGGER.warning(
                    "Generation %s still has outstanding work after %.0fs "
                    "(reads=%d, writes=%d); leaving it DRAINING for operator review",
                    graph_generation_id,
                    self._drain_timeout_seconds,
                    outstanding.get(LeaseClass.READ, 0),
                    outstanding.get(LeaseClass.WRITE, 0),
                )
                return False
            await asyncio.sleep(self._drain_poll_seconds)


async def adopt_existing_generation(
    *,
    snapshot_store: ActiveRuntimeSnapshotStore,
    generation_writer: Neo4jGenerationWriter,
    fencing_tokens: FencingTokenAllocator,
    graph_generation_id: str,
    snapshot_name: str,
    configuration_release_id: str,
    schema_fingerprint: str,
    search_index_release_id: str = "none",
) -> ActiveRuntimeSnapshot:
    """Publish an already-populated generation as activation version 1.

    The migration path off `LEGACY_GENERATION_ID`, and the reason a deployment
    that has been serving from one permanently-ACTIVE generation is not orphaned
    by the first real rebuild. Once this has run, that generation is a *normal*
    predecessor: `build_and_activate` builds its replacement, validates it, swaps
    the snapshot, drains this one and retires it. The live graph keeps serving
    the whole time and is never rebuilt in place.

    A free function rather than a method because adoption drives none of the
    rebuild machinery -- no lease, no coordinator, no validation. There is
    nothing to build; the data is already there.

    **Idempotent, and safe against a concurrent adopter.** If a snapshot already
    exists this returns it unchanged, and the compare-and-swap is conditioned on
    there still being none, so the loser of a race re-reads the winner's
    snapshot instead of overwriting it.

    Adoption also claims a fresh fencing token on the marker. That is not
    bookkeeping: existing markers carry the legacy constant, and until the token
    moves, a writer still holding that constant is indistinguishable from the
    adopting owner. Claiming is what makes the first fence real. Live readers are
    unaffected -- `OnDemandNeo4jGraphWriter` reads the marker's current token on
    every write rather than caching one -- and `MongoSyncCheckpointStore` accepts
    any token at or above the stored one.
    """
    existing = await snapshot_store.read(snapshot_name=snapshot_name)
    if existing is not None:
        return existing

    status = await generation_writer.get_status(graph_generation_id=graph_generation_id)
    if status is None:
        raise ActivationError(
            f"cannot adopt generation {graph_generation_id!r}: it has no GraphGeneration marker",
            stage="ADOPT",
        )
    if status[0] is not GraphGenerationStatus.ACTIVE:
        # Adoption asserts "this generation is what has been serving". Adopting
        # anything else would publish a snapshot pointing at a generation that
        # was never live, and every reader would immediately follow it there.
        raise ActivationError(
            f"cannot adopt generation {graph_generation_id!r}: its marker is "
            f"{status[0].value}, not ACTIVE",
            stage="ADOPT",
        )

    # `floor`: the marker's token may come from outside this counter's history --
    # the legacy constant, or a graph older than the platform database in front
    # of it. Adoption is the one moment that can see both values and is entitled
    # to reconcile them, having just established that this generation is the one
    # serving. Without it every subsequent claim loses to the marker forever.
    token = await fencing_tokens.allocate(scope=snapshot_name, floor=status[1])
    await generation_writer.claim_write_ownership(
        graph_generation_id=graph_generation_id, fencing_token=token
    )

    snapshot = ActiveRuntimeSnapshot(
        snapshot_name=snapshot_name,
        configuration_release_id=configuration_release_id,
        schema_fingerprint=schema_fingerprint,
        graph_generation_id=graph_generation_id,
        search_index_release_id=search_index_release_id,
        activation_id=str(uuid.uuid4()),
        activation_version=1,
        activated_at=datetime.now(UTC),
    )
    swapped = await snapshot_store.compare_and_swap(
        snapshot_name=snapshot_name,
        expected_activation_version=None,
        new_snapshot=snapshot,
    )
    if swapped:
        _LOGGER.info(
            "Adopted existing generation %s as %s activation version 1",
            graph_generation_id,
            snapshot_name,
        )
        return snapshot

    concurrent = await snapshot_store.read(snapshot_name=snapshot_name)
    if concurrent is None:
        raise ActivationError(
            f"adoption of {graph_generation_id!r} lost its compare-and-swap but no "
            f"snapshot exists for {snapshot_name!r}",
            stage="ADOPT",
        )
    return concurrent
