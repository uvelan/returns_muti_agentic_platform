"""GRAPH-01 against real infrastructure: the destructive cutover, from the API's
own entry point.

`test_generation_lifecycle_e2e.py` proves the lifecycle composes when driven
directly by `RebuildTrigger`. What it cannot prove is the thing that was actually
broken: **production did not use it.** `GraphSyncService.sync` -- what
`POST /api/graph-sync/runs` calls -- rebuilt the live graph in place under a
permanently-active `legacy-live` generation and a constant fencing token. Every
test here therefore enters through `GraphSyncService.sync`, not through the
lifecycle, because entering through the lifecycle is exactly the assumption that
was wrong before.

Real MongoDB and real Neo4j, because each property here is a claim about what
concurrently-running code can observe, and every one of them is trivially
"provable" against a mock that was written to agree:

* a reader resolving the active snapshot never sees a partially built candidate;
* the swap is atomic -- concurrent cutovers produce exactly one activation;
* a writer holding a superseded fencing token is rejected by Neo4j itself;
* a candidate that fails validation leaves N active *and still readable*;
* retirement waits for an outstanding reader rather than pulling the generation
  out from under it.

Assembly deliberately mirrors production: this constructs the real
`GraphSyncService` and replaces only `Settings` (values, not behaviour) and
`refresh_schema` (which would reload the shipped descriptor over the reduced
schema these tests build). Every store, connector, writer, validator and
allocator is the real one.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.data_platform.graph.sync_service import (
    GRAPH_SYNC_RUNS_COLLECTION,
    GraphSyncRequest,
    GraphSyncScope,
    GraphSyncService,
)
from return_platform.dynamic_knowledge.graph.generation import (
    LEGACY_FENCING_TOKEN,
    LEGACY_GENERATION_ID,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import (
    GenerationFencingError,
    Neo4jDynamicGraphWriter,
)
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.lifecycle.handle import DEFAULT_SNAPSHOT_NAME
from return_platform.dynamic_knowledge.lifecycle.lease_store import MongoGenerationLeaseStore
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    MongoActiveRuntimeSnapshotStore,
    MongoFencingTokenAllocator,
    MongoRebuildLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.neo4j_validator import Neo4jGenerationValidator
from return_platform.dynamic_knowledge.lifecycle.orchestrator import ActivationError
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    GraphNodeMutation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.checkpoint_store import MongoSyncCheckpointStore


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/return_platform"
        "?authSource=admin&directConnection=true"
    )


def _neo4j_uri() -> str:
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _Settings:
    """Values, not behaviour. The SQL Server fields are present because
    `_sync_participating_sources` builds a connection settings object
    unconditionally; no MSSQL source exists in the reduced schema, so no
    connection is ever opened."""

    dynamic_knowledge_schema_path = Path("config/dynamic_knowledge/active-schema.return-order.yaml")
    neo4j_database: str | None = None
    #: Deliberately tiny, so a build spans several pages and several writes.
    #: A single-page build would make "what does a reader see mid-rebuild?"
    #: unanswerable -- there would be no mid-rebuild.
    graph_sync_batch_size = 2
    graph_sync_max_records = 10_000
    sqlserver_host = "unused.invalid"
    sqlserver_port = 1433
    sqlserver_user = "unused"
    sqlserver_password = _Secret("unused")
    sqlserver_database = "unused"
    operation_timeout_seconds = 10.0
    contact_lookup_hmac_key = _Secret("s" * 32)

    def __init__(self, *, mongo_database: str, source_mongo_database: str) -> None:
        self.mongo_database = mongo_database
        self.source_mongo_database = source_mongo_database


def mongo_only_schema(schema: ActiveSchema) -> ActiveSchema:
    """The shared fixture reduced to its MongoDB half.

    Same reduction, and for the same reason, as
    `test_generation_lifecycle_e2e.mongo_only_schema`: the fixture declares a
    POSTGRESQL source for which no connector exists, and deep validation
    requires every declared node label to be populated -- so leaving
    `ConfiguredBeta` in the schema would fail every activation forever. Derived
    from the fixture rather than hand-authored so it cannot drift from the real
    shape. Restated here rather than imported so a collection-time import
    between test modules cannot take this file down with it.
    """
    return schema.model_copy(
        update={
            "sources": {k: v for k, v in schema.sources.items() if k == "source_a"},
            "entities": {k: v for k, v in schema.entities.items() if k == "entity_a"},
            "graph": schema.graph.model_copy(
                update={
                    "nodes": {k: v for k, v in schema.graph.nodes.items() if k == "node_a"},
                    "relationships": {},
                }
            ),
        }
    )


class _ObservingWriter:
    """Delegates every write to the real Neo4j writer, calling `observe` first.

    The hook is on the *writer* rather than on the coordinator because a build
    is only observable while it is writing: a probe between runs would prove
    nothing about what a reader sees mid-rebuild.
    """

    def __init__(self, inner: Neo4jDynamicGraphWriter, observe: Any) -> None:
        self._inner = inner
        self._observe = observe

    async def write(self, **kwargs: Any) -> Any:
        await self._observe()
        return await self._inner.write(**kwargs)

    async def reconcile_relationships(self, **kwargs: Any) -> Any:
        await self._observe()
        return await self._inner.reconcile_relationships(**kwargs)

    async def reconcile_child_ownership(self, **kwargs: Any) -> Any:
        await self._observe()
        return await self._inner.reconcile_child_ownership(**kwargs)


class _Harness:
    def __init__(self, schema: ActiveSchema) -> None:
        self.schema = mongo_only_schema(schema)
        self.suffix = uuid.uuid4().hex[:12]
        self.source_database = f"cutover_source_{self.suffix}"
        self.platform_database = "return_platform"
        self.mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        self.platform_db = self.mongo[self.platform_database]
        self.snapshot_collection = f"cutover_snapshots_{self.suffix}"
        self.rebuild_collection = f"cutover_rebuild_{self.suffix}"
        self.lease_collection = f"cutover_leases_{self.suffix}"
        self.token_collection = f"cutover_tokens_{self.suffix}"
        self.checkpoint_collection = f"cutover_checkpoints_{self.suffix}"
        self.runs_collection = f"cutover_runs_{self.suffix}"
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)
        self.snapshots = MongoActiveRuntimeSnapshotStore(self.platform_db[self.snapshot_collection])
        self.generation_leases = MongoGenerationLeaseStore(self.platform_db[self.lease_collection])
        self.fencing_tokens = MongoFencingTokenAllocator(self.platform_db[self.token_collection])
        #: Every generation this test touched, so cleanup removes its nodes and
        #: its marker. `legacy-live` is shared with anything else on this Neo4j,
        #: so the *marker* is only removed when this test created it.
        self.generation_ids: list[str] = []
        self.created_legacy_marker = False

    def service(self, *, observe: Any = None) -> GraphSyncService:
        """The real `GraphSyncService`, wired to this test's own collections."""
        service = cast(Any, object.__new__(GraphSyncService))
        settings = _Settings(
            mongo_database=self.platform_database, source_mongo_database=self.source_database
        )
        service._settings = settings
        service._platform_db = self.platform_db
        service._source_db = self.mongo[self.source_database]
        service._driver = self.neo4j
        service._runs = self.platform_db[self.runs_collection]
        service._schema = self.schema
        service._releases = None
        service._checkpoints = MongoSyncCheckpointStore(
            self.mongo, self.platform_database, collection=self.checkpoint_collection
        )
        writer = Neo4jDynamicGraphWriter(self.neo4j)
        service._writer = writer if observe is None else _ObservingWriter(writer, observe)
        service._projector = GenericGraphProjector()
        service._generation_writer = self.generation_writer
        service._snapshots = self.snapshots
        service._rebuild_leases = MongoRebuildLeaseStore(self.platform_db[self.rebuild_collection])
        service._generation_leases = self.generation_leases
        service._fencing_tokens = self.fencing_tokens
        service._validator = Neo4jGenerationValidator(self.neo4j)
        service._owner_instance_id = f"cutover-{self.suffix}"

        async def _keep_schema() -> None:
            """`refresh_schema` would reload the shipped descriptor over the
            reduced schema this harness built. The release store is not what
            these tests are about; everything downstream of the schema is real."""

        service.refresh_schema = _keep_schema
        return cast(GraphSyncService, service)

    async def seed_source(self, *identifiers: str) -> None:
        """`configured_changed_at` must be a real BSON date -- see
        `test_generation_lifecycle_e2e._Harness.seed_source` for why a string
        produces a silently empty build rather than an error."""
        await self.mongo[self.source_database]["objects"].insert_many(
            [
                {
                    "configured_id": identifier,
                    "configured_name": "Configured",
                    "configured_count": 5,
                    "configured_changed_at": datetime(2026, 8, 4, tzinfo=UTC),
                }
                for identifier in identifiers
            ]
        )

    async def node_count(self, graph_generation_id: str) -> int:
        async with self.neo4j.session() as session:
            result = await session.run(
                "MATCH (n {graph_generation_id: $gid}) RETURN count(n) AS total",
                {"gid": graph_generation_id},
            )
            rows = [row async for row in result]
        return int(rows[0]["total"]) if rows else 0

    async def status(self, graph_generation_id: str) -> GraphGenerationStatus | None:
        found = await self.generation_writer.get_status(graph_generation_id=graph_generation_id)
        return None if found is None else found[0]

    async def track_legacy(self) -> None:
        """Remember whether this test created the shared `legacy-live` marker, so
        cleanup does not delete one another environment was already using."""
        existing = await self.generation_writer.get_status(graph_generation_id=LEGACY_GENERATION_ID)
        self.created_legacy_marker = existing is None
        self.generation_ids.append(LEGACY_GENERATION_ID)

    async def cleanup(self) -> None:
        await self.mongo.drop_database(self.source_database)
        for name in (
            self.snapshot_collection,
            self.rebuild_collection,
            self.lease_collection,
            self.token_collection,
            self.checkpoint_collection,
            self.runs_collection,
        ):
            await self.platform_db.drop_collection(name)
        async with self.neo4j.session() as session:
            for generation_id in self.generation_ids:
                await session.run(
                    "MATCH (n {graph_generation_id: $gid}) DETACH DELETE n", {"gid": generation_id}
                )
                if generation_id == LEGACY_GENERATION_ID and not self.created_legacy_marker:
                    continue
                await session.run(
                    "MATCH (g:GraphGeneration {generation_id: $gid}) DETACH DELETE g",
                    {"gid": generation_id},
                )
        await self.mongo.close()
        await self.neo4j.close()


