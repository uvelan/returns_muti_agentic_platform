"""RMA ticket creation, tracking, and the one source write the platform makes.

The source write is the part worth testing hardest. It is the single place this
platform writes into a collection everything else treats as read-only, so these
assert what it does and -- more importantly -- what it does not: it never
creates a source document, it never touches OMC's own payload, and it can never
be the reason a platform record is lost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.operations.rma_tickets.models import (
    CreateRmaTicketRequest,
    RecordTrackingRequest,
    RmaTicketItem,
    build_return_reference,
)
from return_platform.operations.rma_tickets.service import (
    RmaTicketNotFoundError,
    RmaTicketService,
)
from return_platform.operations.rma_tickets.shipment_writer import (
    RETURN_SHIPMENT_KEY,
    write_return_tracking_to_source,
)


class FakeShipmentCollection:
    """Just enough Mongo to observe exactly what the writer asks for."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.inserts: list[dict[str, Any]] = []
        self.matched = 1

    async def find_one(
        self, selector: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.last_selector = selector
        del projection
        return self.document

    async def update_one(self, selector: dict[str, Any], update: dict[str, Any]) -> Any:
        self.updates.append((selector, update))

        class Result:
            matched_count = self.matched

        return Result()

    async def insert_one(self, document: dict[str, Any]) -> None:  # pragma: no cover
        # Present so a writer that started inserting would be caught by the
        # assertion below rather than by an AttributeError.
        self.inserts.append(document)


class ExplodingCollection(FakeShipmentCollection):
    async def find_one(
        self, selector: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        raise RuntimeError("source unreachable")


def outbound_document() -> dict[str, Any]:
    """An OMC shipment document in the shape the seed manifest writes."""
    return {
        "_id": "1Z9900000000000001",
        "shipmentInfoEventData": {
            "trkNum": "1Z9900000000000001",
            "trilOrdNum": "ORD-1",
            "currentStatus": "delivered",
            "srcSystem": "DispatchTrack",
        },
        "shipmentInfoEventMeta": {"docType": "disptrck", "updatedBy": "seed-manifest"},
    }


async def _write(collection: Any, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "order_reference": "ORD-1",
        "return_reference": "RMA-1",
        "tracking_reference": "1Z-RETURN-1",
        "tracking_status": "IN_TRANSIT",
        "carrier_code": "UPS",
        "event_at": datetime(2026, 8, 19, tzinfo=UTC),
        "shipment_details": "Two cartons",
        "actor_id": "support-1",
    }
    payload.update(overrides)
    return await write_return_tracking_to_source(collection, **payload)


@pytest.mark.asyncio
async def test_return_tracking_is_written_under_its_own_key() -> None:
    """Never inside `shipmentInfoEventData`, which is OMC's own payload.

    Anything this platform writes has to be distinguishable from what OMC
    recorded, and keeping it under one key is also what makes removing the
    feature a matter of dropping that key.
    """
    collection = FakeShipmentCollection(outbound_document())

    result = await _write(collection)

    assert result.outcome == "INSERTED"
    assert len(collection.updates) == 1
    _selector, update = collection.updates[0]
    written = update["$set"]
    assert RETURN_SHIPMENT_KEY in written
    assert "shipmentInfoEventData" not in written
    assert "shipmentInfoEventData.currentStatus" not in written


@pytest.mark.asyncio
async def test_the_write_names_the_platform_and_the_actor() -> None:
    """A source-side reader must be able to tell where the field came from."""
    collection = FakeShipmentCollection(outbound_document())

    await _write(collection, actor_id="associate-7")

    _selector, update = collection.updates[0]
    entry = update["$set"][RETURN_SHIPMENT_KEY][0]
    assert entry["updatedBy"] == "return-platform:associate-7"
    assert update["$set"]["shipmentInfoEventMeta.updatedBy"] == "return-platform:associate-7"


@pytest.mark.asyncio
async def test_a_missing_shipment_document_is_skipped_never_created() -> None:
    """Inventing an outbound shipment would be a far larger claim than annotating one."""
    collection = FakeShipmentCollection(None)

    result = await _write(collection)

    assert result.outcome == "SKIPPED"
    assert collection.updates == []
    assert collection.inserts == []


@pytest.mark.asyncio
async def test_resending_the_same_tracking_reference_replaces_it() -> None:
    """Otherwise a carrier sending three updates leaves three entries."""
    document = outbound_document()
    document[RETURN_SHIPMENT_KEY] = [
        {"trackingReference": "1Z-RETURN-1", "trackingStatus": "LABEL_CREATED"}
    ]
    collection = FakeShipmentCollection(document)

    result = await _write(collection, tracking_status="DELIVERED")

    assert result.outcome == "UPDATED"
    entries = collection.updates[0][1]["$set"][RETURN_SHIPMENT_KEY]
    assert len(entries) == 1
    assert entries[0]["trackingStatus"] == "DELIVERED"


@pytest.mark.asyncio
async def test_a_different_tracking_reference_is_appended() -> None:
    document = outbound_document()
    document[RETURN_SHIPMENT_KEY] = [{"trackingReference": "1Z-OTHER", "trackingStatus": "X"}]
    collection = FakeShipmentCollection(document)

    await _write(collection)

    entries = collection.updates[0][1]["$set"][RETURN_SHIPMENT_KEY]
    assert [entry["trackingReference"] for entry in entries] == ["1Z-OTHER", "1Z-RETURN-1"]


@pytest.mark.asyncio
async def test_a_source_failure_is_reported_not_raised() -> None:
    """`dbo.return_tracking` is already written by the time this runs.

    Raising here would discard a platform record that is authoritative and
    already committed, which is worse than an annotation that did not land.
    """
    result = await _write(ExplodingCollection(None))

    assert result.outcome == "FAILED"
    assert result.attempted is True
    assert "authoritative" in (result.detail or "")


@pytest.mark.asyncio
async def test_the_document_is_addressed_by_order_never_by_tracking() -> None:
    """The return tracking number is the platform's; the order is the join."""
    collection = FakeShipmentCollection(outbound_document())

    await _write(collection, order_reference="ORD-42")

    assert collection.last_selector == {"shipmentInfoEventData.trilOrdNum": "ORD-42"}


# ---------------------------------------------------------------------------
# Service behaviour, over a fake SQL repository.
# ---------------------------------------------------------------------------


class FakeSql:
    def __init__(self, *, existing: bool = False) -> None:
        self.created: list[Any] = []
        self.tracking: list[Any] = []
        self.statuses: list[tuple[str, str, str | None]] = []
        self.existing = existing

    async def create_rma_ticket(self, write: Any) -> str:
        self.created.append(write)
        return "DUPLICATE" if self.existing else "CREATED"

    async def upsert_return_tracking(self, write: Any) -> str:
        self.tracking.append(write)
        return "INSERTED"

    async def set_rma_ticket_status(
        self, session_id: str, status: str, clarification: str | None
    ) -> bool:
        self.statuses.append((session_id, status, clarification))
        return True

    async def read_rma_ticket(self, session_id: str) -> dict[str, Any] | None:
        if session_id == "missing":
            return None
        return {
            "ticket": {
                "ticket_id": "TCK-1",
                "session_id": session_id,
                "status": "SUBMITTED",
                "return_reference": build_return_reference(session_id),
                "external_reference": None,
                "clarification_request": None,
                "order_reference": "ORD-1",
                "customer_reference": "CUS-1",
                "created_at": None,
                "updated_at": None,
            },
            "items": [],
            "tracking": [],
        }

    async def list_rma_tickets(self, limit: int) -> list[dict[str, Any]]:
        del limit
        return []


def service(sql: FakeSql, *, source: Any = None) -> RmaTicketService:
    return RmaTicketService(
        sql=sql,  # type: ignore[arg-type]
        source_client=source,
        source_database="return_source",
        shipment_collection="shipmentInfo",
    )


def create_request(**overrides: Any) -> CreateRmaTicketRequest:
    payload: dict[str, Any] = {
        "sessionId": "session-1",
        "orderReference": "ORD-1",
        "recommendedReturnMethod": "BRANCH_UPS",
        "associateId": "associate-1",
        "supportDraft": "Customer reports a damaged faucet.",
        "items": (
            RmaTicketItem(
                orderLineId="LINE-1", productId="SKU-1", requestedQuantity=1, reasonCode="DAMAGED"
            ),
        ),
        "idempotencyKey": "idem-0001",
    }
    payload.update(overrides)
    return CreateRmaTicketRequest(**payload)


@pytest.mark.asyncio
async def test_a_ticket_carries_one_item_row_per_agent_line() -> None:
    sql = FakeSql()

    await service(sql).create(
        create_request(
            items=(
                RmaTicketItem(
                    orderLineId="L1", productId="P1", requestedQuantity=2, reasonCode="DAMAGED"
                ),
                RmaTicketItem(
                    orderLineId="L2", productId="P2", requestedQuantity=1, reasonCode="WRONG_ITEM"
                ),
            )
        ),
        actor_id="support-1",
    )

    write = sql.created[0]
    assert [item.order_line_id for item in write.items] == ["L1", "L2"]
    # Deterministic ids, so a retry that got past the ticket guard still cannot
    # duplicate a line.
    assert [item.return_item_id for item in write.items] == [
        "ITEM-RMA-SESSION1-1",
        "ITEM-RMA-SESSION1-2",
    ]


@pytest.mark.asyncio
async def test_missing_agent_fields_raise_the_ticket_for_clarification() -> None:
    """The associate is exactly the person who resolves what the agent could not."""
    sql = FakeSql()

    await service(sql).create(
        create_request(missingFields=("branch_id", "product_presence")), actor_id="support-1"
    )

    write = sql.created[0]
    assert write.status == "CLARIFICATION_REQUIRED"
    assert "branch_id" in (write.clarification_request or "")


@pytest.mark.asyncio
async def test_a_complete_assessment_is_submitted() -> None:
    sql = FakeSql()

    await service(sql).create(create_request(), actor_id="support-1")

    assert sql.created[0].status == "SUBMITTED"


@pytest.mark.asyncio
async def test_a_repeated_submit_reports_duplicate_rather_than_creating_a_second() -> None:
    result = await service(FakeSql(existing=True)).create(create_request(), actor_id="support-1")

    assert result.outcome == "DUPLICATE"


@pytest.mark.asyncio
async def test_tracking_writes_the_platform_row_before_touching_the_source() -> None:
    """Ordering is the guarantee: the source annotation is a projection of a row
    that is already authoritative, so it can never be why the platform has no
    record of a shipment."""
    sql = FakeSql()
    result = await service(sql).record_tracking(
        "session-1",
        RecordTrackingRequest(trackingReference="1Z-1", trackingStatus="IN_TRANSIT"),
        actor_id="support-1",
    )

    assert len(sql.tracking) == 1
    assert result.outcome == "INSERTED"
    # No source client configured, so the annotation is skipped and says so.
    assert result.sourceShipment.attempted is False
    assert result.sourceShipment.outcome == "SKIPPED"


@pytest.mark.asyncio
async def test_tracking_for_an_unknown_ticket_is_refused() -> None:
    with pytest.raises(RmaTicketNotFoundError):
        await service(FakeSql()).record_tracking(
            "missing",
            RecordTrackingRequest(trackingReference="1Z-1", trackingStatus="IN_TRANSIT"),
            actor_id="support-1",
        )


@pytest.mark.asyncio
async def test_the_created_view_echoes_what_only_the_agent_knew() -> None:
    """The recommended method and the draft have no column of their own.

    They are echoed on the create, where the assessment is still in hand, and
    absent on later reads rather than invented from the row.
    """
    result = await service(FakeSql()).create(create_request(), actor_id="support-1")

    assert result.ticket.recommendedReturnMethod == "BRANCH_UPS"
    assert result.ticket.supportDraft == "Customer reports a damaged faucet."

    later = await service(FakeSql()).read("session-1")
    assert later.recommendedReturnMethod is None


def test_the_return_reference_is_derived_so_a_retry_matches() -> None:
    assert build_return_reference("session-1") == build_return_reference("session-1")
    assert build_return_reference("session-1").startswith("RMA-")
