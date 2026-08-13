"""A real salesInv document, fetched on demand, landing in a real Neo4j.

`test_on_demand_sync_production_wiring.py` already proves the wiring against
real infrastructure -- but against the synthetic `active_schema` fixture, whose
one entity has four flat top-level fields, no `where` selector and no exploded
children. That shape cannot express the defect this module exists to pin: the
targeted read's projection was derived from the anchoring entity's own mapped
fields, which for the *production* schema silently removes
`salesHdrEventData.docType` (the discriminator `sales_order`'s `where` tests)
and the whole of `salesLines[]`. The result was a sync that reported SUCCEEDED
having written no order at all.

So this runs the shipped `active-schema.return-order.yaml` against a real
MongoDB collection with a real salesInv-shaped document, through the real
`MongoDBSourceScanConnector.targeted_read` -- so the projection is applied by
MongoDB itself rather than by anything in this repository -- and asserts the
order, its lines and its customer are in Neo4j afterwards, and that the run is
visible in the platform's one sync ledger.

Real-infra: needs the compose MongoDB replica set and Neo4j. Never run by the
host suite.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.data_platform.graph.sync_service import (
    GRAPH_SYNC_RUNS_COLLECTION,
    MongoTargetedSyncRunLedger,
    sync_run_view,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.mongo_store import MongoOnDemandSyncStore
from return_platform.dynamic_knowledge.integration.on_demand_sync_adapters import (
    OnDemandNeo4jGraphWriter,
    targeted_connector_registry,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import SyncOrigin
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector

pytestmark = pytest.mark.asyncio

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
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin&directConnection=true"


def _neo4j_uri() -> str:
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _sales_inv(account_id: str, order_number: str) -> dict[str, Any]:
    """A salesInv header shaped the way `active-schema.return-order.yaml` maps it.

    `docType` and the extra un-mapped keys are both deliberate: the first is
    what the entity's `where` selector tests and the second is what proves the
    read is still projected rather than pulling whatever the collection holds.
    """
    return {
        "_id": f"{account_id}*{order_number}",
        "salesHdrEventMeta": {"lastUpdateTs": "2026-08-04T09:00:00Z"},
        "salesHdrEventData": {
            "accountId": account_id,
            "orderId": order_number,
            "docType": "headerLines",
            "orderStatus": "OPEN",
            "salesType": "COUNTER",
            "sellWhseId": "W1",
            "shipFromWhseId": "W1",
            "srcSysCode": "ECLIPSE",
        },
        "salesHdr": {
            "salesHdrData": {
                "custId": "C-REAL-1",
                "custName": "Jane Doe",
                "custPONumber": "PO-77",
                "jobName": "Kitchen refit",
                "orderDate": "2026-08-01T00:00:00Z",
                "invoiceDate": "2026-08-02T00:00:00Z",
                "shipping": {
                    "commitDate": "2026-08-05T00:00:00Z",
                    "shipDate": "2026-08-03T00:00:00Z",
                    "shipViaDesc": "Ground",
                    "shipTo": {
                        "address": {
                            "shipToName": "Jane Doe",
                            "shipToPhone": "555-0100",
                            "city": "Charlotte",
                            "state": "NC",
                            "zipCode": "28202",
                        }
                    },
                },
            }
        },
        "customer": {
            "address": [
                {
                    "email": "jane@example.com",
                    "phoneNumber": "555-0100",
                    "address1": "1 High Street",
                    "city": "Charlotte",
                    "state": "NC",
                    "postalCode": "28202",
                    "county": "Mecklenburg",
                }
            ]
        },
        "salesLines": [
            {
                "salesLnsEventData": {"lineNumber": "1", "lineType": "PRODUCT"},
                "lineData": {
                    "productId": "P-1",
                    "masterProductId": "MP-1",
                    "altCode1": "FAU-1234",
                    "productDesc": "Chrome faucet",
                    "orderQty": 2,
                    "shipQty": 2,
                    "boQty": 0,
                    "netPrice": 89.0,
                    "lineNetAmt": 178.0,
                    "invenWhse": "W1",
                },
            },
            {
                "salesLnsEventData": {"lineNumber": "2", "lineType": "PRODUCT"},
                "lineData": {
                    "productId": "P-2",
                    "masterProductId": "MP-2",
                    "altCode1": "SNK-9",
                    "productDesc": "Stainless sink",
                    "orderQty": 1,
                    "shipQty": 0,
                    "boQty": 1,
                    "netPrice": 240.0,
                    "lineNetAmt": 240.0,
                    "invenWhse": "W1",
                },
            },
        ],
        # Never mapped by the schema. If this reaches the graph the read stopped
        # being a governed projection.
        "internalMargin": {"cost": 12.5, "notes": "do not surface"},
    }


class _Harness:
    """Owns one test's real Mongo/Neo4j resources, uniquely named, and drops them."""

    def __init__(self, schema: ActiveSchema) -> None:
        self.schema = schema
        self.suffix = uuid.uuid4().hex[:12]
        self.source_database_name = f"on_demand_ferguson_test_{self.suffix}"
        self.sync_store_collection = f"on_demand_sync_ferguson_test_{self.suffix}"
        self.graph_generation_id = f"ferguson-gen-{self.suffix}"
        self.account_id = f"ACC{self.suffix[:6].upper()}"
        self.order_number = f"CW{self.suffix[:6].upper()}"
        self.mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)
        self.ledger = MongoTargetedSyncRunLedger(self.mongo, PLATFORM_DATABASE)

    @property
    def order_key(self) -> str:
        return f"{self.account_id}*{self.order_number}"

    def coordinator(self) -> OnDemandSyncCoordinator:
        return OnDemandSyncCoordinator(
            connectors=targeted_connector_registry(
                schema=self.schema,
                mongo=MongoDBSourceScanConnector(
                    self.mongo[self.source_database_name], schema=self.schema
                ),
                sqlserver=None,
            ),
            extractor=GenericSourceRecordExtractor(),
            projector=GenericGraphProjector(),
            writer=OnDemandNeo4jGraphWriter(
                Neo4jDynamicGraphWriter(self.neo4j), self.generation_writer
            ),
            store=MongoOnDemandSyncStore(
                self.mongo, PLATFORM_DATABASE, collection=self.sync_store_collection
            ),
            run_ledger=self.ledger,
        )

    async def seed(self) -> None:
        # The collection name comes from the schema's own object_ref, so this
        # writes where the connector will look rather than where the test thinks
        # it should.
        collection = self.schema.sources["source_sales"].object_ref["name"]
        await self.mongo[self.source_database_name][collection].insert_one(
            _sales_inv(self.account_id, self.order_number)
        )
        await self.generation_writer.create_generation(
            graph_generation_id=self.graph_generation_id, fencing_token=1
        )

    async def nodes(self, label: str) -> list[dict[str, Any]]:
        async with self.neo4j.session() as session:
            result = await session.run(
                f"MATCH (n:`{label}` {{graph_generation_id: $gid}}) RETURN n AS node "
                "ORDER BY n.line_number",
                {"gid": self.graph_generation_id},
            )
            return [dict(record["node"]) async for record in result]

    async def cleanup(self) -> None:
        await self.mongo.drop_database(self.source_database_name)
        await self.mongo[PLATFORM_DATABASE][self.sync_store_collection].drop()
        await self.mongo[PLATFORM_DATABASE][GRAPH_SYNC_RUNS_COLLECTION].delete_many(
            {"graphGenerationId": self.graph_generation_id}
        )
        async with self.neo4j.session() as session:
            await session.run(
                "MATCH (n {graph_generation_id: $gid}) DETACH DELETE n",
                {"gid": self.graph_generation_id},
            )
            await session.run(
                "MATCH (g:GraphGeneration {generation_id: $gid}) DETACH DELETE g",
                {"gid": self.graph_generation_id},
            )
            await session.run(
                "MATCH (r:GraphWriteReceipt {graph_generation_id: $gid}) DETACH DELETE r",
                {"gid": self.graph_generation_id},
            )
        await self.mongo.close()
        await self.neo4j.close()


