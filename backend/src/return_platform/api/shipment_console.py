"""The Shipment Status Console's surface: list, lookup, event append, catalog.

The console exists so a return shipment can be driven through its ladder by
hand -- exercising Fulfillment without a live carrier feed. Everything the
screen renders comes from configuration: the catalog (codes, labels,
transitions, colours) is the release's `shipment_tracking` block, and the
documents live in the collection that block names, under the field names it
maps. No status code, collection name or field name is a literal here.

An appended event does two things, in order:

1. appends to the shipment document's append-only event array and recomputes
   the current status (the console's own ledger);
2. drives the existing `ReturnShipmentStateService.record_update`, so the
   authoritative `dbo.return_tracking` row, the graph projection, the case
   facts and the associate's original conversation all follow the same C4
   chain every carrier observation already travels. Fulfillment reads the
   graph, never this collection.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.api.return_shipments import _service as _shipment_state_service
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.dynamic_knowledge.integration.shipment_state_sync import (
    ShipmentStateSyncFailed,
)
from return_platform.operations.fulfillment_progress import FulfillmentProgress
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.shipment_tracking import (
    ShipmentTrackingStore,
    ShipmentTrackingUnconfigured,
    TransitionRejected,
)
from return_platform.operations.sql_business_state import ShipmentUpdate
from return_platform.resources import RuntimeResources
from return_platform.security import capabilities
from return_platform.security.authorization import require_capability
from return_platform.shared.contracts import APIResponse, ResponseMeta

logger = logging.getLogger("return_platform.api.shipment_console")

router = APIRouter(prefix="/api", tags=["Shipment Console"])


class ShipmentEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=64)
    location: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=1_000)
    #: Defaults to now server-side; editable for backdating a real-world event.
    eventAt: datetime | None = None
    #: The audited escape hatch for testing invalid transitions. Off by
    #: default; its use and reason are written onto the event itself.
    override: bool = False
    overrideReason: str | None = Field(default=None, max_length=256)
    #: Freight only: the PRO the carrier assigned, typically recorded with the
    #: at-origin-terminal event. Becomes the shipment's tracking key.
    proNumber: str | None = Field(default=None, max_length=64)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=getattr(request.state, "correlation_id", "unknown"))


def _store(request: Request) -> ShipmentTrackingStore:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.source_mongo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SHIPMENT_STORE_UNAVAILABLE",
                "message": "The shipment store's source database is unavailable.",
            },
        )
    source = resources.source_mongo
    database = resources.settings.source_mongo_database or resources.settings.mongo_database

    def configuration():
        loaded = getattr(request.app.state, "return_configuration", None)
        if isinstance(loaded, LoadedReturnConfiguration):
            return loaded.configuration.shipment_tracking
        return None

    return ShipmentTrackingStore(
        collection_of=lambda name: source[database][name],
        configuration=configuration,
    )


@router.get("/shipment-status-catalog", response_model=APIResponse[dict[str, Any]])
async def shipment_status_catalog(request: Request) -> APIResponse[dict[str, Any]]:
    """The release's catalog, verbatim, for the console to build its UI from."""
    loaded = getattr(request.app.state, "return_configuration", None)
    tracking = (
        loaded.configuration.shipment_tracking
        if isinstance(loaded, LoadedReturnConfiguration)
        else None
    )
    if tracking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SHIPMENT_TRACKING_UNCONFIGURED",
                "message": "The active release declares no shipment_tracking catalog.",
            },
        )
    return APIResponse(
        data={
            "statuses": [s.model_dump(mode="json") for s in tracking.statuses],
            "initialStatusParcel": tracking.initial_status_parcel,
            "initialStatusFreight": tracking.initial_status_freight,
            "freightMethods": list(tracking.freight_methods),
        },
        meta=_meta(request),
    )


@router.get("/shipments", response_model=APIResponse[list[dict[str, Any]]])
async def list_shipments(
    request: Request,
    status_code: str | None = Query(default=None, alias="status", max_length=64),
    case: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
) -> APIResponse[list[dict[str, Any]]]:
    try:
        shipments = await _store(request).list_shipments(
            status=status_code, case_id=case, search=search, limit=limit
        )
    except ShipmentTrackingUnconfigured as unconfigured:
        raise HTTPException(status_code=404, detail=str(unconfigured)) from unconfigured
    return APIResponse(data=shipments, meta=_meta(request))


