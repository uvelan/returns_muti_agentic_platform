"""W2.6: fulfillment reads a shipment instead of inferring one.

Against the production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`), the real
`CypherCompiler` and a real `OnDemandSyncCoordinator`. The connector, projector,
writer, store and graph read are local stand-ins -- what is under test is the
plan the adapter builds, the query it compiles and how it reads an answer, and a
test that needed Neo4j to assert those would not be run often enough to catch a
regression in them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.shipment_observations import (
    SHIPMENT_ENTITY_ID,
    SHIPMENT_TRACKING_FIELD_ID,
    GraphShipmentObservations,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    RawSourceDocument,
    RawSourcePage,
    SyncReceipt,
    SyncReservation,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    SourceContractStatus,
)
from return_platform.source_connectors.contracts import LogicalTargetedReadPlan
from return_platform.workflows.fulfillment_tracking import ShipmentEvidence

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
TRACKING = "1Z999AA10123456784"
GENERATION = "gen-1"


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


class _Resolver:
    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return GENERATION


class _Connector:
    """Answers a targeted read with one shipmentInfo-shaped document."""

    def __init__(self) -> None:
        self.plans: list[LogicalTargetedReadPlan] = []

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        del schema
        self.plans.append(plan)
        return RawSourcePage(
            documents=(
                RawSourceDocument(
                    operation="UPSERT",
                    document={
                        "shipmentInfoEventData": {
                            "trkNum": TRACKING,
                            "currentStatus": "PICKED_UP",
                        }
                    },
                    source_identity=TRACKING,
                ),
            ),
            observed_at=datetime(2026, 8, 12, tzinfo=UTC),
        )


class _Registry:
    def __init__(self, connector: _Connector) -> None:
        self._connector = connector

    def resolve(self, source_asset_id: str) -> _Connector:
        return self._connector


class _Writer:
    def __init__(self) -> None:
        self.writes = 0

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: GraphMutationBatch
    ) -> tuple[int, int]:
        del schema, graph_generation_id
        self.writes += 1
        return len(batch.node_mutations), len(batch.relationship_mutations)


class _Store:
    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        del request_digest, schema_version, graph_generation_id
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        del receipt


class _Reader:
    """Captures the compiled read and answers with configured rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.cypher = ""
        self.parameters: dict[str, Any] = {}

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        del schema, graph_generation_id, plan
        self.cypher = compiled_cypher
        self.parameters = parameters
        return {"rows": self.rows, "count": len(self.rows)}


def _observations(
    schema: ActiveSchema, reader: _Reader, connector: _Connector | None = None
) -> GraphShipmentObservations:
    coordinator = OnDemandSyncCoordinator(
        connectors=_Registry(connector or _Connector()),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=_Writer(),
        store=_Store(),
    )
    return GraphShipmentObservations(
        schema=schema,
        on_demand_sync=coordinator,
        generation_handles=GenerationHandleProvider(_Resolver()),
        knowledge_gateway=reader,
    )


def _connected(schema: ActiveSchema) -> ActiveSchema:
    """The descriptor with `shipment` promoted to CONNECTED_SYNC.

    The shipped descriptor declares it `SEED_ONLY` with an `UNVERIFIED` source
    contract, because no `shipmentInfo` sample has been supplied and its physical
    paths are carried over from the original schema unchecked. That is a real
    configuration statement and the adapter honours it, so exercising the sync
    half at all requires stating the promotion explicitly here rather than
    quietly in production.
    """
    document = schema.model_dump(mode="json")
    document["entities"][SHIPMENT_ENTITY_ID]["source_access"] = (
        EntitySourceAccess.CONNECTED_SYNC.value
    )
    document["entities"][SHIPMENT_ENTITY_ID]["source_contract_status"] = (
        SourceContractStatus.VERIFIED.value
    )
    return ActiveSchema.model_validate(document)


# ---------------------------------------------------------------------------
# The descriptor W2.6 was promised
# ---------------------------------------------------------------------------


def test_the_shipment_binding_the_step_relies_on_is_already_in_the_descriptor(
    descriptor: ActiveSchema,
) -> None:
    """W2.6 is the one Wave 2 step that needs no descriptor work.

    If `shipmentInfo` -> `shipment` -> `Shipment` or the tracking anchor were
    missing, the step's premise would be wrong and this whole path would be
    reading an entity that does not exist.
    """
    entity = descriptor.entities[SHIPMENT_ENTITY_ID]
    source = descriptor.sources[entity.source_asset_id]

    assert source.object_ref["name"] == "shipmentInfo"
    assert descriptor.entity_node(SHIPMENT_ENTITY_ID).label == "Shipment"
    assert entity.natural_key == (SHIPMENT_TRACKING_FIELD_ID,)
    assert entity.fields[SHIPMENT_TRACKING_FIELD_ID].capabilities.on_demand_sync_anchor


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_observed_shipment_carries_the_status_the_graph_holds(
    descriptor: ActiveSchema,
) -> None:
    """The reading that replaces "a reference exists, therefore it moved"."""
    reader = _Reader(
        [
            {
                SHIPMENT_TRACKING_FIELD_ID: TRACKING,
                "current_status": "DELIVERED",
                "carrier_code": "UPS",
                "shipment_id": "SHP-1",
            }
        ]
    )

    observation = await _observations(descriptor, reader).observe(TRACKING)

    assert observation.evidence is ShipmentEvidence.OBSERVED
    assert observation.current_status == "DELIVERED"
    assert observation.carrier_code == "UPS"
    assert observation.graph_generation_id == GENERATION


