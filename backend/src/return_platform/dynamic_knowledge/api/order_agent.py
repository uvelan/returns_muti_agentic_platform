"""Versioned API boundary for the dynamic Order Discovery Agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from return_platform.dynamic_knowledge.knowledge.guards import GuardContext
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnRequest, AgentTurnResult
from return_platform.dynamic_knowledge.order_agent.coordinator import DynamicOrderAgentCoordinator, OrderAgentFailure

router = APIRouter(prefix="/api/v2/order-agent", tags=["Order Agent"])


class DynamicOrderAgentRuntime:
    """Application-owned dependency bundle attached during startup."""

    def __init__(
        self,
        *,
        coordinator: DynamicOrderAgentCoordinator,
        guard_context_factory: Callable[[Request, str], Awaitable[GuardContext]],
    ) -> None:
        self.coordinator = coordinator
        self.guard_context_factory = guard_context_factory


def resolve_runtime(request: Request) -> DynamicOrderAgentRuntime:
    runtime = getattr(request.app.state, "dynamic_order_agent_runtime", None)
    if not isinstance(runtime, DynamicOrderAgentRuntime):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_AGENT_UNAVAILABLE",
                "message": "The Order Discovery Agent runtime is not initialized.",
                "retryable": True,
            },
        )
    return runtime


@router.post("/conversations/{conversation_id}/turns", response_model=AgentTurnResult)
async def process_turn(
    conversation_id: str,
    payload: AgentTurnRequest,
    request: Request,
    runtime: Annotated[DynamicOrderAgentRuntime, Depends(resolve_runtime)],
) -> AgentTurnResult:
    if payload.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "CONVERSATION_ID_MISMATCH",
                "message": "Path and payload conversation identifiers must match.",
                "retryable": False,
            },
        )
    context = await runtime.guard_context_factory(request, payload.agent_id)
    try:
        return await runtime.coordinator.process_turn(payload, context)
    except OrderAgentFailure as exc:
        http_status = {
            "ORDER_AGENT_OUT_OF_SCOPE": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "CONVERSATION_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
            "ORDER_AGENT_QUERY_BUDGET_EXCEEDED": status.HTTP_422_UNPROCESSABLE_ENTITY,
        }.get(exc.code, status.HTTP_503_SERVICE_UNAVAILABLE if exc.retryable else status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc
