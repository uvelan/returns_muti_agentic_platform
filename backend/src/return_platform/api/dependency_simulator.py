"""Development/test dependency simulator API with dedicated operational views."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from return_platform.ai.routing.tasks import LoadedAIGatewayConfiguration
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.dependency_simulation.configuration import (
    LoadedDependencySimulationConfiguration,
)
from return_platform.dependency_simulation.models import (
    DependencySimulationSummary,
    SimulationAdvanceRequest,
    SimulationAIUsageMetric,
    SimulationE2ERequest,
    SimulationE2EResult,
    SimulationOperationRequest,
    SimulationOperationView,
    SimulationResetRequest,
)
from return_platform.dependency_simulation.repository import MongoSimulationRepository
from return_platform.dependency_simulation.service import DependencySimulationService
from return_platform.dependency_simulation.workflow_bridge import signal_workflow
from return_platform.operations.production_event_authorization import PLATFORM_SERVICE_ROLES
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_read_roles, require_write_roles
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

router = APIRouter(prefix="/api/v1/dependency-simulator", tags=["Dependency Simulator"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=cast(str, getattr(request.state, "correlation_id", "unknown")))


def _service(
    request: Request,
) -> tuple[
    DependencySimulationService, RuntimeResources, LoadedReturnConfiguration, OperationalRepository
]:
    resources = getattr(request.app.state, "resources", None)
    loaded = getattr(request.app.state, "dependency_simulation_configuration", None)
    loaded_returns = getattr(request.app.state, "return_configuration", None)
    loaded_ai_gateway = getattr(request.app.state, "ai_gateway_configuration", None)
    if not isinstance(resources, RuntimeResources) or resources.mongo is None:
        raise HTTPException(
            status_code=503, detail="Platform MongoDB is required for dependency simulation."
        )
    if not isinstance(loaded, LoadedDependencySimulationConfiguration) or not isinstance(
        loaded_returns, LoadedReturnConfiguration
    ):
        raise HTTPException(
            status_code=503, detail="Dependency simulation configuration is unavailable."
        )
    if resources.settings.environment == "production" or not loaded.configuration.enabled:
        raise HTTPException(
            status_code=403, detail="Dependency simulation is disabled in this environment."
        )
    repository = MongoSimulationRepository(resources.mongo, resources.settings)
    operational = OperationalRepository(resources.mongo, resources.settings, resources.source_mongo)
    return (
        DependencySimulationService(
            repository,
            resources.settings,
            loaded,
            loaded_ai_gateway=(
                loaded_ai_gateway
                if isinstance(loaded_ai_gateway, LoadedAIGatewayConfiguration)
                else None
            ),
            route_pool=getattr(request.app.state, "ai_gateway_route_pool", None),
            # AI-01. The simulator dispatches to a real provider like any other
            # caller, so it is gated like one. Both values are already in hand.
            interception_store=getattr(request.app.state, "ai_interception_store", None),
            gateway_settings=operational,
        ),
        resources,
        loaded_returns,
        operational,
    )


def _simulation_header(response: Response) -> None:
    response.headers["X-Simulation-Mode"] = "true"
    response.headers["Cache-Control"] = "no-store"


@router.get("/summary", response_model=APIResponse[DependencySimulationSummary])
async def summary(
    request: Request, response: Response, _actor: str = Depends(require_read_roles)
) -> APIResponse[DependencySimulationSummary]:
    service, resources, _, _ = _service(request)
    _simulation_header(response)
    data = DependencySimulationSummary(
        enabled=service.configuration.enabled,
        banner=service.configuration.modeBanner,
        environment=resources.settings.environment,
        modes={
            "OMC": resources.settings.omc_dependency_mode,
            "PARCEL": resources.settings.parcel_dependency_mode,
            "FREIGHT": resources.settings.freight_dependency_mode,
            "LSI": resources.settings.lsi_dependency_mode,
        },
        operationCounts=await service.repository.operation_counts(),
        ai=await service.repository.ai_summary(),
        configurationSha256=service.loaded.sha256,
    )
    return APIResponse(data=data, meta=_meta(request))


@router.get("/operations", response_model=APIResponse[list[SimulationOperationView]])
async def operations(
    request: Request,
    response: Response,
    dependency: str | None = Query(default=None),
    session_id: str | None = Query(default=None, alias="sessionId"),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SimulationOperationView]]:
    service, _, _, _ = _service(request)
    _simulation_header(response)
    return APIResponse(
        data=await service.repository.list_operations(dependency=dependency, session_id=session_id),
        meta=_meta(request),
    )


@router.get("/operations/{operation_id}", response_model=APIResponse[SimulationOperationView])
async def operation_detail(
    operation_id: str,
    request: Request,
    response: Response,
    _actor: str = Depends(require_read_roles),
) -> APIResponse[SimulationOperationView]:
    service, _, _, _ = _service(request)
    _simulation_header(response)
    item = await service.repository.get_operation(operation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Simulation operation not found.")
    return APIResponse(data=item, meta=_meta(request))


@router.post("/operations", response_model=APIResponse[SimulationOperationView])
async def create_operation(
    payload: SimulationOperationRequest,
    request: Request,
    response: Response,
    actor: str = Depends(require_write_roles),
) -> APIResponse[SimulationOperationView]:
    service, resources, loaded_returns, operational = _service(request)
    _simulation_header(response)
    item = await service.execute(payload)
    if payload.signalWorkflow and item.status.value == "CONFIRMED":
        try:
            event_type, signal_status = await signal_workflow(
                item,
                resources=resources,
                loaded_returns=loaded_returns,
                repository=operational,
                actor_id=actor,
            )
        except Exception:
            event_type, signal_status = None, "SIGNAL_FAILED"
        item = await service.repository.update_operation(
            item.id, {"workflowEventType": event_type, "workflowSignalStatus": signal_status}
        )
    return APIResponse(data=item, meta=_meta(request))


@router.post(
    "/operations/{operation_id}/advance", response_model=APIResponse[SimulationOperationView]
)
async def advance_operation(
    operation_id: str,
    payload: SimulationAdvanceRequest,
    request: Request,
    response: Response,
    actor: str = Depends(require_write_roles),
) -> APIResponse[SimulationOperationView]:
    service, resources, loaded_returns, operational = _service(request)
    _simulation_header(response)
    try:
        item = await service.advance(operation_id, payload)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Simulation operation not found.") from error
    if payload.signalWorkflow and item.status.value == "CONFIRMED":
        try:
            event_type, signal_status = await signal_workflow(
                item,
                resources=resources,
                loaded_returns=loaded_returns,
                repository=operational,
                actor_id=actor,
            )
        except Exception:
            event_type, signal_status = None, "SIGNAL_FAILED"
        item = await service.repository.update_operation(
            item.id, {"workflowEventType": event_type, "workflowSignalStatus": signal_status}
        )
    return APIResponse(data=item, meta=_meta(request))


@router.get("/ai-metrics", response_model=APIResponse[list[SimulationAIUsageMetric]])
async def ai_metrics(
    request: Request,
    response: Response,
    session_id: str | None = Query(default=None, alias="sessionId"),
    _actor: str = Depends(require_read_roles),
) -> APIResponse[list[SimulationAIUsageMetric]]:
    service, _, _, _ = _service(request)
    _simulation_header(response)
    return APIResponse(
        data=await service.repository.list_ai_metrics(session_id=session_id), meta=_meta(request)
    )


async def _record_initial_events(
    session_id: str,
    *,
    resources: RuntimeResources,
    loaded_returns: LoadedReturnConfiguration,
    operational: OperationalRepository,
    actor: str,
) -> None:
    if resources.temporal is None:
        return
    session = await operational.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    coordinator = ProductionWorkflowCoordinator(
        temporal=resources.temporal,
        repository=operational,
        configuration=loaded_returns.configuration,
        task_queue=resources.settings.return_workflow_task_queue,
    )
    await coordinator.ensure_started(session, actor_id=actor)
    for index, event_type in enumerate(
        (
            ProductionReturnEventType.DISCOVERY_CONFIRMED,
            ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
            ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
            ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ),
        start=1,
    ):
        await coordinator.record_event(
            session_id,
            event_id=f"SIM-BOOT-{index}-{session_id}",
            event_type=event_type,
            evidence_reference=f"SIMULATION:BOOT:{index}",
            actor_id=actor,
            # The simulator stands in for an external system responding, not for
            # the operator who pressed the button -- see PLATFORM_SERVICE_ROLES.
            actor_roles=PLATFORM_SERVICE_ROLES,
            business_payload={"sourceSystem": "DEPENDENCY_SIMULATOR"},
        )


@router.post("/e2e/{session_id}/run", response_model=APIResponse[SimulationE2EResult])
async def run_e2e(
    session_id: str,
    payload: SimulationE2ERequest,
    request: Request,
    response: Response,
    actor: str = Depends(require_write_roles),
) -> APIResponse[SimulationE2EResult]:
    service, resources, loaded_returns, operational = _service(request)
    _simulation_header(response)
    session = await operational.get_return(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Return session not found.")
    return_methods = {
        "BRANCH_PARCEL": "PREPAID_PARCEL",
        "OFFSITE_HEAVY": "OFFSITE_LTL",
        "BRANCH_LTL": "BRANCH_LTL",
        "OFFSITE_PARCEL": "OFFSITE_PARCEL",
        "DIRECT_VENDOR": "DIRECT_VENDOR",
        "NO_PHYSICAL_RETURN": "NO_PHYSICAL_RETURN",
    }
    return_method = return_methods[payload.scenario]
    handling_units = await operational.list_handling_units(session_id)
    if not handling_units:
        await operational.persist_return_intake_records(
            session_id=session_id,
            order_line_id=session.itemReferences[0],
            product_id=(
                session.productReferences[0] if session.productReferences else "PRODUCT-SIM-001"
            ),
            reason_code=session.reasonCode,
            requested_quantity=session.returnQuantity,
            approved_method=return_method,
            product_presence=(session.productPresence or "PRESENT_AT_BRANCH"),
            package_count=max(1, session.packageCount),
            pickup_assessment=session.pickupAssessment,
            attachment_ids=[],
            actor_id=actor,
        )
        handling_units = await operational.list_handling_units(session_id)
    handling_unit_id = str(handling_units[0]["handlingUnitId"])
    await _record_initial_events(
        session_id,
        resources=resources,
        loaded_returns=loaded_returns,
        operational=operational,
        actor=actor,
    )
    operations: list[SimulationOperationView] = []

    async def run(
        dependency: str, operation: str, suffix: str, data: dict[str, object]
    ) -> SimulationOperationView:
        item = await service.execute(
            SimulationOperationRequest(
                dependency=dependency,
                operation=operation,
                sessionId=session_id,
                idempotencyKey=f"{session_id}:{suffix}",
                payload=data,
                useAiNarrative=payload.useAiNarrative,
                signalWorkflow=True,
            )
        )
        event_type, signal_status = await signal_workflow(
            item,
            resources=resources,
            loaded_returns=loaded_returns,
            repository=operational,
            actor_id=actor,
        )
        item = await service.repository.update_operation(
            item.id, {"workflowEventType": event_type, "workflowSignalStatus": signal_status}
        )
        operations.append(item)
        return item

    async def record_workflow_event(
        event_type: ProductionReturnEventType,
        suffix: str,
        data: dict[str, object] | None = None,
    ) -> None:
        if resources.temporal is None:
            return
        coordinator = ProductionWorkflowCoordinator(
            temporal=resources.temporal,
            repository=operational,
            configuration=loaded_returns.configuration,
            task_queue=resources.settings.return_workflow_task_queue,
        )
        await coordinator.record_event(
            session_id,
            event_id=f"SIM-PATH-{suffix}-{session_id}",
            event_type=event_type,
            evidence_reference=f"SIMULATION:PATH:{payload.scenario}:{suffix}",
            actor_id=actor,
            # The simulator stands in for an external system responding, not for
            # the operator who pressed the button -- see PLATFORM_SERVICE_ROLES.
            actor_roles=PLATFORM_SERVICE_ROLES,
            business_payload={
                "sourceSystem": "DEPENDENCY_SIMULATOR",
                "scenario": payload.scenario,
                **(data or {}),
            },
        )

    rma = await run(
        "OMC",
        "CREATE_RMA",
        "omc-rma",
        {"returnMethod": return_method, "items": [{"quantity": 1}]},
    )
    await run(
        "OMC",
        "SET_RETURN_METHOD",
        "omc-return-method",
        {"returnMethod": return_method},
    )
    if payload.scenario in {"BRANCH_PARCEL", "OFFSITE_PARCEL"}:
        label = await run(
            "PARCEL", "CREATE_RETURN_LABEL", "parcel-label", {"handlingUnitId": handling_unit_id}
        )
        await run(
            "PARCEL",
            "ADVANCE_TRACKING",
            "parcel-ready",
            {
                "trackingNumber": label.externalReference,
                "targetStatus": "PACKAGE_READY",
                "handlingUnitId": handling_unit_id,
            },
        )
        await run(
            "PARCEL",
            "ADVANCE_TRACKING",
            "parcel-carrier-accepted",
            {
                "trackingNumber": label.externalReference,
                "targetStatus": "CARRIER_ACCEPTED",
                "handlingUnitId": handling_unit_id,
            },
        )
    elif payload.scenario in {"OFFSITE_HEAVY", "BRANCH_LTL"}:
        await run("FREIGHT", "REQUEST_QUOTES", "freight-quotes", {"weight": 386, "palletCount": 1})
        await run("FREIGHT", "APPROVE_QUOTE", "freight-approve", {})
        bol = await run("FREIGHT", "CREATE_BOL", "freight-bol", {})
        await run(
            "FREIGHT", "TENDER_SHIPMENT", "freight-tender", {"bolReference": bol.externalReference}
        )
        await run(
            "FREIGHT", "CONFIRM_BOOKING", "freight-book", {"bolReference": bol.externalReference}
        )
        await run(
            "FREIGHT",
            "CONFIRM_PICKUP",
            "freight-pickup",
            {"bolReference": bol.externalReference, "handlingUnitId": handling_unit_id},
        )
    elif payload.scenario == "DIRECT_VENDOR":
        await record_workflow_event(
            ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
            "direct-vendor-authorization",
            {"returnMethod": return_method},
        )
        await run(
            "OMC",
            "UPDATE_RETURN_STATUS",
            "direct-vendor-shipped",
            {"status": "DIRECT_VENDOR_SHIPPED"},
        )
        await run(
            "OMC",
            "UPDATE_RETURN_STATUS",
            "direct-vendor-received",
            {"status": "DIRECT_VENDOR_RECEIVED"},
        )
        await record_workflow_event(
            ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED,
            "direct-vendor-no-license-plate",
            {"authorization": "DIRECT_VENDOR"},
        )
        await record_workflow_event(
            ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED,
            "direct-vendor-no-warehouse",
            {"authorization": "DIRECT_VENDOR"},
        )
    else:
        await record_workflow_event(
            ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED,
            "no-physical-return-authorization",
            {"returnMethod": return_method, "authorization": "CUSTOMER_KEEP_OR_FIELD_SCRAP"},
        )
        await record_workflow_event(
            ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED,
            "no-physical-no-license-plate",
        )
        await record_workflow_event(
            ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED,
            "no-physical-no-warehouse",
        )
    if payload.scenario not in {"DIRECT_VENDOR", "NO_PHYSICAL_RETURN"}:
        await run(
            "LSI",
            "RECORD_RECEIPT",
            "lsi-receipt",
            {
                "rmaId": rma.externalReference,
                "cartItemId": rma.responsePayload.get("cartItemIds", ["CI-SIM-1"])[0],
                "handlingUnitId": handling_unit_id,
            },
        )
        await run(
            "LSI",
            "ASSIGN_LICENSE_PLATE",
            "lsi-license",
            {"handlingUnitId": handling_unit_id},
        )
    product_dependency = (
        "OMC" if payload.scenario in {"DIRECT_VENDOR", "NO_PHYSICAL_RETURN"} else "LSI"
    )
    product_resolution = "CUSTOMER_KEEP" if payload.scenario == "NO_PHYSICAL_RETURN" else "RTV"
    await run(
        product_dependency,
        "SET_PRODUCT_RESOLUTION",
        "product-resolution",
        {"productResolution": product_resolution},
    )
    vendor_recovery = payload.includeVendorRecovery and payload.scenario != "NO_PHYSICAL_RETURN"
    vendor_dependency = "OMC" if payload.scenario == "DIRECT_VENDOR" else "LSI"
    if vendor_recovery:
        # Record the RTV vendor-recovery requirement before all customer-facing
        # completion conditions become true, so the durable state cannot close
        # before downstream RGA and vendor-credit evidence arrives.
        await run(vendor_dependency, "CREATE_RGA", "vendor-rga", {})
    await run(
        "OMC", "SET_CUSTOMER_RESOLUTION", "customer-refund", {"customerResolution": "REFUNDED"}
    )
    if payload.scenario not in {"DIRECT_VENDOR", "NO_PHYSICAL_RETURN"}:
        await run("LSI", "COMPLETE_WAREHOUSE_PROCESSING", "warehouse-complete", {})
    if vendor_recovery:
        await run(vendor_dependency, "RECORD_VENDOR_CREDIT", "vendor-credit", {})
    workflow_stage = None
    case_fully_closed = None
    if resources.temporal is not None:
        coordinator = ProductionWorkflowCoordinator(
            temporal=resources.temporal,
            repository=operational,
            configuration=loaded_returns.configuration,
            task_queue=resources.settings.return_workflow_task_queue,
        )
        state = await coordinator.query_state(session_id)
        workflow_stage = state.stage.value
        case_fully_closed = state.case_fully_closed
    return APIResponse(
        data=SimulationE2EResult(
            sessionId=session_id,
            scenario=payload.scenario,
            operationIds=[item.id for item in operations],
            workflowStage=workflow_stage,
            caseFullyClosed=case_fully_closed,
        ),
        meta=_meta(request),
    )


@router.post("/reset", response_model=APIResponse[dict[str, bool]])
async def reset(
    payload: SimulationResetRequest,
    request: Request,
    response: Response,
    _actor: str = Depends(require_write_roles),
) -> APIResponse[dict[str, bool]]:
    service, _, _, _ = _service(request)
    _simulation_header(response)
    await service.repository.reset(payload.sessionId)
    return APIResponse(data={"reset": True}, meta=_meta(request))
