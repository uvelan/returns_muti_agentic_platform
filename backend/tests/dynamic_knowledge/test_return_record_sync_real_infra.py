"""W2.5 against real Mongo and real Neo4j: the RMA is queryable afterwards.

The step's Validation clause is "a newly created return is queryable on the next
agent turn", and the only way to establish that is to write the record where
`OperationalRepository` writes it, run the record-scoped sync, and then read the
graph back through the same `CypherCompiler` an agent's plan compiles through.

The one thing a unit test cannot show is the part that was actually broken:
`MongoDBSourceScanConnector` is bound to one database for its lifetime and
ignores what the source declares, so before the per-source override
`source_return_records` resolved to the *upstream* connector, found no such
collection, and the sync reported SUCCEEDED having written nothing. That is
invisible unless two real databases exist.
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
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.integration.return_record_sync import (
    RETURN_RECORD_ENTITY_ID,
    RETURN_RECORD_KEY_FIELD_ID,
    GraphReturnRecordSync,
    ReturnRecordSyncFailed,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import platform_store_source_ids
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector

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
    """`directConnection=true` deliberately.

    The deployment runs a single-node replica set that advertises its *container*
    hostname, so ordinary topology discovery from the host resolves a name that
    does not exist there and every operation times out. A direct connection skips
    discovery and works identically from inside the network, so one DSN serves
    both -- which is the difference between this proof being run and being
    skipped.
    """
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


class _Resolver:
    def __init__(self, graph_generation_id: str) -> None:
        self._graph_generation_id = graph_generation_id

    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return self._graph_generation_id


class _Harness:
    """Owns the real resources for one test and their teardown."""

    def __init__(self, schema: ActiveSchema) -> None:
        self.schema = schema
        self.suffix = uuid.uuid4().hex[:12]
        self.generation = f"test-gen-{self.suffix}"
        self.case_id = f"case-{self.suffix}"
        self.record_id = f"rec-{self.suffix}"
        self.sync_collection = f"dynamic_order_agent_on_demand_sync_test_{self.suffix}"
        self.mongo: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)

    def sync(self, *, upstream_only: bool = False) -> GraphReturnRecordSync:
        """The production stack, with one knob.

        `upstream_only` reproduces the wiring as it was before the per-source
        override: every Mongo source resolved to the upstream connector. It
        exists so a test can demonstrate what that produced rather than assert
        the fix in the abstract.
        """
        platform_connector = MongoDBSourceScanConnector(
            self.mongo[PLATFORM_DATABASE], schema=self.schema
        )
        upstream_connector = MongoDBSourceScanConnector(
            self.mongo[f"upstream_absent_{self.suffix}"], schema=self.schema
        )
        overrides = (
            {}
            if upstream_only
            else {
                source_id: platform_connector
                for source_id in platform_store_source_ids(self.schema, PLATFORM_DATABASE)
            }
        )
        coordinator = OnDemandSyncCoordinator(
            connectors=targeted_connector_registry(
                schema=self.schema,
                mongo=upstream_connector,
                sqlserver=None,
                overrides=overrides,
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
        return GraphReturnRecordSync(
            schema=self.schema,
            on_demand_sync=coordinator,
            generation_handles=GenerationHandleProvider(_Resolver(self.generation)),
        )

    async def write_return_record(self) -> None:
        """Exactly the shape `OperationalRepository.create_return_record` writes."""
        now = datetime.now(UTC)
        await self.mongo[PLATFORM_DATABASE]["return_records"].insert_one(
            {
                "returnRecordId": self.record_id,
                "caseId": self.case_id,
                "returnReference": f"RMA-{self.suffix}",
                "status": "ISSUED",
                "returnLocation": "DC-7",
                "trackingReference": "1Z999AA10123456784",
                "labelReference": "LBL-1",
                "shippingInstructionReference": None,
                "sourceSystem": "RETURN_SUPPORT",
                "version": 1,
                "createdAt": now,
                "updatedAt": now,
            }
        )

    async def read_back(self) -> list[dict[str, Any]]:
        """Through the compiler, the way an agent's plan reaches the graph."""
        plan = LogicalQueryPlan(
            operation=QueryOperation.FILTER,
            start_entity_id=RETURN_RECORD_ENTITY_ID,
            fields=(RETURN_RECORD_KEY_FIELD_ID, "return_reference", "return_record_status"),
            filters=(
                QueryCondition(
                    entity_id=RETURN_RECORD_ENTITY_ID,
                    field_id=RETURN_RECORD_KEY_FIELD_ID,
                    operator="EQUALS",
                    value=self.record_id,
                ),
            ),
            limit=5,
        )
        compiled = CypherCompiler().compile_read(self.schema, plan)
        async with self.neo4j.session() as session:
            result = await session.run(compiled.cypher, compiled.parameters)
            return [dict(record) async for record in result]

    async def cleanup(self) -> None:
        await self.mongo[PLATFORM_DATABASE]["return_records"].delete_many(
            {"returnRecordId": self.record_id}
        )
        await self.mongo[PLATFORM_DATABASE][self.sync_collection].drop()
        await self.mongo.drop_database(f"upstream_absent_{self.suffix}")
        async with self.neo4j.session() as session:
            await session.run(
                "MATCH (n:ReturnRecord {return_record_id: $id}) DETACH DELETE n",
                {"id": self.record_id},
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
async def test_a_committed_return_record_is_queryable_from_the_graph_afterwards(
    descriptor: ActiveSchema,
) -> None:
    """The step's Validation clause, against both real stores.

    The record is written where the platform writes it, the record-scoped sync
    runs, and the graph then answers a compiled plan for it. Before this, the
    only thing that projected `return_records` was the scheduled sync, so an
    associate asking "did the RMA come through" was told no until the next run.
    """
    harness = _Harness(descriptor)
    try:
        await harness.write_return_record()
        await harness.generation_writer.create_generation(
            graph_generation_id=harness.generation, fencing_token=1
        )

        # Nothing is in the graph for this record yet.
        assert await harness.read_back() == []

        outcome = await harness.sync().synchronize_records(
            case_id=harness.case_id, return_record_ids=(harness.record_id,)
        )

        assert outcome.graph_generation_id == harness.generation
        assert outcome.nodes_written >= 1
        rows = await harness.read_back()
        assert len(rows) == 1
        assert rows[0]["return_reference"] == f"RMA-{harness.suffix}"
        assert rows[0]["return_record_status"] == "ISSUED"
    finally:
        await harness.cleanup()


@pytest.mark.asyncio
async def test_the_upstream_connector_cannot_reach_the_platform_store(
    descriptor: ActiveSchema,
) -> None:
    """What the per-source override fixes, demonstrated rather than described.

    Routed to the upstream connector, the targeted read finds no `return_records`
    collection -- no error, an empty page -- and the projection writes nothing.
    The guard turns that into a loud failure instead of a SUCCEEDED receipt over
    an empty graph, which is the only reason it is visible at all.
    """
    harness = _Harness(descriptor)
    try:
        await harness.write_return_record()
        await harness.generation_writer.create_generation(
            graph_generation_id=harness.generation, fencing_token=1
        )

        with pytest.raises(ReturnRecordSyncFailed, match="without writing a node"):
            await harness.sync(upstream_only=True).synchronize_records(
                case_id=harness.case_id, return_record_ids=(harness.record_id,)
            )

        assert await harness.read_back() == []
    finally:
        await harness.cleanup()
