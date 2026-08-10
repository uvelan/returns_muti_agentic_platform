"""Pure deterministic state machine for the production Ferguson return lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class ProductionReturnStage(StrEnum):
    INTAKE = "INTAKE"
    ORDER_DISCOVERY = "ORDER_DISCOVERY"
    ORDER_CONFIRMATION = "ORDER_CONFIRMATION"
    RETURN_DETAILS = "RETURN_DETAILS"
    SUPPORT_REQUEST = "SUPPORT_REQUEST"
    RETURN_CREATION = "RETURN_CREATION"
    PHYSICAL_RETURN_SETUP = "PHYSICAL_RETURN_SETUP"
    RETURN_SHIPMENT = "RETURN_SHIPMENT"
    RECEIPT = "RECEIPT"
    CUSTOMER_RESOLUTION = "CUSTOMER_RESOLUTION"
    PRODUCT_DISPOSITION = "PRODUCT_DISPOSITION"
    WAREHOUSE_PROCESSING = "WAREHOUSE_PROCESSING"
    CUSTOMER_RETURN_COMPLETE = "CUSTOMER_RETURN_COMPLETE"
    VENDOR_RECOVERY = "VENDOR_RECOVERY"
    FULLY_CLOSED = "FULLY_CLOSED"
    CANCELLED = "CANCELLED"


class ProductionReturnEventType(StrEnum):
    DISCOVERY_CONFIRMED = "DISCOVERY_CONFIRMED"
    RETURN_DETAILS_CONFIRMED = "RETURN_DETAILS_CONFIRMED"
    SUPPORT_REQUEST_CREATED = "SUPPORT_REQUEST_CREATED"
    SUPPORT_ACKNOWLEDGED = "SUPPORT_ACKNOWLEDGED"
    OMC_RETURN_CREATED = "OMC_RETURN_CREATED"
    SHIPPING_INSTRUCTIONS_ISSUED = "SHIPPING_INSTRUCTIONS_ISSUED"
    BOL_TENDERED = "BOL_TENDERED"
    CARRIER_BOOKING_CONFIRMED = "CARRIER_BOOKING_CONFIRMED"
    PHYSICAL_HANDOFF_CONFIRMED = "PHYSICAL_HANDOFF_CONFIRMED"
    PHYSICAL_RETURN_NOT_REQUIRED = "PHYSICAL_RETURN_NOT_REQUIRED"
    RECEIPT_CONFIRMED = "RECEIPT_CONFIRMED"
    LICENSE_PLATE_NOT_REQUIRED = "LICENSE_PLATE_NOT_REQUIRED"
    WAREHOUSE_PROCESSING_NOT_REQUIRED = "WAREHOUSE_PROCESSING_NOT_REQUIRED"
    LICENSE_PLATE_ASSIGNED = "LICENSE_PLATE_ASSIGNED"
    CUSTOMER_RESOLUTION_COMPLETED = "CUSTOMER_RESOLUTION_COMPLETED"
    PRODUCT_DISPOSITION_COMPLETED = "PRODUCT_DISPOSITION_COMPLETED"
    WAREHOUSE_PROCESSING_COMPLETED = "WAREHOUSE_PROCESSING_COMPLETED"
    VENDOR_RECOVERY_REQUIRED = "VENDOR_RECOVERY_REQUIRED"
    VENDOR_RECOVERY_COMPLETED = "VENDOR_RECOVERY_COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ProductionReturnWorkflowInput:
    session_id: str
    correlation_id: str
    workflow_version: str
    assumption_set_version: str


@dataclass(frozen=True, slots=True)
class ProductionReturnEvent:
    event_id: str
    event_type: ProductionReturnEventType
    evidence_reference: str


@dataclass(frozen=True, slots=True)
class ProductionReturnWorkflowState:
    session_id: str
    correlation_id: str
    workflow_version: str
    assumption_set_version: str
    stage: ProductionReturnStage
    applied_event_ids: tuple[str, ...]
    discovery_confirmed: bool = False
    return_details_confirmed: bool = False
    support_request_created: bool = False
    support_acknowledged: bool = False
    return_created: bool = False
    shipping_instructions_issued: bool = False
    bol_tendered: bool = False
    carrier_booking_confirmed: bool = False
    physical_return_complete: bool = False
    physical_return_required: bool = True
    receipt_confirmed: bool = False
    receipt_required: bool = True
    license_plate_assigned: bool = False
    license_plate_required: bool = True
    customer_resolution_complete: bool = False
    product_disposition_complete: bool = False
    warehouse_processing_complete: bool = False
    warehouse_processing_required: bool = True
    vendor_recovery_required: bool = False
    vendor_recovery_complete: bool = False
    case_fully_closed: bool = False
    cancelled: bool = False


def _advance_stage(state: ProductionReturnWorkflowState) -> ProductionReturnStage:
    if state.cancelled:
        return ProductionReturnStage.CANCELLED
    if state.case_fully_closed:
        return ProductionReturnStage.FULLY_CLOSED
    if state.vendor_recovery_required and not state.vendor_recovery_complete:
        return ProductionReturnStage.VENDOR_RECOVERY
    if (
        state.customer_resolution_complete
        and state.physical_return_complete
        and state.product_disposition_complete
        and (state.warehouse_processing_complete or not state.warehouse_processing_required)
    ):
        return ProductionReturnStage.CUSTOMER_RETURN_COMPLETE
    receipt_satisfied = state.receipt_confirmed or not state.receipt_required
    if receipt_satisfied and not state.product_disposition_complete:
        return ProductionReturnStage.PRODUCT_DISPOSITION
    if (
        receipt_satisfied
        and state.warehouse_processing_required
        and not state.warehouse_processing_complete
    ):
        return ProductionReturnStage.WAREHOUSE_PROCESSING
    if state.physical_return_complete and state.receipt_required and not state.receipt_confirmed:
        return ProductionReturnStage.RECEIPT
    if state.shipping_instructions_issued and not state.physical_return_complete:
        return ProductionReturnStage.RETURN_SHIPMENT
    if state.return_created and not state.shipping_instructions_issued:
        return ProductionReturnStage.PHYSICAL_RETURN_SETUP
    if state.support_acknowledged and not state.return_created:
        return ProductionReturnStage.RETURN_CREATION
    if state.support_request_created and not state.support_acknowledged:
        return ProductionReturnStage.SUPPORT_REQUEST
    if state.return_details_confirmed and not state.support_request_created:
        return ProductionReturnStage.RETURN_DETAILS
    if state.discovery_confirmed and not state.return_details_confirmed:
        return ProductionReturnStage.ORDER_CONFIRMATION
    return ProductionReturnStage.ORDER_DISCOVERY


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _already_recorded(
    state: ProductionReturnWorkflowState, event_type: ProductionReturnEventType
) -> bool:
    """Has this transition's effect already been recorded?

    Distinct from the `applied_event_ids` check above, which catches a *retry of
    the same event*. This catches a **different** event id claiming a transition
    that has already happened -- which is what two endpoints recording one
    real-world fact necessarily produce, because each derives its own event id.

    `_validate_transition` cannot catch it: it checks preconditions, and the
    preconditions for a transition remain satisfied after that transition
    occurs. `CARRIER_BOOKING_CONFIRMED` requires `bol_tendered`, which stays true
    forever, so a second one was accepted -- appending to `applied_event_ids`
    (carried in Temporal workflow state, so it grows without bound) and
    re-running `_project_business_event`, which writes a second `shipment_events`
    row because the two paths derive different `(sourceSystem, sourceEventId)`
    dedup keys.

    Each event type is keyed on the flag it owns. `PHYSICAL_HANDOFF_CONFIRMED` is
    the one exception: `physical_return_complete` is also set by
    `RECEIPT_CONFIRMED` and `PHYSICAL_RETURN_NOT_REQUIRED`. That is correct
    rather than approximate -- a handoff after receipt is out of order anyway,
    and a handoff after the physical return was waived is contradictory.
    """
    return {
        ProductionReturnEventType.DISCOVERY_CONFIRMED: state.discovery_confirmed,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED: state.return_details_confirmed,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED: state.support_request_created,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED: state.support_acknowledged,
        ProductionReturnEventType.OMC_RETURN_CREATED: state.return_created,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED: state.shipping_instructions_issued,
        ProductionReturnEventType.BOL_TENDERED: state.bol_tendered,
        ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED: state.carrier_booking_confirmed,
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED: state.physical_return_complete,
        ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED: not state.physical_return_required,
        ProductionReturnEventType.RECEIPT_CONFIRMED: state.receipt_confirmed,
        ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED: not state.license_plate_required,
        ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED: (
            not state.warehouse_processing_required
        ),
        ProductionReturnEventType.LICENSE_PLATE_ASSIGNED: state.license_plate_assigned,
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED: (
            state.customer_resolution_complete
        ),
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED: (
            state.product_disposition_complete
        ),
        ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED: (
            state.warehouse_processing_complete
        ),
        ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED: state.vendor_recovery_required,
        ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED: state.vendor_recovery_complete,
        # `CANCELLED` is handled by the `state.cancelled` early return, which
        # short-circuits every event once a return is cancelled.
        ProductionReturnEventType.CANCELLED: state.cancelled,
    }[event_type]


def _validate_transition(
    state: ProductionReturnWorkflowState, event_type: ProductionReturnEventType
) -> None:
    if event_type is ProductionReturnEventType.CANCELLED:
        return
    if event_type is ProductionReturnEventType.DISCOVERY_CONFIRMED:
        return
    if event_type is ProductionReturnEventType.RETURN_DETAILS_CONFIRMED:
        _require(state.discovery_confirmed, "Discovery must be confirmed first")
    elif event_type is ProductionReturnEventType.SUPPORT_REQUEST_CREATED:
        _require(state.return_details_confirmed, "Return details must be confirmed first")
    elif event_type is ProductionReturnEventType.SUPPORT_ACKNOWLEDGED:
        _require(state.support_request_created, "Support request must exist first")
    elif event_type is ProductionReturnEventType.OMC_RETURN_CREATED:
        _require(state.support_acknowledged, "Support must acknowledge the request first")
    elif event_type is ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED:
        _require(state.return_created, "An authoritative return must exist first")
    elif event_type is ProductionReturnEventType.BOL_TENDERED:
        _require(state.shipping_instructions_issued, "Shipping instructions must exist first")
    elif event_type is ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED:
        _require(state.bol_tendered, "BOL must be tendered before carrier booking")
    elif event_type is ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED:
        _require(
            state.shipping_instructions_issued,
            "Shipping or disposition instructions must exist first",
        )
        if state.bol_tendered:
            _require(
                state.carrier_booking_confirmed,
                "Freight booking must be confirmed before pickup",
            )
    elif event_type is ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED:
        _require(state.return_created, "An authoritative return must exist first")
    elif event_type is ProductionReturnEventType.RECEIPT_CONFIRMED:
        _require(state.physical_return_complete, "Physical handoff must be confirmed first")
    elif event_type is ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED:
        _require(
            state.receipt_confirmed or not state.receipt_required,
            "Receipt or an approved no-receipt path is required first",
        )
    elif event_type is ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED:
        _require(state.return_created, "An authoritative return must exist first")
    elif event_type is ProductionReturnEventType.LICENSE_PLATE_ASSIGNED:
        _require(state.receipt_confirmed, "Receipt must be confirmed first")
    elif event_type is ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED:
        _require(state.return_created, "An authoritative return must exist first")
    elif event_type is ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED:
        _require(
            state.license_plate_assigned or not state.license_plate_required,
            "License plate or an approved no-license-plate path is required first",
        )
    elif event_type is ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED:
        _require(state.receipt_confirmed, "Receipt must be confirmed first")
    elif event_type is ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED:
        _require(
            state.product_disposition_complete,
            "Product disposition must be completed first",
        )
    elif event_type is ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED:
        _require(state.vendor_recovery_required, "Vendor recovery is not required")


def apply_production_return_event(
    state: ProductionReturnWorkflowState,
    event: ProductionReturnEvent,
) -> ProductionReturnWorkflowState:
    """Apply one idempotent, deterministic, business-data-free workflow event."""
    if event.event_id in state.applied_event_ids:
        return state
    if state.cancelled or state.case_fully_closed:
        return state
    if _already_recorded(state, event.event_type):
        raise ValueError(
            f"{event.event_type.value} is already recorded for this return. "
            "Re-sending the original event id is a no-op; a new event id for a "
            "transition that has already happened is either a duplicate report "
            "or a second real occurrence, and both need a decision rather than "
            "a silent second application."
        )
    _validate_transition(state, event.event_type)
    updates: dict[str, Any] = {
        "applied_event_ids": (*state.applied_event_ids, event.event_id),
    }
    event_type = event.event_type
    if event_type is ProductionReturnEventType.DISCOVERY_CONFIRMED:
        updates["discovery_confirmed"] = True
    elif event_type is ProductionReturnEventType.RETURN_DETAILS_CONFIRMED:
        updates["return_details_confirmed"] = True
    elif event_type is ProductionReturnEventType.SUPPORT_REQUEST_CREATED:
        updates["support_request_created"] = True
    elif event_type is ProductionReturnEventType.SUPPORT_ACKNOWLEDGED:
        updates["support_acknowledged"] = True
    elif event_type is ProductionReturnEventType.OMC_RETURN_CREATED:
        updates["return_created"] = True
    elif event_type is ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED:
        updates["shipping_instructions_issued"] = True
    elif event_type is ProductionReturnEventType.BOL_TENDERED:
        updates["bol_tendered"] = True
    elif event_type is ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED:
        updates["carrier_booking_confirmed"] = True
    elif event_type is ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED:
        updates["physical_return_complete"] = True
    elif event_type is ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED:
        updates["physical_return_required"] = False
        updates["physical_return_complete"] = True
        updates["receipt_required"] = False
        updates["license_plate_required"] = False
        updates["warehouse_processing_required"] = False
    elif event_type is ProductionReturnEventType.RECEIPT_CONFIRMED:
        updates["receipt_confirmed"] = True
        updates["physical_return_complete"] = True
    elif event_type is ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED:
        updates["license_plate_required"] = False
    elif event_type is ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED:
        updates["warehouse_processing_required"] = False
    elif event_type is ProductionReturnEventType.LICENSE_PLATE_ASSIGNED:
        updates["license_plate_assigned"] = True
    elif event_type is ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED:
        updates["customer_resolution_complete"] = True
    elif event_type is ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED:
        updates["product_disposition_complete"] = True
    elif event_type is ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED:
        updates["warehouse_processing_complete"] = True
    elif event_type is ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED:
        updates["vendor_recovery_required"] = True
    elif event_type is ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED:
        updates["vendor_recovery_complete"] = True
    elif event_type is ProductionReturnEventType.CANCELLED:
        updates["cancelled"] = True
    next_state = replace(state, **updates)
    fully_closed = (
        next_state.customer_resolution_complete
        and next_state.physical_return_complete
        and next_state.product_disposition_complete
        and (
            next_state.warehouse_processing_complete or not next_state.warehouse_processing_required
        )
        and (not next_state.vendor_recovery_required or next_state.vendor_recovery_complete)
    )
    next_state = replace(next_state, case_fully_closed=fully_closed)
    return replace(next_state, stage=_advance_stage(next_state))
