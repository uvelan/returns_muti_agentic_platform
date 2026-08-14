"""Production Returns Support workbench APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.production_event_authorization import (
    unauthorized_events_for,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.operations.return_support.service import (
    CreateSupportMessageRequest,
    CreateSupportWorkItemRequest,
    ReturnSupportService,
    SupportActionRequest,
    SupportMessageView,
    SupportWorkItemView,
    production_events_for_support_action,
)
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    actor_roles,
    require_associate_roles,
    require_read_roles,
    require_return_collaboration_roles,
    require_support_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType
from return_platform.workflows.return_case_workflow import (
    SupportResponseNotice,
    SupportReturnRecord,
    return_case_workflow_id,
)

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
    roles = actor_roles(request)
    #: Every workflow event this action will record, derived from the payload and
    #: the active policy so the whole set can be authorized before the work item
    #: is mutated. A 403 raised after `apply_action` would leave the support
    #: record advanced and the workflow un-signalled.
    #:
    #: Resolved once, here, and reused for the recording below -- the same
    #: `planned_events` tuple decides both, so the check and the act cannot read
    #: different policies even if a release lands mid-request. An absent
    #: configuration yields no BOL event *and* skips the whole recording block
    #: further down, so the two stay consistent rather than one half acting on a
    #: policy the other could not read.
    loaded = getattr(request.app.state, "return_configuration", None)
    planned_events = production_events_for_support_action(
        payload,
        bol_tendering_instruction_types=(
            loaded.configuration.return_policy.bol_tendering_instruction_types
            if isinstance(loaded, LoadedReturnConfiguration)
            else ()
        ),
    )
    refused = unauthorized_events_for(event_types=planned_events, actor_roles=roles)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=(
                "Role cannot record "
                + ", ".join(event.value for event in refused)
                + ", which this action would produce."
            ),
        )
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
    event_type = planned_events[0] if planned_events else None
    resources = getattr(request.app.state, "resources", None)
    if (
        event_type is not None
        # A case thread has no session, so there is no session-scoped
        # production workflow to signal. Channel B outcomes on a case reach the
        # return through `ReturnCaseWorkflow`'s `support_response` signal
        # instead; bridging here as well would be two paths to the same event.
        and data.sessionId is not None
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
                    actor_roles=roles,
                    business_payload=business_payload,
                )
                # Same `planned_events` the authorization above was computed
                # from, rather than re-deriving the LTL condition here. Two
                # copies of "does this action tender a BOL?" is how the check
                # and the act drift apart.
                if ProductionReturnEventType.BOL_TENDERED in planned_events:
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
                        actor_roles=roles,
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


class ReturnOutcomeRecord(BaseModel):
    """One RMA as Support is issuing it."""

    model_config = ConfigDict(extra="forbid")

    returnReference: str = Field(min_length=1, max_length=128)
    trackingReference: str | None = Field(default=None, max_length=128)
    labelReference: str | None = Field(default=None, max_length=256)
    returnLocation: str | None = Field(default=None, max_length=128)
    shippingInstructionReference: str | None = Field(default=None, max_length=128)
    orderLineReferences: tuple[str, ...] = Field(default=(), max_length=200)


class ReturnOutcomeRequest(BaseModel):
    """What Support is sending back to the case.

    A *list* of records, because one reply can issue several RMAs with
    different labels going to different places -- the shape the case model was
    changed to carry, and the one a single set of fields cannot express.
    """

    model_config = ConfigDict(extra="forbid")

    records: tuple[ReturnOutcomeRecord, ...] = Field(default=(), max_length=100)
    rejected: bool = False
    reason: str | None = Field(default=None, max_length=2_000)


@router.post(
    "/work-items/{work_item_id}/return-outcome", response_model=APIResponse[dict[str, str]]
)
async def submit_return_outcome(
    work_item_id: str,
    payload: ReturnOutcomeRequest,
    request: Request,
    # Support roles only: issuing an RMA is Support's act, and the same gate
    # every other outcome-recording action on this router uses.
    _actor_id: str = Depends(require_support_roles),
) -> APIResponse[dict[str, str]]:
    """Send Support's answer to the case that asked for it.

    This is the step that made Channel B -> Channel A possible. The work item
    knows its case, the case knows its workflow, and the workflow is waiting on
    a durable timer for exactly this signal -- so the outcome reaches the
    associate's original conversation instead of the browser trying to match an
    order reference across two collections.

    A signal, not a write. `ReturnCaseWorkflow` owns what happens next: it
    records the return records, moves the case status and stops the reminder
    cadence, and it does so once however many times Support presses send --
    duplicate responses are ignored by the workflow, not deduplicated here.
    """
    item = await _service(request).get_work_item(work_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WORK_ITEM_NOT_FOUND", "message": "No such work item."},
        )
    if item.caseId is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WORK_ITEM_HAS_NO_CASE",
                "message": (
                    "This work item belongs to a return session rather than a case; "
                    "record the outcome through the support action instead."
                ),
                "retryable": False,
            },
        )

    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.temporal is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WORKFLOW_HOST_UNAVAILABLE",
                "message": "The case workflow cannot be reached from this process.",
                "retryable": True,
            },
        )

    handle = resources.temporal.get_workflow_handle(return_case_workflow_id(item.caseId))
    await handle.signal(
        "support_response",
        SupportResponseNotice(
            work_item_id=work_item_id,
            records=tuple(
                SupportReturnRecord(
                    return_reference=record.returnReference,
                    tracking_reference=record.trackingReference,
                    label_reference=record.labelReference,
                    return_location=record.returnLocation,
                    shipping_instruction_reference=record.shippingInstructionReference,
                    order_line_references=record.orderLineReferences,
                )
                for record in payload.records
            ),
            rejected=payload.rejected,
            reason=payload.reason,
        ),
    )
    return APIResponse(data={"caseId": item.caseId}, meta=_meta(request))
