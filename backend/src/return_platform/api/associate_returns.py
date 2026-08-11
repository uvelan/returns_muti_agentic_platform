"""Associate-first conversational return intake APIs.

FROZEN -- superseded by ``/api/v2/order-agent`` (``dynamic_knowledge.api.order_agent``).

The HTTP surface of :mod:`return_platform.operations.associate_flow`; see that
module's header for why. Still mounted, so an unknown consumer is not broken
without warning, and marked deprecated in the generated contract.

**Do not add routes here.** New discovery behaviour belongs on the canonical
agent. ``tests/test_frozen_modules_gain_no_new_callers.py`` enforces this.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.operations.associate_flow import (
    AssociateChatTurnRequest,
    AssociateConversationService,
    AssociateConversationView,
    ConfirmDiscoveryRequest,
    ContinueAssociateConversationRequest,
    ReturnDetailsRequest,
    StartAssociateConversationRequest,
    redact_ambiguous_candidates,
)
from return_platform.operations.associate_service_factory import (
    build_associate_conversation_service,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import (
    actor_roles,
    require_associate_roles,
    require_read_roles,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(
    prefix="/api/v1/associate-returns",
    tags=["Associate Returns"],
    # Still mounted so an unknown consumer is not broken without warning; marked
    # here so the generated contract says so to anyone reading it before writing
    # a new caller.
    deprecated=True,
)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(request: Request) -> AssociateConversationService:
    return build_associate_conversation_service(request)


@router.get("/conversations", response_model=APIResponse[list[AssociateConversationView]])
async def list_conversations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[AssociateConversationView]]:
    service = _service(request)
    await service.ensure_indexes()
    conversations = await service.list(limit)
    return APIResponse(
        data=[redact_ambiguous_candidates(item) for item in conversations],
        meta=_meta(request),
    )


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
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


@router.post(
    "/chat",
    response_model=APIResponse[AssociateConversationView],
    status_code=status.HTTP_201_CREATED,
)
async def start_chat(
    payload: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        data = await _service(request).start_chat(payload, actor_id=actor)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        import logging

        logging.exception("Associate chat start failed")
        raise HTTPException(
            status_code=502,
            detail=f"Associate chat start failed: {type(error).__name__}",
        ) from error
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


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
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=APIResponse[AssociateConversationView],
)
async def continue_conversation(
    conversation_id: str,
    payload: ContinueAssociateConversationRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        data = await _service(request).continue_discovery(
            conversation_id,
            payload,
            actor_id=actor,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Associate conversation not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


@router.post(
    "/conversations/{conversation_id}/chat",
    response_model=APIResponse[AssociateConversationView],
)
async def continue_chat(
    conversation_id: str,
    payload: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        data = await _service(request).continue_chat(
            conversation_id,
            payload,
            actor_id=actor,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Associate conversation not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


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
    return APIResponse(data=redact_ambiguous_candidates(data), meta=_meta(request))


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
                actor_roles=actor_roles(request),
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
