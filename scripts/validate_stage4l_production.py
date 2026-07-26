#!/usr/bin/env python3
"""Dependency-light validation for Stage 4L production return implementation."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

sys.path.insert(0, str(BACKEND_ROOT / "src"))

from return_platform.agents.registry import ReturnAgentRegistry
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.data_platform.schema_registry import load_schema_registry
from return_platform.workflows.production_return_state import (
    ProductionReturnEvent,
    ProductionReturnEventType,
    ProductionReturnStage,
    ProductionReturnWorkflowState,
    apply_production_return_event,
)


def _initial_state() -> ProductionReturnWorkflowState:
    return ProductionReturnWorkflowState(
        session_id="stage4l-validation",
        correlation_id="stage4l-correlation",
        workflow_version="2.0",
        assumption_set_version="FERGUSON-RETURN-ASSUMPTIONS-1.0",
        stage=ProductionReturnStage.INTAKE,
        applied_event_ids=(),
    )


def _apply(
    state: ProductionReturnWorkflowState,
    *events: ProductionReturnEventType,
) -> ProductionReturnWorkflowState:
    for index, event_type in enumerate(events, start=len(state.applied_event_ids) + 1):
        state = apply_production_return_event(
            state,
            ProductionReturnEvent(
                event_id=f"event-{index}-{event_type.value}",
                event_type=event_type,
                evidence_reference=f"EVIDENCE:{event_type.value}",
            ),
        )
    return state


def validate() -> dict[str, object]:
    checks: list[str] = []

    loaded = load_return_configuration(BACKEND_ROOT / "config" / "returns" / "production.yaml")
    configuration = loaded.configuration
    expected_agents = {
        "order_discovery",
        "return_workflow",
        "return_fulfillment",
        "bay_assignment",
        "feedback_learning",
    }
    assert set(configuration.agents) == expected_agents
    registry_instance = ReturnAgentRegistry.build(configuration)
    assert all(
        getattr(registry_instance, name) is not None
        for name in (
            "order_discovery",
            "return_workflow",
            "return_fulfillment",
            "bay_assignment",
            "feedback_learning",
        )
    )
    assert configuration.discovery.auto_confirmation_allowed is False
    assert configuration.omc.rga_is_customer_return is False
    assert configuration.omc.tendered_is_pickup is False
    assert configuration.extensions.ocr_processing is False
    assert configuration.extensions.image_processing is False
    assert all(
        not topic.ai_may_fabricate_success
        for topic in (
            configuration.integrations.omc_return_create,
            configuration.integrations.external_support_mirror,
            configuration.integrations.carrier_booking,
            configuration.integrations.customer_notification,
        )
    )
    checks.append("CONFIGURATION_AND_FIVE_AGENTS")

    branch = _apply(
        _initial_state(),
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
        ProductionReturnEventType.RECEIPT_CONFIRMED,
        ProductionReturnEventType.LICENSE_PLATE_ASSIGNED,
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
        ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,
    )
    assert branch.case_fully_closed is True
    assert branch.stage is ProductionReturnStage.FULLY_CLOSED
    checks.append("BRANCH_PARCEL_LIFECYCLE")

    freight = _apply(
        _initial_state(),
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
        ProductionReturnEventType.BOL_TENDERED,
    )
    try:
        _apply(freight, ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED)
    except ValueError as error:
        assert "booking" in str(error).lower()
    else:
        raise AssertionError("Tendered BOL incorrectly allowed physical pickup")
    freight = _apply(
        freight,
        ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
    )
    assert freight.bol_tendered and freight.carrier_booking_confirmed
    assert freight.physical_return_complete
    checks.append("BOL_TENDER_BOOKING_PICKUP_SEPARATION")

    no_physical = _apply(
        _initial_state(),
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED,
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
    )
    assert no_physical.case_fully_closed
    assert not no_physical.physical_return_required
    checks.append("NO_PHYSICAL_RETURN_LIFECYCLE")

    direct_vendor = _apply(
        _initial_state(),
        ProductionReturnEventType.DISCOVERY_CONFIRMED,
        ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
        ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
        ProductionReturnEventType.OMC_RETURN_CREATED,
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
        ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
        ProductionReturnEventType.RECEIPT_CONFIRMED,
        ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED,
        ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED,
        ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
        ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED,
    )
    assert direct_vendor.case_fully_closed
    checks.append("DIRECT_VENDOR_WITHOUT_LSI_LICENSE_PLATE")

    registry = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")
    physical_assets = {asset.asset_id for asset in registry.assets}
    required_assets = {
        "platform.mongodb.associate_messages",
        "platform.mongodb.discovery_snapshots",
        "platform.mongodb.return_request_snapshots",
        "platform.mongodb.operational_return_items",
        "platform.mongodb.handling_units",
        "platform.mongodb.pickup_sites",
        "platform.mongodb.pickup_requests",
        "platform.mongodb.branch_staging_records",
        "platform.mongodb.document_artifacts",
        "platform.mongodb.shipping_instructions",
        "platform.mongodb.shipment_events",
        "platform.mongodb.omc_command_records",
        "platform.mongodb.agent_decisions",
        "platform.mongodb.vendor_return_links",
        "platform.mongodb.integration_outbox",
        "platform.mongodb.support_work_items",
        "platform.mongodb.support_messages",
        "platform.mongodb.return_configuration_snapshots",
        "platform.sql.bay_reservation",
        "platform.sql.return_policy_version",
    }
    assert required_assets <= physical_assets
    checks.append("SCHEMA_REGISTRY_COVERAGE")

    main_text = (BACKEND_ROOT / "src" / "return_platform" / "main.py").read_text()
    for route_name in (
        "return_agents_router",
        "return_support_router",
        "production_workflow_router",
        "physical_operations_router",
        "return_artifacts_router",
        "warehouse_placement_router",
        "integration_outbox_router",
    ):
        assert f"include_router({route_name})" in main_text
    checks.append("BACKEND_ROUTE_REGISTRATION")

    repository_text = (
        BACKEND_ROOT / "src" / "return_platform" / "operations" / "repository.py"
    ).read_text()
    models_text = (
        BACKEND_ROOT / "src" / "return_platform" / "operations" / "models.py"
    ).read_text()
    assert 'default="PRODUCTION_V2"' in models_text
    assert '{"workflowMode": "LEGACY_V1"}' in repository_text
    checks.append("LEGACY_ORCHESTRATOR_ISOLATION")

    frontend_routes = (REPOSITORY_ROOT / "frontend" / "src" / "routes.ts").read_text()
    for path in (
        "/operations/return-agents",
        "/return-support/workbench",
        "/logistics/returns",
        "/warehouse/returns",
        "/tracking/returns",
        "/system/integration-outbox",
        "/operations/returns/:sessionId",
    ):
        assert path in frontend_routes
    checks.append("PRODUCTION_SCREEN_REGISTRATION")

    support_api = (
        BACKEND_ROOT / "src" / "return_platform" / "api" / "return_support.py"
    ).read_text()
    assert "require_associate_roles" in support_api
    assert "require_return_collaboration_roles" in support_api
    assert "require_support_roles" in support_api
    checks.append("ROLE_SEPARATION")

    outbox_worker = (
        BACKEND_ROOT / "src" / "return_platform" / "workers" / "integration_outbox.py"
    ).read_text()
    for topic in (
        "omc.return.create",
        "carrier.return.book",
        "customer.return.notify",
    ):
        assert topic in outbox_worker
    checks.append("EXTERNAL_INTEGRATION_BOUNDARIES")

    migration_text = "\n".join(
        path.read_text()
        for path in sorted((REPOSITORY_ROOT / "infra" / "sqlserver" / "init").glob("*.sql"))
    )
    assert "platform.bay_reservation" in migration_text
    assert "platform.return_policy_version" in migration_text
    assert "omc.returns" not in migration_text.lower()
    checks.append("FORWARD_ONLY_PLATFORM_SQL_MIGRATIONS")

    return {
        "status": "SOURCE_VALIDATED",
        "stage": "4L",
        "checks": checks,
        "checkCount": len(checks),
        "configurationSha256": loaded.sha256,
        "assumptionSetVersion": configuration.assumption_set_version,
        "agents": sorted(expected_agents),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2, sort_keys=True))
