from __future__ import annotations

import ast
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from return_platform.data_platform.schema_registry import load_schema_registry
from return_platform.operations.seed_manifest import (
    SEED_SCENARIOS,
    materialize_domain_seed,
    scenario_counts,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _frozenset_assignment(name: str) -> frozenset[str]:
    path = BACKEND_ROOT / "src" / "return_platform" / "data_platform" / "ai_studio.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.func, ast.Name)
        assert node.value.func.id == "frozenset"
        assert node.value.args
        return frozenset(str(item) for item in ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"Missing frozenset assignment: {name}")


def test_seed_matrix_has_required_positive_negative_and_review_coverage() -> None:
    assert scenario_counts() == {"positive": 5, "negative": 3, "reviewRequired": 2, "total": 10}
    assert len({str(item["id"]) for item in SEED_SCENARIOS}) == len(SEED_SCENARIOS)


def test_domain_seed_keys_are_coherent_across_hld_source_collections() -> None:
    records = materialize_domain_seed(
        "contract-test",
        datetime(2026, 7, 24, tzinfo=UTC),
        "contract-test-evidence-key",
    )
    assert set(records) == {
        "salesInv",
        "customerOutboundCDM",
        "shipmentInfo",
        "lkpSearchProduct",
    }

    customers = {str(item["customerId"]) for item in records["customerOutboundCDM"]}
    products = {str(item["productId"]) for item in records["lkpSearchProduct"]}
    assert len(records["salesInv"]) == 1_000
    assert len(records["customerOutboundCDM"]) == 1_000
    assert len(records["lkpSearchProduct"]) == 1_000
    sample_indexes = (0, 9, 999)
    for index in sample_indexes:
        order = records["salesInv"][index]
        shipment = records["shipmentInfo"][index]
        header = order["salesHdrEventData"]
        customer = order["salesHdr"]["salesHdrData"]
        line = order["salesLines"][0]["lineData"]
        order_reference = str(header["orderId"])
        assert str(customer["custId"]) in customers
        assert str(line["productId"]) in products
        assert str(line["orderLineId"]).startswith(order_reference)
        assert str(shipment["shipmentInfoEventData"]["trilOrdNum"]) == order_reference


def test_schema_registry_models_every_required_physical_store_and_graph_type() -> None:
    registry = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")
    engines = Counter(asset.engine for asset in registry.assets)
    assert engines["MONGODB"] >= 33
    assert engines["SQLSERVER"] >= 8

    physical = {(asset.engine, asset.namespace, asset.name) for asset in registry.assets}
    for collection in (
        "salesInv",
        "customerOutboundCDM",
        "shipmentInfo",
        "lkpSearchProduct",
        "customers",
        "products",
        "orders",
        "operational_returns",
        "operational_events",
        "support_cases",
        "ai_gateway_traces",
        "ai_gateway_settings",
        "ai_gateway_rate_limits",
        "worker_heartbeats",
        "seed_metadata",
        "return_sessions",
        "return_session_audit_events",
        "return_session_outbox_events",
        "return_session_agent_decisions",
        "workspaces",
        "sandbox_records",
        "jobs",
        "job_commands",
        "job_artifacts",
        "scenarios",
        "scenario_records",
        "audit",
        "graph_evidence_runs",
        "associate_conversations",
        "discovery_locks",
        "ai_studio_proposals",
        "graph_sync_runs",
        "feedback_learning_records",
        "associate_messages",
        "discovery_snapshots",
        "return_request_snapshots",
        "operational_return_items",
        "handling_units",
        "pickup_sites",
        "pickup_requests",
        "branch_staging_records",
        "document_artifacts",
        "shipping_instructions",
        "shipment_events",
        "omc_command_records",
        "agent_decisions",
        "vendor_return_links",
        "integration_outbox",
        "support_work_items",
        "support_messages",
        "schema_migrations",
        "return_configuration_snapshots",
    ):
        assert ("MONGODB", None, collection) in physical

    for namespace, table in (
        ("dbo", "return_requests"),
        ("dbo", "return_items"),
        ("dbo", "return_fulfillment"),
        ("dbo", "return_tracking"),
        ("integration", "return_support_ticket"),
        ("platform", "bay_configuration"),
        ("platform", "bay_assignment"),
        ("platform", "bay_reservation"),
        ("platform", "return_policy_version"),
    ):
        assert ("SQLSERVER", namespace, table) in physical

    labels = {node.label for node in registry.graph.nodes}
    assert {
        "Customer",
        "SalesOrder",
        "OrderLine",
        "Product",
        "Warehouse",
        "Shipment",
        "Return",
        "ReturnItem",
        "ReturnTracking",
        "Bay",
        "BayAssignment",
        "SupportTicket",
    } <= labels

    relationships = {relationship.type for relationship in registry.graph.relationships}
    assert {
        "PLACED_ORDER",
        "HAS_ORDER_LINE",
        "REFERENCES_PRODUCT",
        "HAS_RETURN",
        "HAS_RETURN_ITEM",
        "RETURN_ITEM_FOR_LINE",
        "ASSIGNED_TO_BAY",
        "TRACKED_BY_SUPPORT_TICKET",
    } <= relationships


def test_sql_registry_tables_are_created_by_versioned_migrations() -> None:
    migrations_dir = BACKEND_ROOT / "src" / "return_platform" / "configuration" / "sql_migrations"
    migration_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(migrations_dir.glob("*.sql"))
    ).lower()
    registry = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")
    for asset in registry.assets:
        if asset.engine != "SQLSERVER":
            continue
        assert asset.namespace is not None
        assert f"{asset.namespace}.{asset.name}".lower() in migration_text


def test_ai_studio_registry_generators_are_explicitly_supported() -> None:
    supported_generators = _frozenset_assignment("SUPPORTED_GENERATORS")
    registry = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")
    declared = {field.generator for asset in registry.assets for field in asset.fields}
    assert None not in declared
    assert declared == supported_generators


def test_ai_studio_direct_write_allowlists_exclude_service_owned_state() -> None:
    direct_mongo_collections = _frozenset_assignment("DIRECT_MONGO_COLLECTIONS")
    direct_sql_assets = _frozenset_assignment("DIRECT_SQL_ASSETS")
    registry = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")
    source_or_sandbox_mongo = {
        asset.name
        for asset in registry.assets
        if asset.engine == "MONGODB"
        and asset.writable_in_sandbox
        and asset.name
        in {
            "salesInv",
            "customerOutboundCDM",
            "shipmentInfo",
            "lkpSearchProduct",
            "customers",
            "products",
            "orders",
            "workspaces",
            "sandbox_records",
            "scenarios",
            "scenario_records",
        }
    }
    assert direct_mongo_collections == source_or_sandbox_mongo

    direct_sql = {
        asset.asset_id
        for asset in registry.assets
        if asset.engine == "SQLSERVER" and asset.writable_in_sandbox
    }
    assert direct_sql_assets == direct_sql

    forbidden = {
        "operational_returns",
        "operational_events",
        "support_cases",
        "ai_gateway_traces",
        "return_sessions",
        "return_session_audit_events",
        "return_session_outbox_events",
        "return_session_agent_decisions",
        "associate_conversations",
        "discovery_locks",
        "graph_sync_runs",
        "feedback_learning_records",
    }
    assert direct_mongo_collections.isdisjoint(forbidden)
