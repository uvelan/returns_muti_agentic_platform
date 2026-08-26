"""Return-shipment documents: seeded from Support's reply, driven by the console.

One document per return record -- never per case -- in the collection the
release names (`shipment_tracking.collection`, today `shipmentInfo`), with an
append-only event array. Everything a reader might hardcode is configuration:
the collection, the physical field names (`shipment_tracking.fields` maps the
logical names used here to stored keys), the status codes, their labels, their
transitions, and which return methods travel the freight ladder.

This module shares a collection with the OMC outbound shipment documents and
must never be mistaken for them: every document it writes carries
``kind: returnShipment`` (under the mapped field name), and every read filters
on it. The OMC documents remain owned by OMC; the one other sanctioned writer
(`rma_tickets/shipment_writer.py`) annotates those outbound documents, while
this store creates return-shipment documents of its own.

The authoritative status chain stays where it is: the console's event endpoint
also drives `ReturnShipmentStateService.record_update`, so `dbo.return_tracking`,
the graph projection, the case facts and the associate's conversation follow
exactly the path contract C4 built. The document here is the console's own
ledger -- identifiers, the ladder position, and the append-only trail of who
moved it when.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection

from return_platform.configuration.return_configuration import (
    ShipmentStatusConfiguration,
    ShipmentTrackingConfiguration,
)

logger = logging.getLogger("return_platform.operations.shipment_tracking")

__all__ = [
    "ShipmentSeed",
    "ShipmentTrackingStore",
    "ShipmentTrackingUnconfigured",
    "TransitionRejected",
]


class ShipmentTrackingUnconfigured(RuntimeError):
    """The release declares no shipment_tracking block; nothing may guess one."""


class TransitionRejected(ValueError):
    """The catalog does not allow this move and the caller did not override."""

    def __init__(self, current: str, requested: str, allowed: tuple[str, ...]):
        self.current = current
        self.requested = requested
        self.allowed = allowed
        super().__init__(
            f"the catalog does not allow {current!r} -> {requested!r}; "
            f"allowed next: {', '.join(allowed) or '(terminal)'}"
        )


class ShipmentSeed:
    """What Support's parsed reply supplies for one return record's shipment."""

    def __init__(
        self,
        *,
        case_id: str,
        return_record_id: str,
        rma_reference: str,
        tracking_reference: str,
        return_method: str | None,
        carrier: str | None = None,
        label_reference: str | None = None,
        bol_reference: str | None = None,
        destination_warehouse: str | None = None,
        destination_bay: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        self.case_id = case_id
        self.return_record_id = return_record_id
        self.rma_reference = rma_reference
        self.tracking_reference = tracking_reference
        self.return_method = return_method
        self.carrier = carrier
        self.label_reference = label_reference
        self.bol_reference = bol_reference
        self.destination_warehouse = destination_warehouse
        self.destination_bay = destination_bay
        self.provenance = dict(provenance or {})


class ShipmentTrackingStore:
    def __init__(
        self,
        collection_of: Callable[[str], AsyncCollection[dict[str, Any]]],
        configuration: Callable[[], ShipmentTrackingConfiguration | None],
    ) -> None:
        #: `collection_of` maps a configured collection *name* to a handle, so
        #: renaming the collection in a release moves the store with it.
        self._collection_of = collection_of
        self._configuration = configuration

    # -- configuration ---------------------------------------------------------
    def _config(self) -> ShipmentTrackingConfiguration:
        configuration = self._configuration()
        if configuration is None:
            raise ShipmentTrackingUnconfigured(
                "the active release declares no shipment_tracking configuration"
            )
        return configuration

    def _collection(self) -> AsyncCollection[dict[str, Any]]:
        return self._collection_of(self._config().collection)

    def _f(self, logical: str) -> str:
        """The stored key for a logical field. Unmapped names store as themselves."""
        return self._config().fields.get(logical, logical)

    def mode_for(self, return_method: str | None) -> str:
        methods = {m.strip().upper() for m in self._config().freight_methods}
        if return_method and return_method.strip().upper() in methods:
            return "freight"
        return "parcel"

    def ladder(self, mode: str) -> list[ShipmentStatusConfiguration]:
        return [
            status
            for status in self._config().statuses
            if status.ladder.strip().lower() == mode.strip().lower()
        ]

    def status_of(self, mode: str, code: str) -> ShipmentStatusConfiguration | None:
        for status in self.ladder(mode):
            if status.code == code:
                return status
        return None

    def initial_status(self, mode: str) -> str:
        configuration = self._config()
        return (
            configuration.initial_status_freight
            if mode == "freight"
            else configuration.initial_status_parcel
        )

    # -- seeding ---------------------------------------------------------------
    async def seed(self, seed: ShipmentSeed) -> dict[str, Any] | None:
        """Create the return-shipment document, once.

        Idempotent on (return record id, tracking number): replaying the
        Support reply matches the existing document and inserts nothing. A
        seed without a tracking number must never reach here -- the caller
        leaves the record awaiting tracking instead of inventing one.
        """
        if not seed.tracking_reference:
            raise ValueError("a shipment cannot be seeded without a tracking reference")
        mode = self.mode_for(seed.return_method)
        f = self._f
        now = datetime.now(UTC)
        document: dict[str, Any] = {
            f("kind"): "returnShipment",
            f("shipment_id"): str(uuid.uuid4()),
            f("case_id"): seed.case_id,
            f("return_record_id"): seed.return_record_id,
            f("rma_reference"): seed.rma_reference,
            f("tracking_reference"): seed.tracking_reference,
            f("carrier"): seed.carrier,
            f("mode"): mode,
            f("label_reference"): seed.label_reference,
            f("destination_warehouse"): seed.destination_warehouse,
            f("destination_bay"): seed.destination_bay,
            f("current_status"): self.initial_status(mode),
            f("events"): [],
            f("created_at"): now,
            f("updated_at"): now,
            f("provenance"): seed.provenance,
        }
        if mode == "freight":
            # The PRO number is the freight tracking key; the BOL is the
            # secondary lookup and never the primary identity.
            document[f("pro_number")] = seed.tracking_reference
            document[f("bol_reference")] = seed.bol_reference
        result = await self._collection().update_one(
            {
                f("kind"): "returnShipment",
                f("return_record_id"): seed.return_record_id,
                f("tracking_reference"): seed.tracking_reference,
            },
            {"$setOnInsert": document},
            upsert=True,
        )
        if result.upserted_id is None:
            logger.info(
                "return_shipment_already_seeded",
                extra={
                    "return_record_id": seed.return_record_id,
                    "tracking_reference": seed.tracking_reference,
                },
            )
            return None
        return document

    # -- reading ---------------------------------------------------------------
    async def list_shipments(
        self,
        *,
        status: str | None = None,
        case_id: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        f = self._f
        query: dict[str, Any] = {f("kind"): "returnShipment"}
        if status:
            query[f("current_status")] = status
        if case_id:
            query[f("case_id")] = case_id
        if search:
            query["$or"] = [
                {f(name): {"$regex": search, "$options": "i"}}
                for name in (
                    "tracking_reference",
                    "pro_number",
                    "bol_reference",
                    "rma_reference",
                    "case_id",
                )
            ]
        documents = (
            await self._collection()
            .find(query)
            .sort(f("updated_at"), -1)
            .to_list(length=limit)
        )
        for document in documents:
            document.pop("_id", None)
        return documents

    async def find(self, identifier: str) -> dict[str, Any] | None:
        """By shipment id, tracking, PRO, BOL, RMA or case -- first match wins."""
        f = self._f
        for name in (
            "shipment_id",
            "tracking_reference",
            "pro_number",
            "bol_reference",
            "rma_reference",
            "case_id",
        ):
            document = await self._collection().find_one(
                {f("kind"): "returnShipment", f(name): identifier}
            )
            if document:
                document.pop("_id", None)
                return document
        return None

    # -- events ----------------------------------------------------------------
    async def append_event(
        self,
        shipment_id: str,
        *,
        status: str,
        actor: str,
        location: str | None = None,
        note: str | None = None,
        event_at: datetime | None = None,
        override: bool = False,
        override_reason: str | None = None,
    ) -> dict[str, Any]:
        """Append one status event and recompute the current status.

        Prior events are never mutated or deleted. An ordinary update must be
        one of the catalog's `allowed_next` for the current status; a terminal
        current status refuses everything without the override, whose use --
        and reason -- is written onto the event itself.
        """
        f = self._f
        document = await self._collection().find_one(
            {f("kind"): "returnShipment", f("shipment_id"): shipment_id}
        )
        if document is None:
            raise KeyError(shipment_id)
        mode = str(document.get(f("mode")) or "parcel")
        current = str(document.get(f("current_status")) or "")
        target = self.status_of(mode, status)
        if target is None:
            raise TransitionRejected(current, status, ())
        if not override:
            current_status = self.status_of(mode, current)
            allowed = tuple(current_status.allowed_next) if current_status else ()
            if current_status is not None and current_status.terminal:
                raise TransitionRejected(current, status, ())
            if status not in allowed:
                raise TransitionRejected(current, status, allowed)
        event: dict[str, Any] = {
            f("event_id"): str(uuid.uuid4()),
            f("status"): status,
            f("location"): location,
            f("note"): note,
            f("event_at"): (event_at or datetime.now(UTC)),
            f("recorded_at"): datetime.now(UTC),
            f("actor"): actor,
        }
        if override:
            event[f("override")] = True
            event[f("override_reason")] = override_reason
        updated = await self._collection().find_one_and_update(
            {f("kind"): "returnShipment", f("shipment_id"): shipment_id},
            {
                "$push": {f("events"): event},
                "$set": {
                    f("current_status"): status,
                    f("updated_at"): datetime.now(UTC),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        updated.pop("_id", None)
        return updated