@router.get("/shipments/{identifier}", response_model=APIResponse[dict[str, Any]])
async def get_shipment(identifier: str, request: Request) -> APIResponse[dict[str, Any]]:
    """By shipment id, tracking number, PRO number, BOL, RMA or case id."""
    try:
        shipment = await _store(request).find(identifier)
    except ShipmentTrackingUnconfigured as unconfigured:
        raise HTTPException(status_code=404, detail=str(unconfigured)) from unconfigured
    if shipment is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SHIPMENT_NOT_FOUND",
                "message": f"No return shipment matches {identifier!r}.",
            },
        )
    return APIResponse(data=shipment, meta=_meta(request))


@router.post("/shipments/{shipment_id}/events", response_model=APIResponse[dict[str, Any]])
async def append_shipment_event(
    shipment_id: str,
    payload: ShipmentEventRequest,
    request: Request,
    actor_id: str = Depends(require_capability(capabilities.RETURNS_LOGISTICS_ACT)),
) -> APIResponse[dict[str, Any]]:
    """Append one status event; the current status is recomputed, never edited.

    The transition must be one of the catalog's `allowed_next` for the current
    status unless `override` is set, and the override travels on the event. The
    accepted event is then driven through the platform's shipment-state chain
    so the case, the graph and the associate's conversation follow.
    """
    store = _store(request)
    try:
        updated = await store.append_event(
            shipment_id,
            status=payload.status,
            actor=actor_id,
            location=payload.location,
            note=payload.note,
            event_at=payload.eventAt,
            override=payload.override,
            override_reason=payload.overrideReason,
            pro_number=payload.proNumber,
        )
    except KeyError as missing:
        raise HTTPException(
            status_code=404,
            detail={"code": "SHIPMENT_NOT_FOUND", "message": f"No shipment {shipment_id!r}."},
        ) from missing
    except TransitionRejected as rejected:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRANSITION_NOT_ALLOWED",
                "message": str(rejected),
                "allowedNext": list(rejected.allowed),
            },
        ) from rejected
    except ShipmentTrackingUnconfigured as unconfigured:
        raise HTTPException(status_code=404, detail=str(unconfigured)) from unconfigured

    # The C4 chain: authoritative row -> graph -> case -> associate. Field
    # names come off the store's own mapping, so a release rename holds here
    # too. `tracking_type` follows the ladder: the mode's configured type.
    f = store._f  # noqa: SLF001 - the store owns the mapping; the router renders it
    rma = str(updated.get(f("rma_reference")) or "")
    mode = str(updated.get(f("mode")) or "parcel")
    # The authoritative row is keyed on a tracking reference: the PRO once the
    # carrier assigned one, the parcel tracking number, or -- for freight still
    # travelling under its paperwork -- the BOL. Nothing is invented.
    tracking = str(
        updated.get(f("pro_number"))
        or updated.get(f("tracking_reference"))
        or updated.get(f("bol_reference"))
        or ""
    )
    if rma and tracking:
        service = await _shipment_state_service(request)
        event_at = (payload.eventAt or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
        try:
            await service.record_update(ShipmentUpdate(
                return_reference=rma,
                tracking_reference=tracking,
                shipment_status=payload.status,
                status_at=event_at,
                tracking_type="BOL" if mode == "freight" else "PPL",
                carrier_code=(str(updated.get(f("carrier")) or "") or None),
                shipment_details=payload.note,
            ))
        except ShipmentStateSyncFailed as sync_failed:
            logger.warning(
                "shipment_console_graph_sync_failed",
                extra={"shipment_id": shipment_id, "rma": rma},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "SHIPMENT_GRAPH_SYNC_FAILED",
                    "message": (
                        "The event was appended and the authoritative row committed, but the "
                        "graph projection failed. Resubmitting the same event is safe."
                    ),
                    "retryable": True,
                },
            ) from sync_failed
    # What the status means for the record and the case -- terminal closes the
    # record (and the case once every record is terminal), an exception-class
    # status surfaces on Operations and in the associate's conversation. The
    # classification is the catalog's, never this router's.
    case_id = str(updated.get(f("case_id")) or "")
    if case_id and rma:
        loaded = getattr(request.app.state, "return_configuration", None)
        progress = FulfillmentProgress(
            resolve_operational_repository(request),
            lambda: (
                loaded.configuration.shipment_tracking
                if isinstance(loaded, LoadedReturnConfiguration)
                else None
            ),
        )
        await progress.apply(
            case_id=case_id,
            return_reference=rma,
            status_code=payload.status,
            mode=mode,
            note=payload.note,
            actor=actor_id,
        )
    return APIResponse(data=updated, meta=_meta(request))
