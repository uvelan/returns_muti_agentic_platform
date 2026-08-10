"""Read-only production return item, handling, pickup, and agent evidence APIs.

**Superseded by `GET /api/returns/{session_id}/evidence`.** This module's single
route is misnamed: "production-artifacts" describes one of the eleven
collections it returns, not the payload, and the name collided with the
unrelated `GET /{session_id}/artifacts` on the same prefix -- which is why the
two looked like a duplicate pair.

The canonical endpoint is narrower: it omits the embedded session and timeline,
both of which have canonical endpoints of their own. This route keeps its
combined shape so the one existing consumer (`frontend/src/api/operations.ts`)
is not broken mid-flight; Wave F migrates it and deletes this module, which
takes the return-domain router count from nine to eight.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles
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
    deprecated=True,
    summary="Deprecated: use GET /api/returns/{session_id}/evidence",
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
