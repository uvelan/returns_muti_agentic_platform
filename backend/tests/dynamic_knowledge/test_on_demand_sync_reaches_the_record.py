"""A targeted sync must come back with the record it was asked for.

The on-demand path was complete end to end -- planner, connector, extractor,
projector, Neo4j writer, idempotency store, generation reservation -- and could
not put an order into the graph. The projection was built from the anchoring
entity's own mapped fields, so the salesInv document that came back had
`salesHdrEventData.docType` stripped out of it; `sales_order` restricts itself
with `where: docType == headerLines`, the `where` failed against a field that
was no longer there, and the order was discarded. Its lines went the same way:
`order_line` reads `salesLines[]`, which nothing had asked for either.

The sync reported SUCCEEDED. The agent retried its plan against a graph that
still did not have the order and told the associate it had checked the source.

These tests run the real schema, the real planner, the real compiler, the real
extractor and the real projector. Only the source connector and the graph writer
are substituted, at the same boundary the discovery smoke net substitutes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    ProjectionReadScope,
    SyncOrigin,
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.source_connectors.compilation import compile_source_read
from return_platform.source_connectors.contracts import (
    LogicalTargetedReadPlan,
    RawSourceDocument,
    RawSourcePage,
)

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
ORDER_NUMBER = "CW273354"
ACCOUNT_ID = "CHARLOTTE"
ORDER_KEY = f"{ACCOUNT_ID}*{ORDER_NUMBER}"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


def sales_inv_document() -> dict[str, Any]:
    """One salesInv header document, shaped the way the schema maps it.

    Two order lines and two contact rows, because a single-line order would pass
    a projection that dropped everything after the first element and a single
    contact would not exercise the exploded child at all.
    """
    return {
        "_id": ORDER_KEY,
        "salesHdrEventMeta": {"lastUpdateTs": "2026-08-04T09:00:00Z"},
        "salesHdrEventData": {
            "accountId": ACCOUNT_ID,
            "orderId": ORDER_NUMBER,
            "docType": "headerLines",
            "orderStatus": "OPEN",
            "salesType": "COUNTER",
            "sellWhseId": "W1",
            "shipFromWhseId": "W1",
            "srcSysCode": "ECLIPSE",
        },
        "salesHdr": {
            "salesHdrData": {
                "custId": "C-1",
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
                },
                {
                    "email": "jane.doe@work.example.com",
                    "phoneNumber": "555-0100",
                    "address1": "1 High Street",
                    "city": "Charlotte",
                    "state": "NC",
                    "postalCode": "28202",
                    "county": "Mecklenburg",
                },
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
    }


def reduce_to_projection(document: Any, paths: tuple[tuple[str, ...], ...]) -> Any:
    """The document as a projected read returns it: only the selected paths.

    Array-aware, because the projections that matter here address fields inside
    `salesLines[]` and `customer.address[]` and a dict-only reducer would answer
    the wrong question -- it would drop the children this test exists to keep.
    Not a MongoDB emulator: the real driver is exercised by the real-infra test.
    """
    if isinstance(document, list):
        return [reduce_to_projection(element, paths) for element in document]
    reduced: dict[str, Any] = {}
    for path in paths:
        head, *rest = path
        if not isinstance(document, dict) or head not in document:
            continue
        if not rest:
            reduced[head] = document[head]
            continue
        nested = reduce_to_projection(
            document[head], tuple(tail for first, *tail in paths if first == head and tail)
        )
        if nested not in ({}, []):
            reduced[head] = nested
    return reduced


def anchored_plan(schema: ActiveSchema) -> LogicalTargetedReadPlan:
    return build_targeted_read_plan(
        schema=schema,
        entity_id="sales_order",
        normalized_anchors={"order_key": ("EXACT", ORDER_KEY)},
    )


def projection_of(schema: ActiveSchema) -> set[str]:
    compiled = compile_source_read(schema, anchored_plan(schema))
    return set(compiled.statement["projection"])


async def test_the_read_projects_the_discriminator_its_own_where_clause_tests(
    schema: ActiveSchema,
) -> None:
    """The single field whose absence discarded every order the agent fetched.

    `salesHdrEventData.docType` is no entity's mapped field, so a projection
    derived from mapped fields alone cannot contain it -- and `sales_order`'s
    `where` compares it to `headerLines` before the record is kept.
    """
    assert "salesHdrEventData.docType" in projection_of(schema)


async def test_the_read_projects_the_lines_the_order_document_carries(
    schema: ActiveSchema,
) -> None:
    """An order without its lines does not answer the question that was asked.

    The associate is looking for what they bought. `order_line` explodes
    `salesLines[]` and reads its fields relative to each element, so the
    projection has to name them under that prefix rather than at the root.
    """
    projection = projection_of(schema)
    assert "salesLines.salesLnsEventData.lineNumber" in projection
    assert "salesLines.lineData.productDesc" in projection


async def test_the_read_projects_nothing_the_schema_did_not_name(
    schema: ActiveSchema,
) -> None:
    """Widening the projection must not become reading the whole document.

    The governance property the narrow projection was there for: a targeted read
    may pull only paths configuration describes. Asserted against the raw source
    document, which carries `_id` -- itself a mapped field -- and nothing else
    ungoverned, so any future stray key in the fixture would have to be
    justified here.
    """
    projection = projection_of(schema)
    mapped = {
        ".".join(field.physical_path)
        for entity in schema.entities.values()
        if entity.source_asset_id == "source_sales"
        for field in entity.fields.values()
        if field.physical_path is not None
    }
    where_paths = {
        ".".join(selector.physical_path)
        for entity in schema.entities.values()
        if entity.source_asset_id == "source_sales"
        for selector in entity.where
    }
    for path in projection:
        # Each projected path is a configured path, or a configured path with
        # the record_path of the entity that owns it in front.
        assert any(
            path == candidate or path.endswith(f".{candidate}")
            for candidate in mapped | where_paths
        ), f"{path} is not a path the schema names"


async def test_a_document_limited_to_the_projection_still_extracts_the_order(
    schema: ActiveSchema,
) -> None:
    """The end of the chain: what the connector returns is what gets projected.

    Extraction runs every entity bound to `source_sales` over one document, so
    the assertion is on all of them at once -- an order, both of its lines, and
    the contact rows -- not just on the entity the anchor named.
    """
    compiled = compile_source_read(schema, anchored_plan(schema))
    reduced = reduce_to_projection(sales_inv_document(), compiled.projected_physical_paths)

    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id="source_sales",
        page=RawSourcePage(
            documents=(
                RawSourceDocument(operation="UPSERT", document=reduced, source_identity=ORDER_KEY),
            ),
            observed_at=datetime.now(UTC),
        ),
        read_scope=ProjectionReadScope.PARTIAL_TARGETED_READ,
    )

    by_entity: dict[str, list[dict[str, Any]]] = {}
    for mutation in mutations:
        by_entity.setdefault(mutation.entity_id, []).append(mutation.resolved_key)

    assert by_entity.get("sales_order") == [
        {"account_id": ACCOUNT_ID, "sales_order_number": ORDER_NUMBER}
    ]
    assert sorted(key["line_number"] for key in by_entity.get("order_line", [])) == ["1", "2"]
    assert by_entity.get("customer") == [{"account_id": ACCOUNT_ID, "customer_id": "C-1"}]
    # Two contacts sharing one address; `contact_point` is `distinct` on its
    # natural key, so the two distinct emails survive and the repeated address
    # does not multiply them.
    assert len(by_entity.get("contact_point", [])) == 2


# ---------------------------------------------------------------------------
# The coordinator, with only the source and the graph substituted
# ---------------------------------------------------------------------------


class ProjectingConnector:
    """A source that honours the projection it is handed, like a real one.

    Substituted at the same boundary the discovery smoke net substitutes the
    model and graph execution. Honouring the projection is the whole point: a
    fake that returned the full document regardless would have passed against
    the broken projection too.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document
        self.plans: list[LogicalTargetedReadPlan] = []

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        self.plans.append(plan)
        compiled = compile_source_read(schema, plan)
        return RawSourcePage(
            documents=(
                RawSourceDocument(
                    operation="UPSERT",
                    document=reduce_to_projection(
                        self._document, compiled.projected_physical_paths
                    ),
                    source_identity=ORDER_KEY,
                ),
            ),
            observed_at=datetime.now(UTC),
        )