@pytest_asyncio.fixture
async def harness(active_schema: ActiveSchema) -> AsyncIterator[_Harness]:
    instance = _Harness(active_schema)
    try:
        yield instance
    finally:
        await instance.cleanup()


async def _establish_serving_generation(harness: _Harness) -> str:
    """Get the harness into the state a real deployment is in before this change:
    data in the graph under an adopted generation, with a snapshot naming it."""
    await harness.track_legacy()
    service = harness.service()
    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="setup"
    )
    assert run.status == "COMPLETED"
    snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    assert snapshot is not None
    return snapshot.graph_generation_id


@pytest.mark.asyncio
async def test_the_legacy_generation_is_adopted_rather_than_orphaned(harness: _Harness) -> None:
    """Backward compatibility, against the real stores.

    An existing deployment's graph lives under `legacy-live`. The first run must
    keep writing there -- moving the data would be the rebuild-in-place this task
    removes -- while publishing the snapshot that makes it a normal generation,
    and replacing the constant token on its marker with an allocated one.
    """
    await harness.seed_source("cutover-1", "cutover-2")

    generation = await _establish_serving_generation(harness)

    assert generation == LEGACY_GENERATION_ID
    assert await harness.node_count(LEGACY_GENERATION_ID) >= 2
    snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    assert snapshot is not None and snapshot.activation_version == 1
    marker = await harness.generation_writer.get_status(graph_generation_id=LEGACY_GENERATION_ID)
    assert marker is not None
    assert marker[1] > LEGACY_FENCING_TOKEN, "the marker still carries the constant token"


