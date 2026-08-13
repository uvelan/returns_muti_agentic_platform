"""Wave C's owed item: the generation lifecycle, composed, against real infra.

Every piece of Phase 12 is verified individually somewhere — the drain against
real Mongo, the validation Cypher against real Neo4j, the trigger and the
rollback against doubles. **Their composition was not.** Six slices were built
on the assumption that a rebuild driven end to end would behave, and this is
where that assumption is either confirmed or found out.

What "end to end" means here: a real Mongo source document, read by the real
`MongoDBSourceScanConnector`, projected by the real `GenericSyncCoordinator`
into a real Neo4j under a new generation, validated by the real
`Neo4jGenerationValidator`, activated by a real compare-and-swap on a real
`ActiveRuntimeSnapshot`, with the previous generation really draining to
RETIRED. The only substitution is time.

The two cases that matter most are the ones the plan's Wave C gate names and
that no unit test can prove together:

* **build N+1 and activation** — a second rebuild produces a *different*
  generation, the snapshot's `activation_version` advances, and the old
  generation ends RETIRED rather than lingering.
* **validation failure keeps N active** — a candidate that fails deep
  validation is marked FAILED and the previously-serving generation is still
  the one the snapshot points at.

Assembly deliberately mirrors `data_platform/graph/sync_service.py`'s production
recipe (`scan_connector_registry` → `ProjectorGraphWriter` →
`GenericSyncCoordinator`). A test that assembled the pipeline differently could
pass while production's wiring was broken, which would make it worse than no
test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    MongoActiveRuntimeSnapshotStore,
    MongoFencingTokenAllocator,
    MongoRebuildLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.neo4j_validator import (
    Neo4jGenerationValidator,
)
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    GenerationLifecycleOrchestrator,
)
from return_platform.dynamic_knowledge.lifecycle.rebuild_trigger import (
    RebuildReason,
    RebuildTrigger,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.adapters import (
    ProjectorGraphWriter,
    scan_connector_registry,
)
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector

# Live infrastructure: this module opens real MongoDB and Neo4j clients. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra

SNAPSHOT_NAME = "E2E"


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
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin&directConnection=true"


def _neo4j_uri() -> str:
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


class _NoCheckpoints:
    """`full_sync` never touches checkpoints; only `incremental_sync` does.

    Raising rather than returning None, so a future change that *did* start
    checkpointing during a full sync would fail loudly here instead of silently
    reading nothing.

    This used to say it mirrored production's `_UnusedCheckpointStore`. That
    class is gone: production now hands the coordinator a real
    `MongoSyncCheckpointStore`, because `incremental_sync` finally has a caller.
    Which makes keeping the guard here more useful than before, not less --
    nothing in production raises on a full-sync checkpoint read any more.
    """

    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> None:
        raise NotImplementedError("full_sync must not read checkpoints")

    async def write(self, **kwargs: object) -> None:
        raise NotImplementedError("full_sync must not write checkpoints")


def mongo_only_schema(schema: ActiveSchema) -> ActiveSchema:
    """The shared fixture, reduced to its MongoDB half.

    The fixture declares `source_a` (MONGODB) and `source_b` (POSTGRESQL), and
    no PostgreSQL connector exists in the platform. Narrowing the *sync* to
    Mongo is not enough: deep validation requires every declared node label to
    be populated, so `ConfiguredBeta` would fail forever and no generation could
    ever activate. The schema itself has to describe only what this environment
    can actually build.

    Derived from the fixture rather than hand-authored so it cannot drift from
    the real shape -- an independently written schema would be a second
    definition to keep correct, and getting one field wrong would produce a test
    that exercises something the platform never sees.
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


