"""The return side, from real MongoDB documents to a real Neo4j graph.

Everything else about this change is asserted against the schema, the extractor
and the compiler in isolation. Two things only a live run can prove, and both are
the sort of failure that reports success:

* **The platform store is actually reached.** A connector is bound to one
  database, and the return-side sources name a different one from the Ferguson
  collections. Routed wrongly, the scan finds no `cases` collection, writes
  nothing, and the run completes.
* **Stage B really joins across the two stores.** The projector only sees one
  batch, so a case and its RMA -- read from two collections, pages apart -- can
  only be joined by the graph-side reconciliation pass. Unit-testing the
  compiled Cypher proves the text, not that Neo4j matches on it.

Assembly mirrors `data_platform/graph/sync_service.py`'s production recipe
(two `MongoDBSourceScanConnector`s, a `scan_connector_registry` with a per-source
override, `ProjectorGraphWriter`, `GenericSyncCoordinator`). A test that wired it
differently could pass while production's wiring was broken, which is worse than
no test.

Documents are written through `OperationalRepository`, not inserted by hand: the
shapes under test are the ones that repository produces, and a hand-built
document would drift the moment a writer changed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.sync_service import GraphSyncService
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType
from return_platform.dynamic_knowledge.sync.adapters import (
    ProjectorGraphWriter,
    scan_connector_registry,
)
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector

pytestmark = pytest.mark.asyncio(loop_scope="module")

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
GENERATION_ID = "return-side-real-infra"
FENCING_TOKEN = 1

CASE_ID = "case-real-1"
RECORD_ID = "rec-real-1"
SESSION_ID = "sess-real-1"
ORDER_REFERENCE = "CW273354"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn(database: str) -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        f"{database}?authSource=admin&directConnection=true"
    )


def _neo4j_uri() -> str:
    host = os.getenv("PLATFORM_TEST_NEO4J_HOST", "localhost")
    return f"bolt://{host}:7687"


class _NoCheckpoints:
    """`full_sync` never touches checkpoints, and a future change that started
    would fail loudly here rather than silently reading nothing."""

    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> None:
        raise NotImplementedError("full_sync must not read checkpoints")

    async def write(self, **kwargs: object) -> None:
        raise NotImplementedError("full_sync must not write checkpoints")


def _rebound(schema: ActiveSchema, platform_database: str) -> ActiveSchema:
    """The shipped schema, with the platform sources pointed at this run's database.

    Derived from the real descriptor rather than hand-authored: an independently
    written schema would be a second definition to keep correct, and one wrong
    field path would produce a test that exercises something production never
    sees. Only `object_ref.database` moves, which is exactly the value
    `GraphSyncService` routes on.
    """
    platform_ids = GraphSyncService.platform_store_source_ids(
        schema, frozenset(schema.sources), "return_platform"
    )
    sources = {
        source_id: (
            source.model_copy(
                update={"object_ref": {**source.object_ref, "database": platform_database}}
            )
            if source_id in platform_ids
            else source
        )
        for source_id, source in schema.sources.items()
    }
    return schema.model_copy(update={"sources": sources})


class _Harness:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.platform_database = f"return_side_platform_{self.suffix}"
        self.source_database = f"return_side_source_{self.suffix}"
        self.schema = _rebound(load_active_schema(SCHEMA_PATH), self.platform_database)
        self.mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            _mongo_dsn(self.platform_database)
        )
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )

    @property
    def settings(self) -> Settings:
        """Only what `OperationalRepository` reads. Everything else comes from the
        environment, the same four overrides `tests/api/test_case_detail_multi_rma`
        uses to point a repository at a throwaway database."""
        return Settings(
            environment="test",
            mongo_dsn=SecretStr(_mongo_dsn(self.platform_database)),
            mongo_database=self.platform_database,
            source_mongo_database=self.source_database,
        )

    def coordinator(self) -> GenericSyncCoordinator:
        """Assembled exactly as `GraphSyncService._sync_participating_sources` does."""
        mongo_source_ids = frozenset(
            source_id
            for source_id, source in self.schema.sources.items()
            if source.connector_type is ConnectorType.MONGODB
        )
        platform_ids = GraphSyncService.platform_store_source_ids(
            self.schema, mongo_source_ids, self.platform_database
        )
        upstream = MongoDBSourceScanConnector(self.mongo[self.source_database], schema=self.schema)
        platform = MongoDBSourceScanConnector(
            self.mongo[self.platform_database], schema=self.schema
        )
        writer = Neo4jDynamicGraphWriter(self.neo4j)
        return GenericSyncCoordinator(
            connectors=scan_connector_registry(
                schema=self.schema,
                mongo_connector=upstream,
                sqlserver_connector=None,
                overrides={source_id: platform for source_id in platform_ids},
            ),
            extractor=GenericSourceRecordExtractor(),
            writer=ProjectorGraphWriter(
                projector=GenericGraphProjector(),
                writer=writer,
                sync_run_id=f"return-side-{self.suffix}",
            ),
            checkpoints=_NoCheckpoints(),
            reconciler=writer,
            ownership_reconciler=writer,
        )

    def participating(self) -> frozenset[str]:
        """Only the platform store and the order source it joins to.

        The other upstream collections are absent from this run's throwaway
        source database, and scanning them would add nothing but time.
        """
        mongo_source_ids = frozenset(
            source_id
            for source_id, source in self.schema.sources.items()
            if source.connector_type is ConnectorType.MONGODB
        )
        return GraphSyncService.platform_store_source_ids(
            self.schema, mongo_source_ids, self.platform_database
        ) | {"source_sales"}

    async def rows(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        async with self.neo4j.session() as session:
            result = await session.run(cypher, parameters)
            return [dict(record) async for record in result]

    async def close(self) -> None:
        await self.mongo.drop_database(self.platform_database)
        await self.mongo.drop_database(self.source_database)
        await self.neo4j.close()
        await self.mongo.close()


async def _seed_operational_documents(harness: _Harness) -> None:
    """Through the repository, so the shapes are the ones production writes."""
    repository = OperationalRepository(harness.mongo, harness.settings)
    await repository.ensure_indexes()

    await repository.create_case(
        case_id=CASE_ID,
        tenant_id="tenant-a",
        principal_id="associate-1",
        branch_id="CHARLOTTE",
        channel_a_conversation_id="conv-1",
        confirmed_order_reference=ORDER_REFERENCE,
        confirmation_key=f"tenant-a|conv-1|{ORDER_REFERENCE}|L1,L2",
    )
    # The session link the placement edge joins on. Written directly because no
    # production writer sets it yet -- see the report accompanying this change.
    case = await repository.get_case(CASE_ID)
    assert case is not None
    await repository.update_case(
        CASE_ID, {"sessionId": SESSION_ID}, expected_version=int(case["version"])
    )

    await repository.create_return_record(
        return_record_id=RECORD_ID,
        case_id=CASE_ID,
        return_reference="RMA-1001",
        status="ISSUED",
        source_system="RETURN_SUPPORT",
    )
    await repository.update_return_record(
        RECORD_ID,
        {"returnLocation": "DC-7", "trackingReference": "1Z999AA10123456784"},
        expected_version=0,
    )

    # Two items on one RMA -- decision D6 -- and a third the RMA does not cover.
    for index, line in enumerate(("L1", "L2"), start=1):
        await repository.create_case_return_item(
            return_item_id=f"item-{index}",
            case_id=CASE_ID,
            return_record_id=RECORD_ID,
            order_line_reference=line,
            product_reference="3180140",
            quantity=1,
            reason="Damaged on arrival",
        )
    await repository.create_case_return_item(
        return_item_id="item-3",
        case_id=CASE_ID,
        return_record_id=None,
        order_line_reference="L3",
    )

    await repository.persist_return_intake_records(
        session_id=SESSION_ID,
        order_line_id="L1",
        product_id="3180140",
        reason_code="DAMAGED",
        requested_quantity=1,
        approved_method="BRANCH_UPS",
        product_presence="IN_BRANCH",
        package_count=1,
        pickup_assessment=None,
        attachment_ids=[],
        actor_id="associate-1",
    )
    handling_unit_id = f"{SESSION_ID}:HU:1"
    handling_unit = await repository.get_handling_unit(handling_unit_id)
    assert handling_unit is not None
    await repository.update_handling_unit(
        handling_unit_id,
        {
            "physicalStatus": "WAREHOUSE_STAGED",
            "bayId": "BAY-04",
            "warehouseId": "1969",
            "reservationId": "res-1",
            "assignmentId": "asn-1",
        },
        expected_version=int(handling_unit["version"]),
    )


async def _seed_order_document(harness: _Harness) -> None:
    """One `salesInv` header, so the case has an order to join to.

    Inserted rather than built through a repository because `salesInv` is an
    upstream collection this platform only ever reads. The paths are the ones
    the active schema configures for `sales_order`.
    """
    await harness.mongo[harness.source_database]["salesInv"].insert_one(
        {
            "_id": f"CHARLOTTE*{ORDER_REFERENCE}",
            "salesHdrEventMeta": {"lastUpdateTs": "2026-08-12T09:00:00Z"},
            "salesHdrEventData": {
                "accountId": "CHARLOTTE",
                "orderId": ORDER_REFERENCE,
                "docType": "headerLines",
                "orderStatus": "INVOICED",
            },
            "salesHdr": {
                "salesHdrData": {
                    "custId": "9911",
                    "custName": "Atlas Mechanical",
                }
            },
            "customer": {"address": []},
            "salesLines": [],
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def harness() -> AsyncIterator[_Harness]:
    built = _Harness()
    async with built.neo4j.session() as session:
        await (
            await session.run(
                "MERGE (g:GraphGeneration {generation_id: $id}) "
                "ON CREATE SET g.fencing_token = $token, g.status = $status",
                id=GENERATION_ID,
                token=FENCING_TOKEN,
                status=GraphGenerationStatus.ACTIVE.value,
            )
        ).consume()
    await _seed_operational_documents(built)
    await _seed_order_document(built)
    await built.coordinator().full_sync(
        schema=built.schema,
        graph_generation_id=GENERATION_ID,
        fencing_token=FENCING_TOKEN,
        source_asset_ids=built.participating(),
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    try:
        yield built
    finally:
        async with built.neo4j.session() as session:
            await (
                await session.run(
                    "MATCH (n {graph_generation_id: $id}) DETACH DELETE n", id=GENERATION_ID
                )
            ).consume()
            await (
                await session.run(
                    "MATCH (g:GraphGeneration {generation_id: $id}) DETACH DELETE g",
                    id=GENERATION_ID,
                )
            ).consume()
        await built.close()


async def test_the_platform_store_is_actually_read(harness: _Harness) -> None:
    """The silent failure this whole routing change exists to prevent.

    Bound to the upstream database, the scan finds no `cases` collection, writes
    nothing, and reports a completed run.
    """
    rows = await harness.rows(
        "MATCH (c:ReturnCase {graph_generation_id: $id}) "
        "RETURN c.case_id AS case_id, c.case_status AS status, "
        "c.confirmed_order_reference AS order_reference",
        id=GENERATION_ID,
    )

    assert [row["case_id"] for row in rows] == [CASE_ID]
    assert rows[0]["order_reference"] == ORDER_REFERENCE


async def test_every_return_side_label_is_populated(harness: _Harness) -> None:
    for label, expected in (
        ("ReturnCase", 1),
        ("ReturnRecord", 1),
        # Three case-scoped items plus the session-scoped intake item, which
        # shares the collection and must not be dropped.
        ("ReturnItem", 4),
        ("ReturnHandlingUnit", 1),
    ):
        rows = await harness.rows(
            f"MATCH (n:`{label}` {{graph_generation_id: $id}}) RETURN count(n) AS total",
            id=GENERATION_ID,
        )
        assert rows[0]["total"] == expected, label


async def test_the_case_reaches_the_order_it_was_raised_against(harness: _Harness) -> None:
    """`Customer -PLACED_ORDER-> SalesOrder <-COVERS_ORDER- ReturnCase`, joined by
    Stage B across two databases -- which is the traversal behind "has this
    customer returned this before"."""
    rows = await harness.rows(
        "MATCH (c:ReturnCase {graph_generation_id: $id})-[:COVERS_ORDER]->"
        "(o:SalesOrder {graph_generation_id: $id}) "
        "RETURN o.sales_order_number AS order_number, o.account_id AS account_id",
        id=GENERATION_ID,
    )

    assert rows == [{"order_number": ORDER_REFERENCE, "account_id": "CHARLOTTE"}]