@pytest.mark.asyncio
async def test_an_empty_graph_answer_is_absent_and_not_an_error(
    descriptor: ActiveSchema,
) -> None:
    """No shipment for this number is a state a return is legitimately in."""
    observation = await _observations(descriptor, _Reader([])).observe(TRACKING)

    assert observation.evidence is ShipmentEvidence.ABSENT
    assert observation.current_status is None


@pytest.mark.asyncio
async def test_the_read_is_compiled_from_the_schema_and_anchored_on_the_tracking_number(
    descriptor: ActiveSchema,
) -> None:
    """Not hand-written Cypher.

    Going through `CypherCompiler` is what keeps the read subject to the same
    identifier validation and field mapping as an agent's query -- so a
    descriptor change that moves `current_status` moves this read with it
    instead of leaving a literal behind.
    """
    reader = _Reader([])

    await _observations(descriptor, reader).observe(TRACKING)

    assert "MATCH (n0:`Shipment`)" in reader.cypher
    assert "n0.`tracking_number` = $p0" in reader.cypher
    assert reader.parameters["p0"] == TRACKING
    # Read-only, and the gateway refuses anything else.
    assert reader.cypher.strip().startswith("MATCH")


@pytest.mark.asyncio
async def test_a_graph_outage_is_raised_rather_than_reported_as_no_shipment(
    descriptor: ActiveSchema,
) -> None:
    """Reporting ABSENT on an outage would look like data.

    Every return in the deployment would degrade to `AWAITING_HANDOFF` and
    nothing would say why. The caller owns the `best_effort` policy and turns
    this into `UNAVAILABLE` with a reason.
    """

    class _Broken(_Reader):
        async def execute(self, **kwargs: Any) -> Any:
            raise RuntimeError("bolt connection refused")

    with pytest.raises(RuntimeError):
        await _observations(descriptor, _Broken([])).observe(TRACKING)


# ---------------------------------------------------------------------------
# The sync half
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sync_is_skipped_and_named_when_the_descriptor_forbids_it(
    descriptor: ActiveSchema,
) -> None:
    """`shipment` ships SEED_ONLY, and that is not something to route around.

    Its physical paths are carried over unverified, so a targeted read would
    compile paths nobody has confirmed against a real document. Skipping while
    still reading the graph is the honest behaviour: a scheduled sync may have
    brought the shipment in, and the reason is recorded so the skip is visible.
    """
    connector = _Connector()
    reader = _Reader([])

    observation = await _observations(descriptor, reader, connector).observe(TRACKING)

    assert descriptor.entities[SHIPMENT_ENTITY_ID].source_access is EntitySourceAccess.SEED_ONLY
    assert observation.sync_skipped_reason == "SOURCE_ACCESS_SEED_ONLY"
    assert connector.plans == []
    # The read still happened.
    assert reader.cypher


@pytest.mark.asyncio
async def test_a_connected_shipment_entity_is_synced_before_the_read(
    descriptor: ActiveSchema,
) -> None:
    """The step's Validation clause, in the unit that can assert it.

    A tracking number is anchored, one targeted source read is issued for it,
    and only that number -- never a source-wide scan of shipmentInfo.
    """
    connector = _Connector()
    reader = _Reader([{SHIPMENT_TRACKING_FIELD_ID: TRACKING, "current_status": "IN_TRANSIT"}])

    observation = await _observations(_connected(descriptor), reader, connector).observe(TRACKING)

    assert observation.sync_skipped_reason is None
    assert observation.sync_request_id is not None
    assert len(connector.plans) == 1
    plan = connector.plans[0]
    assert plan.entity_id == SHIPMENT_ENTITY_ID
    assert [(c.field_id, c.operator, c.value) for c in plan.conditions] == [
        (SHIPMENT_TRACKING_FIELD_ID, "EXACT", TRACKING)
    ]


@pytest.mark.asyncio
async def test_a_failed_sync_does_not_stop_the_read(descriptor: ActiveSchema) -> None:
    """The source being down is not a reason to ignore what the graph holds.

    A scheduled sync may already have projected this shipment, and refusing to
    look would turn a source outage into a fulfillment state that is wrong in
    the direction of claiming less than is known.
    """

    class _BrokenConnector(_Connector):
        async def targeted_read(self, **kwargs: Any) -> RawSourcePage:
            raise RuntimeError("source unreachable")

    reader = _Reader([{SHIPMENT_TRACKING_FIELD_ID: TRACKING, "current_status": "PICKED_UP"}])

    observation = await _observations(_connected(descriptor), reader, _BrokenConnector()).observe(
        TRACKING
    )

    assert observation.evidence is ShipmentEvidence.OBSERVED
    assert observation.sync_skipped_reason is not None
    assert observation.sync_skipped_reason.startswith("SYNC_FAILED_")
