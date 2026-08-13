"""Warehouse bay recommendation and transactional assignment APIs."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.dynamic_knowledge.integration.bay_observations import (
    GraphWarehouseBayObservations,
)
from return_platform.dynamic_knowledge.integration.targeted_sync import (
    build_targeted_graph_access,
)
from return_platform.operations.production_event_authorization import (
    ProductionEventNotPermitted,
    authorize_production_event,
)
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.operations.warehouse.observations import (
    BayEvidence,
    WarehouseBayObservationPort,
    WarehouseObservation,
)
from return_platform.operations.warehouse.service import WarehousePlacementService
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import actor_roles, require_warehouse_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/warehouse/returns", tags=["Warehouse Placement"])

logger = logging.getLogger("return_platform.api.warehouse_placement")

#: Where the built port is cached on app state. Building it opens a release
#: store, a lease store and a sync store, so it is assembled once per process
#: rather than per request -- and the failure to build it is remembered too,
#: because retrying a Neo4j connection on every bay recommendation would turn a
#: `best_effort` stage into a per-request timeout.
_OBSERVATIONS_ATTRIBUTE = "warehouse_bay_observations"


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


async def _observations(
    request: Request, resources: RuntimeResources
) -> WarehouseBayObservationPort | None:
    """The graph reader bay candidates come from, or nothing.

    Bay placement is `best_effort` by declared policy, so a graph this process
    cannot reach must not take the endpoint down: the recommendation comes back
    with no candidate and `bayObservation` saying `WAREHOUSE_UNAVAILABLE`, which
    is a reading an operator can act on. Falling back to the SQL bay query would
    be the other option and is the one W2.7 exists to remove -- the point is not
    that the SQL read is slow, it is that an agent path reading a source directly
    bypasses every guard and generation fence the graph read goes through.
    """
    if hasattr(request.app.state, _OBSERVATIONS_ATTRIBUTE):
        cached: WarehouseBayObservationPort | None = getattr(
            request.app.state, _OBSERVATIONS_ATTRIBUTE
        )
        return cached
    port: WarehouseBayObservationPort | None = None
    if resources.mongo is not None and resources.neo4j is not None:
        try:
            access = await build_targeted_graph_access(
                settings=resources.settings,
                platform_mongo=resources.mongo,
                source_mongo=resources.source_mongo or resources.mongo,
                neo4j_driver=resources.neo4j,
                owner_role="warehouse-placement",
            )
            port = GraphWarehouseBayObservations.from_access(access)
        except Exception:  # noqa: BLE001 - see docstring; the stage is best_effort
            logger.exception("warehouse_bay_observations_unavailable")
    setattr(request.app.state, _OBSERVATIONS_ATTRIBUTE, port)
    return port


async def _service(
    request: Request,
) -> tuple[
    RuntimeResources, LoadedReturnConfiguration, OperationalRepository, WarehousePlacementService
]:
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
        observations=await _observations(request, resources),
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
    _, _, _, service = await _service(request)
    try:
        assessment, candidates, observation = await service.recommend_with_evidence(
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
        data={
            "assessment": assessment.model_dump(mode="json"),
            "candidates": candidates,
            # Reported rather than inferred from an empty candidate list. "This
            # warehouse has no eligible bay", "we have never heard of this
            # warehouse" and "we could not look" all produce zero candidates and
            # oblige entirely different things of whoever is holding the parcel.
            "bayObservation": _observation_payload(observation),
        },
        meta=_meta(request),
    )


def _observation_payload(observation: WarehouseObservation | None) -> dict[str, Any]:
    if observation is None:
        return {
            "evidence": BayEvidence.UNAVAILABLE.value,
            "evidenceReference": "WAREHOUSE_UNAVAILABLE:NOT_ATTEMPTED",
            "detail": "no graph reader is configured for this process",
        }
    return {
        "evidence": observation.evidence.value,
        "evidenceReference": observation.evidence_reference,
        "warehouseReference": observation.warehouse_reference,
        "graphGenerationId": observation.graph_generation_id,
        "absentReason": observation.absent_reason,
        "syncRequestId": observation.sync_request_id,
        "syncSkippedReason": observation.sync_skipped_reason,
    }


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
    roles = actor_roles(request)
    # Authorized before anything is resolved or mutated: staging the last
    # handling unit records WAREHOUSE_PROCESSING_COMPLETED, and a 403 raised
    # after the assignment would leave the bay taken and the workflow
    # un-signalled. Ahead of `_service` too, so a caller who may not cause the
    # transition is refused whether or not the datastore is up.
    try:
        authorize_production_event(
            event_type=ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,
            actor_roles=roles,
        )
    except ProductionEventNotPermitted as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    resources, loaded, repository, service = await _service(request)
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
        if (
            handling_units
            and all(unit.get("physicalStatus") == "WAREHOUSE_STAGED" for unit in handling_units)
            and resources.temporal is not None
        ):
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
                actor_roles=roles,
            )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Return or handling unit not found.") from error
    except ConcurrencyConflictError as error:
        raise HTTPException(status_code=409, detail="Handling unit version conflict.") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=result, meta=_meta(request))
