"""SHIP-01, closed: a shipment update reaches the graph and then the associate.

RG-09 proved the write half against real SQL Server -- a duplicate changes
nothing, and DELIVERED@15:00 followed by IN_TRANSIT@14:00 still reads DELIVERED.
This closes the loop that half opened. Contract C4 requires shipment state to be
"persisted, graph-synchronized and fulfilment-readable", and the first of those
three was the only one anything proved.

The whole chain, against real SQL Server, real MongoDB and real Neo4j, through
the production adapters and **on the descriptor as shipped**:

    record_shipment_update -> dbo.return_tracking (SQL Server)
                           -> targeted sync       (Neo4j, generation-leased)
                           -> fulfilment read     (the graph, not SQL)
                           -> case fact           (MongoDB)
                           -> the associate's next turn

Nothing here is a mock. The one thing standing in for production is the
composition root: `run_return_workflow_worker.py` builds this stack once and
hands it to the repository, and this file builds the same stack from the same
`from_access`-shaped pieces. A test that mocked the sync could not tell an
APPLIED update from a STALE one, which is the distinction the whole module is
about.

The negative cases matter as much as the positive one. A DUPLICATE or a STALE
update changed no stored truth, so neither may spend a targeted sync nor append a
case fact -- an event log in which a *refused* regressive carrier event looks
exactly like an accepted one is a log nobody can audit.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pymssql
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
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
    GraphShipmentObservations,
)
from return_platform.dynamic_knowledge.integration.shipment_state_sync import (
    GraphShipmentStateSync,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_shipment_state import (
    FULFILLMENT_EVIDENCE_FACT,
    FULFILLMENT_STATUS_FACT,
    ReturnShipmentStateService,
)
from return_platform.operations.sql_business_state import (
    SHIPMENT_UPDATE_APPLIED,
    SHIPMENT_UPDATE_DUPLICATE,
    SHIPMENT_UPDATE_STALE,
    CaseReturnRecordsWrite,
    ReturnRecordWrite,
    ShipmentUpdate,
    SQLBusinessStateRepository,
)
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.workflows.fulfillment_tracking import ShipmentEvidence
from return_platform.workflows.stage_results import FulfillmentTrackingStatus

pytestmark = pytest.mark.asyncio(loop_scope="module")

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
SHIPMENT_DATABASE = "return_shipment_graph_probe"
_CONNECT_DEADLINE_SECONDS = 30

#: `dbo.return_tracking` comes from 002 and is extended by 006; `dbo.return_case`
#: and `dbo.return_record` come from 005 and are what makes an RMA resolvable to
#: a case. All four applied in order, so this suite proves the migration chain
#: rather than a hand-copied table definition.
_MIGRATIONS = (
    "001_return_business_state.sql",
    "002_domain_models.sql",
    "005_case_return_records.sql",
    "006_return_shipment_state.sql",
)

TENANT = "tenant-a"
PRINCIPAL = "associate-1"

_1400 = datetime(2026, 8, 14, 14, 0, tzinfo=UTC).replace(tzinfo=None)
_1500 = datetime(2026, 8, 14, 15, 0, tzinfo=UTC).replace(tzinfo=None)


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


def _connect(settings: Settings, database: str) -> Any:
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=database,
        login_timeout=10,
        timeout=30,
        autocommit=True,
    )


def _connect_within_deadline(settings: Settings, database: str) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shipment-graph-connect")
    future = executor.submit(_connect, settings, database)
    try:
        return future.result(timeout=_CONNECT_DEADLINE_SECONDS)
    except FutureTimeoutError:
        raise RuntimeError(
            f"SQL Server at {settings.sqlserver_host}:{settings.sqlserver_port} did not "
            f"complete login within {_CONNECT_DEADLINE_SECONDS}s."
        ) from None
    finally:
        executor.shutdown(wait=False)


def _open_with_retry(settings: Settings, database: str) -> Any:
    deadline = time.monotonic() + _CONNECT_DEADLINE_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _connect_within_deadline(settings, database)
        except pymssql.Error as exc:
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"{database} did not become connectable: {last}")


def _batches(migration: str) -> tuple[str, ...]:
    """The migration's own SQL, with its `USE` stripped by line.

    By line and not by batch: these files open with a comment block, so `USE`
    shares batch one with it and a batch-level filter silently lets it through --
    which points every statement at the application's own database.
    """
    text = (
        files("return_platform")
        .joinpath("configuration/sql_migrations")
        .joinpath(migration)
        .read_text(encoding="utf-8")
    )
    without_use = re.sub(
        r"^\s*USE\s+\[?[A-Za-z0-9_]+\]?\s*;?\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE
    )
    return tuple(
        batch.strip()
        for batch in re.split(r"^\s*GO\s*$", without_use, flags=re.IGNORECASE | re.MULTILINE)
        if batch.strip()
    )


class _Resolver:
    """The generation this run serves from.

    A fixed id rather than the deployment's active one: this suite creates and
    retires its own generation, and leasing production's would make a parallel
    track's cutover this suite's problem.
    """

    def __init__(self, graph_generation_id: str) -> None:
        self._graph_generation_id = graph_generation_id

    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return self._graph_generation_id


class _Harness:
    """The composition root, assembled as `run_return_workflow_worker.py` does.

    One targeted-sync stack, shared by the write side (`GraphShipmentStateSync`)
    and the read side (`GraphShipmentObservations`), because that is how the
    worker builds it: two stacks would take two leases against two views of the
    same generation and the drain would count neither correctly.
    """

    def __init__(self, schema: ActiveSchema, settings: Settings) -> None:
        self.schema = schema
        self.suffix = uuid.uuid4().hex[:12]
        self.generation = f"ship-gen-{self.suffix}"
        self.platform_database = f"shipment_graph_platform_{self.suffix}"
        self.source_database = f"shipment_graph_source_{self.suffix}"
        self.settings = settings.model_copy(
            update={
                "mongo_dsn": SecretStr(_mongo_dsn(self.platform_database)),
                "mongo_database": self.platform_database,
                "source_mongo_database": self.source_database,
                "sqlserver_database": SHIPMENT_DATABASE,
            }
        )
        self.mongo: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            _mongo_dsn(self.platform_database)
        )
        self.neo4j = AsyncGraphDatabase.driver(
            _neo4j_uri(), auth=("neo4j", _required_env("GRAPH_PASSWORD"))
        )
        self.generation_writer = Neo4jGenerationWriter(self.neo4j)
        self.handles = GenerationHandleProvider(_Resolver(self.generation))
        coordinator = OnDemandSyncCoordinator(
            connectors=targeted_connector_registry(
                schema=schema,
                mongo=MongoDBSourceScanConnector(self.mongo[self.source_database], schema=schema),
                sqlserver=None,
            ),
            extractor=GenericSourceRecordExtractor(),
            projector=GenericGraphProjector(),
            writer=OnDemandNeo4jGraphWriter(
                Neo4jDynamicGraphWriter(self.neo4j), self.generation_writer
            ),
            store=MongoOnDemandSyncStore(self.mongo, self.platform_database),
        )
        self.observations = GraphShipmentObservations(
            schema=schema,
            on_demand_sync=coordinator,
            generation_handles=self.handles,
            knowledge_gateway=Neo4jKnowledgeGateway(self.neo4j, database="neo4j"),
        )
        self.business_state = SQLBusinessStateRepository(
            self.settings,
            shipment_graph_sync=GraphShipmentStateSync(
                schema=schema,
                on_demand_sync=coordinator,
                generation_handles=self.handles,
            ),
        )
        self.repository = OperationalRepository(self.mongo, self.settings)
        self.service = ReturnShipmentStateService(
            business_state=self.business_state,
            repository=self.repository,
            observations=self.observations,
        )
        self.tracking_references: list[str] = []

    async def start(self) -> None:
        await self.generation_writer.create_generation(
            graph_generation_id=self.generation, fencing_token=1
        )

    def parcel(self, label: str) -> str:
        tracking = f"1Z{self.suffix.upper()}{label}"
        self.tracking_references.append(tracking)
        return tracking

    async def publish_to_carrier_source(self, tracking: str, status: str = "intransit") -> None:
        """A `shipmentInfo` document in the shape real ones have.

        Every key is one the verified contract declares, including the meta block
        the change timestamp lives in -- there is no root `updatedAt` on a real
        document, and a fixture that invented one would let a wrong cursor path
        pass.
        """
        await self.mongo[self.source_database]["shipmentInfo"].insert_one(
            {
                "_id": f"DIST*CW273354*{tracking}",
                "shipmentInfoEventData": {
                    "trkNum": tracking,
                    "trilOrdNum": "CW273354",
                    "shipmentId": f"SHP-{tracking}",
                    "acctId": "DIST",
                    "currentStatus": status,
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

    async def issue_rma(self, tracking: str) -> tuple[str, str]:
        """Persist a case and its RMA through the production write (T-14).

        Not raw INSERTs: `read_return_record_by_reference` has to find the case
        by the same `UQ_return_record_reference` the real writer relies on, and a
        hand-built row could satisfy this test while the real one did not.
        """
        case_id = f"case-{uuid.uuid4().hex[:12]}"
        return_reference = f"RMA-{uuid.uuid4().hex[:10].upper()}"
        await self.business_state.persist_case_return_records(
            CaseReturnRecordsWrite(
                case_id=case_id,
                tenant_id=TENANT,
                principal_id=PRINCIPAL,
                order_reference="CW273354",
                records=(
                    ReturnRecordWrite(
                        return_record_id=str(uuid.uuid4()),
                        return_reference=return_reference,
                        tracking_reference=tracking,
                    ),
                ),
            )
        )
        return case_id, return_reference

    async def shipment_nodes(self, tracking: str) -> list[dict[str, Any]]:
        async with self.neo4j.session() as session:
            result = await session.run(
                "MATCH (n:Shipment {tracking_number: $t}) RETURN n", {"t": tracking}
            )
            return [dict(record["n"]) async for record in result]

    async def facts(self, case_id: str, fact_name: str) -> list[dict[str, Any]]:
        return [
            fact
            for fact in await self.repository.list_case_facts(case_id)
            if fact["factName"] == fact_name
        ]

    async def cleanup(self) -> None:
        from return_platform.operations.sql_connection_pool import close_sql_connection_pools

        close_sql_connection_pools(drain_timeout_seconds=10.0)
        async with self.neo4j.session() as session:
            for tracking in self.tracking_references:
                await session.run(
                    "MATCH (n:Shipment {tracking_number: $t}) DETACH DELETE n", {"t": tracking}
                )
            await session.run(
                "MATCH (g:GraphGeneration {generationId: $gid}) DETACH DELETE g",
                {"gid": self.generation},
            )
            await session.run(
                "MATCH (r:GraphWriteReceipt {graph_generation_id: $gid}) DETACH DELETE r",
                {"gid": self.generation},
            )
        await self.mongo.drop_database(self.source_database)
        await self.mongo.drop_database(self.platform_database)
        await self.neo4j.close()
        await self.mongo.close()


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


@pytest.fixture
def shipment_settings(test_settings: Settings) -> Settings:
    """Function-scoped because `test_settings` is, and every statement below is
    `IF NOT EXISTS` -- re-applying the chain per test costs a few round trips and
    keeps the migrations, rather than a hand-copied schema, as the definition."""
    admin = _connect_within_deadline(test_settings, "master")
    with admin:
        with admin.cursor() as cursor:
            cursor.execute(
                "IF DB_ID(%(name)s) IS NULL EXEC('CREATE DATABASE [' + %(name)s + ']')",
                {"name": SHIPMENT_DATABASE},
            )

    settings = test_settings.model_copy(update={"sqlserver_database": SHIPMENT_DATABASE})
    owner = _open_with_retry(settings, SHIPMENT_DATABASE)
    with owner:
        with owner.cursor() as cursor:
            for migration in _MIGRATIONS:
                for batch in _batches(migration):
                    cursor.execute(batch)
    return settings


@pytest_asyncio.fixture(loop_scope="module")
async def harness(descriptor: ActiveSchema, shipment_settings: Settings) -> AsyncIterator[_Harness]:
    """A fresh generation, source database and platform database per test.

    Per test rather than per module because the sync store deduplicates on a
    digest that includes the generation: a second test reusing the first's would
    have its sync answered from the first's receipt and would prove nothing about
    its own update.
    """
    harness = _Harness(descriptor, shipment_settings)
    await harness.start()
    try:
        yield harness
    finally:
        cleanup = _open_with_retry(harness.settings, SHIPMENT_DATABASE)
        with cleanup:
            with cleanup.cursor() as cursor:
                cursor.execute("DELETE FROM dbo.return_tracking")
                cursor.execute("DELETE FROM dbo.return_record_item")
                cursor.execute("DELETE FROM dbo.return_record")
                cursor.execute("DELETE FROM dbo.return_case")
        await harness.cleanup()


def _update(return_reference: str, tracking: str, status: str, at: datetime) -> ShipmentUpdate:
    return ShipmentUpdate(
        return_reference=return_reference,
        tracking_reference=tracking,
        shipment_status=status,
        status_at=at,
        # Stated rather than defaulted: `tracking_type` lost its `"PPL"` default in
        # CFG-03 because a ship-via is observed, never assumed.
        tracking_type="PPL",
        carrier_code="UPS",
    )


async def test_an_applied_update_puts_the_shipment_in_the_graph(harness: _Harness) -> None:
    """The step SHIP-01 was missing: the write path now reaches the graph.

    Asserted on the store's own return value *and* on Neo4j, before anything
    reads: `ShipmentUpdateOutcome.graph_generation_id` says which generation
    answers the next turn, and the node says the projection actually committed
    there. Reading first establishes the parcel is genuinely absent, so a node
    that appears afterwards can only have come from this update.
    """
    tracking = harness.parcel("A")
    await harness.publish_to_carrier_source(tracking)
    _, return_reference = await harness.issue_rma(tracking)
    assert await harness.shipment_nodes(tracking) == []

    outcome = await harness.business_state.record_shipment_update(
        _update(return_reference, tracking, "IN_TRANSIT", _1400)
    )

    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert outcome.graph_generation_id == harness.generation
    nodes = await harness.shipment_nodes(tracking)
    assert len(nodes) == 1
    assert nodes[0]["current_status"] == "intransit"


async def test_the_update_becomes_a_fulfilment_state_the_associate_can_see(
    harness: _Harness,
) -> None:
    """The closure, end to end: SQL -> graph -> fulfilment -> the case.

    The fact lands on `case_facts`, which is what `RepositoryCaseStore.case_facts`
    projects into the agent's turn context -- the same route that makes an RMA
    appear in the associate's original conversation. `IN_TRANSIT` here is
    concluded from a shipment observed in the graph, never from the platform
    having written a tracking reference into its own store.
    """
    tracking = harness.parcel("B")
    await harness.publish_to_carrier_source(tracking)
    case_id, return_reference = await harness.issue_rma(tracking)

    outcome, reading = await harness.service.record_update(
        _update(return_reference, tracking, "IN_TRANSIT", _1400)
    )

    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert reading is not None
    assert reading.case_id == case_id
    assert reading.evidence is ShipmentEvidence.OBSERVED
    assert reading.status is FulfillmentTrackingStatus.IN_TRANSIT
    assert reading.graph_generation_id == harness.generation

    status_facts = await harness.facts(case_id, FULFILLMENT_STATUS_FACT)
    assert [fact["value"] for fact in status_facts] == ["IN_TRANSIT"]
    evidence_facts = await harness.facts(case_id, FULFILLMENT_EVIDENCE_FACT)
    assert [fact["value"] for fact in evidence_facts] == [
        f"SHIPMENT_OBSERVED:{harness.generation}:intransit"
    ]


async def test_a_duplicate_update_syncs_nothing_and_tells_the_case_nothing(
    harness: _Harness,
) -> None:
    """RG-09's first half, now with the graph and the case behind it.

    The same observation submitted twice is one observation. A second targeted
    sync would write the graph a value it already holds, and a second case fact
    would tell the associate something changed when nothing did.
    """
    tracking = harness.parcel("C")
    await harness.publish_to_carrier_source(tracking)
    case_id, return_reference = await harness.issue_rma(tracking)
    update = _update(return_reference, tracking, "IN_TRANSIT", _1400)

    first, _ = await harness.service.record_update(update)
    repeat, reading = await harness.service.record_update(update)

    assert first.graph_generation_id == harness.generation
    assert repeat.outcome == SHIPMENT_UPDATE_DUPLICATE
    assert repeat.applied is False
    assert repeat.graph_generation_id is None, "a duplicate spent a targeted sync"
    assert reading is None
    assert len(await harness.facts(case_id, FULFILLMENT_STATUS_FACT)) == 1


async def test_a_stale_update_never_reaches_the_graph_or_the_case(harness: _Harness) -> None:
    """RG-09's second half: DELIVERED@15:00, then IN_TRANSIT@14:00.

    The older event is submitted last, on purpose. The stored truth must still be
    DELIVERED, the graph must not have been told otherwise, and the case must not
    carry a reading derived from an update the store refused -- which is what
    would make a rejected regressive event indistinguishable from an accepted one
    everywhere downstream.
    """
    tracking = harness.parcel("D")
    await harness.publish_to_carrier_source(tracking, status="delivered")
    case_id, return_reference = await harness.issue_rma(tracking)

    delivered, _ = await harness.service.record_update(
        _update(return_reference, tracking, "DELIVERED", _1500)
    )
    regressive, reading = await harness.service.record_update(
        _update(return_reference, tracking, "IN_TRANSIT", _1400)
    )

    assert delivered.outcome == SHIPMENT_UPDATE_APPLIED
    assert regressive.outcome == SHIPMENT_UPDATE_STALE
    assert regressive.graph_generation_id is None, "a stale update spent a targeted sync"
    assert reading is None
    # The outcome reports the truth that stands, not the update that was refused.
    assert regressive.current_status == "DELIVERED"
    assert (await harness.business_state.read_shipment_state(return_reference))[0][
        "tracking_status"
    ] == "DELIVERED"
    assert [fact["value"] for fact in await harness.facts(case_id, FULFILLMENT_STATUS_FACT)] == [
        "IN_TRANSIT"
    ]


async def test_a_parcel_no_carrier_has_filed_is_awaiting_handoff_not_in_transit(
    harness: _Harness,
) -> None:
    """A label printed and left on the counter.

    The platform accepting an update for a tracking number is not evidence that
    anything moved, and the carrier source may not have published the parcel yet.
    The update still applies, the sync still runs and writes no node, and
    fulfilment reports `ABSENT` -- which is a state, not a fault. Raising on a
    sync that wrote nothing would turn "the carrier has not filed it" into a
    failed update whose authoritative row is already committed.
    """
    tracking = harness.parcel("E")
    case_id, return_reference = await harness.issue_rma(tracking)

    outcome, reading = await harness.service.record_update(
        _update(return_reference, tracking, "IN_TRANSIT", _1400)
    )

    assert outcome.outcome == SHIPMENT_UPDATE_APPLIED
    assert outcome.graph_generation_id == harness.generation
    assert await harness.shipment_nodes(tracking) == []
    assert reading is not None
    assert reading.evidence is ShipmentEvidence.ABSENT
    assert reading.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
    assert [fact["value"] for fact in await harness.facts(case_id, FULFILLMENT_EVIDENCE_FACT)] == [
        f"SHIPMENT_ABSENT:{harness.generation}"
    ]


async def test_one_rma_carrying_two_parcels_keeps_them_apart(harness: _Harness) -> None:
    """Contract C4 is RMA-scoped, and a split return is two parcels under one RMA.

    Each has its own state, its own sync and its own reading. Advancing one must
    not move the other, in SQL or in the graph -- the flattening this checks for
    is exactly what a case-scoped or tracking-agnostic sync would produce.
    """
    first, second = harness.parcel("F1"), harness.parcel("F2")
    await harness.publish_to_carrier_source(first, status="delivered")
    await harness.publish_to_carrier_source(second, status="intransit")
    case_id, return_reference = await harness.issue_rma(first)

    delivered, delivered_reading = await harness.service.record_update(
        _update(return_reference, first, "DELIVERED", _1500)
    )
    transit, transit_reading = await harness.service.record_update(
        _update(return_reference, second, "IN_TRANSIT", _1400)
    )

    assert delivered.applied and transit.applied
    assert delivered_reading is not None and transit_reading is not None
    assert delivered_reading.current_status == "delivered"
    assert transit_reading.current_status == "intransit"
    stored = {
        str(row["tracking_reference"]): row
        for row in await harness.business_state.read_shipment_state(return_reference)
    }
    assert stored[first]["tracking_status"] == "DELIVERED"
    assert stored[second]["tracking_status"] == "IN_TRANSIT"
    assert (await harness.shipment_nodes(first))[0]["current_status"] == "delivered"
    assert (await harness.shipment_nodes(second))[0]["current_status"] == "intransit"
    assert len(await harness.facts(case_id, FULFILLMENT_STATUS_FACT)) == 2
