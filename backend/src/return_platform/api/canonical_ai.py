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

from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request

from return_platform.ai.gateway.models import (
    AIRouteHealthView,
    AITaskView,
    AIUsageAttemptView,
    AIUsageSummaryView,
)
from return_platform.ai.gateway.service import AIGatewayService
from return_platform.data_console.api.auth import require_read_roles
from return_platform.operations.repository import (
    OperationalRepository,
    resolve_operational_repository,
)
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
