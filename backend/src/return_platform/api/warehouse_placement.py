"""Warehouse bay recommendation and transactional assignment APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.data_console.api.auth import require_warehouse_roles
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.operations.warehouse.service import WarehousePlacementService
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/warehouse/returns", tags=["Warehouse Placement"])


class WarehouseApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BayRecommendationRequest(WarehouseApiModel):
    handlingUnitId: str = Field(min_length=3, max_length=128)
    requiredCapacity: int = Field(default=1, ge=1, le=10_000)
    oversized: bool = False
    hazardous: bool = False


class BayAssignmentRequest(BayRecommendationRequest):
    bayId: str = Field(min_length=1, max_length=64)
    expectedHandlingUnitVersion: int = Field(ge=0)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(
    request: Request,
) -> tuple[RuntimeResources, LoadedReturnConfiguration, OperationalRepository, WarehousePlacementService]:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "return_configuration", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(loaded, LoadedReturnConfiguration)
    ):
        raise HTTPException(status_code=503, detail="Warehouse placement dependencies unavailable.")
    repository = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    service = WarehousePlacementService(
        repository=repository,
        sql=SQLBusinessStateRepository(resources.settings),
        configuration=loaded.configuration,
    )
    return resources, loaded, repository, service


@router.post(
    "/{session_id}/bay-recommendation",
    response_model=APIResponse[dict[str, Any]],
)
async def recommend_bay(
    session_id: str,
    payload: BayRecommendationRequest,
    request: Request,
    actor: str = Depends(require_warehouse_roles),
) -> APIResponse[dict[str, Any]]:
    _, _, _, service = _service(request)
    try:
        assessment, candidates = await service.recommend(
            session_id,
            handling_unit_id=payload.handlingUnitId,
            required_capacity=payload.requiredCapacity,
            oversized=payload.oversized,
            hazardous=payload.hazardous,
            actor_id=actor,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return or handling unit not found.") from error
    return APIResponse(
        data={"assessment": assessment.model_dump(mode="json"), "candidates": candidates},
        meta=_meta(request),
    )


@router.post(
    "/{session_id}/bay-assignment",
    response_model=APIResponse[dict[str, Any]],
)
async def assign_bay(
    session_id: str,
    payload: BayAssignmentRequest,
    request: Request,
    actor: str = Depends(require_warehouse_roles),
) -> APIResponse[dict[str, Any]]:
    resources, loaded, repository, service = _service(request)
    try:
        result = await service.assign(
            session_id,
            handling_unit_id=payload.handlingUnitId,
            bay_id=payload.bayId,
            required_capacity=payload.requiredCapacity,
            oversized=payload.oversized,
            hazardous=payload.hazardous,
            expected_handling_unit_version=payload.expectedHandlingUnitVersion,
            actor_id=actor,
        )
        handling_units = await repository.list_handling_units(session_id)
        if handling_units and all(
            unit.get("physicalStatus") == "WAREHOUSE_STAGED" for unit in handling_units
        ) and resources.temporal is not None:
            coordinator = ProductionWorkflowCoordinator(
                temporal=resources.temporal,
                repository=repository,
                configuration=loaded.configuration,
                task_queue=resources.settings.return_workflow_task_queue,
            )
            await coordinator.record_event(
                session_id,
                event_id=f"warehouse-staged:{session_id}:{payload.bayId}",
                event_type=ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,
                evidence_reference=f"BAY_ASSIGNMENT:{result['assignmentId']}",
                actor_id=actor,
            )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return or handling unit not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Handling unit version conflict.") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))
