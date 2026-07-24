"""AI Gateway trace, simulator, settings, and interception APIs."""

from __future__ import annotations

import asyncio
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.ai_gateway.service import AIGatewayService
from return_platform.data_console.api.auth import require_read_roles, require_write_roles
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
    resolve_operational_repository,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/ai-gateway", tags=["AI Gateway"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("/requests", response_model=APIResponse[list[AITraceView]])
async def list_requests(
    request: Request,
    trace_status: str | None = Query(default=None, alias="status"),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AITraceView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(data=await repository.list_ai_traces(status=trace_status), meta=_meta(request))


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
        raise HTTPException(status_code=409, detail="AI gateway settings version conflict") from error
    return APIResponse(data=data, meta=_meta(request))


@router.post("/simulator", response_model=APIResponse[AITraceView])
async def simulate(
    request: Request,
    payload: AISimulatorRequest,
    _actor_id: str = Depends(require_write_roles),
) -> APIResponse[AITraceView]:
    settings = request.app.state.settings
    if settings.environment not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="AI simulator is disabled outside development and test")
    repository = resolve_operational_repository(request)
    evaluation = await AIGatewayService(repository, settings).evaluate(
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
    evaluation = await AIGatewayService(repository, settings).evaluate(
        session_id=trace.sessionId,
        redacted_input=trace.redactedInput,
        force_provider=payload.provider,
        system_prompt=payload.editedSystemPrompt or trace.systemPrompt,
        original_request_digest=trace.requestDigest,
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
    service = AIGatewayService(repository, settings)
    evaluations = await asyncio.gather(
        *(
            service.evaluate(
                session_id=trace.sessionId,
                redacted_input=trace.redactedInput,
                force_provider=provider,
                system_prompt=trace.systemPrompt,
                original_request_digest=trace.requestDigest,
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
                raise HTTPException(status_code=422, detail="EDIT_AND_DISPATCH requires editedSystemPrompt")
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
            force_provider = next((p for p in gateway_settings.providerOrder if p != "SIMULATOR"), "SIMULATOR")
            evaluation = await AIGatewayService(repository, request.app.state.settings).evaluate(
                session_id=trace.sessionId,
                redacted_input=trace.redactedInput,
                force_provider=force_provider,
                system_prompt=payload.editedSystemPrompt,
                original_request_digest=trace.requestDigest,
            )
            if trace.sessionId is not None:
                await repository.update_return(
                    trace.sessionId,
                    {"aiRequestId": evaluation.trace.id, "status": "QUEUED", "orchestrationState": "QUEUED"},
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
