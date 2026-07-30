"""Version 2 Order Discovery Copilot API.

The v2 surface intentionally exposes only conversational Copilot operations.
Handlers delegate to the production associate flow so evidence, authorization,
locking, and return-submission behavior stay identical to v1.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status

from return_platform.api import associate_returns as v1
from return_platform.data_console.api.auth import require_associate_roles, require_read_roles
from return_platform.operations.associate_flow import (
    AssociateChatTurnRequest,
    AssociateConversationView,
    ConfirmDiscoveryRequest,
    ContinueAssociateConversationRequest,
    ReturnDetailsRequest,
    StartAssociateConversationRequest,
)
from return_platform.shared.contracts import APIResponse

router = APIRouter(prefix="/api/v2/copilot", tags=["Order Discovery Copilot v2"])


@router.get("/conversations", response_model=APIResponse[list[AssociateConversationView]])
async def list_copilot_conversations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    actor: str = Depends(require_read_roles),
) -> APIResponse[list[AssociateConversationView]]:
    return await v1.list_conversations(request=request, limit=limit, _actor=actor)


@router.post(
    "/conversations",
    response_model=APIResponse[AssociateConversationView],
    status_code=status.HTTP_201_CREATED,
)
async def start_copilot_conversation(
    payload: StartAssociateConversationRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.start_conversation(payload=payload, request=request, actor=actor)


@router.post(
    "/chat",
    response_model=APIResponse[AssociateConversationView],
    status_code=status.HTTP_201_CREATED,
)
async def start_copilot_chat(
    payload: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.start_chat(payload=payload, request=request, actor=actor)


@router.get(
    "/conversations/{conversation_id}",
    response_model=APIResponse[AssociateConversationView],
)
async def get_copilot_conversation(
    conversation_id: str,
    request: Request,
    actor: str = Depends(require_read_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.get_conversation(
        conversation_id=conversation_id,
        request=request,
        _actor=actor,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=APIResponse[AssociateConversationView],
)
async def continue_copilot_conversation(
    conversation_id: str,
    payload: ContinueAssociateConversationRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.continue_conversation(
        conversation_id=conversation_id,
        payload=payload,
        request=request,
        actor=actor,
    )


@router.post(
    "/conversations/{conversation_id}/chat",
    response_model=APIResponse[AssociateConversationView],
)
async def continue_copilot_chat(
    conversation_id: str,
    payload: AssociateChatTurnRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.continue_chat(
        conversation_id=conversation_id,
        payload=payload,
        request=request,
        actor=actor,
    )


@router.post(
    "/conversations/{conversation_id}/confirm",
    response_model=APIResponse[AssociateConversationView],
)
async def confirm_copilot_discovery(
    conversation_id: str,
    payload: ConfirmDiscoveryRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[AssociateConversationView]:
    return await v1.confirm_discovery(
        conversation_id=conversation_id,
        payload=payload,
        request=request,
        actor=actor,
    )


@router.post(
    "/conversations/{conversation_id}/details",
    response_model=APIResponse[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def submit_copilot_return_details(
    conversation_id: str,
    payload: ReturnDetailsRequest,
    request: Request,
    actor: str = Depends(require_associate_roles),
) -> APIResponse[dict[str, Any]]:
    return await v1.submit_return_details(
        conversation_id=conversation_id,
        payload=payload,
        request=request,
        actor=actor,
    )
