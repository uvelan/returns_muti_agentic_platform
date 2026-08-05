"""Versioned API boundary for the dynamic Order Discovery Agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from return_platform.dynamic_knowledge.knowledge.guards import GuardContext
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import (
    DynamicOrderAgentCoordinator,
    OrderAgentFailure,
)

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
    if isinstance(runtime, DynamicOrderAgentRuntime):
        return runtime

    status_payload: Any = getattr(
        request.app.state,
        "dynamic_order_agent_status",
        {"state": "NOT_INITIALIZED"},
    )
    state = (
        str(status_payload.get("state", "NOT_INITIALIZED"))
        if isinstance(status_payload, dict)
        else "NOT_INITIALIZED"
    )
    code = {
        "DISABLED": "ORDER_AGENT_DISABLED",
        "DEPENDENCIES_UNAVAILABLE": "ORDER_AGENT_DEPENDENCIES_UNAVAILABLE",
        "INITIALIZATION_FAILED": "ORDER_AGENT_INITIALIZATION_FAILED",
    }.get(state, "ORDER_AGENT_UNAVAILABLE")
    message = {
        "DISABLED": "The Order Discovery Agent is disabled by runtime configuration.",
        "DEPENDENCIES_UNAVAILABLE": (
            "The Order Discovery Agent is waiting for required platform dependencies."
        ),
        "INITIALIZATION_FAILED": (
            "The Order Discovery Agent failed to initialize. Inspect backend startup logs."
        ),
    }.get(state, "The Order Discovery Agent runtime is not initialized.")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": code,
            "message": message,
            "retryable": state != "DISABLED",
        },
    )


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
        }.get(
            exc.code,
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.retryable
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        ) from exc
