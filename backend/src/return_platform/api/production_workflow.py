"""Start, inspect, and signal the production Temporal return workflow."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.operations.models import ReturnSessionView
from return_platform.operations.production_event_authorization import (
    ProductionEventNotPermitted,
    authorize_production_event,
)
from return_platform.operations.production_workflow import (
    ProductionWorkflowCoordinator,
    resolve_production_coordinator,
)
from return_platform.security.authorization import (
    actor_roles,
    require_read_roles,
    require_write_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/production-returns", tags=["Production Return Workflow"])


class WorkflowApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductionEventRequest(WorkflowApiModel):
    eventId: str = Field(min_length=8, max_length=128)
    eventType: ProductionReturnEventType
    evidenceReference: str = Field(min_length=3, max_length=512)
    businessPayload: dict[str, object] = Field(default_factory=dict)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


async def _coordinator_and_session(
    request: Request, session_id: str
) -> tuple[ProductionWorkflowCoordinator, ReturnSessionView]:
    """Both routers resolve the coordinator the same way now.

    This module had its own `_dependencies`/`_coordinator` pair stating which
    dependencies count as required. The canonical router needed the same, and
    two copies of "what counts as available" is how the two surfaces would come
    to disagree about when the API is up. The shared one lives next to the
    coordinator, in `operations.production_workflow`.
    """
    coordinator = resolve_production_coordinator(request)
    session = await coordinator.repository.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    return coordinator, session


def _authorize_event(request: Request, event_type: ProductionReturnEventType) -> None:
    """Refuse before `ensure_started`, so a refused call starts no workflow.

    `record_event` enforces the same rule and is the real boundary -- this is an
    early check, not the only one. It exists because `record_workflow_event`
    calls `ensure_started` first, and a caller who may not record the event
    should not leave a started workflow behind as a side effect of being
    refused.

    The table itself moved to `operations.production_event_authorization` in
    Wave D4. It used to live here, which is why the three routers that record
    events *implicitly* never consulted it.
    """
    try:
        authorize_production_event(event_type=event_type, actor_roles=actor_roles(request))
    except ProductionEventNotPermitted as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post(
    "/{session_id}/start",
    response_model=APIResponse[dict[str, str]],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_workflow(
    session_id: str,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[dict[str, str]]:
    coordinator, session = await _coordinator_and_session(request, session_id)
    workflow_id = await coordinator.ensure_started(session, actor_id=actor)
    return APIResponse(
        data={"workflowId": workflow_id, "status": "STARTED_OR_ALREADY_RUNNING"},
        meta=_meta(request),
    )


@router.get(
    "/{session_id}/state",
    response_model=APIResponse[dict[str, object]],
)
async def workflow_state(
    session_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[dict[str, object]]:
    coordinator, _session = await _coordinator_and_session(request, session_id)
    try:
        state = await coordinator.query_state(session_id)
    except Exception as error:
        raise HTTPException(status_code=404, detail="Production workflow not started.") from error
    return APIResponse(
        data={
            "sessionId": state.session_id,
            "stage": state.stage.value,
            "discoveryConfirmed": state.discovery_confirmed,
            "returnDetailsConfirmed": state.return_details_confirmed,
            "supportRequestCreated": state.support_request_created,
            "supportAcknowledged": state.support_acknowledged,
            "returnCreated": state.return_created,
            "shippingInstructionsIssued": state.shipping_instructions_issued,
            "bolTendered": state.bol_tendered,
            "carrierBookingConfirmed": state.carrier_booking_confirmed,
            "physicalReturnComplete": state.physical_return_complete,
            "physicalReturnRequired": state.physical_return_required,
            "receiptConfirmed": state.receipt_confirmed,
            "receiptRequired": state.receipt_required,
            "licensePlateAssigned": state.license_plate_assigned,
            "licensePlateRequired": state.license_plate_required,
            "customerResolutionComplete": state.customer_resolution_complete,
            "productDispositionComplete": state.product_disposition_complete,
            "warehouseProcessingComplete": state.warehouse_processing_complete,
            "warehouseProcessingRequired": state.warehouse_processing_required,
            "vendorRecoveryRequired": state.vendor_recovery_required,
            "vendorRecoveryComplete": state.vendor_recovery_complete,
            "caseFullyClosed": state.case_fully_closed,
            "cancelled": state.cancelled,
        },
        meta=_meta(request),
    )


@router.post(
    "/{session_id}/events",
    response_model=APIResponse[dict[str, object]],
)
async def record_workflow_event(
    session_id: str,
    payload: ProductionEventRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[dict[str, object]]:
    _authorize_event(request, payload.eventType)
    coordinator, session = await _coordinator_and_session(request, session_id)
    try:
        await coordinator.ensure_started(session, actor_id=actor)
        state = await coordinator.record_event(
            session_id,
            event_id=payload.eventId,
            event_type=payload.eventType,
            evidence_reference=payload.evidenceReference,
            actor_id=actor,
            actor_roles=actor_roles(request),
            business_payload=dict(payload.businessPayload),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail="Production workflow update failed or is not available.",
        ) from error
    return APIResponse(
        data={
            "stage": state.stage.value,
            "caseFullyClosed": state.case_fully_closed,
            "cancelled": state.cancelled,
        },
        meta=_meta(request),
    )
