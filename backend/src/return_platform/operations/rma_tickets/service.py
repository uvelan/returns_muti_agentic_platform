"""Create RMA tickets from what the Return Workflow Agent established.

The agent decides; the support associate issues. This service is the seam
between the two: it takes the agent's assessment whole, writes the platform's
own record of it, and reports back what was written.

WHAT IS WRITTEN, AND WHERE

    integration.return_support_ticket   the ticket itself
    dbo.return_requests                 the session's return record
    dbo.return_items                    one row per line the agent established
    dbo.return_tracking                 the return shipment, per RMA
    shipmentInfo (source)               a return-tracking annotation, by
                                        explicit instruction; see
                                        `shipment_writer` for the full note

The first four are platform-owned and authoritative. The fifth is an additional
projection into a source system, written only after the fourth has succeeded and
reported as an outcome rather than allowed to fail the request.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient

from return_platform.operations.rma_tickets.models import (
    CreateRmaTicketRequest,
    CreateRmaTicketResult,
    RecordTrackingRequest,
    RecordTrackingResult,
    RmaTicketView,
    SourceShipmentEcho,
    TicketItemView,
    TrackingView,
    build_return_reference,
)
from return_platform.operations.rma_tickets.shipment_writer import (
    source_shipment_collection,
    write_return_tracking_to_source,
)
from return_platform.operations.sql_business_state import (
    RmaTicketItemWrite,
    RmaTicketWrite,
    RmaTrackingWrite,
    SQLBusinessStateRepository,
)

logger = logging.getLogger("return_platform.operations.rma_tickets")

#: How many tickets the queue returns. Bounded so the support console cannot
#: ask for the whole table.
MAX_QUEUE_SIZE = 200

#: What is written when the agent could not identify the customer.
#: `dbo.return_requests.customer_reference` is NOT NULL, and a blank would be
#: indistinguishable from a customer whose reference is genuinely empty.
UNIDENTIFIED_CUSTOMER = "UNIDENTIFIED"


class RmaTicketNotFoundError(LookupError):
    """Raised when a ticket does not exist for the given session."""


class RmaTicketService:
    """Create and progress RMA tickets."""

    def __init__(
        self,
        *,
        sql: SQLBusinessStateRepository,
        source_client: AsyncMongoClient[dict[str, Any]] | None,
        source_database: str,
        shipment_collection: str,
    ) -> None:
        self._sql = sql
        self._source_client = source_client
        self._source_database = source_database
        self._shipment_collection = shipment_collection

    async def create(
        self, request: CreateRmaTicketRequest, *, actor_id: str
    ) -> CreateRmaTicketResult:
        """Open one RMA ticket for a session, idempotently."""
        return_reference = build_return_reference(request.sessionId)
        digest = hashlib.sha256(
            f"{request.sessionId}:{request.idempotencyKey}".encode()
        ).hexdigest()

        # A ticket whose agent could not establish every field is raised as
        # CLARIFICATION_REQUIRED rather than refused: the associate is exactly
        # the person who resolves what the agent could not.
        status = "CLARIFICATION_REQUIRED" if request.missingFields else "SUBMITTED"
        clarification = (
            "The workflow agent could not establish: " + ", ".join(request.missingFields)
            if request.missingFields
            else None
        )

        write = RmaTicketWrite(
            ticket_id=f"TCK-{uuid.uuid4()}",
            session_id=request.sessionId,
            request_digest=digest,
            status=status,
            return_reference=return_reference,
            return_status="PENDING_SUPPORT",
            eligibility_decision="APPROVE",
            order_reference=request.orderReference,
            # `dbo.return_requests` declares both NOT NULL. A correlation id
            # is a trace handle, and when none was supplied the session *is*
            # the trace, so deriving one states a fact rather than inventing
            # a value. An unidentified customer is named as such rather than
            # blanked, so a reader can tell it from an empty string.
            customer_reference=request.customerReference or UNIDENTIFIED_CUSTOMER,
            correlation_id=request.correlationId or f"SESSION:{request.sessionId}"[:64],
            primary_reason_code=request.items[0].reasonCode,
            external_reference=None,
            clarification_request=clarification,
            items=tuple(
                RmaTicketItemWrite(
                    # Deterministic, so a retry that got past the ticket guard
                    # still cannot duplicate a line.
                    return_item_id=f"ITEM-{return_reference}-{index + 1}",
                    order_line_id=item.orderLineId,
                    product_id=item.productId,
                    quantity=item.requestedQuantity,
                    reason_code=item.reasonCode,
                )
                for index, item in enumerate(request.items)
            ),
        )

        outcome = await self._sql.create_rma_ticket(write)
        logger.info(
            "rma_ticket_%s session=%s rma=%s items=%d actor=%s",
            outcome.casefold(),
            request.sessionId,
            return_reference,
            len(request.items),
            actor_id,
        )
        ticket = await self.read(request.sessionId, agent=request)
        return CreateRmaTicketResult(
            ticket=ticket, outcome="CREATED" if outcome == "CREATED" else "DUPLICATE"
        )

    async def record_tracking(
        self, session_id: str, request: RecordTrackingRequest, *, actor_id: str
    ) -> RecordTrackingResult:
        """Insert or update this RMA's return tracking, then annotate the source."""
        current = await self._sql.read_rma_ticket(session_id)
        if current is None:
            raise RmaTicketNotFoundError(session_id)
        reference = current["ticket"].get("return_reference")
        if not reference:
            raise ValueError("This ticket has no RMA reference to attach tracking to.")

        event_at = request.eventAt or datetime.now(UTC)
        outcome = await self._sql.upsert_return_tracking(
            RmaTrackingWrite(
                tracking_id=f"TRACK-{reference}-{request.trackingReference}"[:64],
                return_reference=str(reference),
                tracking_type=request.trackingType,
                tracking_reference=request.trackingReference,
                carrier_code=request.carrierCode,
                tracking_status=request.trackingStatus,
                event_at=event_at,
                shipment_details=request.shipmentDetails,
            )
        )

        # Only after the authoritative row is written. The source annotation is
        # a projection of it, so it can never be the reason the platform has no
        # record of a shipment.
        echo = await self._annotate_source(
            order_reference=request.orderReference
            or (current["ticket"].get("order_reference") or ""),
            return_reference=str(reference),
            request=request,
            event_at=event_at,
            actor_id=actor_id,
        )

        ticket = await self.read(session_id)
        return RecordTrackingResult(
            ticket=ticket,
            outcome="INSERTED" if outcome == "INSERTED" else "UPDATED",
            sourceShipment=echo,
        )

    async def _annotate_source(
        self,
        *,
        order_reference: str,
        return_reference: str,
        request: RecordTrackingRequest,
        event_at: datetime,
        actor_id: str,
    ) -> SourceShipmentEcho:
        if self._source_client is None:
            return SourceShipmentEcho(
                attempted=False,
                outcome="SKIPPED",
                detail="The source database is not configured for this process.",
            )
        if not order_reference:
            return SourceShipmentEcho(
                attempted=False,
                outcome="SKIPPED",
                detail="No order reference, so no shipment document could be addressed.",
            )
        result = await write_return_tracking_to_source(
            source_shipment_collection(
                self._source_client, self._source_database, self._shipment_collection
            ),
            order_reference=order_reference,
            return_reference=return_reference,
            tracking_reference=request.trackingReference,
            tracking_status=request.trackingStatus,
            carrier_code=request.carrierCode,
            event_at=event_at,
            shipment_details=request.shipmentDetails,
            actor_id=actor_id,
        )
        return SourceShipmentEcho(
            attempted=result.attempted,
            matchedDocument=result.matched_document,
            outcome=result.outcome,
            detail=result.detail,
        )

    async def set_status(
        self, session_id: str, status: str, clarification: str | None, *, actor_id: str
    ) -> RmaTicketView:
        moved = await self._sql.set_rma_ticket_status(session_id, status, clarification)
        if not moved:
            raise RmaTicketNotFoundError(session_id)
        logger.info("rma_ticket_status session=%s status=%s actor=%s", session_id, status, actor_id)
        return await self.read(session_id)

    async def read(
        self, session_id: str, *, agent: CreateRmaTicketRequest | None = None
    ) -> RmaTicketView:
        stored = await self._sql.read_rma_ticket(session_id)
        if stored is None:
            raise RmaTicketNotFoundError(session_id)
        return _view(stored, agent)

    async def queue(self, limit: int) -> list[RmaTicketView]:
        rows = await self._sql.list_rma_tickets(min(max(limit, 1), MAX_QUEUE_SIZE))
        return [_view({"ticket": row, "items": [], "tracking": []}, None) for row in rows]


