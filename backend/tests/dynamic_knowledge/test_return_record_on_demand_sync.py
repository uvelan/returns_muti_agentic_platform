"""W2.5: the committed return record reaches the graph before any agent reads.

Against the production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`) and a real
`OnDemandSyncCoordinator`. The documents are shaped exactly as
`OperationalRepository.create_return_record` writes them, because a fixture with
a tidier shape would prove the fixture projects rather than that the platform's
own writes do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.return_record_sync import (
    RETURN_RECORD_ENTITY_ID,
    RETURN_RECORD_KEY_FIELD_ID,
    GraphReturnRecordSync,
    ReturnRecordSyncFailed,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import platform_store_source_ids
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
CASE_ID = "case-7f3a"
RECORD_ID = "rec-1"
GENERATION = "gen-1"
NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


def _return_record_document(return_record_id: str) -> dict[str, Any]:
    """`create_return_record` then `update_return_record`, as
    `ReturnCaseActivities.record_support_outcome` writes it."""
    return {
        "returnRecordId": return_record_id,
        "caseId": CASE_ID,
        "returnReference": "RMA-1001",
        "status": "ISSUED",
        "returnLocation": "DC-7",
        "trackingReference": "1Z999AA10123456784",
        "labelReference": "LBL-1",
        "shippingInstructionReference": None,
        "sourceSystem": "RETURN_SUPPORT",
        "version": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


class _Resolver:
    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return GENERATION


class _Connector:
    """Answers a targeted read with the one record the anchor names."""

    def __init__(self, *, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self.plans: list[LogicalTargetedReadPlan] = []
        self._documents = documents

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        del schema
        self.plans.append(plan)
        anchored = {condition.field_id: condition.value for condition in plan.conditions}
        record_id = str(anchored[RETURN_RECORD_KEY_FIELD_ID])
        available = (
            self._documents
            if self._documents is not None
            else {record_id: _return_record_document(record_id)}
        )
        document = available.get(record_id)
        return RawSourcePage(
            documents=(
                (
                    RawSourceDocument(
                        operation="UPSERT", document=document, source_identity=record_id
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
) -> GraphReturnRecordSync:
    coordinator = OnDemandSyncCoordinator(
        connectors=_Registry(connector),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=writer or _Writer(),
        store=store or _Store(),
    )
    return GraphReturnRecordSync(
        schema=schema,
        on_demand_sync=coordinator,
        generation_handles=GenerationHandleProvider(_Resolver()),
    )


# ---------------------------------------------------------------------------
# The descriptor and the connector routing W2.5 depends on
# ---------------------------------------------------------------------------


def test_the_return_record_source_names_the_platform_store(descriptor: ActiveSchema) -> None:
    """And so must be routed to a connector bound to that database.

    `MongoDBSourceScanConnector` reads from the database it was constructed with
    and ignores what the source declares. Without the per-source override,
    `source_return_records` resolves to the upstream connector, finds no such
    collection, and the sync reports SUCCEEDED having written nothing -- the
    failure mode that made the anchored order read useless before it was fixed.
    """
    entity = descriptor.entities[RETURN_RECORD_ENTITY_ID]
    source = descriptor.sources[entity.source_asset_id]

    assert source.object_ref["database"] == "return_platform"
    assert entity.source_access is EntitySourceAccess.CONNECTED_SYNC
    assert entity.source_asset_id in platform_store_source_ids(descriptor, "return_platform")


def test_the_upstream_sources_are_not_routed_to_the_platform_store(
    descriptor: ActiveSchema,
) -> None:
    """The override is per source, not a blanket redirect.

    Sending the Ferguson collections to the platform database would empty the
    order side of the graph in exactly the same silent way.
    """
    platform_sources = platform_store_source_ids(descriptor, "return_platform")

    assert (
        not {
            "source_sales",
            "source_customers",
            "source_products",
            "source_shipments",
        }
        & platform_sources
    )
    assert platform_sources == {
        "source_return_cases",
        "source_return_records",
        "source_return_items",
        "source_handling_units",
    }


# ---------------------------------------------------------------------------
# The sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sync_is_scoped_to_the_records_just_committed(
    descriptor: ActiveSchema,
) -> None:
    """Record-scoped, never collection-scoped.

    Three agents firing a collection-wide sync per case would rewrite every
    case, RMA and item in the platform on every return.
    """
    connector = _Connector()

    outcome = await _sync(descriptor, connector).synchronize_records(
        case_id=CASE_ID, return_record_ids=("rec-1", "rec-2")
    )

    assert outcome.graph_generation_id == GENERATION
    assert outcome.synchronized_record_ids == ("rec-1", "rec-2")
    assert len(connector.plans) == 2
    for plan, record_id in zip(connector.plans, ("rec-1", "rec-2"), strict=True):
        assert plan.entity_id == RETURN_RECORD_ENTITY_ID
        assert [(c.field_id, c.operator, c.value) for c in plan.conditions] == [
            (RETURN_RECORD_KEY_FIELD_ID, "EXACT", record_id)
        ]


@pytest.mark.asyncio
async def test_the_record_projects_as_a_returnrecord_node(descriptor: ActiveSchema) -> None:
    """The point of the whole step: the RMA is in the graph afterwards.

    Asserted through the real extractor and the real projector against the
    document `OperationalRepository` actually writes, so a mapping that only
    works for an invented shape fails here.
    """
    writer = _Writer()

    await _sync(descriptor, _Connector(), writer).synchronize_records(
        case_id=CASE_ID, return_record_ids=(RECORD_ID,)
    )

    nodes = [
        node
        for batch in writer.batches
        for node in batch.node_mutations
        if node.entity_id == RETURN_RECORD_ENTITY_ID
    ]
    assert len(nodes) == 1
    assert nodes[0].key_values == {RETURN_RECORD_KEY_FIELD_ID: RECORD_ID}
    assert nodes[0].properties["return_reference"] == "RMA-1001"
    assert nodes[0].properties["return_record_status"] == "ISSUED"
    assert writer.generations == [GENERATION]


@pytest.mark.asyncio
async def test_every_record_in_one_case_lands_in_one_generation(
    descriptor: ActiveSchema,
) -> None:
    """One read lease covers the set.

    Re-resolving per record would let a cutover split a case's RMAs across two
    generations, and a reader pinned to either one would see part of the answer.
    """
    writer = _Writer()

    await _sync(descriptor, _Connector(), writer).synchronize_records(
        case_id=CASE_ID, return_record_ids=("rec-1", "rec-2", "rec-3")
    )

    assert set(writer.generations) == {GENERATION}


@pytest.mark.asyncio
async def test_a_sync_that_writes_nothing_is_a_failure_not_a_success(
    descriptor: ActiveSchema,
) -> None:
    """A SUCCEEDED receipt with zero nodes is the defect this path was rebuilt around.

    The record was written to the store by the activity immediately before this
    one, so an empty read means the sync did not reach it -- most often a
    connector bound to the wrong database. Reporting success would leave the RMA
    invisible to every agent with nothing to say so.
    """
    empty = _Connector(documents={})

    with pytest.raises(ReturnRecordSyncFailed, match="without writing a node"):
        await _sync(descriptor, empty).synchronize_records(
            case_id=CASE_ID, return_record_ids=(RECORD_ID,)
        )


@pytest.mark.asyncio
async def test_an_empty_record_set_is_refused(descriptor: ActiveSchema) -> None:
    """The workflow must decide there is nothing to sync, not this.

    A silent no-op here would make "Support issued no RMAs" and "the workflow
    forgot to pass the ids" the same observable outcome.
    """
    with pytest.raises(ReturnRecordSyncFailed):
        await _sync(descriptor, _Connector()).synchronize_records(
            case_id=CASE_ID, return_record_ids=()
        )


@pytest.mark.asyncio
async def test_a_descriptor_that_forbids_the_sync_fails_loudly(
    descriptor: ActiveSchema,
) -> None:
    """`blocking` means a configuration that cannot answer is still a failure.

    An agent that cannot see the return is the same outcome whether the cause
    was an outage or an entity nobody made syncable.
    """
    document = descriptor.model_dump(mode="json")
    document["entities"][RETURN_RECORD_ENTITY_ID]["source_access"] = (
        EntitySourceAccess.SEED_ONLY.value
    )
    # `ActiveSchema` caps a relationship's access at its weakest endpoint, so
    # downgrading the entity alone is not a schema the platform would accept.
    # Downgrading its edges too is what an operator would actually have to do,
    # and building anything else here would test a shape that cannot exist.
    for relationship in document["graph"]["relationships"].values():
        if RETURN_RECORD_ENTITY_ID in {
            relationship["source_entity_id"],
            relationship["target_entity_id"],
        }:
            relationship["access"] = RelationshipSourceAccess.SEED_ONLY.value
    seed_only = ActiveSchema.model_validate(document)

    with pytest.raises(ReturnRecordSyncFailed, match="source_access"):
        await _sync(seed_only, _Connector()).synchronize_records(
            case_id=CASE_ID, return_record_ids=(RECORD_ID,)
        )


@pytest.mark.asyncio
async def test_the_receipt_is_completed_before_the_call_returns(
    descriptor: ActiveSchema,
) -> None:
    """The step's Validation clause: the sync returns only when the write commits.

    `OnDemandSyncCoordinator` writes the graph and only then completes the
    receipt, so a returned SUCCEEDED is a post-commit fact. A read landing
    inside that window would see a generation-fenced write half-applied.
    """
    store = _Store()
    writer = _Writer()

    await _sync(descriptor, _Connector(), writer, store).synchronize_records(
        case_id=CASE_ID, return_record_ids=(RECORD_ID,)
    )

    assert writer.batches, "nothing was written"
    statuses = [receipt.status.value for receipt in store.completed]
    assert statuses == ["RUNNING", "SUCCEEDED"]


@pytest.mark.asyncio
async def test_two_records_use_two_idempotency_digests(descriptor: ActiveSchema) -> None:
    """One digest per record, or the second record deduplicates against the first.

    The digest is what makes a Temporal retry a no-op; sharing one across
    records would make the retry a no-op for records that never synced.
    """
    store = _Store()

    await _sync(descriptor, _Connector(), None, store).synchronize_records(
        case_id=CASE_ID, return_record_ids=("rec-1", "rec-2")
    )

    assert len(set(store.digests)) == 2
