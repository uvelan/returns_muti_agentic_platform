from return_platform.workflows.production_return_state import (
    ProductionReturnEvent,
    ProductionReturnEventType,
    ProductionReturnStage,
    ProductionReturnWorkflowState,
    apply_production_return_event,
)


def initial_state() -> ProductionReturnWorkflowState:
    return ProductionReturnWorkflowState(
        session_id="session",
        correlation_id="correlation",
        workflow_version="2.0",
        assumption_set_version="FERGUSON-RETURN-ASSUMPTIONS-1.0",
        stage=ProductionReturnStage.INTAKE,
        applied_event_ids=(),
    )


def apply(
    state: ProductionReturnWorkflowState,
    event_type: ProductionReturnEventType,
) -> ProductionReturnWorkflowState:
    return apply_production_return_event(
        state,
        ProductionReturnEvent(
            event_id=f"event-{len(state.applied_event_ids)}-{event_type.value}",
            event_type=event_type,
            evidence_reference=f"evidence:{event_type.value}",
        ),
    )


def confirmed_return() -> ProductionReturnWorkflowState:
    state = initial_state()
    for event_type in (
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
    ):
        state = apply(state, event_type)
    return state


def test_tender_booking_and_pickup_are_separate() -> None:
    state = confirmed_return()
    state = apply(state, ProductionReturnEventType.BOL_TENDERED)
    assert state.bol_tendered is True
    assert state.carrier_booking_confirmed is False
    assert state.physical_return_complete is False

    try:
        apply(state, ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED)
    except ValueError as error:
        assert "booking" in str(error).lower()
    else:
        raise AssertionError("Pickup was accepted before carrier booking")

    state = apply(state, ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED)
    state = apply(state, ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED)
    assert state.physical_return_complete is True
    assert state.stage is ProductionReturnStage.RECEIPT


def test_rga_vendor_recovery_does_not_define_customer_resolution() -> None:
    state = confirmed_return()
    state = apply(state, ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED)
    state = apply(state, ProductionReturnEventType.RECEIPT_CONFIRMED)
    state = apply(state, ProductionReturnEventType.LICENSE_PLATE_ASSIGNED)
    state = apply(state, ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED)
    state = apply(state, ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED)
    state = apply(state, ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED)
    assert state.customer_resolution_complete is True
    assert state.vendor_recovery_required is False
    assert state.case_fully_closed is True


def test_vendor_recovery_is_required_only_after_product_disposition() -> None:
    state = confirmed_return()
    try:
        apply(state, ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED)
    except ValueError as error:
        assert "product disposition" in str(error).lower()
    else:
        raise AssertionError("Vendor recovery was accepted too early")


def test_event_application_is_idempotent() -> None:
    state = initial_state()
    event = ProductionReturnEvent(
        event_id="same-event",
        event_type=ProductionReturnEventType.DISCOVERY_CONFIRMED,
        evidence_reference="discovery-lock",
    )
    once = apply_production_return_event(state, event)
    twice = apply_production_return_event(once, event)
    assert twice == once
    assert twice.applied_event_ids == ("same-event",)


def test_no_physical_return_can_close_without_receipt_or_license_plate() -> None:
    state = confirmed_return()
    state = apply(state, ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED)
    state = apply(state, ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED)
    state = apply(state, ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED)
    assert state.physical_return_required is False
    assert state.receipt_required is False
    assert state.license_plate_required is False
    assert state.warehouse_processing_required is False
    assert state.case_fully_closed is True


def test_direct_vendor_path_can_skip_lsi_license_plate_and_warehouse() -> None:
    state = confirmed_return()
    state = apply(state, ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED)
    state = apply(state, ProductionReturnEventType.RECEIPT_CONFIRMED)
    state = apply(state, ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED)
    state = apply(state, ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED)
    state = apply(state, ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED)
    state = apply(state, ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED)
    assert state.license_plate_required is False
    assert state.warehouse_processing_required is False
    assert state.case_fully_closed is True