@pytest.mark.asyncio
async def test_a_full_sync_cuts_over_and_retires_the_generation_it_replaced(
    harness: _Harness,
) -> None:
    """Contract C9, end to end, through the production entry point.

    Build N+1 -> validate -> atomic swap -> drain -> retire N. The generation
    that was serving must end RETIRED rather than lingering, and the new one must
    be what the snapshot names.
    """
    await harness.seed_source("cutover-1", "cutover-2")
    previous = await _establish_serving_generation(harness)
    before = await harness.node_count(previous)

    run = await harness.service().sync(
        GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator"
    )

    assert run.status == "COMPLETED"
    assert run.graphGenerationId is not None and run.graphGenerationId != previous
    harness.generation_ids.append(run.graphGenerationId)

    snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    assert snapshot is not None
    assert snapshot.graph_generation_id == run.graphGenerationId
    assert snapshot.activation_version == 2
    assert await harness.status(run.graphGenerationId) is GraphGenerationStatus.ACTIVE
    assert await harness.status(previous) is GraphGenerationStatus.RETIRED
    assert await harness.node_count(run.graphGenerationId) >= 2
    # The replaced generation was never rebuilt in place: its nodes are exactly
    # as they were when it was serving.
    assert await harness.node_count(previous) == before


@pytest.mark.asyncio
async def test_a_reader_on_n_never_observes_a_partially_built_n_plus_one(
    harness: _Harness,
) -> None:
    """The defect, stated as what a reader can see.

    The observation runs on every write of the candidate build and resolves the
    generation the way a request does -- through `ActiveRuntimeSnapshot`. While
    N+1 is being written, that resolution must keep returning N, and N's contents
    must not move under it. A reader that resolved before the swap sees a
    complete graph, and one that resolves after sees a complete graph; there is
    no moment in between.
    """
    await harness.seed_source(*[f"cutover-{index}" for index in range(12)])
    previous = await _establish_serving_generation(harness)
    baseline = await harness.node_count(previous)
    observations: list[tuple[str, int]] = []

    async def observe() -> None:
        snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
        assert snapshot is not None
        observations.append(
            (snapshot.graph_generation_id, await harness.node_count(snapshot.graph_generation_id))
        )

    run = await harness.service(observe=observe).sync(
        GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator"
    )

    assert run.graphGenerationId is not None
    harness.generation_ids.append(run.graphGenerationId)
    assert observations, "the build wrote nothing, so nothing was observed"
    assert {generation for generation, _count in observations} == {previous}, (
        "a reader resolved the candidate generation before the swap"
    )
    assert {count for _generation, count in observations} == {baseline}, (
        "the serving generation changed while its replacement was being built"
    )


