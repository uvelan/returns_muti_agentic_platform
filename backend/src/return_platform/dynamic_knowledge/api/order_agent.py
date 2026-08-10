"""Versioned API boundary for the dynamic Order Discovery Agent.

Turn submission goes through a durable per-conversation Temporal workflow
(`OrderDiscoveryWorkflow`) rather than calling `DynamicOrderAgentCoordinator`
directly -- see `workflows/order_discovery_workflow.py`. This route owns
identity extraction (principal/tenant/roles/branch_ids) and the workflow
handle lifecycle; everything about *how* a turn is processed lives in the
workflow/activity/coordinator/graph stack behind it.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)
from return_platform.security.principal import Principal
from return_platform.workflows.order_discovery_workflow import (
    OrderDiscoveryTurnOutcome,
    OrderDiscoveryWorkflow,
    OrderDiscoveryWorkflowInput,
    SubmitOrderDiscoveryTurnCommand,
)

router = APIRouter(prefix="/api/v2/order-agent", tags=["Order Agent"])

_HTTP_STATUS_BY_CODE: dict[str, int] = {
    "ORDER_AGENT_OUT_OF_SCOPE": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "CONVERSATION_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "ORDER_AGENT_QUERY_BUDGET_EXCEEDED": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


class DynamicOrderAgentRuntime:
    """Application-owned dependency bundle attached during startup."""

    def __init__(self, *, temporal_client: Client, task_queue: str) -> None:
        self.temporal_client = temporal_client
        self.task_queue = task_queue


def resolve_runtime(request: Request) -> DynamicOrderAgentRuntime:
    runtime = getattr(request.app.state, "dynamic_order_agent_runtime", None)
    # `isinstance`, not a class-name string. The name check made `runtime` stay
    # `Any`, so `-> DynamicOrderAgentRuntime` was unverified -- and it would
    # also accept any unrelated class that happened to share the name.
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


def _order_discovery_workflow_id(conversation_id: str) -> str:
    return f"order-discovery-{conversation_id}"


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

    principal = cast(Principal, request.state.principal)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    branch_ids_raw: Any = getattr(request.state, "branch_ids", ())
    branch_ids = frozenset(str(value) for value in branch_ids_raw)

    workflow_id = _order_discovery_workflow_id(conversation_id)
    try:
        handle = await runtime.temporal_client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id=conversation_id, agent_id=payload.agent_id),
            id=workflow_id,
            task_queue=runtime.task_queue,
        )
    except WorkflowAlreadyStartedError:
        handle = runtime.temporal_client.get_workflow_handle(workflow_id)

    try:
        outcome = cast(
            OrderDiscoveryTurnOutcome,
            await handle.execute_update(
                OrderDiscoveryWorkflow.submit_turn,
                SubmitOrderDiscoveryTurnCommand(
                    expected_conversation_version=payload.expected_conversation_version,
                    client_turn_id=payload.client_turn_id,
                    idempotency_key=payload.idempotency_key,
                    message_id=payload.message_id,
                    message=payload.message,
                    principal_id=principal.subject,
                    tenant_id=tenant_id,
                    roles=principal.roles,
                    branch_ids=branch_ids,
                ),
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_AGENT_WORKFLOW_FAILED",
                "message": "The Order Discovery workflow could not process the request.",
                "retryable": True,
            },
        ) from exc

    if outcome.error is not None:
        http_status = _HTTP_STATUS_BY_CODE.get(
            outcome.error.code,
            status.HTTP_503_SERVICE_UNAVAILABLE
            if outcome.error.retryable
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
        raise HTTPException(
            status_code=http_status,
            detail={
                "code": outcome.error.code,
                "message": outcome.error.message,
                "retryable": outcome.error.retryable,
            },
        )

    if outcome.result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_AGENT_WORKFLOW_FAILED",
                "message": "The Order Discovery workflow returned no result.",
                "retryable": True,
            },
        )

    return AgentTurnResult.model_validate_json(outcome.result.agent_turn_result_json)