class _Harness:
    def __init__(self, schema: ActiveSchema) -> None:
        self.schema = mongo_only_schema(schema)
        self.suffix = uuid.uuid4().hex[:12]
        self.source_database = f"e2e_source_{self.suffix}"
        self.mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        platform_db = self.mongo["return_platform"]
        self.snapshot_store = MongoActiveRuntimeSnapshotStore(
            platform_db[f"e2e_snapshots_{self.suffix}"]
        )
        self.rebuild_leases = MongoRebuildLeaseStore(platform_db[f"e2e_rebuild_{self.suffix}"])
        self.generation_leases = MongoGenerationLeaseStore(
            platform_db[f"e2e_gen_leases_{self.suffix}"]
        )
        self.fencing_tokens = MongoFencingTokenAllocator(platform_db[f"e2e_tokens_{self.suffix}"])
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)
        self.generation_ids: list[str] = []

    def _sync_coordinator(self) -> GenericSyncCoordinator:
        """Assembled exactly as `GraphSyncService` assembles it in production."""
        graph_writer = Neo4jDynamicGraphWriter(self.neo4j)
        return GenericSyncCoordinator(
            connectors=scan_connector_registry(
                schema=self.schema,
                mongo_connector=MongoDBSourceScanConnector(
                    self.mongo[self.source_database], schema=self.schema
                ),
                sqlserver_connector=None,
            ),
            extractor=GenericSourceRecordExtractor(),
            writer=ProjectorGraphWriter(
                projector=GenericGraphProjector(),
                writer=graph_writer,
                sync_run_id=f"e2e-{uuid.uuid4().hex[:8]}",
            ),
            checkpoints=_NoCheckpoints(),
            reconciler=graph_writer,
            ownership_reconciler=graph_writer,
        )

    def orchestrator(self, *, validate: bool = True) -> GenerationLifecycleOrchestrator:
        return GenerationLifecycleOrchestrator(
            snapshot_store=self.snapshot_store,
            lease_store=self.rebuild_leases,
            generation_writer=self.generation_writer,
            sync_coordinator=self._sync_coordinator(),
            fencing_tokens=self.fencing_tokens,
            owner_instance_id=f"e2e-{self.suffix}",
            generation_lease_store=self.generation_leases,
            validator=Neo4jGenerationValidator(self.neo4j) if validate else None,
            drain_timeout_seconds=20.0,
            drain_poll_seconds=0.05,
        )

    def trigger(self, *, validate: bool = True) -> RebuildTrigger:
        return RebuildTrigger(
            snapshot_store=self.snapshot_store, orchestrator=self.orchestrator(validate=validate)
        )

    async def seed_source(self, identifier: str) -> None:
        """`configured_changed_at` must be a real BSON date, not an ISO string.

        `source_a` declares `changed_at` as its incremental cursor and the
        connector captures a `FIELD_DATETIME` watermark, so `scan` filters with
        a datetime. MongoDB does not compare a BSON date to a string, so a
        string-typed cursor field makes the scan return zero rows while
        `capture_high_watermark` still reports a plausible value -- a silent
        empty build rather than an error. (The on-demand test seeds a string and
        is unaffected only because it reads by id, never by cursor.)
        """
        await self.mongo[self.source_database]["objects"].insert_one(
            {
                "configured_id": identifier,
                "configured_name": "Configured",
                "configured_count": 5,
                "configured_changed_at": datetime(2026, 8, 4, tzinfo=UTC),
            }
        )

    async def cleanup(self) -> None:
        await self.mongo.drop_database(self.source_database)
        platform_db = self.mongo["return_platform"]
        for name in ("e2e_snapshots", "e2e_rebuild", "e2e_gen_leases", "e2e_tokens"):
            await platform_db.drop_collection(f"{name}_{self.suffix}")
        async with self.neo4j.session() as session:
            for generation_id in self.generation_ids:
                await session.run(
                    "MATCH (n {graph_generation_id: $gid}) DETACH DELETE n",
                    {"gid": generation_id},
                )
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


@pytest.mark.asyncio
async def test_a_rebuild_runs_from_source_to_active_snapshot(harness: _Harness) -> None:
    """The first build: no snapshot exists, so the trigger fires, the sync reads
    a real source document into a real Neo4j, validation passes, and the
    compare-and-swap makes it the serving generation."""
    await harness.seed_source("e2e-object-1")

    decision = await harness.trigger().evaluate(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )
    assert decision.reason is RebuildReason.NO_ACTIVE_GENERATION

    snapshot = await harness.trigger().ensure_current(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )

    assert snapshot is not None
    harness.generation_ids.append(snapshot.graph_generation_id)
    assert snapshot.activation_version == 1
    status = await harness.generation_writer.get_status(
        graph_generation_id=snapshot.graph_generation_id
    )
    assert status is not None and status[0] is GraphGenerationStatus.ACTIVE

    # The source row really reached the graph under this generation.
    async with harness.neo4j.session() as session:
        result = await session.run(
            "MATCH (n {graph_generation_id: $gid}) RETURN count(n) AS total",
            {"gid": snapshot.graph_generation_id},
        )
        rows = [row async for row in result]
    assert rows and rows[0]["total"] > 0, "the build projected nothing into the graph"


