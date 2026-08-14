"""SHIP-01: an applied shipment update reaches the graph, RMA-scoped.

Against the production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`) and a real
`OnDemandSyncCoordinator`, extractor and projector. The source documents are
shaped as genuine `shipmentInfo` documents are -- `shipmentInfoEventData` for the
shipment's own fields and `shipmentInfoEventMeta.lastUpdateTs` for the change
timestamp -- because a fixture with a tidier shape would prove the fixture
projects rather than that the descriptor's verified paths do.

The half these tests own is the sync's *shape*: RMA scope, one lease, one digest
per parcel, and the refusals. Whether the sync actually fires on APPLIED and
stays quiet on DUPLICATE and STALE is decided by SQL Server under a row lock, so
it is proven in `tests/operations/test_return_shipment_graph_sync_real_infra.py`
against a real database rather than against a fake that would agree with whatever
this code did.
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
)
from return_platform.dynamic_knowledge.integration.shipment_state_sync import (
    GraphShipmentStateSync,
    ShipmentStateSyncFailed,
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
    RelationshipSourceAccess,
)
from return_platform.source_connectors.contracts import LogicalTargetedReadPlan

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
RMA = "RMA-7F3A"
TRACKING = "TRK-7F3A"
GENERATION = "gen-1"
NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


def _shipment_document(tracking_reference: str) -> dict[str, Any]:
    return {
        "_id": f"DIST*CW273354*{tracking_reference}",
        "shipmentInfoEventData": {
            "trkNum": tracking_reference,
            "trilOrdNum": "CW273354",
            "shipmentId": f"SHP-{tracking_reference}",
            "acctId": "DIST",
            "currentStatus": "intransit",
            "srcSystem": "DispatchTrack",
        },
        "shipmentInfoEventMeta": {
            "docType": "disptrck",
            "insertTs": NOW,
            "lastUpdateTs": NOW,
            "updatedBy": "shipment-writer-v1",
        },
    }


class _Resolver:
    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return GENERATION


class _Connector:
    """Answers a targeted read with the one shipment the anchor names."""

    def __init__(self, *, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self.plans: list[LogicalTargetedReadPlan] = []
        self._documents = documents

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        del schema
        self.plans.append(plan)
        anchored = {condition.field_id: condition.value for condition in plan.conditions}
        tracking = str(anchored[SHIPMENT_TRACKING_FIELD_ID])
        available = (
            self._documents
            if self._documents is not None
            else {tracking: _shipment_document(tracking)}
        )
        document = available.get(tracking)
        return RawSourcePage(
            documents=(
                (
                    RawSourceDocument(
                        operation="UPSERT", document=document, source_identity=tracking
                    ),
                )
                if document is not None
                else ()
            ),
            observed_at=NOW,
        )


class _Registry:
    def __init__(self, connector: _Connector) -> None:
        self._connector = connector

    def resolve(self, source_asset_id: str) -> _Connector:
        return self._connector


class _Writer:
    def __init__(self) -> None:
        self.generations: list[str] = []
        self.batches: list[GraphMutationBatch] = []

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: GraphMutationBatch
    ) -> tuple[int, int]:
        del schema
        self.generations.append(graph_generation_id)
        self.batches.append(batch)
        return len(batch.node_mutations), len(batch.relationship_mutations)


class _Store:
    def __init__(self) -> None:
        self.digests: list[str] = []
        self.completed: list[SyncReceipt] = []

    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        del schema_version, graph_generation_id
        self.digests.append(request_digest)
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        self.completed.append(receipt)


def _sync(
    schema: ActiveSchema,
    connector: _Connector,
    writer: _Writer | None = None,
    store: _Store | None = None,
) -> GraphShipmentStateSync:
    coordinator = OnDemandSyncCoordinator(
        connectors=_Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=writer or _Writer(),
        store=store or _Store(),
    )
    return GraphShipmentStateSync(
        schema=schema,
        on_demand_sync=coordinator,
        generation_handles=GenerationHandleProvider(_Resolver()),
    )


@pytest.mark.asyncio
async def test_the_sync_is_scoped_to_the_rma_and_anchored_on_its_parcels(
    descriptor: ActiveSchema,
) -> None:
    """Contract C4 is RMA-scoped, and one RMA can carry several tracking numbers.

    A split return goes back in two parcels with independent states, so the
    anchored set is a tuple and each parcel gets its own targeted read. A
    case-scoped sync would rewrite every RMA on the case; a collection-scoped one
    would rewrite every shipment in the platform per carrier event.
    """
    connector = _Connector()

    outcome = await _sync(descriptor, connector).synchronize_shipments(
        return_reference=RMA, tracking_references=("TRK-A", "TRK-B")
    )

    assert outcome.graph_generation_id == GENERATION
    assert outcome.synchronized_tracking_references == ("TRK-A", "TRK-B")
    assert len(connector.plans) == 2
    for plan, tracking in zip(connector.plans, ("TRK-A", "TRK-B"), strict=True):
        assert plan.entity_id == SHIPMENT_ENTITY_ID
        assert [(c.field_id, c.operator, c.value) for c in plan.conditions] == [
            (SHIPMENT_TRACKING_FIELD_ID, "EXACT", tracking)
        ]


@pytest.mark.asyncio
async def test_the_shipment_projects_as_a_shipment_node(descriptor: ActiveSchema) -> None:
    """The point of the whole step: the parcel is in the graph afterwards.

    Through the real extractor and the real projector against the document shape
    the verified `shipmentInfo` contract declares, so a mapping that only works
    for an invented shape fails here rather than in production. `current_status`
    is asserted because it is the one property fulfilment concludes `IN_TRANSIT`
    from -- a node projected with a null status reads as a parcel nobody has
    collected.
    """
    writer = _Writer()

    outcome = await _sync(descriptor, _Connector(), writer).synchronize_shipments(
        return_reference=RMA, tracking_references=(TRACKING,)
    )

    nodes = [
        node
        for batch in writer.batches
        for node in batch.node_mutations
        if node.entity_id == SHIPMENT_ENTITY_ID
    ]
    assert len(nodes) == 1
    assert nodes[0].key_values == {SHIPMENT_TRACKING_FIELD_ID: TRACKING}
    assert nodes[0].properties["current_status"] == "intransit"
    assert nodes[0].properties["shipment_id"] == f"SHP-{TRACKING}"
    assert writer.generations == [GENERATION]
    assert outcome.nodes_written == 1


@pytest.mark.asyncio
async def test_every_parcel_of_one_rma_lands_in_one_generation(
    descriptor: ActiveSchema,
) -> None:
    """One read lease covers the RMA.

    Re-resolving per parcel would let a cutover split one RMA's shipments across
    two generations, and a reader pinned to either would see half the return
    moving and half of it not.
    """
    writer = _Writer()

    await _sync(descriptor, _Connector(), writer).synchronize_shipments(
        return_reference=RMA, tracking_references=("TRK-A", "TRK-B", "TRK-C")
    )

    assert set(writer.generations) == {GENERATION}


@pytest.mark.asyncio
async def test_two_parcels_use_two_idempotency_digests(descriptor: ActiveSchema) -> None:
    """One digest per parcel, or the second deduplicates against the first.

    The digest is what makes a retry a no-op; sharing one across parcels would
    make the retry a no-op for parcels that never synced.
    """
    store = _Store()

    await _sync(descriptor, _Connector(), None, store).synchronize_shipments(
        return_reference=RMA, tracking_references=("TRK-A", "TRK-B")
    )

    assert len(set(store.digests)) == 2


@pytest.mark.asyncio
async def test_a_parcel_the_source_has_not_published_is_not_a_failure(
    descriptor: ActiveSchema,
) -> None:
    """The one deliberate divergence from the RMA sync's policy.

    A return record is read back from the platform's own store, so a sync that
    wrote nothing means the projection missed a document that is certainly there.
    A shipment is read from the carrier's source, which may not have filed the
    parcel yet -- and the fulfilment read already reports that honestly as
    `ABSENT`. Raising would turn "the carrier has not filed it" into a failed
    shipment update whose authoritative row is already committed.
    """
    empty = _Connector(documents={})

    outcome = await _sync(descriptor, empty).synchronize_shipments(
        return_reference=RMA, tracking_references=(TRACKING,)
    )

    assert outcome.nodes_written == 0
    assert outcome.graph_generation_id == GENERATION


@pytest.mark.asyncio
async def test_an_empty_parcel_set_is_refused(descriptor: ActiveSchema) -> None:
    """The caller must decide there is nothing to sync, not this.

    A silent no-op would make "this RMA has no shipment" and "the caller forgot
    the tracking reference" the same observable outcome.
    """
    with pytest.raises(ShipmentStateSyncFailed):
        await _sync(descriptor, _Connector()).synchronize_shipments(
            return_reference=RMA, tracking_references=()
        )


@pytest.mark.asyncio
async def test_a_descriptor_that_forbids_the_sync_fails_loudly(
    descriptor: ActiveSchema,
) -> None:
    """Demoting `shipment` stops the write side, and says so.

    The read side records the same refusal as `sync_skipped_reason` and carries
    on, because a scheduled sync may already have brought the parcel in. The
    write side has nothing to fall back on: if the entity is not syncable, the
    update this was called for will not be in the graph at all.
    """
    document = descriptor.model_dump(mode="json")
    document["entities"][SHIPMENT_ENTITY_ID]["source_access"] = EntitySourceAccess.SEED_ONLY.value
    # `ActiveSchema` caps a relationship's access at its weakest endpoint, so
    # downgrading the entity alone is not a schema the platform would accept.
    for relationship in document["graph"]["relationships"].values():
        if SHIPMENT_ENTITY_ID in {
            relationship["source_entity_id"],
            relationship["target_entity_id"],
        }:
            relationship["access"] = RelationshipSourceAccess.SEED_ONLY.value
    seed_only = ActiveSchema.model_validate(document)

    with pytest.raises(ShipmentStateSyncFailed, match="source_access"):
        await _sync(seed_only, _Connector()).synchronize_shipments(
            return_reference=RMA, tracking_references=(TRACKING,)
        )


@pytest.mark.asyncio
async def test_the_receipt_is_completed_before_the_call_returns(
    descriptor: ActiveSchema,
) -> None:
    """A returned generation id is a post-commit fact, not an intention.

    `OnDemandSyncCoordinator` writes the graph and only then completes the
    receipt. The write path stamps that generation onto `ShipmentUpdateOutcome`,
    so "the sync finished" and "which graph answers the associate's next turn"
    have to be the same statement.
    """
    store = _Store()
    writer = _Writer()

    await _sync(descriptor, _Connector(), writer, store).synchronize_shipments(
        return_reference=RMA, tracking_references=(TRACKING,)
    )

    assert writer.batches, "nothing was written"
    assert [receipt.status.value for receipt in store.completed] == ["RUNNING", "SUCCEEDED"]


def test_the_write_and_read_sides_anchor_the_shipment_identically(
    descriptor: ActiveSchema,
) -> None:
    """One anchor vocabulary across both halves.

    The idempotency digest names the anchor shape so two mechanisms anchoring the
    same entity cannot collide. If the write side named its anchor differently
    from the read side, the targeted sync a fulfilment read performs would not
    deduplicate against the one the write path just did -- two syncs per carrier
    event, and the descriptor's `maximum_expected_matches` budget spent twice.
    """
    from return_platform.dynamic_knowledge.integration import shipment_state_sync

    assert shipment_state_sync.SHIPMENT_ENTITY_ID == SHIPMENT_ENTITY_ID
    assert shipment_state_sync.SHIPMENT_TRACKING_FIELD_ID == SHIPMENT_TRACKING_FIELD_ID
    assert shipment_state_sync.SHIPMENT_STRONG_ANCHOR_ID == "exact_tracking"
    assert descriptor.entities[SHIPMENT_ENTITY_ID].source_access is (
        EntitySourceAccess.CONNECTED_SYNC
    ), "the descriptor no longer permits the targeted shipment sync"
