"""Associate-first conversational return intake APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.data_console.api.auth import require_associate_roles, require_read_roles
from return_platform.operations.associate_flow import (
    AssociateConversationService,
    AssociateConversationView,
    ConfirmDiscoveryRequest,
    ReturnDetailsRequest,
    StartAssociateConversationRequest,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/associate-returns", tags=["Associate Returns"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(request: Request) -> AssociateConversationService:
    resources = getattr(request.app.state, "resources", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or resources.source_mongo is None
        or resources.neo4j is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Associate discovery dependencies are unavailable.",
        )
    repository = OperationalRepository(
        resources.mongo,
        resources.settings,
        resources.source_mongo,
    )
    loaded = getattr(request.app.state, "return_configuration", None)
    return AssociateConversationService(
        platform_client=resources.mongo,
        source_client=resources.source_mongo,
        graph=resources.neo4j,
        settings=resources.settings,
        repository=repository,
        return_configuration=(
            loaded.configuration if isinstance(loaded, LoadedReturnConfiguration) else None
        ),
    )


@router.get("/conversations", response_model=APIResponse[list[AssociateConversationView]])
async def list_conversations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[AssociateConversationView]]:
    service = _service(request)
    await service.ensure_indexes()
    return APIResponse(data=await service.list(limit), meta=_meta(request))


@router.post(
    "/conversations",
    response_model=APIResponse[AssociateConversationView],
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation(
    payload: StartAssociateConversationRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        data = await _service(request).start(payload, actor_id=actor)
    except Exception as error:
        import logging

        logging.exception("Associate discovery failed")
        raise HTTPException(
            status_code=502,
            detail=f"Associate discovery failed: {type(error).__name__}",
        ) from error
    return APIResponse(data=data, meta=_meta(request))


@router.get(
    "/conversations/{conversation_id}",
    response_model=APIResponse[AssociateConversationView],
)
async def get_conversation(
    conversation_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[AssociateConversationView]:
    data = await _service(request).get(conversation_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Associate conversation not found.")
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/conversations/{conversation_id}/confirm",
    response_model=APIResponse[AssociateConversationView],
)
async def confirm_discovery(
    conversation_id: str,
    payload: ConfirmDiscoveryRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        data = await _service(request).confirm(conversation_id, payload, actor_id=actor)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Associate conversation not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=data, meta=_meta(request))


@router.post(
    "/conversations/{conversation_id}/details",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def submit_return_details(
    conversation_id: str,
    payload: ReturnDetailsRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[dict[str, Any]]:
    try:
        conversation, session = await _service(request).submit_details(
            conversation_id,
            payload,
            actor_id=actor,
            correlation_id=_meta(request).request_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Associate conversation not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        isinstance(resources, RuntimeResources)
        and resources.temporal is not None
        and isinstance(loaded, LoadedReturnConfiguration)
        and conversation.discoveryLock is not None
        and session.supportWorkItemId is not None
        and resources.mongo is not None
    ):
        repository = OperationalRepository(
            resources.mongo, resources.settings, resources.source_mongo
        )
        coordinator = ProductionWorkflowCoordinator(
            temporal=resources.temporal,
            repository=repository,
            configuration=loaded.configuration,
            task_queue=resources.settings.return_workflow_task_queue,
        )
        try:
            await coordinator.seed_confirmed_intake(
                session,
                discovery_evidence=f"DISCOVERY_LOCK:{conversation.discoveryLock.lockDigest}",
                details_evidence=(
                    f"RETURN_REQUEST_SNAPSHOT:{conversation.id}:v{conversation.version}"
                ),
                support_evidence=f"SUPPORT_WORK_ITEM:{session.supportWorkItemId}",
                actor_id=actor,
            )
            session = await repository.get_return(session.id) or session
        except Exception as error:
            await repository.append_event(
                session.id,
                event_type="PRODUCTION_WORKFLOW_START_DEFERRED",
                actor_type="SYSTEM",
                actor_id="associate-api",
                payload={"errorType": type(error).__name__},
                deduplication_key=f"workflow-deferred:{session.id}",
            )
    return APIResponse(
        data={
            "conversation": conversation.model_dump(mode="json"),
            "returnSession": session.model_dump(mode="json"),
        },
        meta=_meta(request),
    )