async def test_an_anchored_order_arrives_in_the_graph_with_its_lines(
    production_schema: ActiveSchema,
) -> None:
    """The failure that started this: the order was fetched and then discarded.

    MongoDB applies the projection here, not a helper in this repository, so a
    projection that omits `salesHdrEventData.docType` really does hand the
    extractor a document whose `where` cannot pass -- and this test really does
    fail rather than pass on a technicality.
    """
    harness = _Harness(production_schema)
    try:
        await harness.seed()
        receipt = await harness.coordinator().synchronize(
            schema=production_schema,
            graph_generation_id=harness.graph_generation_id,
            request_digest=f"ferguson-{harness.suffix}",
            plan=build_targeted_read_plan(
                schema=production_schema,
                entity_id="sales_order",
                normalized_anchors={"order_key": ("EXACT", harness.order_key)},
            ),
        )

        assert receipt.status.value == "SUCCEEDED"
        assert receipt.nodes_written > 0

        orders = await harness.nodes("SalesOrder")
        assert [order["sales_order_number"] for order in orders] == [harness.order_number]
        assert orders[0]["order_status"] == "OPEN"

        lines = await harness.nodes("OrderLine")
        assert [line["line_number"] for line in lines] == ["1", "2"]
        assert {line["product_description"] for line in lines} == {
            "Chrome faucet",
            "Stainless sink",
        }

        # The customer travels on the same document, so an anchored order read
        # resolves the customer relationship without a second sync.
        customers = await harness.nodes("Customer")
        assert [customer["customer_id"] for customer in customers] == ["C-REAL-1"]
    finally:
        await harness.cleanup()