class OneConnector:
    def __init__(self, connector: ProjectingConnector) -> None:
        self._connector = connector

    def resolve(self, source_asset_id: str) -> ProjectingConnector:
        assert source_asset_id == "source_sales"
        return self._connector


class RecordingWriter:
    def __init__(self) -> None:
        self.batches: list[GraphMutationBatch] = []

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: GraphMutationBatch
    ) -> tuple[int, int]:
        self.batches.append(batch)
        return len(batch.node_mutations), len(batch.relationship_mutations)


class AcceptingStore:
    def __init__(self) -> None:
        self.receipts: list[SyncReceipt] = []

    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        self.receipts.append(receipt)


class RecordingLedger:
    def __init__(self) -> None:
        self.records: list[tuple[str, SyncStatus, SyncOrigin | None]] = []

    async def record(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        receipt: SyncReceipt,
        origin: SyncOrigin | None,
    ) -> None:
        self.records.append((source_asset_id, receipt.status, origin))


def _origin() -> SyncOrigin:
    return SyncOrigin(
        agent_id="order-discovery-agent",
        conversation_id="conv-1",
        client_turn_id="turn-1",
        entity_id="sales_order",
        strong_anchor_id="exact_order_key",
        anchor_field_ids=("order_key",),
    )


async def test_a_targeted_sync_writes_the_order_and_its_lines(schema: ActiveSchema) -> None:
    """SUCCEEDED with nothing written was the bug. Count the nodes, not the status."""
    connector = ProjectingConnector(sales_inv_document())
    writer = RecordingWriter()
    coordinator = OnDemandSyncCoordinator(
        connectors=OneConnector(connector),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=writer,
        store=AcceptingStore(),
    )

    receipt = await coordinator.synchronize(
        schema=schema,
        graph_generation_id="gen-1",
        request_digest="digest-1",
        plan=anchored_plan(schema),
    )

    assert receipt.status is SyncStatus.SUCCEEDED
    assert receipt.nodes_written > 0, "a sync that writes nothing has not reached the source"
    labels = [
        schema.graph.nodes[mutation.projection_id].label
        for batch in writer.batches
        for mutation in batch.node_mutations
    ]
    assert "SalesOrder" in labels
    assert labels.count("OrderLine") == 2