@pytest.mark.asyncio
async def test_a_stale_writer_holding_an_old_fencing_token_is_rejected(
    harness: _Harness,
) -> None:
    """The fence, against Neo4j rather than against a fake that agrees.

    A writer that held the generation, then had ownership claimed away from it,
    must be refused -- and the new owner must be able to write. Before this
    change both writers presented `1` and both were accepted, which is what made
    "the fencing token is constant, so it fences nothing" literally true.
    """
    await harness.seed_source("cutover-1")
    generation = await _establish_serving_generation(harness)
    marker = await harness.generation_writer.get_status(graph_generation_id=generation)
    assert marker is not None
    superseded_token = marker[1]

    fresh_token = await harness.fencing_tokens.allocate(scope=DEFAULT_SNAPSHOT_NAME)
    await harness.generation_writer.claim_write_ownership(
        graph_generation_id=generation, fencing_token=fresh_token
    )

    writer = Neo4jDynamicGraphWriter(harness.neo4j)
    batch = GraphMutationBatch(
        node_mutations=(
            GraphNodeMutation(
                operation="UPSERT",
                projection_id="node_a",
                entity_id="entity_a",
                key_values={"configured_id": "stale-writer"},
                properties={"configured_name": "written by a stale owner"},
            ),
        ),
        relationship_mutations=(),
    )
    with pytest.raises(GenerationFencingError):
        await writer.write(
            schema=harness.schema,
            graph_generation_id=generation,
            fencing_token=superseded_token,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
            sync_run_id=f"stale-{harness.suffix}",
            chunk_id="chunk-1",
            batch=batch,
        )

    receipt = await writer.write(
        schema=harness.schema,
        graph_generation_id=generation,
        fencing_token=fresh_token,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        sync_run_id=f"current-{harness.suffix}",
        chunk_id="chunk-1",
        batch=batch,
    )
    assert receipt.nodes_written >= 1, "the current owner must still be able to write"


