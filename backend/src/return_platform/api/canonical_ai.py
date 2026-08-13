"""The canonical AI Control Center API: `/api/ai`.

The fourth and last canonical domain. Versionless, matching
`/api/graph-schema`, `/api/config` and `/api/returns`.

**What an AI Control Center is for.** Not "look at the model's answers" — it is
the operator's view of *the execution path*: which routes exist and whether they
are healthy, which tasks are configured and what each is allowed to do, what the
recent attempts cost and how often they failed over, and what is currently held
waiting on a human. Every one of those is a property of routing and policy, not
of any particular answer.

**Structured observability, never private reasoning.** The plan is explicit:
"Expose structured node/action observability, not private chain-of-thought."
Nothing here returns a prompt, a completion, or a model's working. `routes`
returns health counters, `tasks` returns configuration, `metrics` returns
attempt records, and `interceptions` returns *identifiers and status* — a
pending item's sealed prompt is fetched only through the store's explicit
`request_payload`, by whoever is actually answering it, and is not on this
surface at all.

`/api/v1/ai-gateway` keeps working until Wave F. This reads through the same
`AIGatewayService` and `OperationalRepository`; it is a surface, not a second
implementation.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai.gateway.models import (
    AIRouteHealthView,
    AITaskView,
    AIUsageAttemptView,
    AIUsageSummaryView,
)
from return_platform.ai.gateway.service import AIGatewayService
from return_platform.ai.interception.records import InterceptionStatus
from return_platform.ai.interception.store import InterceptionNotPending
from return_platform.operations.models import AICompareRequest, AIReplayRequest, AITraceView
from return_platform.operations.repository import (
    OperationalRepository,
    resolve_operational_repository,
)
from return_platform.security import capabilities
from return_platform.security.authorization import require_capability, require_read_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/ai", tags=["AI Control Center"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _gateway(request: Request, repository: OperationalRepository) -> AIGatewayService:
    """Same construction as the legacy router, and for the same reason.

    `OperationalRepository.create_ai_trace` takes explicit kwargs, which is more
    specific than the `AIGatewayRepository` protocol's `**kwargs: Any`. The cast
    is safe because every caller passes named arguments the repository accepts.
    """
    from return_platform.ai.gateway.service import AIGatewayRepository

    return AIGatewayService(
        cast(AIGatewayRepository, repository),
        request.app.state.settings,
        loaded_configuration=getattr(request.app.state, "ai_gateway_configuration", None),
        route_pool=getattr(request.app.state, "ai_gateway_route_pool", None),
    )


@router.get("/routes", response_model=APIResponse[list[AIRouteHealthView]])
async def list_routes(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AIRouteHealthView]]:
    """Every resolved `(provider, model, credential, tier)` and its live health.

    This is the screen that answers "why did that task fail over?" -- circuit
    state and per-route counters are the only place that is visible.
    """
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
    """The configured tasks and what each is permitted.

    `allowedProviders` and `allowedInputKeys` matter operationally: they are the
    policy that stops a caller reaching a provider or sending a field the task
    was never approved for.
    """
    repository = resolve_operational_repository(request)
    configuration = _gateway(request, repository).configuration
    return APIResponse(
        data=[
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
        ],
        meta=_meta(request),
    )


@router.get("/metrics", response_model=APIResponse[list[AIUsageAttemptView]])
async def list_metrics(
    request: Request,
    trace_id: str | None = Query(default=None, alias="traceId"),
    task_id: str | None = Query(default=None, alias="taskId"),
    limit: int = Query(default=500, ge=1, le=10_000),
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[AIUsageAttemptView]]:
    repository = resolve_operational_repository(request)
    return APIResponse(
        data=await repository.list_ai_attempt_metrics(
            trace_id=trace_id, task_id=task_id, limit=limit
        ),
        meta=_meta(request),
    )


@router.get("/metrics/summary", response_model=APIResponse[AIUsageSummaryView])
async def metrics_summary(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[AIUsageSummaryView]:
    repository = resolve_operational_repository(request)
    return APIResponse(data=await repository.summarize_ai_attempt_metrics(), meta=_meta(request))


@router.get("/interceptions", response_model=APIResponse[list[dict[str, Any]]])
async def list_interceptions(
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[list[dict[str, Any]]]:
    """Requests currently held waiting on a human.

    **Identifiers and status only.** The held prompt is sealed at rest and is
    deliberately not on this surface: an operator listing the queue does not
    need to see every pending prompt, and decrypting them all to render a list
    would defeat sealing them. Whoever actually answers one fetches its payload
    explicitly through the store.

    Returns an empty list rather than 503 when no store is configured. A
    deployment with no interception store has no pending interceptions, which is
    a true answer; failing here would break the AI screen for every environment
    that never uses the manual path.
    """
    store = getattr(request.app.state, "ai_interception_store", None)
    if store is None or not hasattr(store, "list_pending"):
        return APIResponse(data=[], meta=_meta(request))
    pending = await store.list_pending()
    return APIResponse(
        data=[
            {
                "interceptionId": record.interception_id,
                "taskId": record.task_id,
                "status": record.status.value,
                "createdAt": record.created_at.isoformat(),
                "expiresAt": record.expires_at.isoformat(),
                "answeredBy": record.answered_by,
            }
            for record in pending
        ],
        meta=_meta(request),
    )


# ---------------------------------------------------------------------------
# The operator surface: opening a held request and answering it
# ---------------------------------------------------------------------------
#
# The listing above is deliberately identity-and-status only. These two are how
# an operator actually does the work, and they are separate endpoints precisely
# so that unsealing a prompt is a distinct, auditable act rather than a side
# effect of looking at a queue.


class InterceptionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responseText: str = Field(min_length=1, max_length=64_000)


def _require_interception_store(request: Request) -> Any:
    """503 rather than an empty answer.

    The queue listing returns `[]` when no store is configured, because "no
    pending interceptions" is a true answer for a deployment that never uses the
    manual path. Opening or answering a specific one is different: there is no
    truthful empty response to "give me interception X", and pretending it does
    not exist would read as "already handled".
    """
    store = getattr(request.app.state, "ai_interception_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI interception store is configured in this process.",
        )
    return store


@router.get("/interceptions/{interception_id}/request", response_model=APIResponse[dict[str, Any]])
async def read_interception_request(
    interception_id: str,
    request: Request,
    _actor_id: str = Depends(require_capability(capabilities.AI_INTERCEPTION_ACT)),
) -> APIResponse[dict[str, Any]]:
    """The held prompt, unsealed.

    Gated on `ai.interception.act` rather than `ai.interception.read`, which is
    the narrower of the two on purpose. The payload can contain block 5
    UNTRUSTED SOURCE SAMPLE -- rows read out of a customer's database -- which is
    why it is sealed at rest at all. You unseal it because you are about to
    answer it; browsing the queue needs only the read capability.
    """
    store = _require_interception_store(request)
    payload = await store.request_payload(interception_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Interception {interception_id} has no readable request payload.",
        )
    return APIResponse(data=dict(payload), meta=_meta(request))


@router.post("/interceptions/{interception_id}/answer", response_model=APIResponse[dict[str, Any]])
async def answer_interception(
    interception_id: str,
    payload: InterceptionAnswer,
    request: Request,
    actor_id: str = Depends(require_capability(capabilities.AI_INTERCEPTION_ACT)),
) -> APIResponse[dict[str, Any]]:
    """Record a human answer to a held request.

    The store's `answer` is a conditional write filtered on `PENDING`, so two
    operators answering at once produce one winner and one 409 -- never a silent
    overwrite of somebody's text. That is the store's guarantee; this surface
    only has to report it honestly.

    **Answering does not resume the workflow here.** It transitions the
    interception, and `InterceptionResumeDispatcher` turns answered records into
    resume commands, which the reasoning resume worker delivers as Temporal
    signals. Doing the enqueue inline would put a cross-collection write on the
    request path and lose the at-least-once replay that makes the bridge safe.
    """
    store = _require_interception_store(request)
    try:
        record = await store.answer(
            interception_id=interception_id,
            response_text=payload.responseText,
            answered_by=actor_id,
        )
    except InterceptionNotPending as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return APIResponse(
        data={
            "interceptionId": record.interception_id,
            "status": record.status.value,
            "answeredBy": record.answered_by,
            "answeredAt": record.answered_at.isoformat() if record.answered_at else None,
        },
        meta=_meta(request),
    )


@router.post("/interceptions/{interception_id}/cancel", response_model=APIResponse[dict[str, Any]])
async def cancel_interception(
    interception_id: str,
    request: Request,
    _actor_id: str = Depends(require_capability(capabilities.AI_INTERCEPTION_ACT)),
) -> APIResponse[dict[str, Any]]:
    """Abandon a held request rather than answering it.

    The counterpart to `answer`, and it needs to exist for the same reason:
    without it the only way out of a PENDING interception is to answer it or
    wait for `expiresAt`, and an operator who can see the prompt is wrong has no
    way to say so. The caller that is blocked on it fails fast instead of
    holding a workflow open until the expiry sweeps it.

    Same conditional-write discipline as `answer` -- filtered on PENDING, so
    cancelling an already-answered interception cannot overwrite the answer.

    **`store.cancel` reports nothing, so the outcome is read back.** It returns
    silently when the record is missing or already terminal, because its other
    caller is the expiry sweep, for which "somebody got there first" is a normal
    outcome and not an error. An operator pressing Cancel needs the opposite:
    being told their cancel did nothing because an answer landed first. Checking
    the state *before* cancelling would be a race; reading it back after reports
    what actually happened.

    Gated on `.act`, not `.read`: this ends somebody's request.
    """
    store = _require_interception_store(request)
    await store.cancel(interception_id=interception_id, status=InterceptionStatus.CANCELLED)
    record = await store.get(interception_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"interception '{interception_id}' does not exist",
        )
    if record.status is not InterceptionStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"interception '{interception_id}' is {record.status.value} and was not cancelled"
            ),
        )
    return APIResponse(
        data={"interceptionId": record.interception_id, "status": record.status.value},
        meta=_meta(request),
    )


@router.post("/requests/{trace_id}/replay", response_model=APIResponse[AITraceView])
async def replay_request(
    trace_id: str,
    payload: AIReplayRequest,
    request: Request,
    actor_id: str = Depends(require_capability(capabilities.AI_REPLAY_READ)),
) -> APIResponse[AITraceView]:
    """Re-run a recorded request, optionally against a different provider.

    The mechanism existed on `/api/v1/ai-gateway` and had no caller (C4.5), so
    the first question anyone asks about a bad answer -- "was that the model or
    the prompt?" -- could only be answered from the database. Mounting it on the
    canonical surface is what S8 needs; the implementation is the same
    `AIGatewayService.evaluate`, not a second one.

    **The original trace is never modified.** A replay produces a new trace
    carrying `originalRequestDigest`, so the pair is comparable and the recorded
    evidence stays as recorded. Editing the original would destroy the only
    account of what actually happened.

    The forced provider is validated against production rules here rather than
    trusted from the console: SIMULATOR is refused in production, and a custom
    prompt is refused outside development and test.
    """
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"AI request {trace_id} does not exist"
        )
    settings = request.app.state.settings
    if settings.environment == "production" and payload.provider == "SIMULATOR":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SIMULATOR is forbidden in production",
        )
    if payload.editedSystemPrompt is not None and settings.environment not in {
        "development",
        "test",
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Custom prompts are forbidden in this environment",
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
    trace_id: str,
    payload: AICompareRequest,
    request: Request,
    actor_id: str = Depends(require_capability(capabilities.AI_REPLAY_READ)),
) -> APIResponse[list[AITraceView]]:
    """The same recorded request against several providers at once.

    Concurrent rather than sequential because the point is a comparison, and a
    serial run spreads the attempts across minutes of changing rate-limit and
    circuit state -- which is a different experiment from the one the operator
    asked for.

    Each provider produces its own trace, and each is priced by the same catalog
    at the same instant, so "which provider answers this well, and what does it
    cost" is one read afterwards rather than an estimate.
    """
    repository = resolve_operational_repository(request)
    trace = await repository.get_ai_trace(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"AI request {trace_id} does not exist"
        )
    settings = request.app.state.settings
    if settings.environment == "production" and "SIMULATOR" in payload.providers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SIMULATOR is forbidden in production",
        )
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
