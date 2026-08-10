"""AI Gateway trace, simulator, settings, and interception APIs."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.ai_gateway.models import (
    AIRouteHealthView,
    AISafetyTestRequest,
    AISafetyTestResponse,
    AITaskView,
    AIUsageAttemptView,
    AIUsageSummaryView,
)
from return_platform.ai_gateway.safety import inspect_input
from return_platform.ai_gateway.service import AIGatewayService
from return_platform.operations.models import (
    AICompareRequest,
    AIGatewaySettingsUpdate,
    AIGatewaySettingsView,
    AIInterceptionRequest,
    AIReplayRequest,
    AIRequestStatus,
    AISimulatorRequest,
    AITraceView,
)
from return_platform.operations.repository import (
    ConcurrencyConflictError,
    OperationalRepository,
    resolve_operational_repository,
)
from return_platform.security.authorization import require_read_roles, require_write_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/ai-gateway", tags=["AI Gateway"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _gateway(request: Request, repository: OperationalRepository) -> AIGatewayService:
    # OperationalRepository.create_ai_trace uses explicit kwargs (more specific than the
    # AIGatewayRepository protocol's **kwargs: Any). The cast is safe: all callers pass
    # named arguments that OperationalRepository accepts.
    from return_platform.ai_gateway.service import AIGatewayRepository

    return AIGatewayService(
        cast(AIGatewayRepository, repository),
        request.app.state.settings,
        loaded_configuration=getattr(request.app.state, "ai_gateway_configuration", None),
        route_pool=getattr(request.app.state, "ai_gateway_route_pool", None),
    )


@router.get("/requests", response_model=APIResponse[list[AITraceView]])
async def list_requests(
    request: Request,
    trace_status: str | None = Query(default=None, alias="status"),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AITraceView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_ai_traces(status=trace_status), meta=_meta(request)
    )


@router.get("/requests/{trace_id}", response_model=APIResponse[AITraceView])
async def get_request(
    request: Request,
    trace_id: str,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[AITraceView]:
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    return APIResponse(data=trace, meta=_meta(request))


@router.get("/settings", response_model=APIResponse[AIGatewaySettingsView])
async def get_settings(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[AIGatewaySettingsView]:
    repository = resolve_operational_repository(request)
    return APIResponse(data=await repository.get_ai_settings(), meta=_meta(request))


@router.get("/routes", response_model=APIResponse[list[AIRouteHealthView]])
async def list_routes(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AIRouteHealthView]]:
    repository = resolve_operational_repository(request)
    health = await _gateway(request, repository).route_pool.health()
    return APIResponse(
        data=[AIRouteHealthView.model_validate(asdict(item)) for item in health],
        meta=_meta(request),
    )


@router.get("/tasks", response_model=APIResponse[list[AITaskView]])
async def list_tasks(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AITaskView]]:
    repository = resolve_operational_repository(request)
    configuration = _gateway(request, repository).configuration
    values = [
        AITaskView(
            taskId=task_id,
            tier=task.tier,
            promptVersion=task.promptVersion,
            fallbackStrategy=task.fallbackStrategy,
            fallbackTemplate=task.fallbackTemplate,
            maximumOutputTokens=task.maximumOutputTokens,
            maximumInputTokens=task.maximumInputTokens,
            allowTierEscalation=task.allowTierEscalation,
            allowedProviders=task.allowedProviders,
            allowedInputKeys=task.allowedInputKeys,
        )
        for task_id, task in sorted(configuration.tasks.items())
    ]
    return APIResponse(data=values, meta=_meta(request))


@router.get("/metrics", response_model=APIResponse[list[AIUsageAttemptView]])
async def list_usage_metrics(
    request: Request,
    trace_id: str | None = Query(default=None, alias="traceId"),
    task_id: str | None = Query(default=None, alias="taskId"),
    limit: int = Query(default=500, ge=1, le=10_000),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AIUsageAttemptView]]:
    repository = resolve_operational_repository(request)
    data = await repository.list_ai_attempt_metrics(
        trace_id=trace_id,
        task_id=task_id,
        limit=limit,
    )
    return APIResponse(data=data, meta=_meta(request))


@router.get("/metrics/summary", response_model=APIResponse[AIUsageSummaryView])
async def usage_summary(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[AIUsageSummaryView]:
    repository = resolve_operational_repository(request)
    return APIResponse(data=await repository.summarize_ai_attempt_metrics(), meta=_meta(request))


@router.post("/safety-test", response_model=APIResponse[AISafetyTestResponse])
async def safety_test(
    request: Request,
    payload: AISafetyTestRequest,
    _actor_id: str = Depends(require_write_roles),
) -> APIResponse[AISafetyTestResponse]:
    settings = request.app.state.settings
    if settings.environment not in {"development", "test"}:
        raise HTTPException(
            status_code=403, detail="AI safety test is disabled in this environment"
        )
    repository = resolve_operational_repository(request)
    gateway = _gateway(request, repository)
    if payload.taskId not in gateway.configuration.tasks:
        raise HTTPException(status_code=422, detail="Unknown AI task")
    inspection = inspect_input(payload.payload)
    deterministic = (
        {"status": "ALLOWED", "message": "Input passed deterministic AI safety checks."}
        if inspection.allowed
        else {
            "status": inspection.status.value,
            "message": "This assistant supports Ferguson return operations only.",
        }
    )
    return APIResponse(
        data=AISafetyTestResponse(
            taskId=payload.taskId,
            status=inspection.status,
            signals=inspection.signals,
            allowed=inspection.allowed,
            deterministicResponse=deterministic,
        ),
        meta=_meta(request),
    )


@router.put("/settings", response_model=APIResponse[AIGatewaySettingsView])
async def update_settings(
    request: Request,
    payload: AIGatewaySettingsUpdate,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[AIGatewaySettingsView]:
    settings = request.app.state.settings
    if settings.environment == "production" and "SIMULATOR" in payload.providerOrder:
        raise HTTPException(status_code=422, detail="SIMULATOR is forbidden in production")
    repository = resolve_operational_repository(request)
    try:
        data = await repository.update_ai_settings(
            intercept_mode=payload.interceptMode,
            provider_order=payload.providerOrder,
            expected_version=payload.expectedVersion,
            actor_id=actor_id,
        )
    except ConcurrencyConflictError as error:
        raise HTTPException(
            status_code=409, detail="AI gateway settings version conflict"
        ) from error
    return APIResponse(data=data, meta=_meta(request))


@router.post("/simulator", response_model=APIResponse[AITraceView])
async def simulate(
    request: Request,
    payload: AISimulatorRequest,
    _actor_id: str = Depends(require_write_roles),
) -> APIResponse[AITraceView]:
    settings = request.app.state.settings
    if settings.environment not in {"development", "test"}:
        raise HTTPException(
            status_code=403, detail="AI simulator is disabled outside development and test"
        )
    repository = resolve_operational_repository(request)
    evaluation = await _gateway(request, repository).evaluate(
        session_id=None,
        redacted_input={
            "customerReference": payload.customerReference,
            "orderReferences": payload.orderReferences,
            "reasonCode": payload.reasonCode,
            "orderStatus": "DELIVERED",
            "daysSinceDelivery": 1,
        },
        requested_decision=payload.requestedDecision,
        force_provider="SIMULATOR",
    )
    return APIResponse(data=evaluation.trace, meta=_meta(request))


@router.post("/requests/{trace_id}/replay", response_model=APIResponse[AITraceView])
async def replay_request(
    request: Request,
    trace_id: str,
    payload: AIReplayRequest,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[AITraceView]:
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    settings = request.app.state.settings
    if settings.environment == "production" and payload.provider == "SIMULATOR":
        raise HTTPException(status_code=422, detail="SIMULATOR is forbidden in production")
    if payload.editedSystemPrompt is not None and settings.environment not in {
        "development",
        "test",
    }:
        raise HTTPException(
            status_code=422, detail="Custom prompts are forbidden in this environment"
        )
    evaluation = await _gateway(request, repository).evaluate(
        session_id=trace.sessionId,
        redacted_input=trace.redactedInput,
        force_provider=payload.provider,
        system_prompt=payload.editedSystemPrompt,
        original_request_digest=trace.requestDigest,
        task_id=trace.taskId,
    )
    await repository.append_audit(
        action="AI_REQUEST_REPLAY",
        actor=actor_id,
        target=trace.id,
        details={
            "replacementTraceId": evaluation.trace.id,
            "provider": payload.provider,
            "originalRequestDigest": trace.requestDigest,
            "replacementRequestDigest": evaluation.trace.requestDigest,
        },
    )
    return APIResponse(data=evaluation.trace, meta=_meta(request))


@router.post("/requests/{trace_id}/compare", response_model=APIResponse[list[AITraceView]])
async def compare_request(
    request: Request,
    trace_id: str,
    payload: AICompareRequest,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[list[AITraceView]]:
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    settings = request.app.state.settings
    if settings.environment == "production" and "SIMULATOR" in payload.providers:
        raise HTTPException(status_code=422, detail="SIMULATOR is forbidden in production")
    service = _gateway(request, repository)
    evaluations = await asyncio.gather(
        *(
            service.evaluate(
                session_id=trace.sessionId,
                redacted_input=trace.redactedInput,
                force_provider=provider,
                original_request_digest=trace.requestDigest,
                task_id=trace.taskId,
            )
            for provider in payload.providers
        )
    )
    traces = [evaluation.trace for evaluation in evaluations]
    await repository.append_audit(
        action="AI_REQUEST_COMPARE",
        actor=actor_id,
        target=trace.id,
        details={
            "providers": payload.providers,
            "comparisonTraceIds": [item.id for item in traces],
            "originalRequestDigest": trace.requestDigest,
        },
    )
    return APIResponse(data=traces, meta=_meta(request))


@router.post("/requests/{trace_id}/intercept", response_model=APIResponse[AITraceView])
async def intercept(
    request: Request,
    trace_id: str,
    payload: AIInterceptionRequest,
    actor_id: str = Depends(require_write_roles),
) -> APIResponse[AITraceView]:
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="AI request not found")
    if trace.status is not AIRequestStatus.INTERCEPTION_PENDING:
        raise HTTPException(status_code=409, detail="AI request is not awaiting interception")
    try:
        if payload.action == "EDIT_AND_DISPATCH":
            if payload.editedSystemPrompt is None:
                raise HTTPException(
                    status_code=422, detail="EDIT_AND_DISPATCH requires editedSystemPrompt"
                )
            if request.app.state.settings.environment not in {"development", "test"}:
                raise HTTPException(
                    status_code=422,
                    detail="Custom prompt dispatch is forbidden in this environment",
                )
            await repository.update_ai_trace(
                trace.id,
                {
                    "status": AIRequestStatus.CANCELLED.value,
                    "interceptedBy": actor_id,
                    "interceptionReason": payload.reason,
                },
                expected_version=payload.expectedVersion,
            )
            gateway_settings = await repository.get_ai_settings()
            force_provider = next(
                (p for p in gateway_settings.providerOrder if p != "SIMULATOR"), "SIMULATOR"
            )
            evaluation = await _gateway(request, repository).evaluate(
                session_id=trace.sessionId,
                redacted_input=trace.redactedInput,
                force_provider=force_provider,
                system_prompt=payload.editedSystemPrompt,
                original_request_digest=trace.requestDigest,
                task_id=trace.taskId,
            )
            if trace.sessionId is not None:
                await repository.update_return(
                    trace.sessionId,
                    {
                        "aiRequestId": evaluation.trace.id,
                        "status": "QUEUED",
                        "orchestrationState": "QUEUED",
                    },
                )
            await repository.append_audit(
                action="AI_EDIT_AND_DISPATCH",
                actor=actor_id,
                target=trace.id,
                details={
                    "reason": payload.reason,
                    "replacementTraceId": evaluation.trace.id,
                    "originalRequestDigest": trace.requestDigest,
                    "replacementRequestDigest": evaluation.trace.requestDigest,
                },
            )
            return APIResponse(data=evaluation.trace, meta=_meta(request))
        if payload.action == "CANCEL":
            updated = await repository.update_ai_trace(
                trace.id,
                {
                    "status": AIRequestStatus.CANCELLED.value,
                    "interceptedBy": actor_id,
                    "interceptionReason": payload.reason,
                },
                expected_version=payload.expectedVersion,
            )
        else:
            updated = await repository.update_ai_trace(
                trace.id,
                {
                    "status": AIRequestStatus.MANUAL_OVERRIDE.value,
                    "decision": payload.action,
                    "explanation": payload.reason,
                    "confidenceMillionths": 1_000_000,
                    "provider": "MANUAL",
                    "model": "ai-console-override-v1",
                    "interceptedBy": actor_id,
                    "interceptionReason": payload.reason,
                },
                expected_version=payload.expectedVersion,
            )
            if trace.sessionId is not None:
                await repository.update_return(
                    trace.sessionId,
                    {"status": "QUEUED", "orchestrationState": "QUEUED"},
                )
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="AI request version conflict") from error
    await repository.append_audit(
        action=f"AI_INTERCEPT_{payload.action}",
        actor=actor_id,
        target=trace.id,
        details={
            "reason": payload.reason,
            "requestDigest": trace.requestDigest,
            "sessionId": trace.sessionId,
        },
    )
    if trace.sessionId is not None:
        await repository.append_event(
            trace.sessionId,
            event_type=f"AI_INTERCEPT_{payload.action}",
            actor_type="SUPPORT",
            actor_id=actor_id,
            payload={"aiRequestId": trace.id, "reason": payload.reason},
        )
    return APIResponse(data=updated, meta=_meta(request))