async def test_the_sync_is_attributed_to_the_turn_that_asked_for_it(
    schema: ActiveSchema,
) -> None:
    """An operator seeing a run they did not start must be able to find out why.

    Recorded twice -- once RUNNING, once terminal -- so the screen shows a
    targeted sync while it is in flight rather than only after it lands.
    """
    ledger = RecordingLedger()
    coordinator = OnDemandSyncCoordinator(
        connectors=OneConnector(ProjectingConnector(sales_inv_document())),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=RecordingWriter(),
        store=AcceptingStore(),
        run_ledger=ledger,
    )

    await coordinator.synchronize(
        schema=schema,
        graph_generation_id="gen-1",
        request_digest="digest-2",
        plan=anchored_plan(schema),
        origin=_origin(),
    )

    assert [status for _, status, _ in ledger.records] == [
        SyncStatus.RUNNING,
        SyncStatus.SUCCEEDED,
    ]
    assert all(source == "source_sales" for source, _, _ in ledger.records)
    recorded = ledger.records[-1][2]
    assert recorded is not None
    assert recorded.conversation_id == "conv-1"
    # Field ids, never the order number itself.
    assert recorded.anchor_field_ids == ("order_key",)


async def test_a_broken_ledger_does_not_break_the_sync(schema: ActiveSchema) -> None:
    """Observability is not a dependency.

    A ledger outage that failed the sync would tell the associate the source
    could not be reached about a source that answered.
    """

    class BrokenLedger(RecordingLedger):
        async def record(self, **kwargs: Any) -> None:
            raise RuntimeError("mongo is unreachable")

    coordinator = OnDemandSyncCoordinator(
        connectors=OneConnector(ProjectingConnector(sales_inv_document())),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=RecordingWriter(),
        store=AcceptingStore(),
        run_ledger=BrokenLedger(),
    )

    receipt = await coordinator.synchronize(
        schema=schema,
        graph_generation_id="gen-1",
        request_digest="digest-3",
        plan=anchored_plan(schema),
        origin=_origin(),
    )

    assert receipt.status is SyncStatus.SUCCEEDED


async def test_every_configured_source_reaches_the_source_through_one_path(
    schema: ActiveSchema,
) -> None:
    """Not one implementation per source.

    Which sources exist is configuration, so a plan can be built and compiled
    for every anchor the schema declares without any code knowing which source
    it belongs to. A second sync path for a second source would show up here as
    an anchor that plans on one asset and not on another.
    """
    anchored = [
        (entity.entity_id, anchor_id, anchor)
        for entity in schema.entities.values()
        for anchor_id, anchor in entity.strong_anchors.items()
        if anchor.on_demand_sync_allowed
    ]
    assert len({entity_id for entity_id, _, _ in anchored}) > 1, (
        "fixture no longer covers >1 entity"
    )

    for entity_id, anchor_id, anchor in anchored:
        plan = build_targeted_read_plan(
            schema=schema,
            entity_id=entity_id,
            normalized_anchors={
                field.field_id: ("EXACT", "probe") for field in anchor.fields if field.required
            }
            or {anchor.fields[0].field_id: ("EXACT", "probe")},
        )
        compiled = compile_source_read(schema, plan)
        assert compiled.statement["projection"], f"{entity_id}/{anchor_id} projects nothing"
        assert plan.source_asset_id == schema.entities[entity_id].source_asset_id
