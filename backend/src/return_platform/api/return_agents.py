"""Production return-agent assessment and configuration inspection APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.agents.contracts import (
    BayAssessment,
    BayAssessmentRequest,
    DiscoveryAssessment,
    DiscoveryAssessmentRequest,
    FeedbackAssessment,
    FeedbackAssessmentRequest,
    FulfillmentAssessment,
    FulfillmentAssessmentRequest,
    ReturnWorkflowAssessment,
    ReturnWorkflowAssessmentRequest,
)
from return_platform.agents.registry import ReturnAgentRegistry
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/return-agents", tags=["Return Agents"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _loaded(request: Request) -> LoadedReturnConfiguration:
    value = getattr(request.app.state, "return_configuration", None)
    if not isinstance(value, LoadedReturnConfiguration):
        raise HTTPException(status_code=503, detail="Return agent configuration is unavailable.")
    return value


def _registry(request: Request) -> ReturnAgentRegistry:
    return ReturnAgentRegistry.build(_loaded(request).configuration)


async def _persist_decision(request: Request, decision: Any, *, actor_id: str) -> None:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable.")
    client = resources.mongo
    collection = client[resources.settings.mongo_database]["agent_decisions"]
    await collection.create_index([("createdAt", -1)])
    await collection.create_index([("agent", 1), ("decisionType", 1), ("createdAt", -1)])
    payload = decision.model_dump(mode="json")
    await collection.insert_one(
        {
            "_id": str(uuid.uuid4()),
            **payload,
            "actorId": actor_id,
            "correlationId": _meta(request).request_id,
            "createdAt": datetime.now(UTC),
        }
    )


@router.get("/configuration", response_model=APIResponse[dict[str, Any]])
async def configuration(
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    loaded = _loaded(request)
    config = loaded.configuration
    return APIResponse(
        data={
            "schemaVersion": config.schema_version,
            "assumptionSetVersion": config.assumption_set_version,
            "sha256": loaded.sha256,
            "agents": {key: value.model_dump(mode="json") for key, value in config.agents.items()},
            "workflow": config.workflow.model_dump(mode="json"),
            "support": config.support.model_dump(mode="json"),
            "bay": config.bay.model_dump(mode="json"),
            "integrations": config.integrations.model_dump(mode="json"),
            "extensions": config.extensions.model_dump(mode="json"),
        },
        meta=_meta(request),
    )


@router.post("/order-discovery/assess", response_model=APIResponse[DiscoveryAssessment])
async def assess_discovery(
    payload: DiscoveryAssessmentRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[DiscoveryAssessment]:
    result = _registry(request).order_discovery.assess(payload)
    await _persist_decision(request, result.decision, actor_id=actor)
    return APIResponse(data=result, meta=_meta(request))


@router.post("/return-workflow/assess", response_model=APIResponse[ReturnWorkflowAssessment])
async def assess_return_workflow(
    payload: ReturnWorkflowAssessmentRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[ReturnWorkflowAssessment]:
    result = _registry(request).return_workflow.assess(payload)
    await _persist_decision(request, result.decision, actor_id=actor)
    return APIResponse(data=result, meta=_meta(request))


@router.post("/fulfillment/assess", response_model=APIResponse[FulfillmentAssessment])
async def assess_fulfillment(
    payload: FulfillmentAssessmentRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[FulfillmentAssessment]:
    result = _registry(request).return_fulfillment.assess(payload)
    await _persist_decision(request, result.decision, actor_id=actor)
    return APIResponse(data=result, meta=_meta(request))


@router.post("/bay-assignment/assess", response_model=APIResponse[BayAssessment])
async def assess_bay(
    payload: BayAssessmentRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[BayAssessment]:
    result = _registry(request).bay_assignment.assess(payload)
    await _persist_decision(request, result.decision, actor_id=actor)
    return APIResponse(data=result, meta=_meta(request))


@router.post("/feedback/assess", response_model=APIResponse[FeedbackAssessment])
async def assess_feedback(
    payload: FeedbackAssessmentRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[FeedbackAssessment]:
    result = _registry(request).feedback_learning.assess(payload)
    await _persist_decision(request, result.decision, actor_id=actor)
    return APIResponse(data=result, meta=_meta(request))
