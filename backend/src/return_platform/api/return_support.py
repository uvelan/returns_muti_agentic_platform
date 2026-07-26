"""Production Returns Support workbench APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.data_console.api.auth import (
    require_associate_roles,
    require_read_roles,
    require_return_collaboration_roles,
    require_support_roles,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.operations.return_support.service import (
    CreateSupportMessageRequest,
    CreateSupportWorkItemRequest,
    ReturnSupportService,
    SupportAction,
    SupportActionRequest,
    SupportMessageView,
    SupportWorkItemView,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/return-support", tags=["Returns Support"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(request: Request) -> ReturnSupportService:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        raise HTTPException(status_code=503, detail="Returns Support dependencies are unavailable.")
    repository = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    return ReturnSupportService(
        client=resources.mongo,
        settings=resources.settings,
        configuration=loaded.configuration,
        operational_repository=repository,
    )


@router.post(
    "/work-items",
    response_model=APIResponse[SupportWorkItemView],
    status_code=status.HTTP_201_CREATED,
)
async def create_work_item(
    payload: CreateSupportWorkItemRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[SupportWorkItemView]:
    try:
        data = await _service(request).create_work_item(
            payload,
            actor_id=actor,
            correlation_id=_meta(request).request_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return session not found.") from error
    return APIResponse(data=data, meta=_meta(request))


@router.get("/work-items", response_model=APIResponse[list[SupportWorkItemView]])
async def list_work_items(
    request: Request,
    work_item_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SupportWorkItemView]]:
    return APIResponse(
        data=await _service(request).list_work_items(status=work_item_status, limit=limit),
        meta=_meta(request),
    )


@router.get("/work-items/{work_item_id}", response_model=APIResponse[SupportWorkItemView])
async def get_work_item(
    work_item_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SupportWorkItemView]:
    data = await _service(request).get_work_item(work_item_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Support work item not found.")
    return APIResponse(data=data, meta=_meta(request))


@router.get(
    "/work-items/{work_item_id}/messages",
    response_model=APIResponse[list[SupportMessageView]],
)
async def list_messages(
    work_item_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SupportMessageView]]:
    item = await _service(request).get_work_item(work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Support work item not found.")
    return APIResponse(
        data=await _service(request).list_messages(item.threadId), meta=_meta(request)
    )


@router.post(
    "/work-items/{work_item_id}/messages",
    response_model=APIResponse[dict[str, object]],
    status_code=status.HTTP_201_CREATED,
)
async def add_message(
    work_item_id: str,
    payload: CreateSupportMessageRequest,
    request: Request,
    actor: str = Depends(require_return_collaboration_roles),
) -> APIResponse[dict[str, object]]:
    principal = getattr(request.state, "principal", None)
    actor_role = (
        "RETURN_SUPPORT"
        if principal is not None and "return_support" in principal.roles
        else "ASSOCIATE"
    )
    try:
        item, message = await _service(request).add_message(
            work_item_id,
            payload,
            actor_id=actor,
            actor_role=actor_role,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Support work item not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(
            status_code=409, detail="Support work item version conflict."
        ) from error
    return APIResponse(
        data={"workItem": item.model_dump(mode="json"), "message": message.model_dump(mode="json")},
        meta=_meta(request),
    )


@router.post(
    "/work-items/{work_item_id}/actions",
    response_model=APIResponse[SupportWorkItemView],
)
async def apply_action(
    work_item_id: str,
    payload: SupportActionRequest,
    request: Request,
    actor: str = Depends(require_support_roles),
) -> APIResponse[SupportWorkItemView]:
    try:
        data = await _service(request).apply_action(work_item_id, payload, actor_id=actor)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Support work item not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(
            status_code=409, detail="Support work item version conflict."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    event_type = {
        SupportAction.ACKNOWLEDGE: ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        SupportAction.RECORD_RETURN_CREATION: ProductionReturnEventType.OMC_RETURN_CREATED,
        SupportAction.RECORD_SHIPPING_INSTRUCTIONS: (
            ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED
        ),
        SupportAction.RECORD_CUSTOMER_RESOLUTION: (
            ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED
        ),
        SupportAction.CANCEL: ProductionReturnEventType.CANCELLED,
    }.get(payload.action)
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        event_type is not None
        and isinstance(resources, RuntimeResources)
        and resources.temporal is not None
        and resources.mongo is not None
        and isinstance(loaded, LoadedReturnConfiguration)
    ):
        repository = OperationalRepository(
            resources.mongo, resources.settings, resources.source_mongo
        )
        session = await repository.get_return(data.sessionId)
        if session is not None:
            coordinator = ProductionWorkflowCoordinator(
                temporal=resources.temporal,
                repository=repository,
                configuration=loaded.configuration,
                task_queue=resources.settings.return_workflow_task_queue,
            )
            try:
                await coordinator.ensure_started(session, actor_id=actor)
                business_payload: dict[str, object] = {
                    "supportWorkItemId": data.id,
                    "returnVersion": data.returnVersion,
                    "returnReference": data.returnReference,
                    "shippingInstructionReference": (data.shippingInstructionReference),
                    "instructionType": payload.shippingInstructionType,
                    "carrier": payload.carrier,
                    "trackingNumbers": list(payload.trackingNumbers),
                    "bolReference": payload.bolReference,
                    "customerResolution": data.customerResolutionStatus,
                }
                await coordinator.record_event(
                    data.sessionId,
                    event_id=f"support-action:{data.id}:{payload.action.value}:{data.version}",
                    event_type=event_type,
                    evidence_reference=f"SUPPORT_WORK_ITEM:{data.id}:v{data.version}",
                    actor_id=actor,
                    business_payload=business_payload,
                )
                instruction_type = (payload.shippingInstructionType or "").upper()
                if (
                    payload.action is SupportAction.RECORD_SHIPPING_INSTRUCTIONS
                    and instruction_type
                    in {
                        "LTL",
                        "BOL",
                        "BRANCH_LTL",
                        "OFFSITE_LTL",
                        "HEAVY_TRUCK_PICKUP",
                    }
                ):
                    await coordinator.record_event(
                        data.sessionId,
                        event_id=(f"support-action:{data.id}:BOL_TENDERED:{data.version}"),
                        event_type=ProductionReturnEventType.BOL_TENDERED,
                        evidence_reference=(
                            payload.bolReference
                            or data.shippingInstructionReference
                            or f"SUPPORT_WORK_ITEM:{data.id}:v{data.version}"
                        ),
                        actor_id=actor,
                        business_payload={
                            "sourceSystem": "OMC_OR_SUPPORT_READBACK",
                            "sourceEventId": (f"{data.id}:BOL_TENDERED:{data.version}"),
                            "bolReference": payload.bolReference,
                            "carrier": payload.carrier,
                        },
                    )
            except Exception as error:
                await repository.append_event(
                    data.sessionId,
                    event_type="PRODUCTION_WORKFLOW_SIGNAL_DEFERRED",
                    actor_type="SYSTEM",
                    actor_id="return-support-api",
                    payload={
                        "supportAction": payload.action.value,
                        "errorType": type(error).__name__,
                    },
                    deduplication_key=(
                        f"support-signal-deferred:{data.id}:{payload.action.value}:{data.version}"
                    ),
                )
    return APIResponse(data=data, meta=_meta(request))
