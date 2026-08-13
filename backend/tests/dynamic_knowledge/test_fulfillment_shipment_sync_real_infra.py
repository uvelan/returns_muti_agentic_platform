"""W2.6 against real Mongo and real Neo4j: a tracking number is synced, then read.

The step's Validation clause is "a tracking number not previously in the graph is
synced on demand and then read **from the graph, not SQL**". This establishes it
against both real stores, through the production adapters.

**One deviation from the shipped descriptor, stated rather than hidden.**
`shipment` ships as `source_access: SEED_ONLY` with `source_contract_status:
UNVERIFIED`, because no `shipmentInfo` sample has ever been supplied and its
physical paths are carried over from the original schema unchecked. The adapter
honours that and skips the sync -- so exercising the sync half at all requires
promoting the entity here. That is a real precondition, not test scaffolding:
until somebody verifies `shipmentInfoEventData.*` against a genuine document and
flips the descriptor, fulfillment reads whatever a scheduled sync left behind
and reports `SHIPMENT_ABSENT` otherwise. Nothing else in the mechanism changes.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.mongo_store import MongoOnDemandSyncStore
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.integration.shipment_observations import (
    SHIPMENT_ENTITY_ID,
    GraphShipmentObservations,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    SourceContractStatus,
)
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.workflows.fulfillment_tracking import ShipmentEvidence

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
PLATFORM_DATABASE = "return_platform"


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
        f"mongodb://{username}:{password}@{host}:27017/"
        f"{PLATFORM_DATABASE}?authSource=admin&directConnection=true"
    )


def _neo4j_uri() -> str:
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


def _connected(schema: ActiveSchema) -> ActiveSchema:
    """The descriptor with `shipment` promoted -- see the module docstring."""
    document = schema.model_dump(mode="json")
    document["entities"][SHIPMENT_ENTITY_ID]["source_access"] = (
        EntitySourceAccess.CONNECTED_SYNC.value
    )
    document["entities"][SHIPMENT_ENTITY_ID]["source_contract_status"] = (
        SourceContractStatus.VERIFIED.value
    )
    return ActiveSchema.model_validate(document)


class _Resolver:
    def __init__(self, graph_generation_id: str) -> None:
        self._graph_generation_id = graph_generation_id

    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return self._graph_generation_id


class _Harness:
    def __init__(self, schema: ActiveSchema) -> None:
        self.schema = schema
        self.suffix = uuid.uuid4().hex[:12]
        self.generation = f"test-gen-{self.suffix}"
        self.tracking = f"1Z{self.suffix.upper()}"
        self.source_database = f"shipment_source_test_{self.suffix}"
        self.sync_collection = f"dynamic_order_agent_on_demand_sync_test_{self.suffix}"
        self.mongo: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)

    def observations(self) -> GraphShipmentObservations:
        coordinator = OnDemandSyncCoordinator(
            connectors=targeted_connector_registry(
                schema=self.schema,
                mongo=MongoDBSourceScanConnector(
                    self.mongo[self.source_database], schema=self.schema
                ),
                sqlserver=None,
            ),
            extractor=GenericSourceRecordExtractor(),
            projector=GenericGraphProjector(),
            writer=OnDemandNeo4jGraphWriter(
                Neo4jDynamicGraphWriter(self.neo4j), self.generation_writer
            ),
            store=MongoOnDemandSyncStore(
                self.mongo, PLATFORM_DATABASE, collection=self.sync_collection
            ),
        )
        return GraphShipmentObservations(
            schema=self.schema,
            on_demand_sync=coordinator,
            generation_handles=GenerationHandleProvider(_Resolver(self.generation)),
            knowledge_gateway=Neo4jKnowledgeGateway(self.neo4j, database="neo4j"),
        )

    async def write_shipment(self) -> None:
        """A `shipmentInfo` document at the paths the descriptor declares."""
        await self.mongo[self.source_database]["shipmentInfo"].insert_one(
            {
                "shipmentInfoEventData": {
                    "trkNum": self.tracking,
                    "trilOrdNum": "CW273354",
                    "carrierCode": "UPS",
                    "shipmentId": f"SHP-{self.suffix}",
                    "currentStatus": "PICKED_UP",
                    "srcSystem": "DISPATCHTRACK",
                },
                "updatedAt": datetime.now(UTC),
            }
        )

    async def cleanup(self) -> None:
        await self.mongo.drop_database(self.source_database)
        await self.mongo[PLATFORM_DATABASE][self.sync_collection].drop()
        async with self.neo4j.session() as session:
            await session.run(
                "MATCH (n:Shipment {tracking_number: $t}) DETACH DELETE n", {"t": self.tracking}
            )
            await session.run(
                "MATCH (g:GraphGeneration {generationId: $gid}) DETACH DELETE g",
                {"gid": self.generation},
            )
            await session.run(
                "MATCH (r:GraphWriteReceipt {graph_generation_id: $gid}) DETACH DELETE r",
                {"gid": self.generation},
            )
        await self.mongo.close()
        await self.neo4j.close()


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


@pytest.mark.asyncio
async def test_a_tracking_number_absent_from_the_graph_is_synced_and_then_read(
    descriptor: ActiveSchema,
) -> None:
    """The step's Validation clause, against both real stores.

    Read first to establish the number is genuinely absent, then observe. The
    status that comes back is the one the *source* holds, not one inferred from
    the platform having written a reference -- which is the whole of what W2.6
    changes.
    """
    harness = _Harness(_connected(descriptor))
    try:
        await harness.write_shipment()
        await harness.generation_writer.create_generation(
            graph_generation_id=harness.generation, fencing_token=1
        )
        observations = harness.observations()

        async with harness.neo4j.session() as session:
            result = await session.run(
                "MATCH (n:Shipment {tracking_number: $t}) RETURN n", {"t": harness.tracking}
            )
            assert [record async for record in result] == []

        observation = await observations.observe(harness.tracking)

        assert observation.evidence is ShipmentEvidence.OBSERVED
        assert observation.current_status == "PICKED_UP"
        assert observation.carrier_code == "UPS"
        assert observation.sync_request_id is not None
        assert observation.sync_skipped_reason is None
        assert observation.graph_generation_id == harness.generation
    finally:
        await harness.cleanup()


@pytest.mark.asyncio
async def test_a_tracking_number_no_source_knows_reads_as_absent(
    descriptor: ActiveSchema,
) -> None:
    """Not an error, and not `IN_TRANSIT`.

    A label printed and left on the counter produces exactly this: a reference
    the platform minted and a carrier that has never seen the parcel.
    """
    harness = _Harness(_connected(descriptor))
    try:
        await harness.generation_writer.create_generation(
            graph_generation_id=harness.generation, fencing_token=1
        )

        observation = await harness.observations().observe(harness.tracking)

        assert observation.evidence is ShipmentEvidence.ABSENT
        assert observation.current_status is None
    finally:
        await harness.cleanup()


@pytest.mark.asyncio
async def test_the_shipped_descriptor_skips_the_sync_and_still_reads_the_graph(
    descriptor: ActiveSchema,
) -> None:
    """What actually happens in production today, asserted rather than assumed.

    `shipment` is SEED_ONLY, so no targeted read is issued -- and the graph is
    still consulted, because a scheduled sync may have projected the shipment. A
    reader of the fulfillment evidence sees `SOURCE_ACCESS_SEED_ONLY` on the
    observation and `SHIPMENT_ABSENT` on the stage result rather than a silent
    downgrade.
    """
    harness = _Harness(descriptor)
    try:
        await harness.write_shipment()
        await harness.generation_writer.create_generation(
            graph_generation_id=harness.generation, fencing_token=1
        )

        observation = await harness.observations().observe(harness.tracking)

        assert observation.sync_skipped_reason == "SOURCE_ACCESS_SEED_ONLY"
        assert observation.sync_request_id is None
        assert observation.evidence is ShipmentEvidence.ABSENT
    finally:
        await harness.cleanup()
