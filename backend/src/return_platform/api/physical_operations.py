"""Production branch-staging, offsite-pickup, and evidence-artifact APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.physical.service import (
    BranchStagingRequest,
    BranchStagingService,
    DocumentArtifactRegistration,
    PickupAction,
    PickupActionRequest,
    PickupCoordinationService,
)
from return_platform.operations.production_event_authorization import (
    ProductionEventNotPermitted,
    authorize_production_event,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    actor_roles,
    require_associate_roles,
    require_logistics_roles,
    require_read_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/returns", tags=["Physical Return Operations"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _dependencies(
    request: Request,
) -> tuple[RuntimeResources, LoadedReturnConfiguration, OperationalRepository]:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        raise HTTPException(status_code=503, detail="Physical return dependencies unavailable.")
    return (
        resources,
        loaded,
        OperationalRepository(resources.mongo, resources.settings, resources.source_mongo),
    )


@router.post(
    "/{session_id}/branch-staging",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def confirm_branch_staging(
    session_id: str,
    payload: BranchStagingRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[dict[str, Any]]:
    _, loaded, repository = _dependencies(request)
    try:
        result = await BranchStagingService(repository, loaded.configuration).confirm(
            session_id, payload, actor_id=actor
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return or handling unit not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Handling unit version conflict.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post(
    "/{session_id}/pickup-actions",
    response_model=APIResponse[dict[str, Any]],
)
async def apply_pickup_action(
    session_id: str,
    payload: PickupActionRequest,
    request: Request,
    actor: str = Depends(require_logistics_roles),
) -> APIResponse[dict[str, Any]]:
    roles = actor_roles(request)
    #: Which workflow event this action will record, and which field of the
    #: service result carries its evidence. Derived from the action alone, so the
    #: authorization below can run *before* the service mutates anything -- a 403
    #: raised after `apply()` would leave the pickup request updated and the
    #: workflow un-signalled.
    event_type_for_action = {
        PickupAction.CONFIRM_BOOKING: (
            ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
            "bookingConfirmationReference",
        ),
        PickupAction.SCHEDULE: (
            ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
            "bookingConfirmationReference",
        ),
        PickupAction.RECORD_PICKUP: (
            ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
            "pickupConfirmationReference",
        ),
    }.get(payload.action)
    if event_type_for_action is not None:
        try:
            authorize_production_event(event_type=event_type_for_action[0], actor_roles=roles)
        except ProductionEventNotPermitted as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
    # Resolved after the authorization check: a caller who may not cause the
    # transition should be refused whether or not the datastore happens to be up,
    # rather than being told 503 first and 403 only once infrastructure recovers.
    resources, loaded, repository = _dependencies(request)
    try:
        result = await PickupCoordinationService(repository, loaded.configuration).apply(
            session_id, payload, actor_id=actor
        )
        workflow_event = (
            None
            if event_type_for_action is None
            else (event_type_for_action[0], result.get(event_type_for_action[1]))
        )
        if workflow_event is not None and resources.temporal is not None:
            coordinator = ProductionWorkflowCoordinator(
                temporal=resources.temporal,
                repository=repository,
                configuration=loaded.configuration,
                task_queue=resources.settings.return_workflow_task_queue,
            )
            event_type, evidence = workflow_event
            await coordinator.record_event(
                session_id,
                event_id=(
                    f"pickup:{result.get('pickupRequestId')}:"
                    f"{payload.action.value}:{result.get('version')}"
                ),
                event_type=event_type,
                evidence_reference=str(evidence),
                actor_id=actor,
                actor_roles=roles,
                business_payload={
                    "sourceSystem": "LOGISTICS_CONFIRMATION",
                    "sourceEventId": (
                        f"{result.get('pickupRequestId')}:"
                        f"{payload.action.value}:{result.get('version')}"
                    ),
                    "bolReference": result.get("bolReference"),
                    "carrier": result.get("carrier"),
                },
            )
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Return or pickup request not found."
        ) from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Pickup request version conflict.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post(
    "/{session_id}/artifacts",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def register_artifact(
    session_id: str,
    payload: DocumentArtifactRegistration,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[dict[str, Any]]:
    _, _, repository = _dependencies(request)
    if await repository.get_return(session_id) is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    result = await repository.register_document_artifact(
        session_id=session_id,
        artifact_id=payload.artifactId,
        artifact_type=payload.artifactType,
        storage_provider=payload.storageProvider,
        storage_key=payload.storageKey,
        content_type=payload.contentType,
        size_bytes=payload.sizeBytes,
        sha256=payload.sha256.lower(),
        classification=payload.classification,
        actor_id=actor,
    )
    await repository.append_event(
        session_id,
        event_type="DOCUMENT_ARTIFACT_REGISTERED",
        actor_type="USER",
        actor_id=actor,
        payload={
            "artifactId": payload.artifactId,
            "artifactType": payload.artifactType,
            "processingStatus": "REGISTERED",
        },
        deduplication_key=f"artifact:{payload.artifactId}",
    )
    return APIResponse(
        data={key: value for key, value in result.items() if key != "_id"},
        meta=_meta(request),
    )


@router.get(
    "/{session_id}/artifacts",
    response_model=APIResponse[list[dict[str, Any]]],
    deprecated=True,
    summary="Deprecated: use GET /api/returns/{session_id}/artifacts",
)
async def list_artifacts(
    session_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    _, _, repository = _dependencies(request)
    if await repository.get_return(session_id) is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    records = await repository.list_document_artifacts(session_id)
    return APIResponse(
        data=[{key: value for key, value in item.items() if key != "_id"} for item in records],
        meta=_meta(request),
    )
