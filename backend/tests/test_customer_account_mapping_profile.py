"""Deterministic tests for the first Customer CDM mapping profile."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import ValidationError

from return_platform.data_platform.mapping import (
    CanonicalEntityType,
    DataPlatformMappingBundle,
    PhysicalPathScope,
    RelationshipDirection,
)

_CONFIG_DIRECTORY = Path(__file__).parents[1] / "config" / "data_platform"
_EXPECTED_FILES = (
    "sources.yaml",
    "canonical_mappings.yaml",
    "graph_projection.yaml",
    "sync_pipelines.yaml",
)
_EXPECTED_CATALOG_ASSET_ID = "source.mongodb.customer_outbound_cdm"
_EXPECTED_CANONICAL_MAPPING_COUNT = 2
_EXPECTED_GRAPH_NODE_COUNT = 2


def _load_yaml_mapping(filename: str) -> dict[str, Any]:
    """Load one trusted test fixture as a root YAML mapping."""
    path = _CONFIG_DIRECTORY / filename
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, Mapping), filename
    return {str(key): value for key, value in document.items()}


def _load_bundle_payload() -> dict[str, Any]:
    """Merge the four profile files into the contract's bundle shape."""
    documents = {filename: _load_yaml_mapping(filename) for filename in _EXPECTED_FILES}
    versions = {cast("str", document["schema_version"]) for document in documents.values()}
    assert len(versions) == 1

    return {
        "schema_version": versions.pop(),
        "source_assets": documents["sources.yaml"]["source_assets"],
        "canonical_mappings": documents["canonical_mappings.yaml"]["canonical_mappings"],
        "graph_nodes": documents["graph_projection.yaml"]["graph_nodes"],
        "graph_relationships": documents["graph_projection.yaml"]["graph_relationships"],
        "sync_pipelines": documents["sync_pipelines.yaml"]["sync_pipelines"],
    }


def _load_bundle() -> DataPlatformMappingBundle:
    """Validate and return the complete Customer foundation bundle."""
    return DataPlatformMappingBundle.model_validate(_load_bundle_payload())


def test_profile_files_exist_and_have_only_expected_roots() -> None:
    """Verify test profile files exist and have only expected roots."""
    expected_roots = {
        "sources.yaml": {"schema_version", "source_assets"},
        "canonical_mappings.yaml": {"schema_version", "canonical_mappings"},
        "graph_projection.yaml": {
            "schema_version",
            "graph_nodes",
            "graph_relationships",
        },
        "sync_pipelines.yaml": {"schema_version", "sync_pipelines"},
    }

    for filename, roots in expected_roots.items():
        assert set(_load_yaml_mapping(filename)) == roots


def test_customer_foundation_profile_validates_as_one_bundle() -> None:
    """Verify test customer foundation profile validates as one bundle."""
    bundle = _load_bundle()

    assert bundle.schema_version == "1.0"
    assert len(bundle.source_assets) == 1
    assert len(bundle.canonical_mappings) == _EXPECTED_CANONICAL_MAPPING_COUNT
    assert len(bundle.graph_nodes) == _EXPECTED_GRAPH_NODE_COUNT
    assert len(bundle.graph_relationships) == 1
    assert len(bundle.sync_pipelines) == 1


def test_source_profile_uses_confirmed_customer_cdm_binding() -> None:
    """Verify test source profile uses confirmed customer cdm binding."""
    source = _load_bundle().source_assets[0]

    assert source.source_id == "source.customer_cdm.v1"
    assert source.catalog_asset_id == _EXPECTED_CATALOG_ASSET_ID
    assert source.source_system == "CUSTOMER_CDM"
    assert source.required_for_sync is True


def test_customer_mapping_uses_only_confirmed_party_identity_path() -> None:
    """Verify test customer mapping uses only confirmed party identity path."""
    bundle = _load_bundle()
    customer = next(
        mapping
        for mapping in bundle.canonical_mappings
        if mapping.entity_type is CanonicalEntityType.CUSTOMER
    )

    assert customer.record_path is None
    assert customer.identity.key_field == "customer_key"
    assert customer.identity.component_fields == ("party_id",)
    assert {path for field in customer.fields for path in field.source_paths} == {"partyId"}


