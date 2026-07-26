"""Translate confirmed simulator results into production Temporal events."""

from __future__ import annotations

from typing import Any

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.dependency_simulation.models import SimulationOperationView
from return_platform.operations.production_workflow import ProductionWorkflowCoordinator
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

_EVENT_MAP: dict[tuple[str, str, str | None], ProductionReturnEventType] = {
    ("OMC", "CREATE_RMA", None): ProductionReturnEventType.OMC_RETURN_CREATED,
    ("OMC", "CREATE_LEGACY_RETURN", None): ProductionReturnEventType.OMC_RETURN_CREATED,
    (
        "OMC",
        "SET_CUSTOMER_RESOLUTION",
        None,
    ): ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
    (
        "OMC",
        "SET_PRODUCT_RESOLUTION",
        None,
    ): ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
    (
        "OMC",
        "UPDATE_RETURN_STATUS",
        "DIRECT_VENDOR_SHIPPED",
    ): ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
    (
        "OMC",
        "UPDATE_RETURN_STATUS",
        "DIRECT_VENDOR_RECEIVED",
    ): ProductionReturnEventType.RECEIPT_CONFIRMED,
    ("OMC", "CREATE_RGA", None): ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED,
    ("OMC", "RECORD_VENDOR_CREDIT", None): ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED,
    ("PARCEL", "CREATE_RETURN_LABEL", None): ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
    (
        "PARCEL",
        "ADVANCE_TRACKING",
        "CARRIER_ACCEPTED",
    ): ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
    ("FREIGHT", "CREATE_BOL", None): ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
    ("FREIGHT", "TENDER_SHIPMENT", None): ProductionReturnEventType.BOL_TENDERED,
    ("FREIGHT", "CONFIRM_BOOKING", None): ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
    ("FREIGHT", "CONFIRM_PICKUP", None): ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
    ("LSI", "RECORD_RECEIPT", None): ProductionReturnEventType.RECEIPT_CONFIRMED,
    ("LSI", "ASSIGN_LICENSE_PLATE", None): ProductionReturnEventType.LICENSE_PLATE_ASSIGNED,
    (
        "LSI",
        "SET_PRODUCT_RESOLUTION",
        None,
    ): ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
    (
        "LSI",
        "COMPLETE_WAREHOUSE_PROCESSING",
        None,
    ): ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,
    ("LSI", "CREATE_RGA", None): ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED,
    ("LSI", "RECORD_VENDOR_CREDIT", None): ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED,
}


def event_for(operation: SimulationOperationView) -> ProductionReturnEventType | None:
    return _EVENT_MAP.get(
        (operation.dependency.value, operation.operation, operation.simulatedState)
    ) or _EVENT_MAP.get((operation.dependency.value, operation.operation, None))


async def signal_workflow(
    operation: SimulationOperationView,
    *,
    resources: RuntimeResources,
    loaded_returns: LoadedReturnConfiguration,
    repository: OperationalRepository,
    actor_id: str,
) -> tuple[str | None, str]:
    event_type = event_for(operation)
    if event_type is None:
        return None, "NOT_APPLICABLE"
    if resources.temporal is None:
        return event_type.value, "TEMPORAL_UNAVAILABLE"
    session = await repository.get_return(operation.sessionId)
    if session is None:
        return event_type.value, "RETURN_SESSION_NOT_FOUND"
    coordinator = ProductionWorkflowCoordinator(
        temporal=resources.temporal,
        repository=repository,
        configuration=loaded_returns.configuration,
        task_queue=resources.settings.return_workflow_task_queue,
    )
    await coordinator.ensure_started(session, actor_id=actor_id)
    payload: dict[str, Any] = dict(operation.responsePayload)
    payload["sourceSystem"] = f"{operation.dependency.value}_SIMULATOR"
    payload["sourceEventId"] = operation.id
    if "rgaId" in payload:
        payload["omcRgaId"] = payload["rgaId"]
        payload["omcRgaNumber"] = payload["rgaId"]
    if "creditMemoId" in payload:
        payload["creditMemoIds"] = [payload["creditMemoId"]]
    await coordinator.record_event(
        operation.sessionId,
        event_id=operation.id,
        event_type=event_type,
        evidence_reference=f"SIMULATION:{operation.id}",
        actor_id=actor_id,
        business_payload=payload,
    )
    return event_type.value, "SIGNALLED"