async def test_the_rma_covers_only_the_items_assigned_to_it(harness: _Harness) -> None:
    """One RMA covers many items, and an item on no RMA yet joins nothing.

    The negative half is the one that matters: a graph in which every item hung
    off the first record would look correct and send half the shipment to the
    wrong place.
    """
    covered = await harness.rows(
        "MATCH (r:ReturnRecord {graph_generation_id: $id})-[:COVERS_RETURN_ITEM]->"
        "(i:ReturnItem {graph_generation_id: $id}) "
        "RETURN i.order_line_reference AS line ORDER BY line",
        id=GENERATION_ID,
    )

    assert [row["line"] for row in covered] == ["L1", "L2"]


async def test_an_item_with_no_rma_is_still_reachable_from_the_case(
    harness: _Harness,
) -> None:
    """The associate names the lines before Support says how many RMAs they
    become. Reaching items only through the record would make that window
    invisible."""
    rows = await harness.rows(
        "MATCH (c:ReturnCase {graph_generation_id: $id})-[:INCLUDES_RETURN_ITEM]->"
        "(i:ReturnItem {graph_generation_id: $id}) "
        "WHERE i.return_record_id IS NULL "
        "RETURN i.order_line_reference AS line",
        id=GENERATION_ID,
    )

    assert [row["line"] for row in rows] == ["L3"]


