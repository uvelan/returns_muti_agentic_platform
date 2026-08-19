"""The Returns Support RMA ticket surface.

The Return Workflow Agent establishes what a return is; this is where a support
associate turns that into an RMA the platform owns. One create carries the whole
assessment, so nothing the agent learned has to be retyped.

Authorization is the support role, not the associate one: issuing an RMA is a
support action, and the capability that gates the rest of the support workbench
gates this too.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.operations.rma_tickets.models import (
    CreateRmaTicketRequest,
    CreateRmaTicketResult,
    RecordTrackingRequest,
    RecordTrackingResult,
    RmaTicketView,
    TicketStatus,
)
from return_platform.operations.rma_tickets.service import (
    RmaTicketNotFoundError,
    RmaTicketService,
)
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles, require_support_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

# Versionless on purpose. `/api/v1/return-support/*` is a *record* of a gap --
# Channel B's backend was only ever mounted there -- not a prefix new surfaces
# join. `api/noVersionedPaths.test.ts` asserts exactly that, and this router is
# new, so it belongs on the canonical surface.
router = APIRouter(prefix="/api/rma-tickets", tags=["Returns Support"])


def _meta(request: Request) -> ResponseMeta:
    correlation = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=correlation if isinstance(correlation, str) else "unknown")


def _service(request: Request) -> RmaTicketService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(status_code=503, detail="Application resources are unavailable.")
    configuration = getattr(request.app.state, "return_configuration", None)
    source = getattr(configuration, "configuration", None) if configuration else None
    resolution = getattr(source, "source_resolution", None) if source else None
    if resolution is None:
        raise HTTPException(
            status_code=503,
            detail="Return configuration is unavailable, so no shipment binding can be resolved.",
        )
    return RmaTicketService(
        sql=SQLBusinessStateRepository(resources.settings),
        source_client=resources.source_mongo,
        source_database=resources.settings.source_mongo_database,
        # Resolved from configuration, never a literal: rebinding the collection
        # is an operator edit everywhere else in this platform and stays one here.
        shipment_collection=resolution.shipment_collection,
    )


@router.post(
    "", response_model=APIResponse[CreateRmaTicketResult], status_code=status.HTTP_201_CREATED
)
async def create_ticket(
    payload: CreateRmaTicketRequest,
    request: Request,
    actor_id: str = Depends(require_support_roles),
) -> APIResponse[CreateRmaTicketResult]:
    """Open an RMA ticket from the workflow agent's assessment."""
    try:
        result = await _service(request).create(payload, actor_id=actor_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.get("", response_model=APIResponse[list[RmaTicketView]])
async def list_tickets(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[RmaTicketView]]:
    """The support queue, newest first."""
    return APIResponse(data=await _service(request).queue(limit), meta=_meta(request))


@router.get("/{session_id}", response_model=APIResponse[RmaTicketView])
async def get_ticket(
    session_id: str, request: Request, _actor_id: str = Depends(require_read_roles)
) -> APIResponse[RmaTicketView]:
    try:
        ticket = await _service(request).read(session_id)
    except RmaTicketNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="No RMA ticket exists for this session."
        ) from error
    return APIResponse(data=ticket, meta=_meta(request))


@router.post("/{session_id}/tracking", response_model=APIResponse[RecordTrackingResult])
async def record_tracking(
    session_id: str,
    payload: RecordTrackingRequest,
    request: Request,
    actor_id: str = Depends(require_support_roles),
) -> APIResponse[RecordTrackingResult]:
    """Insert or update this RMA's return tracking.

    `dbo.return_tracking` is written first and is authoritative. The source
    shipment annotation follows and reports its own outcome, so a source-side
    failure is visible without discarding a platform record that already stands.
    """
    try:
        result = await _service(request).record_tracking(session_id, payload, actor_id=actor_id)
    except RmaTicketNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="No RMA ticket exists for this session."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post("/{session_id}/status", response_model=APIResponse[RmaTicketView])
async def set_status(
    session_id: str,
    payload: dict[str, str],
    request: Request,
    actor_id: str = Depends(require_support_roles),
) -> APIResponse[RmaTicketView]:
    """Move a ticket through the statuses its table already allows."""
    ticket_status = payload.get("status", "")
    allowed = cast(tuple[str, ...], TicketStatus.__args__)  # type: ignore[attr-defined]
    if ticket_status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Status must be one of {', '.join(allowed)}.",
        )
    try:
        ticket = await _service(request).set_status(
            session_id, ticket_status, payload.get("clarification"), actor_id=actor_id
        )
    except RmaTicketNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="No RMA ticket exists for this session."
        ) from error
    return APIResponse(data=ticket, meta=_meta(request))