@pytest.mark.asyncio
async def test_a_failed_candidate_leaves_n_active_and_still_serving(harness: _Harness) -> None:
    """`If N+1 fails at any point, N stays active.`

    The candidate is built from an emptied source, so deep validation's
    NODE_LABEL_POPULATED check fails against real Neo4j. Rejecting the candidate
    while damaging the live generation would satisfy a weaker reading of the
    contract and be a worse outcome than activating the bad build, so the live
    generation's contents are asserted too, not just its status.
    """
    await harness.seed_source("cutover-1", "cutover-2")
    previous = await _establish_serving_generation(harness)
    before = await harness.node_count(previous)
    await harness.mongo[harness.source_database].drop_collection("objects")

    with pytest.raises(ActivationError) as caught:
        await harness.service().sync(
            GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator"
        )

    assert caught.value.stage == "VALIDATE"
    snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    assert snapshot is not None
    assert snapshot.graph_generation_id == previous
    assert snapshot.activation_version == 1, "the pointer moved for a candidate that failed"
    assert await harness.status(previous) is GraphGenerationStatus.ACTIVE
    assert await harness.node_count(previous) == before
    runs = await harness.platform_db[harness.runs_collection].find({"status": "FAILED"}).to_list()
    assert len(runs) == 1 and runs[0]["errorCode"]


@pytest.mark.asyncio
async def test_retirement_waits_for_an_outstanding_reader(harness: _Harness) -> None:
    """Drain before retire, with a real lease held across the cutover.

    A reader that resolved the snapshot just before the swap is still reading N.
    Retirement must leave it DRAINING -- unreachable to new work, not yet removed
    -- rather than marking it RETIRED while that reader is mid-request.
    """
    await harness.seed_source("cutover-1")
    previous = await _establish_serving_generation(harness)
    lease = await harness.generation_leases.acquire_read_lease(
        graph_generation_id=previous,
        snapshot_activation_version=1,
        owner_instance_id="reader-1",
        ttl_seconds=3600,
    )
    assert lease is not None

    # Bounded so the test does not sit on the orchestrator's default two-minute
    # wait for a lease that is deliberately never released.
    run = await _sync_with_short_drain(harness.service())

    assert run.graphGenerationId is not None
    harness.generation_ids.append(run.graphGenerationId)
    assert await harness.status(previous) is GraphGenerationStatus.DRAINING, (
        "the generation was retired while a reader still held a lease on it"
    )
    # The cutover itself still succeeded: a stuck drain is an operator concern,
    # not a failed activation.
    assert await harness.status(run.graphGenerationId) is GraphGenerationStatus.ACTIVE


async def _sync_with_short_drain(service: GraphSyncService) -> Any:
    """Run a full sync with the orchestrator's drain wait shortened.

    The timeout is a constructor argument the service does not expose, so this
    patches the default on the class for the duration of the call rather than
    duplicating `_rebuild_and_activate`'s wiring -- a copy would be the one place
    a test stopped exercising production's assembly.
    """
    from return_platform.dynamic_knowledge.lifecycle import orchestrator as orchestrator_module

    original = orchestrator_module.GenerationLifecycleOrchestrator.__init__

    def patched(self: Any, **kwargs: Any) -> None:
        kwargs["drain_timeout_seconds"] = 1.0
        kwargs["drain_poll_seconds"] = 0.05
        original(self, **kwargs)

    orchestrator_module.GenerationLifecycleOrchestrator.__init__ = patched  # type: ignore[method-assign]
    try:
        return await service.sync(
            GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator"
        )
    finally:
        orchestrator_module.GenerationLifecycleOrchestrator.__init__ = original  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_concurrent_cutovers_produce_exactly_one_activation(harness: _Harness) -> None:
    """The swap is atomic.

    Two operators pressing resync at the same moment must never leave two
    generations both believing they are live, and the pointer must advance once
    per activation rather than once per attempt.

    Asserted as invariants rather than as "exactly one wins", deliberately: which
    of the two gets the rebuild lease depends on scheduling, and if the first
    finishes before the second starts, two sequential activations are a correct
    outcome, not a violation. What is never correct under any interleaving is a
    second ACTIVE generation or a version that does not match the number of
    activations -- so those are what this checks. Any attempt that does not
    activate must fail on the lease, not by half-activating.
    """
    await harness.seed_source("cutover-1", "cutover-2")
    previous = await _establish_serving_generation(harness)

    results = await asyncio.gather(
        harness.service().sync(
            GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator-a"
        ),
        harness.service().sync(
            GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator-b"
        ),
        return_exceptions=True,
    )

    activated = [result for result in results if not isinstance(result, BaseException)]
    refused = [result for result in results if isinstance(result, BaseException)]
    assert activated, f"neither cutover activated: {results}"
    for failure in refused:
        assert isinstance(failure, ActivationError), failure
        assert failure.stage == "ACQUIRE_REBUILD_LEASE", failure.stage

    generations = [run.graphGenerationId for run in activated]
    assert all(generation is not None for generation in generations)
    harness.generation_ids.extend(cast(list[str], generations))

    snapshot = await harness.snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
    assert snapshot is not None
    assert snapshot.activation_version == 1 + len(activated), "the pointer moved out of step"
    assert snapshot.graph_generation_id in generations

    live = [
        candidate
        for candidate in [previous, *cast(list[str], generations)]
        if await harness.status(candidate) is GraphGenerationStatus.ACTIVE
    ]
    assert live == [snapshot.graph_generation_id], (
        f"exactly one generation may be ACTIVE after a cutover; found {live}"
    )


