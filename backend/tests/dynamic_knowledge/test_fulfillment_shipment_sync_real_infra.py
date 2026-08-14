"""W2.6 against real Mongo and real Neo4j: a tracking number is synced, then read.

The step's Validation clause is "a tracking number not previously in the graph is
synced on demand and then read **from the graph, not SQL**". This establishes it
against both real stores, through the production adapters, **on the descriptor as
shipped**.

That last part is the point of this module. These tests used to promote
`shipment` from `SEED_ONLY`/`UNVERIFIED` to `CONNECTED_SYNC`/`VERIFIED` in the
fixture, because no `shipmentInfo` sample had ever been supplied and the adapter
rightly refuses a targeted read against paths nobody has confirmed. A sample now
exists, the paths are confirmed, and the descriptor says so -- so the promotion
is gone and the sync exercised below is the one production runs. A test that has
to edit the configuration to reach the behaviour it asserts is evidence about a
configuration that does not exist.

The documents written here carry the shape the verified contract declares:
`shipmentInfoEventData` for the shipment's own fields and
`shipmentInfoEventMeta.lastUpdateTs` for the change timestamp. Writing the old
`carrierCode`/`shippedAt`/root-`updatedAt` shape would pass just as happily and
prove nothing about real documents.
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
    RelationshipSourceAccess,
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


def _demoted(schema: ActiveSchema) -> ActiveSchema:
    """The descriptor with `shipment` pushed back to `SEED_ONLY`.

    The inverse of the promotion this module used to need. One test still wants
    the refusal path, and demoting for it keeps every other test on the shipped
    descriptor rather than the other way round.
    """
    document = schema.model_dump(mode="json")
    document["entities"][SHIPMENT_ENTITY_ID]["source_access"] = EntitySourceAccess.SEED_ONLY.value
    document["entities"][SHIPMENT_ENTITY_ID]["source_contract_status"] = (
        SourceContractStatus.UNVERIFIED.value
    )
    # Relationship access is capped by its endpoints, so demoting the entity
    # would otherwise make the whole descriptor fail validation.
    document["graph"]["relationships"]["order_shipped_as"]["access"] = (
        RelationshipSourceAccess.SEED_ONLY.value
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
        """A `shipmentInfo` document in the shape real ones have.

        Every key is one the 100-document sample confirmed, including the
        meta block the change timestamp lives in -- there is no root `updatedAt`
        on a real document, and a fixture that invented one would let a wrong
        cursor path pass.
        """
        await self.mongo[self.source_database]["shipmentInfo"].insert_one(
            {
                "_id": f"DIST*CW273354*{self.tracking}",
                "shipmentInfoEventData": {
                    "trkNum": self.tracking,
                    "trilOrdNum": "CW273354",
                    "shipmentId": f"SHP-{self.suffix}",
                    "acctId": "DIST",
                    "currentStatus": "intransit",
                    "srcSystem": "DispatchTrack",
                },
                "shipmentInfoEventMeta": {
                    "docType": "disptrck",
                    "insertTs": datetime.now(UTC),
                    "lastUpdateTs": datetime.now(UTC),
                    "updatedBy": "shipment-writer-v1",
                },
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
    """The step's Validation clause, against both real stores and no promotion.

    Read first to establish the number is genuinely absent, then observe. The
    status that comes back is the one the *source* holds, not one inferred from
    the platform having written a reference -- which is the whole of what W2.6
    changes.

    Would catch a descriptor that stopped permitting the targeted sync, and a
    physical path that stopped resolving against a real document: the observation
    would come back `ABSENT` with a `sync_skipped_reason`, or `OBSERVED` with a
    status of `None`.
    """
    harness = _Harness(descriptor)
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
        assert observation.current_status == "intransit"
        assert observation.shipment_id == f"SHP-{harness.suffix}"
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
    harness = _Harness(descriptor)
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
async def test_an_entity_demoted_to_seed_only_skips_the_sync_and_still_reads_the_graph(
    descriptor: ActiveSchema,
) -> None:
    """The refusal path, which is a configuration setting and not dead code.

    An entity whose source contract stops holding is demoted, and the adapter
    must then decline the targeted read while still consulting the graph -- a
    scheduled sync may have projected the shipment. A reader of the fulfillment
    evidence sees `SOURCE_ACCESS_SEED_ONLY` on the observation and
    `SHIPMENT_ABSENT` on the stage result rather than a silent downgrade.

    Would catch a promotion that made the access check unreachable: with the
    check gone, this demoted schema would sync anyway and the observation would
    come back OBSERVED.
    """
    harness = _Harness(_demoted(descriptor))
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
