"""Read-only production return item, handling, pickup, and agent evidence APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.data_console.api.auth import require_read_roles
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/returns", tags=["Production Return Evidence"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _repository(request: Request) -> OperationalRepository:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(status_code=503, detail="Return evidence store unavailable.")
    return OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)


@router.get(
    "/{session_id}/production-artifacts",
    response_model=APIResponse[dict[str, Any]],
)
async def production_artifacts(
    session_id: str,
    request: Request,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[dict[str, Any]]:
    repository = _repository(request)
    session = await repository.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    return APIResponse(
        data={
            "return": session.model_dump(mode="json"),
            "returnItems": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_return_items(session_id)
            ],
            "handlingUnits": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_handling_units(session_id)
            ],
            "pickup": await repository.get_pickup_projection(session_id),
            "branchStaging": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_branch_staging_records(session_id)
            ],
            "documentArtifacts": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_document_artifacts(session_id)
            ],
            "shippingInstructions": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_shipping_instructions(session_id)
            ],
            "shipmentEvents": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_shipment_events(session_id)
            ],
            "omcCommands": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_omc_commands(session_id)
            ],
            "integrationCommands": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_integration_commands(session_id)
            ],
            "vendorReturnLinks": [
                {key: value for key, value in item.items() if key != "_id"}
                for item in await repository.list_vendor_return_links(session_id)
            ],
            "agentDecisions": await repository.list_agent_decisions(session_id),
            "timeline": [
                item.model_dump(mode="json")
                for item in await repository.list_events(session_id, limit=1_000)
            ],
        },
        meta=_meta(request),
    )
