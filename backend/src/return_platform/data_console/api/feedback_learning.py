"""Feedback-learning review queue APIs."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.data_console.api.auth import require_read_roles
from return_platform.operations.feedback_service import (
    FeedbackLearningService,
    FeedbackLearningView,
)
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/feedback-learning", tags=["Feedback Learning"])


def _service(request: Request) -> FeedbackLearningService:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=503,
            detail="Feedback-learning dependencies are unavailable.",
        )
    return FeedbackLearningService(
        resources.mongo,
        resources.settings,
        SQLBusinessStateRepository(resources.settings),
    )


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("", response_model=APIResponse[list[FeedbackLearningView]])
async def list_feedback_records(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[FeedbackLearningView]]:
    service = _service(request)
    await service.ensure_indexes()
    return APIResponse(data=await service.list(limit), meta=_meta(request))


@router.get("/{record_id}", response_model=APIResponse[FeedbackLearningView])
async def get_feedback_record(
    record_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[FeedbackLearningView]:
    result = await _service(request).get(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Feedback-learning record not found.")
    return APIResponse(data=result, meta=_meta(request))
