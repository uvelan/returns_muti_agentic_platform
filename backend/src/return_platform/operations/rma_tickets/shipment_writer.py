"""Write return tracking into the `shipmentInfo` source collection.

READ THIS BEFORE CHANGING ANYTHING HERE

This module writes to a collection the rest of the platform treats as read-only.
That is a deliberate instruction from the repository owner, taken with the
consequence stated, and it is the only place in the codebase permitted to do it.

WHAT THE REST OF THE PLATFORM BELIEVES

`sql_migrations/006_return_shipment_state.sql` sets out the position at length:
`shipmentInfo` describes the *outbound* shipment -- how the goods reached the
customer -- and is owned by OMC, while a *return* shipment is a different
shipment the platform owns in `dbo.return_tracking`. The Graph Schema Analyzer
enforces the same boundary structurally: `SourceInspectionPort` has no mutating
method and no free-form query parameter, and tests assert that shape.

None of that changes. This writer is not reachable from the analyzer, from any
connector, or from any agent tool. It is a single function, called from one
service method, behind a capability gate, and every call reports what it did.

WHY IT IS SHAPED LIKE THIS

* **`dbo.return_tracking` is still written first and is still authoritative.**
  This is an additional projection, not a replacement. If it fails the platform
  record stands, which is why the caller receives an outcome rather than an
  exception.
* **Update in place, never insert blind.** The document is addressed by the
  order reference the tracking belongs to. A missing document is reported
  SKIPPED rather than fabricated, because inventing an outbound shipment in a
  source system would be a considerably larger claim than annotating one.
* **Return tracking is written under its own key.** It goes to
  `returnShipmentInfo`, beside the OMC-owned `shipmentInfoEventData` rather
  than inside it, so nothing this platform writes can be mistaken for something
  OMC recorded -- and so removing this feature is a matter of dropping one key.
* **Every write stamps who made it.** `updatedBy` names the platform and the
  associate, so a source-side reader can tell where the field came from.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger("return_platform.operations.rma_tickets.shipment_writer")

#: The key return tracking is written under. Never `shipmentInfoEventData`,
#: which is OMC's own payload.
RETURN_SHIPMENT_KEY = "returnShipmentInfo"

#: How the order is addressed inside an OMC shipment document.
_ORDER_PATH = "shipmentInfoEventData.trilOrdNum"


class ShipmentInfoWriteOutcome:
    """What one attempted source write did. Never raises past the caller."""

    __slots__ = ("attempted", "detail", "matched_document", "outcome")

    def __init__(
        self,
        *,
        attempted: bool,
        outcome: str,
        matched_document: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.attempted = attempted
        self.outcome = outcome
        self.matched_document = matched_document
        self.detail = detail


async def write_return_tracking_to_source(
    collection: AsyncCollection[dict[str, Any]],
    *,
    order_reference: str,
    return_reference: str,
    tracking_reference: str,
    tracking_status: str,
    carrier_code: str | None,
    event_at: datetime | None,
    shipment_details: str | None,
    actor_id: str,
) -> ShipmentInfoWriteOutcome:
    """Annotate the outbound shipment document with this RMA's return tracking.

    Returns an outcome instead of raising: `dbo.return_tracking` has already
    been written by the time this runs, and a source-side failure must not
    discard a platform record that is already authoritative.
    """
    entry = {
        "returnReference": return_reference,
        "trackingReference": tracking_reference,
        "trackingStatus": tracking_status,
        "carrierCode": carrier_code,
        "eventAt": event_at or datetime.now(UTC),
        "shipmentDetails": shipment_details,
        "updatedBy": f"return-platform:{actor_id}",
        "updatedAt": datetime.now(UTC),
    }

    try:
        # Addressed by order, and only ever an update: a source document that
        # does not exist is not created here.
        existing = await collection.find_one(
            {_ORDER_PATH: order_reference}, projection={"_id": True, RETURN_SHIPMENT_KEY: True}
        )
        if existing is None:
            logger.info(
                "rma_source_shipment_not_found order=%s tracking=%s",
                order_reference,
                tracking_reference,
            )
            return ShipmentInfoWriteOutcome(
                attempted=True,
                outcome="SKIPPED",
                detail=(
                    f"No shipment document matches order {order_reference}; "
                    "the platform tracking record was written and the source was left unchanged."
                ),
            )

        document_id = str(existing.get("_id"))
        current = existing.get(RETURN_SHIPMENT_KEY)
        entries: list[dict[str, Any]] = list(current) if isinstance(current, list) else []
        replaced = False
        for index, item in enumerate(entries):
            if isinstance(item, dict) and item.get("trackingReference") == tracking_reference:
                entries[index] = entry
                replaced = True
                break
        if not replaced:
            entries.append(entry)

        result = await collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    RETURN_SHIPMENT_KEY: entries,
                    # Mirrors how OMC stamps its own metadata, so a reader sees
                    # the document changed without having to diff it.
                    "shipmentInfoEventMeta.lastUpdateTs": datetime.now(UTC),
                    "shipmentInfoEventMeta.updatedBy": f"return-platform:{actor_id}",
                }
            },
        )
        if result.matched_count == 0:
            return ShipmentInfoWriteOutcome(
                attempted=True,
                outcome="SKIPPED",
                matched_document=document_id,
                detail="The shipment document disappeared between the read and the write.",
            )
        logger.info(
            "rma_source_shipment_written document=%s order=%s tracking=%s actor=%s replaced=%s",
            document_id,
            order_reference,
            tracking_reference,
            actor_id,
            replaced,
        )
        return ShipmentInfoWriteOutcome(
            attempted=True,
            outcome="UPDATED" if replaced else "INSERTED",
            matched_document=document_id,
            detail=(
                "Return tracking replaced on the source shipment document."
                if replaced
                else "Return tracking added to the source shipment document."
            ),
        )
    except Exception as error:  # noqa: BLE001 - a source failure must not lose the SQL write
        logger.warning(
            "rma_source_shipment_failed order=%s tracking=%s",
            order_reference,
            tracking_reference,
            exc_info=True,
        )
        return ShipmentInfoWriteOutcome(
            attempted=True,
            outcome="FAILED",
            detail=(
                f"The source shipment document could not be updated ({type(error).__name__}). "
                "The platform tracking record was written and is authoritative."
            ),
        )


def source_shipment_collection(
    client: AsyncMongoClient[dict[str, Any]], database: str, collection: str
) -> AsyncCollection[dict[str, Any]]:
    """Resolve the shipment collection by configured name, never a literal."""
    return client[database][collection]
