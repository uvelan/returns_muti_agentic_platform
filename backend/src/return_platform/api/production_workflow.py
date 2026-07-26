"""Start, inspect, and signal the production Temporal return workflow."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
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


def _dependencies(
    request: Request,
) -> tuple[RuntimeResources, LoadedReturnConfiguration, OperationalRepository]:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.temporal is None
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        raise HTTPException(status_code=503, detail="Production workflow dependencies unavailable.")
    repository = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    return resources, loaded, repository


def _coordinator(
    resources: RuntimeResources,
    loaded: LoadedReturnConfiguration,
    repository: OperationalRepository,
) -> ProductionWorkflowCoordinator:
    assert resources.temporal is not None
    return ProductionWorkflowCoordinator(
        temporal=resources.temporal,
        repository=repository,
        configuration=loaded.configuration,
        task_queue=resources.settings.return_workflow_task_queue,
    )


def _authorize_event(request: Request, event_type: ProductionReturnEventType) -> None:
    principal = getattr(request.state, "principal", None)
    roles = frozenset(getattr(principal, "roles", ()))
    allowed: dict[ProductionReturnEventType, frozenset[str]] = {
        ProductionReturnEventType.DISCOVERY_CONFIRMED: frozenset(
            {"console_admin", "return_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED: frozenset(
            {"console_admin", "return_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED: frozenset(
            {"console_admin", "return_associate", "return_support", "return_platform_service"}
        ),
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED: frozenset(
            {"console_admin", "return_support", "return_platform_service"}
        ),
        ProductionReturnEventType.OMC_RETURN_CREATED: frozenset(
            {"console_admin", "return_support", "return_platform_service"}
        ),
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED: frozenset(
            {"console_admin", "return_support", "logistics_coordinator", "return_platform_service"}
        ),
        ProductionReturnEventType.BOL_TENDERED: frozenset(
            {"console_admin", "logistics_coordinator", "return_platform_service"}
        ),
        ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED: frozenset(
            {"console_admin", "logistics_coordinator", "return_platform_service"}
        ),
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED: frozenset(
            {
                "console_admin",
                "return_associate",
                "logistics_coordinator",
                "return_platform_service",
            }
        ),
        ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED: frozenset(
            {"console_admin", "return_support", "return_platform_service"}
        ),
        ProductionReturnEventType.RECEIPT_CONFIRMED: frozenset(
            {"console_admin", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED: frozenset(
            {
                "console_admin",
                "return_support",
                "warehouse_associate",
                "return_platform_service",
            }
        ),
        ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED: frozenset(
            {
                "console_admin",
                "return_support",
                "warehouse_associate",
                "return_platform_service",
            }
        ),
        ProductionReturnEventType.LICENSE_PLATE_ASSIGNED: frozenset(
            {"console_admin", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED: frozenset(
            {"console_admin", "return_support", "return_platform_service"}
        ),
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED: frozenset(
            {"console_admin", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED: frozenset(
            {"console_admin", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED: frozenset(
            {"console_admin", "return_support", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED: frozenset(
            {"console_admin", "return_support", "warehouse_associate", "return_platform_service"}
        ),
        ProductionReturnEventType.CANCELLED: frozenset(
            {"console_admin", "return_associate", "return_support", "return_platform_service"}
        ),
    }
    if not roles.intersection(allowed[event_type]):
        raise HTTPException(status_code=403, detail="Role cannot record this business event.")


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
    resources, loaded, repository = _dependencies(request)
    session = await repository.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    workflow_id = await _coordinator(resources, loaded, repository).ensure_started(
        session, actor_id=actor
    )
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
    resources, loaded, repository = _dependencies(request)
    if await repository.get_return(session_id) is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    try:
        state = await _coordinator(resources, loaded, repository).query_state(session_id)
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
    resources, loaded, repository = _dependencies(request)
    session = await repository.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    coordinator = _coordinator(resources, loaded, repository)
    try:
        await coordinator.ensure_started(session, actor_id=actor)
        state = await coordinator.record_event(
            session_id,
            event_id=payload.eventId,
            event_type=payload.eventType,
            evidence_reference=payload.evidenceReference,
            actor_id=actor,
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