async def test_the_case_reaches_the_bay_its_parcel_is_staged_in(harness: _Harness) -> None:
    """ "Where is this parcel now", end to end. The bay and warehouse are written
    onto the handling unit by `WarehousePlacementService.assign`; nothing else in
    the graph carries an observed location."""
    rows = await harness.rows(
        "MATCH (c:ReturnCase {graph_generation_id: $id})-[:STAGED_AS]->"
        "(h:ReturnHandlingUnit {graph_generation_id: $id}) "
        "RETURN h.bay_id AS bay_id, h.warehouse_id AS warehouse_id, "
        "h.physical_status AS physical_status",
        id=GENERATION_ID,
    )

    assert rows == [
        {"bay_id": "BAY-04", "warehouse_id": "1969", "physical_status": "WAREHOUSE_STAGED"}
    ]


async def test_the_session_scoped_intake_item_joins_no_case(harness: _Harness) -> None:
    """The older shape in the same collection has no `caseId`.

    It projects -- dropping it would lose the item list the legacy intake flow
    still writes -- but it must not attach itself to a case it never belonged
    to. `Neo4j`'s null semantics do that; this pins that they were relied on
    rather than worked around.
    """
    rows = await harness.rows(
        "MATCH (i:ReturnItem {graph_generation_id: $id}) "
        "WHERE i.case_id IS NULL "
        "RETURN i.return_item_id AS item_id, "
        "size([(c:ReturnCase)-[:INCLUDES_RETURN_ITEM]->(i) | c]) AS owners",
        id=GENERATION_ID,
    )

    assert [row["item_id"] for row in rows] == [f"{SESSION_ID}:L1"]
    assert rows[0]["owners"] == 0
