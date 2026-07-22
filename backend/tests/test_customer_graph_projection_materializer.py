"""Adversarial tests for in-memory Customer graph projection materialization."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from return_platform.canonical import (
    GraphProjectionStatus,
)
from return_platform.data_platform.mapping import (
    CanonicalEntityType,
    CompiledGraphPropertyPlan,
    CompiledGraphPropertySource,
    CustomerGraphProjectionMaterialization,
    CustomerNormalizationResult,
    GraphMaterializationError,
    GraphMaterializationErrorCode,
    GraphNodeUpsertParameters,
    GraphParameterEntry,
    GraphParameterMap,
    GraphRelationshipUpsertParameters,
    MappingExecutionPlan,
    NormalizationRejection,
    NormalizationRejectionCode,
    SourceDocumentEvidence,
    build_customer_account_canonical_model_registry,
    build_customer_account_handler_registry,
    compile_customer_profile_mapping,
    load_data_platform_mapping_configuration,
    materialize_customer_graph_projection,
    normalize_customer_source_document,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

_CONFIG_DIR = Path(__file__).parents[1] / "config" / "data_platform"
_SYNC_RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
_GRAPH_SYNCED_AT = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
_SOURCE_UPDATED_AT = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
_SOURCE_HASH = "a" * 64
_EXPECTED_NODE_COUNT = 3
_EXPECTED_RELATIONSHIP_COUNT = 2
_EXPECTED_PROJECTED_EVIDENCE = 3
_EXPECTED_PARTIAL_NODE_COUNT = 2
_NAIVE_GRAPH_SYNCED_AT = _GRAPH_SYNCED_AT.replace(tzinfo=None)


class _MutableMaterialization(Protocol):
    customer_node: GraphNodeUpsertParameters | None


def _asset() -> AssetCatalogEntry:
    """Create one approved Customer CDM source fixture."""
    return AssetCatalogEntry(
        asset_id="source.mongodb.customer_outbound_cdm",
        store=DataStoreType.MONGODB,
        database="eventMessages",
        namespace=None,
        object_name="customerOutboundCDM",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def _plan() -> MappingExecutionPlan:
    """Compile the approved Customer profile."""
    loaded = load_data_platform_mapping_configuration(_CONFIG_DIR)
    return compile_customer_profile_mapping(
        loaded,
        AssetCatalog(version="1.0", assets=(_asset(),)),
        build_customer_account_handler_registry(),
        build_customer_account_canonical_model_registry(),
    )


def _evidence(*, source_updated_at: datetime | None = _SOURCE_UPDATED_AT) -> SourceDocumentEvidence:
    """Create deterministic source evidence for normalization."""
    return SourceDocumentEvidence(
        source_document_id="P100",
        source_updated_at=source_updated_at,
        source_version="17",
        source_event_id="evt-100",
        source_hash=_SOURCE_HASH,
        observed_at=datetime(2026, 7, 21, 4, 1, tzinfo=UTC),
    )


def _document() -> dict[str, object]:
    """Create one Customer document with two accounts."""
    return {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"accountNumber": "203*C002"},
        ],
    }


def _normalization(
    *,
    plan: MappingExecutionPlan | None = None,
    source_updated_at: datetime | None = _SOURCE_UPDATED_AT,
    document: dict[str, object] | None = None,
) -> CustomerNormalizationResult:
    """Normalize one deterministic Customer document."""
    active_plan = plan or _plan()
    return normalize_customer_source_document(
        active_plan,
        _evidence(source_updated_at=source_updated_at),
        document or _document(),
    )


def _materialize(
    *,
    plan: MappingExecutionPlan | None = None,
    normalization: CustomerNormalizationResult | None = None,
) -> CustomerGraphProjectionMaterialization:
    """Materialize one valid normalization result."""
    active_plan = plan or _plan()
    active_normalization = normalization or _normalization(plan=active_plan)
    return materialize_customer_graph_projection(
        active_plan,
        active_normalization,
        sync_run_id=_SYNC_RUN_ID,
        graph_synced_at=_GRAPH_SYNCED_AT,
    )


def test_materializes_customer_accounts_and_directed_relationships() -> None:
    """Emit only immutable node and HAS_ACCOUNT parameter maps."""
    result = _materialize()

    assert result.node_count == _EXPECTED_NODE_COUNT
    assert result.relationship_count == _EXPECTED_RELATIONSHIP_COUNT
    assert result.rejected_count == 0
    assert result.customer_node is not None
    assert result.customer_node.label == "Customer"
    assert result.customer_node.key_value == "CUSTOMER_CDM:P100"
    assert tuple(node.key_value for node in result.customer_account_nodes) == (
        "CUSTOMER_CDM:202*C001",
        "CUSTOMER_CDM:203*C002",
    )
    assert all(
        relationship.relationship_type == "HAS_ACCOUNT"
        for relationship in result.has_account_relationships
    )
    assert all(
        relationship.source_label == "Customer"
        and relationship.target_label == "CustomerAccount"
        and relationship.source_key_value == "CUSTOMER_CDM:P100"
        for relationship in result.has_account_relationships
    )
    assert len(result.projection_evidence) == _EXPECTED_PROJECTED_EVIDENCE
    assert all(
        evidence.projection_status is GraphProjectionStatus.PROJECTED
        for evidence in result.projection_evidence
    )


def test_materializes_only_compiled_graph_properties_and_runtime_evidence() -> None:
    """Exclude unconfigured canonical fields and inject required evidence."""
    result = _materialize()
    assert result.customer_node is not None
    properties = result.customer_node.properties.as_mapping()

    assert properties["customer_key"] == "CUSTOMER_CDM:P100"
    assert properties["canonical_key"] == "CUSTOMER_CDM:P100"
    assert properties["party_id"] == "P100"
    assert properties["source_database"] == "eventMessages"
    assert properties["source_asset"] == "customerOutboundCDM"
    assert properties["source_updated_at"] == _SOURCE_UPDATED_AT
    assert properties["source_version"] == "17"
    assert properties["source_event_id"] == "evt-100"
    assert properties["source_hash"] == _SOURCE_HASH
    assert properties["identity_quality"] == "VERIFIED"
    assert properties["mapping_version"] == "1.0"
    assert properties["sync_run_id"] == str(_SYNC_RUN_ID)
    assert properties["graph_synced_at"] == _GRAPH_SYNCED_AT
    assert "party_name" not in properties
    assert "organization_name" not in properties


def test_parameter_maps_are_sorted_detached_and_read_only() -> None:
    """Expose deterministic immutable mappings instead of mutable dictionaries."""
    result = _materialize()
    assert result.customer_node is not None
    parameter_map = result.customer_node.properties
    assert tuple(entry.name for entry in parameter_map.entries) == tuple(
        sorted(entry.name for entry in parameter_map.entries)
    )
    exposed = parameter_map.as_mapping()

    with pytest.raises(TypeError):
        cast("dict[str, object]", exposed)["party_id"] = "OTHER"

    assert parameter_map.get("party_id") == "P100"


def test_materialization_is_deterministic_and_frozen() -> None:
    """Repeat input produces identical evidence IDs and immutable output."""
    first = _materialize()
    second = _materialize()

    assert first == second
    assert tuple(item.evidence_id for item in first.projection_evidence) == tuple(
        item.evidence_id for item in second.projection_evidence
    )

    mutable = cast("_MutableMaterialization", first)
    with pytest.raises(ValidationError) as exc_info:
        mutable.customer_node = None
    assert exc_info.value.errors()[0]["type"] == "frozen_instance"


def test_omits_optional_provenance_properties_when_absent() -> None:
    """Do not emit null values that could remove graph properties."""
    plan = _plan()
    evidence = SourceDocumentEvidence(
        source_document_id="P100",
        source_updated_at=_SOURCE_UPDATED_AT,
        observed_at=datetime(2026, 7, 21, 4, 1, tzinfo=UTC),
    )
    normalized = normalize_customer_source_document(plan, evidence, _document())
    result = _materialize(plan=plan, normalization=normalized)
    assert result.customer_node is not None
    properties = result.customer_node.properties.as_mapping()

    assert "source_version" not in properties
    assert "source_event_id" not in properties
    assert "source_hash" not in properties


def test_missing_required_source_updated_at_rejects_customer_and_accounts() -> None:
    """Fail closed instead of fabricating mandatory source update evidence."""
    result = _materialize(normalization=_normalization(source_updated_at=None))

    assert result.customer_node is None
    assert result.customer_account_nodes == ()
    assert result.has_account_relationships == ()
    assert tuple(item.projection_status for item in result.projection_evidence) == (
        GraphProjectionStatus.REJECTED,
        GraphProjectionStatus.UNRESOLVED,
        GraphProjectionStatus.UNRESOLVED,
    )
    assert result.projection_evidence[0].rejection_reason == ("REQUIRED_PROPERTY_MISSING")


def test_source_evidence_mismatch_rejects_record_without_graph_parameters() -> None:
    """Reject canonical records detached from the governed compiled source."""
    normalized = _normalization()
    assert normalized.customer is not None
    bad_provenance = normalized.customer.provenance.model_copy(
        update={"source_asset": "otherCollection"}
    )
    bad_customer = normalized.customer.model_copy(update={"provenance": bad_provenance})
    corrupted = normalized.model_copy(update={"customer": bad_customer})

    result = _materialize(normalization=corrupted)

    assert result.customer_node is None
    assert result.customer_account_nodes == ()
    assert result.projection_evidence[0].rejection_reason == ("SOURCE_EVIDENCE_MISMATCH")


def test_translates_normalization_rejections_without_raw_source_values() -> None:
    """Produce safe REJECTED evidence while preserving healthy siblings."""
    document: dict[str, object] = {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"accountNumber": "secret malformed account"},
        ],
    }
    normalized = _normalization(document=document)
    result = _materialize(normalization=normalized)

    rejected = next(
        evidence
        for evidence in result.projection_evidence
        if evidence.projection_status is GraphProjectionStatus.REJECTED
    )
    assert result.node_count == _EXPECTED_PARTIAL_NODE_COUNT
    assert result.relationship_count == 1
    assert rejected.canonical_entity_key.startswith("EVIDENCE:")
    assert "secret" not in (rejected.rejection_reason or "")
    assert rejected.rejection_reason is not None
    assert "NORMALIZATION_REJECTED" in rejected.rejection_reason


def test_dependency_rejection_maps_to_unresolved_projection_evidence() -> None:
    """Represent missing canonical parents as UNRESOLVED, not projected."""
    normalized = _normalization()
    rejection = NormalizationRejection(
        mapping_id="canonical.customer_account.v1",
        entity_type=CanonicalEntityType.CUSTOMER_ACCOUNT,
        record_locator="$.custAccts[9]",
        record_index=9,
        canonical_field=None,
        code=NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED,
        safe_message="A required canonical parent record was not accepted.",
        cause_code=None,
    )
    corrupted = normalized.model_copy(update={"rejections": (*normalized.rejections, rejection)})

    result = _materialize(normalization=corrupted)
    unresolved = next(
        evidence
        for evidence in result.projection_evidence
        if evidence.projection_status is GraphProjectionStatus.UNRESOLVED
    )

    assert unresolved.projection_status is GraphProjectionStatus.UNRESOLVED
    assert unresolved.rejection_reason is not None
    assert "NORMALIZATION_DEPENDENCY_UNRESOLVED" in unresolved.rejection_reason


def test_rejects_execution_plan_digest_mismatch() -> None:
    """Do not materialize normalization output from another compiled plan."""
    normalized = _normalization().model_copy(update={"execution_plan_digest": "b" * 64})

    with pytest.raises(GraphMaterializationError) as exc_info:
        _materialize(normalization=normalized)

    assert exc_info.value.code is (GraphMaterializationErrorCode.EXECUTION_PLAN_MISMATCH)


def test_rejects_missing_or_extra_graph_node_plans() -> None:
    """Accept only the exact Customer foundation graph-node set."""
    plan = _plan()
    missing = replace(plan, graph_nodes=plan.graph_nodes[:1])
    extra = replace(plan, graph_nodes=(*plan.graph_nodes, plan.graph_nodes[0]))

    for corrupted in (missing, extra):
        with pytest.raises(GraphMaterializationError) as exc_info:
            _materialize(plan=corrupted, normalization=_normalization(plan=plan))
        assert exc_info.value.code in {
            GraphMaterializationErrorCode.PLAN_UNSUPPORTED,
            GraphMaterializationErrorCode.GRAPH_NODE_PLAN_INVALID,
        }


def test_rejects_corrupted_relationship_direction_before_output() -> None:
    """Do not reinterpret a relationship whose emitted endpoints were altered."""
    plan = _plan()
    relationship = plan.graph_relationships[0]
    corrupted_relationship = replace(
        relationship,
        edge_source_node_mapping_id=relationship.edge_target_node_mapping_id,
        edge_target_node_mapping_id=relationship.edge_source_node_mapping_id,
    )
    corrupted_plan = replace(plan, graph_relationships=(corrupted_relationship,))

    with pytest.raises(GraphMaterializationError) as exc_info:
        _materialize(
            plan=corrupted_plan,
            normalization=_normalization(plan=plan),
        )

    assert exc_info.value.code is (GraphMaterializationErrorCode.GRAPH_RELATIONSHIP_PLAN_INVALID)


def test_rejects_unknown_runtime_property_source() -> None:
    """Reject runtime properties not explicitly owned by the materializer."""
    plan = _plan()
    node = plan.graph_nodes[0]
    bad_property = CompiledGraphPropertyPlan(
        graph_property="unexpected_runtime",
        source=CompiledGraphPropertySource.RUNTIME_VALUE,
    )
    corrupted_node = replace(node, properties=(*node.properties, bad_property))
    corrupted_plan = replace(
        plan,
        graph_nodes=(corrupted_node, *plan.graph_nodes[1:]),
    )

    with pytest.raises(GraphMaterializationError) as exc_info:
        _materialize(
            plan=corrupted_plan,
            normalization=_normalization(plan=plan),
        )

    assert exc_info.value.code is (GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)


def test_rejects_unsupported_runtime_parameter_value() -> None:
    """Reject a corrupted canonical model containing a non-scalar graph value."""
    normalized = _normalization()
    assert normalized.customer is not None
    corrupted_customer = normalized.customer.model_copy(
        update={"party_id": {"unexpected": "mapping"}}
    )
    corrupted = normalized.model_copy(update={"customer": corrupted_customer})

    with pytest.raises(GraphMaterializationError) as exc_info:
        _materialize(normalization=corrupted)

    assert exc_info.value.code is (GraphMaterializationErrorCode.GRAPH_PARAMETER_VALUE_INVALID)


def test_graph_parameter_contracts_reject_duplicates_order_and_key_drift() -> None:
    """Fail before a future writer sees ambiguous parameter maps."""
    with pytest.raises(ValidationError):
        GraphParameterMap(
            entries=(
                GraphParameterEntry(name="b", value="2"),
                GraphParameterEntry(name="a", value="1"),
            )
        )
    with pytest.raises(ValidationError):
        GraphParameterMap(
            entries=(
                GraphParameterEntry(name="a", value="1"),
                GraphParameterEntry(name="a", value="2"),
            )
        )
    with pytest.raises(ValidationError):
        GraphNodeUpsertParameters(
            node_mapping_id="graph.customer.v1",
            label="Customer",
            key_property="customer_key",
            key_value="CUSTOMER_CDM:P100",
            properties=GraphParameterMap.from_mapping({"customer_key": "CUSTOMER_CDM:OTHER"}),
        )


def test_relationship_contract_rejects_endpoint_match_drift() -> None:
    """Require endpoint parameter maps to match declared key values exactly."""
    with pytest.raises(ValidationError):
        GraphRelationshipUpsertParameters(
            relationship_mapping_id="graph.customer.has_account.v1",
            relationship_type="HAS_ACCOUNT",
            source_node_mapping_id="graph.customer.v1",
            source_label="Customer",
            source_key_property="customer_key",
            source_key_value="CUSTOMER_CDM:P100",
            source_match=GraphParameterMap.from_mapping({"customer_key": "CUSTOMER_CDM:OTHER"}),
            target_node_mapping_id="graph.customer_account.v1",
            target_label="CustomerAccount",
            target_key_property="account_key",
            target_key_value="CUSTOMER_CDM:202*C001",
            target_match=GraphParameterMap.from_mapping({"account_key": "CUSTOMER_CDM:202*C001"}),
        )


@pytest.mark.parametrize(
    ("plan", "normalization", "sync_run_id", "graph_synced_at"),
    [
        (object(), _normalization(), _SYNC_RUN_ID, _GRAPH_SYNCED_AT),
        (_plan(), object(), _SYNC_RUN_ID, _GRAPH_SYNCED_AT),
        (_plan(), _normalization(), "not-a-uuid", _GRAPH_SYNCED_AT),
        (_plan(), _normalization(), _SYNC_RUN_ID, _NAIVE_GRAPH_SYNCED_AT),
    ],
)
def test_rejects_invalid_dependency_and_runtime_argument_types(
    plan: object,
    normalization: object,
    sync_run_id: object,
    graph_synced_at: object,
) -> None:
    """Reject coercion and naive runtime evidence before materialization."""
    with pytest.raises(GraphMaterializationError) as exc_info:
        materialize_customer_graph_projection(
            cast("MappingExecutionPlan", plan),
            normalization,  # type: ignore[arg-type]
            sync_run_id=cast("UUID", sync_run_id),
            graph_synced_at=cast("datetime", graph_synced_at),
        )
    assert exc_info.value.code is GraphMaterializationErrorCode.INVALID_INPUT_TYPE