@pytest.mark.asyncio
async def test_incremental_checkpoints_advance_only_after_work_completes(
    harness: _Harness,
) -> None:
    """Regression guard on an audited strength, not a new property.

    The checkpoint semantics are the reason an incremental run can be trusted to
    resume: the position advances only after a page's work succeeds, and the
    store refuses a write carrying a token below the one already stored. Both now
    run under an *allocated* token rather than the constant, which is exactly the
    change that could have broken them.
    """
    await harness.seed_source("cutover-1")
    generation = await _establish_serving_generation(harness)

    run = await harness.service().sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, incremental=True, applySchema=False),
        actor_id="operator",
    )

    assert run.status == "COMPLETED"
    assert run.recordScope == "INCREMENTAL"
    stored = await harness.platform_db[harness.checkpoint_collection].find_one(
        {"_id": {"graphGenerationId": generation, "sourceAssetId": "source_a"}}
    )
    assert stored is not None, "a completed incremental run left no resume position"
    assert int(stored["fencingToken"]) > LEGACY_FENCING_TOKEN

    # A writer holding a lower token cannot rewind that position.
    from return_platform.dynamic_knowledge.sync.checkpoint_store import CheckpointFenced
    from return_platform.source_connectors.contracts import SourceCursor

    with pytest.raises(CheckpointFenced):
        await MongoSyncCheckpointStore(
            harness.mongo, harness.platform_database, collection=harness.checkpoint_collection
        ).write(
            source_asset_id="source_a",
            graph_generation_id=generation,
            checkpoint=SourceCursor(cursor_type="FIELD_DATETIME", encoded_value="rewound"),
            fencing_token=LEGACY_FENCING_TOKEN,
        )


@pytest.mark.asyncio
async def test_the_run_ledger_names_the_generation_a_cutover_activated(
    harness: _Harness,
) -> None:
    """An operator reading the ledger after a resync is asking which graph is now
    serving. Before this the answer was always null for a scheduled run, because
    there was only ever one generation to name."""
    await harness.seed_source("cutover-1")
    await _establish_serving_generation(harness)

    service = harness.service()
    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="operator"
    )
    assert run.graphGenerationId is not None
    harness.generation_ids.append(run.graphGenerationId)

    stored = await service.get_run(run.id)
    assert stored is not None
    assert stored.graphGenerationId == run.graphGenerationId
    assert stored.recordScope == "FULL"


def test_the_runs_collection_name_is_the_one_production_uses() -> None:
    """Guards the harness itself: it points the service at a suffixed runs
    collection so a test run cannot pollute the real ledger, which is only safe
    while the production name is a named constant this file can diverge from
    deliberately rather than by accident."""
    assert GRAPH_SYNC_RUNS_COLLECTION == "graph_sync_runs"
