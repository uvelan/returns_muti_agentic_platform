"""What a Returns Support associate creates, and what they see back.

The request shape mirrors what the Return Workflow Agent already produces --
`ReturnWorkflowAssessmentRequest` plus the `ReturnWorkflowAssessment` it returns
-- so the handoff from agent to ticket carries every fact the agent established
rather than making an associate retype it.

Nothing here is invented. Each field maps onto a column that already exists in
`integration.return_support_ticket`, `dbo.return_record`, `dbo.return_items` or
`dbo.return_tracking`, or onto a path in the `shipmentInfo` document.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Statuses `integration.return_support_ticket` accepts. Mirrored from the
#: table's CHECK constraint rather than re-invented -- a value this model
#: allowed and the constraint rejected would fail at the write with a message
#: about a constraint rather than about the field the caller got wrong.
TicketStatus = Literal[
    "DRAFT",
    "SUBMITTED",
    "CLARIFICATION_REQUIRED",
    "RETURN_CREATED",
    "REJECTED",
    "CANCELLED",
    "FAILED",
]

#: `dbo.return_tracking.tracking_type`, from that table's CHECK constraint.
TrackingType = Literal["PPL", "BOL", "CUSTOMER_SHIP", "NO_LABEL", "DIRECT_VENDOR", "FIELD_SCRAP"]


class RmaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RmaTicketItem(RmaModel):
    """One line the agent established, as `dbo.return_items` stores it."""

    orderLineId: str = Field(min_length=1, max_length=64)
    productId: str = Field(min_length=1, max_length=64)
    requestedQuantity: int = Field(ge=1, le=100_000)
    shippedQuantity: int | None = Field(default=None, ge=0, le=100_000)
    reasonCode: str = Field(min_length=1, max_length=32)
    condition: str | None = Field(default=None, max_length=64)


class CreateRmaTicketRequest(RmaModel):
    """Everything the workflow agent posted, in one create."""

    # 36, matching `dbo.return_requests.session_id`. Allowing more here would
    # move a truncation the caller could have prevented into the driver.
    sessionId: str = Field(min_length=1, max_length=36)
    orderReference: str = Field(min_length=1, max_length=64)
    #: Both columns are NOT NULL, and both are things the agent may genuinely
    #: not have established. They are defaulted at the service rather than
    #: required here: refusing the ticket would block support from raising one
    #: for a customer the agent could not identify, which is precisely a case
    #: support exists for.
    customerReference: str | None = Field(default=None, max_length=64)
    correlationId: str | None = Field(default=None, max_length=64)

    # What the agent decided.
    recommendedReturnMethod: str = Field(min_length=1, max_length=32)
    productPresence: str | None = Field(default=None, max_length=48)
    branchId: str | None = Field(default=None, max_length=64)
    associateId: str = Field(min_length=1, max_length=64)
    photoEvidenceRequired: bool = False
    #: The agent's own narrative. Stored as the ticket's draft so the support
    #: associate reads what the agent concluded, not a summary of it.
    supportDraft: str = Field(min_length=1, max_length=16_000)
    #: Fields the agent could not establish. A ticket may be raised with them
    #: outstanding -- that is what CLARIFICATION_REQUIRED is for -- so this is
    #: recorded rather than refused.
    missingFields: tuple[str, ...] = ()

    items: tuple[RmaTicketItem, ...] = Field(min_length=1, max_length=200)

    #: Supplied by the caller so a retried submit is one ticket, not two. The
    #: table's UNIQUE(session_id) is the backstop; this makes the retry answer
    #: DUPLICATE rather than a constraint violation.
    idempotencyKey: str = Field(min_length=8, max_length=128)


class RecordTrackingRequest(RmaModel):
    """A tracking insert or update for one RMA."""

    trackingReference: str = Field(min_length=1, max_length=128)
    trackingType: TrackingType = "CUSTOMER_SHIP"
    carrierCode: str | None = Field(default=None, max_length=32)
    trackingStatus: str = Field(min_length=1, max_length=32)
    #: When the carrier observed the status, which is not when the platform
    #: recorded it. `dbo.return_tracking` carries both for that reason.
    eventAt: datetime | None = None
    shipmentDetails: str | None = Field(default=None, max_length=4_000)
    #: The order this shipment belongs to, needed to address the `shipmentInfo`
    #: document. Absent means the source document is not touched.
    orderReference: str | None = Field(default=None, max_length=64)


class TicketItemView(RmaModel):
    returnItemId: str
    orderLineId: str
    productId: str
    quantity: int
    reasonCode: str
    itemStatus: str


class TrackingView(RmaModel):
    trackingId: str
    trackingReference: str
    trackingType: str
    carrierCode: str | None
    trackingStatus: str
    eventAt: datetime | None
    shipmentDetails: str | None


class SourceShipmentEcho(RmaModel):
    """What the `shipmentInfo` write did, reported back rather than assumed.

    Present so an associate can see that the source document was touched, and
    which one. A silent source write is the thing this whole platform is careful
    about; if it happens it is visible.
    """

    attempted: bool
    matchedDocument: str | None = None
    outcome: Literal["INSERTED", "UPDATED", "SKIPPED", "FAILED"] = "SKIPPED"
    detail: str | None = None


class RmaTicketView(RmaModel):
    ticketId: str
    sessionId: str
    status: TicketStatus
    returnReference: str | None
    externalReference: str | None
    orderReference: str | None
    customerReference: str | None
    associateId: str | None
    recommendedReturnMethod: str | None
    supportDraft: str | None
    missingFields: tuple[str, ...] = ()
    photoEvidenceRequired: bool = False
    items: tuple[TicketItemView, ...] = ()
    tracking: tuple[TrackingView, ...] = ()
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class CreateRmaTicketResult(RmaModel):
    ticket: RmaTicketView
    #: `CREATED` on the first submit, `DUPLICATE` when the idempotency key or
    #: session already produced one. The caller can tell them apart, which a
    #: bare 200 would not allow.
    outcome: Literal["CREATED", "DUPLICATE"]


class RecordTrackingResult(RmaModel):
    ticket: RmaTicketView
    outcome: Literal["INSERTED", "UPDATED"]
    sourceShipment: SourceShipmentEcho


def build_return_reference(session_id: str) -> str:
    """The RMA reference for a session.

    Derived rather than random so a retry of the same session produces the same
    reference, which is what makes the create idempotent without a second
    lookup. Bounded to the column width.
    """
    cleaned = "".join(character for character in session_id.upper() if character.isalnum())
    return f"RMA-{cleaned[:40]}" if cleaned else "RMA-UNKNOWN"


def json_safe(value: Any) -> Any:
    """Narrow a stored value to something a response model can carry."""
    if isinstance(value, datetime | str | int | float | bool) or value is None:
        return value
    return str(value)
