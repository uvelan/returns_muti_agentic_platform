"""Governed AI Studio proposal and sandbox-apply APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from return_platform.data_console.api.auth import require_read_roles, require_write_roles
from return_platform.data_platform.ai_studio import (
    AIStudioApplyRequest,
    AIStudioGenerationRequest,
    AIStudioPromptRequest,
    AIStudioProposalView,
    AIStudioService,
)
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/data-console/v1/ai-studio", tags=["AI Studio"])


def _resources(request: Request) -> RuntimeResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources):
        raise HTTPException(status_code=503, detail="Application resources are unavailable.")
    if (
        resources.mongo is None
        or resources.source_mongo is None
        or resources.schema_registry is None
    ):
        raise HTTPException(status_code=503, detail="AI Studio dependencies are unavailable.")
    return resources


def _service(request: Request) -> AIStudioService:
    resources = _resources(request)
    assert resources.mongo is not None
    assert resources.source_mongo is not None
    assert resources.schema_registry is not None
    return AIStudioService(
        client=resources.mongo,
        source_client=resources.source_mongo,
        settings=resources.settings,
        registry=resources.schema_registry,
    )


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


@router.get("/proposals", response_model=APIResponse[list[AIStudioProposalView]])
async def list_proposals(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[AIStudioProposalView]]:
    service = _service(request)
    await service.ensure_indexes()
    return APIResponse(data=await service.list(limit), meta=_meta(request))


@router.post("/proposals", response_model=APIResponse[AIStudioProposalView], status_code=201)
async def generate_proposal(
    payload: AIStudioGenerationRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[AIStudioProposalView]:
    service = _service(request)
    await service.ensure_indexes()
    try:
        result = await service.generate(payload, actor_id=actor)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.post(
    "/proposals/from-prompt",
    response_model=APIResponse[AIStudioProposalView],
    status_code=201,
)
async def generate_prompt_proposal(
    payload: AIStudioPromptRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[AIStudioProposalView]:
    service = _service(request)
    await service.ensure_indexes()
    try:
        result = await service.generate_from_prompt(payload, actor_id=actor)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))


@router.get(
    "/proposals/{proposal_id}",
    response_model=APIResponse[dict[str, Any]],
)
async def get_proposal(
    proposal_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    result = await _service(request).get(proposal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="AI Studio proposal not found.")
    view, records = result
    return APIResponse(
        data={"proposal": view.model_dump(mode="json"), "records": records},
        meta=_meta(request),
    )


@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=APIResponse[AIStudioProposalView],
)
async def apply_proposal(
    proposal_id: str,
    payload: AIStudioApplyRequest,
    request: Request,
    actor: str = Depends(require_write_roles),
) -> APIResponse[AIStudioProposalView]:
    try:
        result = await _service(request).apply(proposal_id, payload, actor_id=actor)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="AI Studio proposal not found.") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))