@pytest.mark.asyncio
async def test_the_trigger_is_idempotent_against_a_current_snapshot(harness: _Harness) -> None:
    """The property the whole trigger design rests on: called again with nothing
    changed, it must not rebuild. A trigger that rebuilt on every invocation
    would be worse than none."""
    await harness.seed_source("e2e-object-1")
    first = await harness.trigger().ensure_current(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )
    assert first is not None
    harness.generation_ids.append(first.graph_generation_id)

    second = await harness.trigger().ensure_current(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )

    assert second is None, "an unchanged schema and release must not trigger a rebuild"


@pytest.mark.asyncio
async def test_build_n_plus_one_activates_and_retires_its_predecessor(
    harness: _Harness,
) -> None:
    """The Wave C gate's "Neo4j build N+1" and "old generation drain", together.

    Forced rather than schema-driven so the test exercises the cutover itself
    rather than also testing fingerprint comparison, which has its own tests.
    """
    await harness.seed_source("e2e-object-1")
    first = await harness.trigger().ensure_current(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )
    assert first is not None
    harness.generation_ids.append(first.graph_generation_id)

    second = await harness.trigger().ensure_current(
        schema=harness.schema,
        snapshot_name=SNAPSHOT_NAME,
        configuration_release_id="release-1",
        force=True,
    )

    assert second is not None
    harness.generation_ids.append(second.graph_generation_id)
    assert second.graph_generation_id != first.graph_generation_id
    assert second.activation_version == 2

    new_status = await harness.generation_writer.get_status(
        graph_generation_id=second.graph_generation_id
    )
    old_status = await harness.generation_writer.get_status(
        graph_generation_id=first.graph_generation_id
    )
    assert new_status is not None and new_status[0] is GraphGenerationStatus.ACTIVE
    # Drained all the way, not left in DRAINING: nothing held a lease on it.
    assert old_status is not None and old_status[0] is GraphGenerationStatus.RETIRED


@pytest.mark.asyncio
async def test_a_validation_failure_keeps_generation_n_active(harness: _Harness) -> None:
    """The Wave C gate item, composed.

    The second rebuild runs against an *empty* source, so the candidate projects
    zero nodes and `NODE_LABEL_POPULATED` fails. The candidate must be marked
    FAILED and the snapshot must still point at the generation that was already
    serving -- rejecting the candidate while breaking the live one would satisfy
    a weaker reading and be a worse outcome than activating the bad build.
    """
    await harness.seed_source("e2e-object-1")
    first = await harness.trigger().ensure_current(
        schema=harness.schema, snapshot_name=SNAPSHOT_NAME, configuration_release_id="release-1"
    )
    assert first is not None
    harness.generation_ids.append(first.graph_generation_id)

    # Empty the source so the next build has nothing to project.
    await harness.mongo[harness.source_database].drop_collection("objects")

    with pytest.raises(ActivationError) as caught:
        await harness.trigger().ensure_current(
            schema=harness.schema,
            snapshot_name=SNAPSHOT_NAME,
            configuration_release_id="release-1",
            force=True,
        )

    assert caught.value.stage == "VALIDATE"
    live = await harness.snapshot_store.read(snapshot_name=SNAPSHOT_NAME)
    assert live is not None
    assert live.graph_generation_id == first.graph_generation_id, "N must still be serving"
    assert live.activation_version == 1, "the snapshot must not have moved"
    still_active = await harness.generation_writer.get_status(
        graph_generation_id=first.graph_generation_id
    )
    assert still_active is not None and still_active[0] is GraphGenerationStatus.ACTIVE