def _view(stored: dict[str, Any], agent: CreateRmaTicketRequest | None) -> RmaTicketView:
    """Assemble the response from the stored rows.

    `agent` is present only on the create, where the assessment is still in
    hand. Those fields -- the recommended method, the draft, what was missing --
    have no column of their own, so they are echoed on the create and absent on
    later reads rather than being invented from the row.
    """
    ticket = stored["ticket"]
    return RmaTicketView(
        ticketId=str(ticket["ticket_id"]),
        sessionId=str(ticket["session_id"]),
        status=ticket["status"],
        returnReference=ticket.get("return_reference"),
        externalReference=ticket.get("external_reference"),
        orderReference=ticket.get("order_reference"),
        customerReference=ticket.get("customer_reference"),
        associateId=agent.associateId if agent else None,
        recommendedReturnMethod=agent.recommendedReturnMethod if agent else None,
        supportDraft=agent.supportDraft if agent else ticket.get("clarification_request"),
        missingFields=agent.missingFields if agent else (),
        photoEvidenceRequired=agent.photoEvidenceRequired if agent else False,
        items=tuple(
            TicketItemView(
                returnItemId=str(item["return_item_id"]),
                orderLineId=str(item["order_line_id"]),
                productId=str(item["product_id"]),
                quantity=int(item["quantity"]),
                reasonCode=str(item["reason_code"]),
                itemStatus=str(item["item_status"]),
            )
            for item in stored["items"]
        ),
        tracking=tuple(
            TrackingView(
                trackingId=str(row["tracking_id"]),
                trackingReference=str(row["tracking_reference"]),
                trackingType=str(row["tracking_type"]),
                carrierCode=row.get("carrier_code"),
                trackingStatus=str(row["tracking_status"]),
                eventAt=row.get("event_at"),
                shipmentDetails=row.get("shipment_details"),
            )
            for row in stored["tracking"]
        ),
        createdAt=ticket.get("created_at"),
        updatedAt=ticket.get("updated_at"),
    )