def test_customer_account_mapping_uses_explicit_nested_path_scopes() -> None:
    """Verify test customer account mapping uses explicit nested path scopes."""
    bundle = _load_bundle()
    account = next(
        mapping
        for mapping in bundle.canonical_mappings
        if mapping.entity_type is CanonicalEntityType.CUSTOMER_ACCOUNT
    )
    fields = {field.canonical_field: field for field in account.fields}

    assert account.record_path == "custAccts[]"
    assert fields["account_number"].path_scope is PhysicalPathScope.RECORD
    assert fields["account_number"].source_paths == ("accountNumber",)
    assert fields["customer_key"].path_scope is PhysicalPathScope.DOCUMENT
    assert fields["customer_key"].source_paths == ("partyId",)
    assert account.depends_on == ("canonical.customer.v1",)


def test_customer_has_account_relationship_emits_locked_direction() -> None:
    """Verify test customer has account relationship emits locked direction."""
    relationship = _load_bundle().graph_relationships[0]

    assert relationship.source_node_mapping_id == "graph.customer_account.v1"
    assert relationship.target_node_mapping_id == "graph.customer.v1"
    assert relationship.direction is RelationshipDirection.TARGET_TO_SOURCE
    assert relationship.edge_source_node_mapping_id == "graph.customer.v1"
    assert relationship.edge_target_node_mapping_id == "graph.customer_account.v1"
    assert relationship.relationship_type == "HAS_ACCOUNT"
    assert relationship.required is True


def test_pipeline_orders_customer_before_customer_account() -> None:
    """Verify test pipeline orders customer before customer account."""
    stages = _load_bundle().sync_pipelines[0].stages

    assert tuple(stage.stage_id for stage in stages) == (
        "stage.customer.v1",
        "stage.customer_account.v1",
    )
    assert stages[1].depends_on == ("stage.customer.v1",)


def test_profile_contains_no_secrets_addresses_or_executable_queries() -> None:
    """Verify test profile contains no secrets addresses or executable queries."""
    forbidden_tokens = (
        "password",
        "secret",
        "mongodb://",
        "neo4j://",
        "bolt://",
        "SELECT ",
        "MATCH ",
        "$where",
        "__import__",
        "handler(",
    )

    for filename in _EXPECTED_FILES:
        content = (_CONFIG_DIRECTORY / filename).read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in content


def test_profile_excludes_unconfirmed_entities_and_paths() -> None:
    """Verify test profile excludes unconfirmed entities and paths."""
    content = "\n".join(
        (_CONFIG_DIRECTORY / filename).read_text(encoding="utf-8") for filename in _EXPECTED_FILES
    )

    for forbidden in (
        "Package",
        "PPLTracking",
        "SalesOrder",
        "OrderLine",
        "partyNumber",
        "partyName",
        "accountName",
        "branchId",
    ):
        assert forbidden not in content


def test_customer_account_canonical_mapping_cannot_run_first() -> None:
    """Reject CustomerAccount normalization before its Customer dependency."""
    payload = _load_bundle_payload()
    pipeline = cast("list[dict[str, Any]]", payload["sync_pipelines"])[0]
    stages = cast("list[dict[str, Any]]", pipeline["stages"])
    stages[0]["canonical_mapping_ids"] = ["canonical.customer_account.v1"]
    stages[1]["canonical_mapping_ids"] = ["canonical.customer.v1"]

    with pytest.raises(ValidationError) as exc_info:
        DataPlatformMappingBundle.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == ("mapping_bundle_pipeline_canonical_order_invalid")


def test_reversing_has_account_edge_direction_is_detectable() -> None:
    """Verify test reversing has account edge direction is detectable."""
    payload = _load_bundle_payload()
    relationships = cast("list[dict[str, Any]]", payload["graph_relationships"])
    relationships[0]["direction"] = "SOURCE_TO_TARGET"

    bundle = DataPlatformMappingBundle.model_validate(payload)
    relationship = bundle.graph_relationships[0]

    assert relationship.edge_source_node_mapping_id == "graph.customer_account.v1"
    assert relationship.edge_target_node_mapping_id == "graph.customer.v1"
    assert relationship.edge_source_node_mapping_id != "graph.customer.v1"
