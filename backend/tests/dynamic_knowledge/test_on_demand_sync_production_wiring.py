"""Real Mongo + real Neo4j proof that the on-demand-sync production adapters
(runtime_factory.build_dynamic_order_agent_runtime's on_demand_sync stack)
work end-to-end, and that they fence on graph_generation_id matching exactly
-- proving the LEGACY_GENERATION_ID unification fix
(test_mongo_graph_state_provider.py) is load-bearing for on-demand sync, not
just cosmetic: a write targeting any id other than the exact literal a real
GraphGeneration marker was created under must fail closed.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import pytest
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.mongo_store import MongoOnDemandSyncStore
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    NoGenerationMarker,
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector

# Live infrastructure: this module opens real MongoDB and Neo4j clients. It is not named
# `*_real_infra.py`, so this marker is what keeps it out of the default run
# and inside `scripts/dev/run_real_infra_suite.sh`.
pytestmark = pytest.mark.live_infra


def _required_env(name: str) -> str:
    """Mirrors tests/conftest.py's _required_environment_variable -- this
    module deliberately avoids the shared `test_settings` fixture (which also
    requires NVIDIA_API_KEY/GOOGLE_API_KEY for AI-gateway fields this test
    never exercises) so a real-Mongo/real-Neo4j proof of the on-demand-sync
    wiring does not depend on unrelated model-provider credentials."""

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
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


def _neo4j_uri() -> str:
    # Same "localhost by default, compose service name when attached to the
    # platform network" convention tests/conftest.py's test_settings uses for
    # Mongo/SQL Server -- test_settings itself hardcodes "bolt://localhost:7687"
    # since Neo4j (unlike the Mongo replica set) has no internal-hostname
    # topology rewrite, so host-side runs work either way; this override only
    # matters for a container attached directly to the compose network.
    # `PLATFORM_TEST_NEO4J_URI` first -- the same variable
    # `tests/conftest.py::test_settings` reads -- because the published port is
    # not always 7687. Windows dynamically reserves 7454-7553 and 7679-7778,
    # which contain Neo4j's 7474 and 7687, so Docker cannot publish them
    # ("socket forbidden by its access permissions") and the stack maps Bolt to
    # 17687 instead. A helper that let only the *host* move could not be pointed
    # at it at all, and every test in this module failed to connect.
    uri = os.getenv("PLATFORM_TEST_NEO4J_URI")
    if uri and uri.strip():
        return uri.strip()
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


class _Harness:
    """Owns the real Mongo/Neo4j resources for one test and their teardown."""

    def __init__(self, active_schema: ActiveSchema) -> None:
        self.schema = active_schema
        self.suffix = uuid.uuid4().hex[:12]
        self.source_database_name = f"on_demand_source_test_{self.suffix}"
        self.sync_store_collection = f"dynamic_order_agent_on_demand_sync_test_{self.suffix}"
        self.mongo_client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j_driver = AsyncGraphDatabase.driver(
            _neo4j_uri(),
            auth=("neo4j", _required_env("GRAPH_PASSWORD")),
        )
        self.generation_writer = Neo4jGenerationWriter(self.neo4j_driver)
        self.sync_store = MongoOnDemandSyncStore(
            self.mongo_client, "return_platform", collection=self.sync_store_collection
        )

    def build_coordinator(self) -> OnDemandSyncCoordinator:
        connectors = targeted_connector_registry(
            schema=self.schema,
            mongo=MongoDBSourceScanConnector(
                self.mongo_client[self.source_database_name], schema=self.schema
            ),
            sqlserver=None,
        )
        return OnDemandSyncCoordinator(
            connectors=connectors,
            extractor=GenericSourceRecordExtractor(),
            projector=GenericGraphProjector(),
            writer=OnDemandNeo4jGraphWriter(
                Neo4jDynamicGraphWriter(self.neo4j_driver), self.generation_writer
            ),
            store=self.sync_store,
        )

    async def insert_source_document(self, entity_id_value: str) -> None:
        await self.mongo_client[self.source_database_name]["objects"].insert_one(
            {
                "configured_id": entity_id_value,
                "configured_name": "Configured",
                "configured_count": 5,
                "configured_changed_at": "2026-08-04T00:00:00Z",
            }
        )

    async def cleanup(
        self, *, entity_id_values: tuple[str, ...], graph_generation_ids: tuple[str, ...]
    ) -> None:
        await self.mongo_client[self.source_database_name].drop_collection("objects")
        await self.mongo_client["return_platform"][self.sync_store_collection].drop()
        async with self.neo4j_driver.session() as session:
            for entity_id_value in entity_id_values:
                await session.run(
                    "MATCH (n:ConfiguredAlpha {configured_id: $id}) DETACH DELETE n",
                    {"id": entity_id_value},
                )
            for graph_generation_id in graph_generation_ids:
                await session.run(
                    "MATCH (g:GraphGeneration {generationId: $gid}) DETACH DELETE g",
                    {"gid": graph_generation_id},
                )
                await session.run(
                    "MATCH (r:GraphWriteReceipt {graph_generation_id: $gid}) DETACH DELETE r",
                    {"gid": graph_generation_id},
                )
        await self.mongo_client.close()
        await self.neo4j_driver.close()


@pytest.mark.asyncio
async def test_on_demand_sync_writes_a_real_node_when_a_generation_marker_exists(
    active_schema: ActiveSchema,
) -> None:
    harness = _Harness(active_schema)
    graph_generation_id = f"test-gen-{harness.suffix}"
    entity_id_value = f"A-{harness.suffix}"
    try:
        await harness.insert_source_document(entity_id_value)
        await harness.generation_writer.create_generation(
            graph_generation_id=graph_generation_id, fencing_token=1
        )
        coordinator = harness.build_coordinator()
        plan = build_targeted_read_plan(
            schema=active_schema,
            entity_id="entity_a",
            normalized_anchors={"id": ("EXACT", entity_id_value)},
        )
        receipt = await coordinator.synchronize(
            schema=active_schema,
            graph_generation_id=graph_generation_id,
            request_digest=f"digest-{harness.suffix}",
            plan=plan,
        )
        assert receipt.status.value == "SUCCEEDED"
        assert receipt.nodes_written == 1

        async with harness.neo4j_driver.session() as session:
            result = await session.run(
                "MATCH (n:ConfiguredAlpha {configured_id: $id}) RETURN n.configured_name AS name",
                {"id": entity_id_value},
            )
            rows = [record async for record in result]
        assert rows == [{"name": "Configured"}]
    finally:
        await harness.cleanup(
            entity_id_values=(entity_id_value,), graph_generation_ids=(graph_generation_id,)
        )


@pytest.mark.asyncio
async def test_on_demand_sync_fails_closed_when_no_generation_marker_exists(
    active_schema: ActiveSchema,
) -> None:
    harness = _Harness(active_schema)
    graph_generation_id = f"test-gen-missing-{harness.suffix}"
    entity_id_value = f"A-{harness.suffix}"
    try:
        await harness.insert_source_document(entity_id_value)
        # Deliberately never calling create_generation -- no marker exists.
        coordinator = harness.build_coordinator()
        plan = build_targeted_read_plan(
            schema=active_schema,
            entity_id="entity_a",
            normalized_anchors={"id": ("EXACT", entity_id_value)},
        )
        with pytest.raises(NoGenerationMarker):
            await coordinator.synchronize(
                schema=active_schema,
                graph_generation_id=graph_generation_id,
                request_digest=f"digest-{harness.suffix}",
                plan=plan,
            )
    finally:
        await harness.cleanup(
            entity_id_values=(entity_id_value,), graph_generation_ids=(graph_generation_id,)
        )


def test_legacy_generation_id_is_the_one_shared_literal() -> None:
    """Pure sanity check that this module reasons about the same literal
    data_platform.graph.sync_service creates its Neo4j marker under and
    MongoGraphStateProvider's fallback resolves to (see
    test_mongo_graph_state_provider.py for the real-Mongo proof of the
    latter) -- both must agree byte-for-byte or every on-demand write against
    a not-yet-generation-managed schema fence-rejects, as the next test
    demonstrates against real Neo4j."""
    from return_platform.data_platform.graph.sync_service import _LEGACY_GENERATION_ID

    assert _LEGACY_GENERATION_ID == LEGACY_GENERATION_ID == "legacy-live"


@pytest.mark.asyncio
async def test_on_demand_sync_requires_the_generation_id_to_match_the_marker_exactly(
    active_schema: ActiveSchema,
) -> None:
    """Proves the on-demand write path fences on graph_generation_id equality,
    not mere presence of *some* marker -- the exact failure mode the
    LEGACY_GENERATION_ID unification fixes for the real "legacy-live" literal.
    Uses fresh, uniquely-suffixed stand-in ids (never the shared "legacy-live"
    literal itself) so this test cannot disturb marker state any other
    suite/dev workflow depends on."""
    harness = _Harness(active_schema)
    entity_id_value = f"A-{harness.suffix}"
    real_generation_id = f"legacy-live-test-{harness.suffix}"
    pre_fix_derived_id = f"legacy-{harness.suffix}-checksum-derived"
    try:
        await harness.insert_source_document(entity_id_value)
        # Mirrors what sync_service.py creates for the real "legacy-live" marker.
        await harness.generation_writer.create_generation(
            graph_generation_id=real_generation_id, fencing_token=1
        )
        coordinator = harness.build_coordinator()
        plan = build_targeted_read_plan(
            schema=active_schema,
            entity_id="entity_a",
            normalized_anchors={"id": ("EXACT", entity_id_value)},
        )

        # Mirrors the pre-fix bug: MongoGraphStateProvider's old derivation
        # resolved a different literal than the marker actually created under,
        # so every on-demand write fence-rejected.
        with pytest.raises(NoGenerationMarker):
            await coordinator.synchronize(
                schema=active_schema,
                graph_generation_id=pre_fix_derived_id,
                request_digest=f"digest-mismatch-{harness.suffix}",
                plan=plan,
            )

        # The exact literal the marker was created under succeeds.
        receipt = await coordinator.synchronize(
            schema=active_schema,
            graph_generation_id=real_generation_id,
            request_digest=f"digest-match-{harness.suffix}",
            plan=plan,
        )
        assert receipt.status.value == "SUCCEEDED"
    finally:
        await harness.cleanup(
            entity_id_values=(entity_id_value,),
            graph_generation_ids=(real_generation_id,),
        )
