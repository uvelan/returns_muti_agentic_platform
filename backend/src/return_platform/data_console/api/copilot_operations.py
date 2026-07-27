"""Administrator-only operations endpoints for the production Copilot runtime."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from return_platform.data_console.api.auth import require_admin_roles
from return_platform.operations.associate_flow import (
    AssociateChatTurnRequest,
    AssociateConversationView,
)
from return_platform.operations.associate_service_factory import (
    build_associate_conversation_service,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(
    prefix="/data-console/v1/copilot-operations",
    tags=["Copilot Operations"],
)


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


@router.get("/sessions", response_model=APIResponse[list[AssociateConversationView]])
async def list_sessions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[list[AssociateConversationView]]:
    service = build_associate_conversation_service(request)
    await service.ensure_indexes()
    return APIResponse(data=await service.list(limit), meta=_meta(request))


@router.get(
    "/sessions/{conversation_id}",
    response_model=APIResponse[AssociateConversationView],
)
async def get_session(
    conversation_id: str,
    request: Request,
    _actor: str = Depends(require_admin_roles),
) -> APIResponse[AssociateConversationView]:
    conversation = await build_associate_conversation_service(request).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Copilot conversation not found")
    return APIResponse(data=conversation, meta=_meta(request))


@router.post(
    "/sessions",
    response_model=APIResponse[AssociateConversationView],
    status_code=status.HTTP_201_CREATED,
)
async def start_session(
    body: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        conversation = await build_associate_conversation_service(request).start_chat(
            body,
            actor_id=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse(data=conversation, meta=_meta(request))


@router.post(
    "/sessions/{conversation_id}/messages",
    response_model=APIResponse[AssociateConversationView],
)
async def continue_session(
    conversation_id: str,
    body: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_admin_roles),
) -> APIResponse[AssociateConversationView]:
    try:
        conversation = await build_associate_conversation_service(request).continue_chat(
            conversation_id,
            body,
            actor_id=actor,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Copilot conversation not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse(data=conversation, meta=_meta(request))