async def test_the_targeted_read_still_projects_only_governed_paths(
    production_schema: ActiveSchema,
) -> None:
    """Widening the projection must not have become reading the whole document.

    `internalMargin` is in the source document and in no entity's field map. A
    node carrying it would mean the targeted read had stopped being a governed
    projection -- the property the narrow projection existed to provide.
    """
    harness = _Harness(production_schema)
    try:
        await harness.seed()
        await harness.coordinator().synchronize(
            schema=production_schema,
            graph_generation_id=harness.graph_generation_id,
            request_digest=f"ferguson-governed-{harness.suffix}",
            plan=build_targeted_read_plan(
                schema=production_schema,
                entity_id="sales_order",
                normalized_anchors={"order_key": ("EXACT", harness.order_key)},
            ),
        )

        for order in await harness.nodes("SalesOrder"):
            assert "internalMargin" not in order
            assert "cost" not in order
    finally:
        await harness.cleanup()


async def test_the_run_appears_in_the_platform_sync_ledger(
    production_schema: ActiveSchema,
) -> None:
    """The screen's read. One ledger, and a targeted run attributed to its turn."""
    harness = _Harness(production_schema)
    try:
        await harness.seed()
        await harness.coordinator().synchronize(
            schema=production_schema,
            graph_generation_id=harness.graph_generation_id,
            request_digest=f"ferguson-ledger-{harness.suffix}",
            plan=build_targeted_read_plan(
                schema=production_schema,
                entity_id="sales_order",
                normalized_anchors={"order_key": ("EXACT", harness.order_key)},
            ),
            origin=SyncOrigin(
                agent_id="order-discovery-agent",
                conversation_id=f"conv-{harness.suffix}",
                client_turn_id="turn-1",
                entity_id="sales_order",
                strong_anchor_id="exact_order_key",
                anchor_field_ids=("order_key",),
            ),
        )

        documents = await (
            harness.mongo[PLATFORM_DATABASE][GRAPH_SYNC_RUNS_COLLECTION]
            .find({"graphGenerationId": harness.graph_generation_id})
            .to_list()
        )
        assert len(documents) == 1
        view = sync_run_view(documents[0])

        assert view.mode == "ON_DEMAND"
        # COMPLETED, not SUCCEEDED: the ledger speaks one vocabulary regardless
        # of which mechanism produced the run.
        assert view.status == "COMPLETED"
        assert view.completedAt is not None
        assert view.sourceCounts["source_sales"] > 0
        assert view.requestedBy is not None
        assert view.requestedBy.conversationId == f"conv-{harness.suffix}"
        # Field ids, never the order number.
        assert view.requestedBy.anchorFieldIds == ["order_key"]
        assert harness.order_number not in str(documents[0])
    finally:
        await harness.cleanup()
