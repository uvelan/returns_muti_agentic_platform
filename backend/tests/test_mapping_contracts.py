"""Deterministic tests for versioned data-platform mapping contracts."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Protocol, cast

import pytest
from pydantic import ValidationError

from return_platform.canonical.base import IdentityQuality
from return_platform.data_platform.mapping import (
    CanonicalEntityMapping,
    CanonicalEntityType,
    DataPlatformMappingBundle,
    GraphNodeMapping,
    GraphPropertyMapping,
    GraphRelationshipMapping,
    IdentityMapping,
    PhysicalFieldMapping,
    SourceAssetDefinition,
    SourceLifecycle,
    SyncPipelineDefinition,
    SyncStageDefinition,
)


class _MutableSourceDefinition(Protocol):
    required_for_sync: bool


def _error_type(exc_info: pytest.ExceptionInfo[ValidationError]) -> str:
    """Return the first stable Pydantic error type."""
    return str(exc_info.value.errors()[0]["type"])


def _field(
    canonical_field: str,
    source_path: str,
    *,
    required: bool = True,
    handler: str | None = None,
) -> PhysicalFieldMapping:
    """Create one valid physical field mapping."""
    return PhysicalFieldMapping(
        canonical_field=canonical_field,
        source_paths=(source_path,),
        required=required,
        handler=handler,
    )


def _customer_mapping(
    *,
    mapping_id: str = "canonical.customer",
    depends_on: tuple[str, ...] = (),
    entity_type: CanonicalEntityType = CanonicalEntityType.CUSTOMER,
) -> CanonicalEntityMapping:
    """Create a valid Customer canonical mapping."""
    return CanonicalEntityMapping(
        mapping_id=mapping_id,
        version="1.0",
        source_id="source.customer_cdm",
        entity_type=entity_type,
        fields=(
            _field("party_id", "partyId"),
            _field("party_name", "partyName", required=False),
        ),
        identity=IdentityMapping(
            key_field="customer_key",
            handler="customer_key_v1",
            component_fields=("party_id",),
            identity_quality=IdentityQuality.VERIFIED,
        ),
        depends_on=depends_on,
    )


def _account_mapping(
    *,
    mapping_id: str = "canonical.customer_account",
    depends_on: tuple[str, ...] = ("canonical.customer",),
) -> CanonicalEntityMapping:
    """Create a valid CustomerAccount canonical mapping."""
    return CanonicalEntityMapping(
        mapping_id=mapping_id,
        version="1.0",
        source_id="source.customer_cdm",
        entity_type=CanonicalEntityType.CUSTOMER_ACCOUNT,
        record_path="custAccts[]",
        fields=(
            _field("account_number", "accountNumber"),
            _field(
                "customer_key",
                "partyId",
                handler="customer_key_v1",
            ),
        ),
        identity=IdentityMapping(
            key_field="account_key",
            handler="customer_account_key_v1",
            component_fields=("account_number",),
            identity_quality=IdentityQuality.VERIFIED,
        ),
        depends_on=depends_on,
    )


def _customer_node(
    *,
    node_mapping_id: str = "graph.customer",
    canonical_mapping_id: str = "canonical.customer",
    key_field: str = "customer_key",
) -> GraphNodeMapping:
    """Create a valid Customer graph-node mapping."""
    return GraphNodeMapping(
        node_mapping_id=node_mapping_id,
        canonical_mapping_id=canonical_mapping_id,
        label="Customer",
        key_field=key_field,
        properties=(
            GraphPropertyMapping(
                canonical_field="customer_key",
                graph_property="customer_key",
            ),
            GraphPropertyMapping(
                canonical_field="party_id",
                graph_property="party_id",
            ),
            GraphPropertyMapping(
                canonical_field="party_name",
                graph_property="party_name",
            ),
        ),
    )


def _account_node() -> GraphNodeMapping:
    """Create a valid CustomerAccount graph-node mapping."""
    return GraphNodeMapping(
        node_mapping_id="graph.customer_account",
        canonical_mapping_id="canonical.customer_account",
        label="CustomerAccount",
        key_field="account_key",
        properties=(
            GraphPropertyMapping(
                canonical_field="account_key",
                graph_property="account_key",
            ),
            GraphPropertyMapping(
                canonical_field="account_number",
                graph_property="account_number",
            ),
            GraphPropertyMapping(
                canonical_field="customer_key",
                graph_property="customer_key",
            ),
        ),
    )


def _relationship() -> GraphRelationshipMapping:
    """Create a valid CustomerAccount-to-Customer relationship mapping."""
    return GraphRelationshipMapping(
        relationship_mapping_id="graph.customer_account.has_customer",
        relationship_type="BELONGS_TO_CUSTOMER",
        source_node_mapping_id="graph.customer_account",
        target_node_mapping_id="graph.customer",
        source_reference_field="customer_key",
        target_key_field="customer_key",
        required=True,
    )


def _pipeline() -> SyncPipelineDefinition:
    """Create a valid ordered synchronization pipeline."""
    return SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.customer",
                canonical_mapping_ids=("canonical.customer",),
                node_mapping_ids=("graph.customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.customer_account",
                canonical_mapping_ids=("canonical.customer_account",),
                node_mapping_ids=("graph.customer_account",),
                depends_on=("stage.customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.customer_relationship",
                relationship_mapping_ids=("graph.customer_account.has_customer",),
                depends_on=("stage.customer_account",),
            ),
        ),
    )


def _bundle(
    *,
    source_assets: tuple[SourceAssetDefinition, ...] | None = None,
    canonical_mappings: tuple[CanonicalEntityMapping, ...] | None = None,
    graph_nodes: tuple[GraphNodeMapping, ...] | None = None,
    graph_relationships: tuple[GraphRelationshipMapping, ...] | None = None,
    sync_pipelines: tuple[SyncPipelineDefinition, ...] | None = None,
) -> DataPlatformMappingBundle:
    """Create a valid cross-file mapping bundle."""
    return DataPlatformMappingBundle(
        schema_version="1.0",
        source_assets=(
            source_assets
            if source_assets is not None
            else (
                SourceAssetDefinition(
                    source_id="source.customer_cdm",
                    catalog_asset_id="source.mongodb.customer_outbound_cdm",
                    source_system="CUSTOMER_CDM",
                    lifecycle=SourceLifecycle.ACTIVE,
                    required_for_sync=True,
                ),
            )
        ),
        canonical_mappings=(
            canonical_mappings
            if canonical_mappings is not None
            else (_customer_mapping(), _account_mapping())
        ),
        graph_nodes=(
            graph_nodes if graph_nodes is not None else (_customer_node(), _account_node())
        ),
        graph_relationships=(
            graph_relationships if graph_relationships is not None else (_relationship(),)
        ),
        sync_pipelines=(sync_pipelines if sync_pipelines is not None else (_pipeline(),)),
    )


def _valid_yaml_like_payload() -> dict[str, object]:
    """Return a valid dict/list payload representative of parsed YAML."""
    return {
        "schema_version": "1.0",
        "source_assets": [
            {
                "source_id": "source.customer_cdm",
                "catalog_asset_id": "source.mongodb.customer_outbound_cdm",
                "source_system": "CUSTOMER_CDM",
                "lifecycle": "ACTIVE",
                "required_for_sync": True,
            },
        ],
        "canonical_mappings": [
            {
                "mapping_id": "canonical.customer",
                "version": "1.0",
                "source_id": "source.customer_cdm",
                "entity_type": "Customer",
                "record_path": None,
                "fields": [
                    {
                        "canonical_field": "party_id",
                        "source_paths": ["partyId"],
                        "required": True,
                        "handler": None,
                    },
                    {
                        "canonical_field": "party_name",
                        "source_paths": ["partyName", "organizationName"],
                        "required": False,
                        "handler": None,
                    },
                ],
                "identity": {
                    "key_field": "customer_key",
                    "handler": "customer_key_v1",
                    "component_fields": ["party_id"],
                    "identity_quality": "VERIFIED",
                },
                "depends_on": [],
            },
        ],
        "graph_nodes": [
            {
                "node_mapping_id": "graph.customer",
                "canonical_mapping_id": "canonical.customer",
                "label": "Customer",
                "key_field": "customer_key",
                "properties": [
                    {
                        "canonical_field": "customer_key",
                        "graph_property": "customer_key",
                    },
                    {
                        "canonical_field": "party_id",
                        "graph_property": "party_id",
                    },
                ],
            },
        ],
        "graph_relationships": [],
        "sync_pipelines": [
            {
                "pipeline_id": "pipeline.graph_v1",
                "version": "1.0",
                "stages": [
                    {
                        "stage_id": "stage.customer",
                        "canonical_mapping_ids": ["canonical.customer"],
                        "node_mapping_ids": ["graph.customer"],
                        "relationship_mapping_ids": [],
                        "depends_on": [],
                    },
                ],
            },
        ],
    }


def test_valid_yaml_like_bundle_is_normalized_to_immutable_tuples() -> None:
    """Accept safe YAML lists and exact enum strings without scalar coercion."""
    bundle = DataPlatformMappingBundle.model_validate(_valid_yaml_like_payload())

    assert bundle.schema_version == "1.0"
    assert isinstance(bundle.source_assets, tuple)
    assert bundle.source_assets[0].lifecycle is SourceLifecycle.ACTIVE
    assert bundle.canonical_mappings[0].entity_type is CanonicalEntityType.CUSTOMER
    assert bundle.canonical_mappings[0].identity.identity_quality is IdentityQuality.VERIFIED
    assert bundle.canonical_mappings[0].fields[1].source_paths == (
        "partyName",
        "organizationName",
    )


def test_mapping_models_are_frozen() -> None:
    """Reject runtime mutation of loaded configuration contracts."""
    source = SourceAssetDefinition(
        source_id="source.customer_cdm",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system="CUSTOMER_CDM",
    )
    mutable_source = cast("_MutableSourceDefinition", source)

    with pytest.raises(ValidationError) as exc_info:
        mutable_source.required_for_sync = False

    assert _error_type(exc_info) == "frozen_instance"


def test_unknown_fields_are_rejected() -> None:
    """Reject undeclared configuration language features."""
    payload = _valid_yaml_like_payload()
    payload["cypher"] = "MATCH (n) DELETE n"

    with pytest.raises(ValidationError) as exc_info:
        DataPlatformMappingBundle.model_validate(payload)

    assert _error_type(exc_info) == "extra_forbidden"


@pytest.mark.parametrize("value", [1, "true", "yes"])
def test_source_required_for_sync_is_strict_boolean(value: object) -> None:
    """Reject permissive Boolean coercion in parsed configuration."""
    with pytest.raises(ValidationError) as exc_info:
        SourceAssetDefinition.model_validate(
            {
                "source_id": "source.customer_cdm",
                "catalog_asset_id": "source.mongodb.customer_outbound_cdm",
                "source_system": "CUSTOMER_CDM",
                "required_for_sync": value,
            },
        )

    assert _error_type(exc_info) == "bool_type"


def test_source_lifecycle_rejects_unknown_value() -> None:
    """Keep source lifecycle states code-owned."""
    with pytest.raises(ValidationError) as exc_info:
        SourceAssetDefinition.model_validate(
            {
                "source_id": "source.customer_cdm",
                "catalog_asset_id": "source.mongodb.customer_outbound_cdm",
                "source_system": "CUSTOMER_CDM",
                "lifecycle": "RETIRED",
            },
        )

    assert _error_type(exc_info) == "enum"


@pytest.mark.parametrize(
    "source_id",
    ["SOURCE.customer", "source", "source..customer", "source.customer!"],
)
def test_source_identifier_is_bounded_lowercase_dotted(source_id: str) -> None:
    """Reject ambiguous or unstable configuration identifiers."""
    with pytest.raises(ValidationError):
        SourceAssetDefinition(
            source_id=source_id,
            catalog_asset_id="source.mongodb.customer_outbound_cdm",
            source_system="CUSTOMER_CDM",
        )


def test_physical_alias_order_is_explicit_precedence() -> None:
    """Preserve ordered aliases instead of accepting an ambiguous set."""
    mapping = PhysicalFieldMapping(
        canonical_field="order_total_amount",
        source_paths=("orderTotalAmt", "totalAmount"),
        required=True,
    )

    assert mapping.source_paths == ("orderTotalAmt", "totalAmount")


def test_duplicate_physical_aliases_are_rejected() -> None:
    """Reject repeated aliases that create meaningless precedence."""
    with pytest.raises(ValidationError) as exc_info:
        PhysicalFieldMapping(
            canonical_field="order_total_amount",
            source_paths=("orderTotalAmt", "orderTotalAmt"),
        )

    assert _error_type(exc_info) == "physical_field_mapping_duplicate_source_path"


@pytest.mark.parametrize(
    "path",
    [
        "$where",
        "salesLines[0].lineData",
        "salesLines..lineData",
        "salesLines[?(@.qty>0)]",
        "field;MATCH(n)",
    ],
)
def test_physical_paths_reject_queries_indexes_and_code(path: str) -> None:
    """Allow only bounded field traversal, never query expressions."""
    with pytest.raises(ValidationError):
        PhysicalFieldMapping(
            canonical_field="party_id",
            source_paths=(path,),
        )


@pytest.mark.parametrize(
    "handler",
    [
        "package.module:function",
        "module.handler",
        "handler()",
        "__import__",
        "UPPERCASE",
    ],
)
def test_handler_names_are_registry_tokens_not_executable_code(handler: str) -> None:
    """Reject import paths, calls, and non-allow-listed handler syntax."""
    with pytest.raises(ValidationError):
        PhysicalFieldMapping(
            canonical_field="party_id",
            source_paths=("partyId",),
            handler=handler,
        )


def test_syntactically_safe_handler_is_deferred_to_registry_compiler() -> None:
    """Allow a safe token while deferring registry existence to compilation."""
    mapping = PhysicalFieldMapping(
        canonical_field="party_id",
        source_paths=("partyId",),
        handler="approved_handler_name",
    )

    assert mapping.handler == "approved_handler_name"


@pytest.mark.parametrize("value", [1, "false"])
def test_physical_field_required_is_strict_boolean(value: object) -> None:
    """Reject Boolean coercion for required field semantics."""
    with pytest.raises(ValidationError) as exc_info:
        PhysicalFieldMapping.model_validate(
            {
                "canonical_field": "party_id",
                "source_paths": ["partyId"],
                "required": value,
            },
        )

    assert _error_type(exc_info) == "bool_type"


def test_identity_mapping_rejects_duplicate_components() -> None:
    """Reject duplicate identity evidence fields."""
    with pytest.raises(ValidationError) as exc_info:
        IdentityMapping(
            key_field="customer_key",
            handler="customer_key_v1",
            component_fields=("party_id", "party_id"),
            identity_quality=IdentityQuality.VERIFIED,
        )

    assert _error_type(exc_info) == "identity_mapping_duplicate_component"


def test_identity_mapping_rejects_key_as_its_own_component() -> None:
    """Reject recursive identity definitions."""
    with pytest.raises(ValidationError) as exc_info:
        IdentityMapping(
            key_field="customer_key",
            handler="customer_key_v1",
            component_fields=("customer_key",),
            identity_quality=IdentityQuality.VERIFIED,
        )

    assert _error_type(exc_info) == "identity_mapping_key_is_component"


def test_identity_quality_rejects_unapproved_state() -> None:
    """Keep identity evidence states code-owned."""
    with pytest.raises(ValidationError) as exc_info:
        IdentityMapping.model_validate(
            {
                "key_field": "customer_key",
                "handler": "customer_key_v1",
                "component_fields": ["party_id"],
                "identity_quality": "UNKNOWN",
            },
        )

    assert _error_type(exc_info) == "enum"


def test_canonical_mapping_rejects_duplicate_target_fields() -> None:
    """Map each canonical field at most once."""
    with pytest.raises(ValidationError) as exc_info:
        CanonicalEntityMapping(
            mapping_id="canonical.customer",
            version="1.0",
            source_id="source.customer_cdm",
            entity_type=CanonicalEntityType.CUSTOMER,
            fields=(
                _field("party_id", "partyId"),
                _field("party_id", "legacyPartyId"),
            ),
            identity=IdentityMapping(
                key_field="customer_key",
                handler="customer_key_v1",
                component_fields=("party_id",),
                identity_quality=IdentityQuality.VERIFIED,
            ),
        )

    assert _error_type(exc_info) == "canonical_entity_mapping_duplicate_field"


def test_canonical_mapping_requires_every_identity_component_mapping() -> None:
    """Reject identity inputs not produced by physical normalization."""
    with pytest.raises(ValidationError) as exc_info:
        CanonicalEntityMapping(
            mapping_id="canonical.customer",
            version="1.0",
            source_id="source.customer_cdm",
            entity_type=CanonicalEntityType.CUSTOMER,
            fields=(_field("party_name", "partyName"),),
            identity=IdentityMapping(
                key_field="customer_key",
                handler="customer_key_v1",
                component_fields=("party_id",),
                identity_quality=IdentityQuality.VERIFIED,
            ),
        )

    assert _error_type(exc_info) == "canonical_entity_mapping_identity_component_missing"


def test_canonical_mapping_rejects_self_dependency() -> None:
    """Reject direct dependency cycles locally."""
    with pytest.raises(ValidationError) as exc_info:
        _customer_mapping(depends_on=("canonical.customer",))

    assert _error_type(exc_info) == "canonical_entity_mapping_self_dependency"


def test_canonical_mapping_rejects_duplicate_dependencies() -> None:
    """Reject repeated dependency references."""
    with pytest.raises(ValidationError) as exc_info:
        _account_mapping(
            depends_on=("canonical.customer", "canonical.customer"),
        )

    assert _error_type(exc_info) == "canonical_entity_mapping_duplicate_dependency"


@pytest.mark.parametrize("entity_type", ["Package", "PPLTracking"])
def test_deferred_entities_are_not_mapping_contract_members(entity_type: str) -> None:
    """Keep Package and PPLTracking excluded from the approved v1 model."""
    payload = _valid_yaml_like_payload()
    canonical_mappings = cast("list[dict[str, object]]", payload["canonical_mappings"])
    canonical_mappings[0]["entity_type"] = entity_type

    with pytest.raises(ValidationError) as exc_info:
        DataPlatformMappingBundle.model_validate(payload)

    assert _error_type(exc_info) == "enum"


def test_graph_node_rejects_duplicate_canonical_fields() -> None:
    """Reject one canonical field being projected twice."""
    with pytest.raises(ValidationError) as exc_info:
        GraphNodeMapping(
            node_mapping_id="graph.customer",
            canonical_mapping_id="canonical.customer",
            label="Customer",
            key_field="customer_key",
            properties=(
                GraphPropertyMapping(
                    canonical_field="customer_key",
                    graph_property="customer_key",
                ),
                GraphPropertyMapping(
                    canonical_field="customer_key",
                    graph_property="duplicate_key",
                ),
            ),
        )

    assert _error_type(exc_info) == "graph_node_mapping_duplicate_canonical_field"


def test_graph_node_rejects_duplicate_graph_property_names() -> None:
    """Reject property overwrite ambiguity."""
    with pytest.raises(ValidationError) as exc_info:
        GraphNodeMapping(
            node_mapping_id="graph.customer",
            canonical_mapping_id="canonical.customer",
            label="Customer",
            key_field="customer_key",
            properties=(
                GraphPropertyMapping(
                    canonical_field="customer_key",
                    graph_property="key",
                ),
                GraphPropertyMapping(
                    canonical_field="party_id",
                    graph_property="key",
                ),
            ),
        )

    assert _error_type(exc_info) == "graph_node_mapping_duplicate_graph_property"


def test_graph_node_requires_projected_key_property() -> None:
    """Require the constrained node key to be part of the projection."""
    with pytest.raises(ValidationError) as exc_info:
        GraphNodeMapping(
            node_mapping_id="graph.customer",
            canonical_mapping_id="canonical.customer",
            label="Customer",
            key_field="customer_key",
            properties=(
                GraphPropertyMapping(
                    canonical_field="party_id",
                    graph_property="party_id",
                ),
            ),
        )

    assert _error_type(exc_info) == "graph_node_mapping_key_property_missing"


@pytest.mark.parametrize("label", ["customer", "Customer:Admin", "`Customer`"])
def test_graph_labels_are_allow_listed_tokens(label: str) -> None:
    """Reject arbitrary Cypher label syntax."""
    with pytest.raises(ValidationError):
        GraphNodeMapping(
            node_mapping_id="graph.customer",
            canonical_mapping_id="canonical.customer",
            label=label,
            key_field="customer_key",
            properties=(
                GraphPropertyMapping(
                    canonical_field="customer_key",
                    graph_property="customer_key",
                ),
            ),
        )


def test_graph_relationship_rejects_same_endpoint_mapping() -> None:
    """Reject an accidental self-endpoint configuration."""
    with pytest.raises(ValidationError) as exc_info:
        GraphRelationshipMapping(
            relationship_mapping_id="graph.customer.self",
            relationship_type="RELATED_TO",
            source_node_mapping_id="graph.customer",
            target_node_mapping_id="graph.customer",
            source_reference_field="customer_key",
            target_key_field="customer_key",
        )

    assert _error_type(exc_info) == "graph_relationship_mapping_same_endpoint"


@pytest.mark.parametrize(
    "relationship_type",
    ["belongs_to", "BELONGS-TO", "BELONGS_TO` MATCH"],
)
def test_relationship_types_are_allow_listed_tokens(
    relationship_type: str,
) -> None:
    """Reject arbitrary Cypher relationship syntax."""
    with pytest.raises(ValidationError):
        GraphRelationshipMapping(
            relationship_mapping_id="graph.account.customer",
            relationship_type=relationship_type,
            source_node_mapping_id="graph.customer_account",
            target_node_mapping_id="graph.customer",
            source_reference_field="customer_key",
            target_key_field="customer_key",
        )


@pytest.mark.parametrize("value", [1, "false"])
def test_relationship_required_is_strict_boolean(value: object) -> None:
    """Reject permissive relationship optionality coercion."""
    with pytest.raises(ValidationError) as exc_info:
        GraphRelationshipMapping.model_validate(
            {
                "relationship_mapping_id": "graph.account.customer",
                "relationship_type": "BELONGS_TO_CUSTOMER",
                "source_node_mapping_id": "graph.customer_account",
                "target_node_mapping_id": "graph.customer",
                "source_reference_field": "customer_key",
                "target_key_field": "customer_key",
                "required": value,
            },
        )

    assert _error_type(exc_info) == "bool_type"


def test_sync_stage_rejects_empty_work() -> None:
    """Reject stages that cannot execute anything."""
    with pytest.raises(ValidationError) as exc_info:
        SyncStageDefinition(stage_id="stage.empty")

    assert _error_type(exc_info) == "sync_stage_empty"


@pytest.mark.parametrize(
    ("field_name", "error_type"),
    [
        ("canonical_mapping_ids", "sync_stage_duplicate_canonical_mapping"),
        ("node_mapping_ids", "sync_stage_duplicate_node_mapping"),
        (
            "relationship_mapping_ids",
            "sync_stage_duplicate_relationship_mapping",
        ),
        ("depends_on", "sync_stage_duplicate_dependency"),
    ],
)
def test_sync_stage_rejects_duplicate_references(
    field_name: str,
    error_type: str,
) -> None:
    """Reject duplicated stage references in every reference category."""
    payload: dict[str, object] = {
        "stage_id": "stage.customer",
        "canonical_mapping_ids": ["canonical.customer"],
    }
    payload[field_name] = ["value.duplicate", "value.duplicate"]

    with pytest.raises(ValidationError) as exc_info:
        SyncStageDefinition.model_validate(payload)

    assert _error_type(exc_info) == error_type


def test_sync_stage_rejects_self_dependency() -> None:
    """Reject direct stage dependency cycles."""
    with pytest.raises(ValidationError) as exc_info:
        SyncStageDefinition(
            stage_id="stage.customer",
            canonical_mapping_ids=("canonical.customer",),
            depends_on=("stage.customer",),
        )

    assert _error_type(exc_info) == "sync_stage_self_dependency"


def test_pipeline_rejects_duplicate_stage_ids() -> None:
    """Require unique deterministic stage identities."""
    stage = SyncStageDefinition(
        stage_id="stage.customer",
        canonical_mapping_ids=("canonical.customer",),
    )
    with pytest.raises(ValidationError) as exc_info:
        SyncPipelineDefinition(
            pipeline_id="pipeline.graph_v1",
            version="1.0",
            stages=(stage, stage),
        )

    assert _error_type(exc_info) == "sync_pipeline_duplicate_stage"


def test_pipeline_rejects_forward_stage_dependency() -> None:
    """Require dependencies to refer only to earlier tuple positions."""
    with pytest.raises(ValidationError) as exc_info:
        SyncPipelineDefinition(
            pipeline_id="pipeline.graph_v1",
            version="1.0",
            stages=(
                SyncStageDefinition(
                    stage_id="stage.customer",
                    canonical_mapping_ids=("canonical.customer",),
                    depends_on=("stage.later",),
                ),
                SyncStageDefinition(
                    stage_id="stage.later",
                    canonical_mapping_ids=("canonical.customer_account",),
                ),
            ),
        )

    assert _error_type(exc_info) == "sync_pipeline_stage_dependency_order_invalid"


@pytest.mark.parametrize(
    ("first_field", "second_field", "error_type"),
    [
        (
            "canonical_mapping_ids",
            "canonical_mapping_ids",
            "sync_pipeline_canonical_mapping_repeated",
        ),
        (
            "node_mapping_ids",
            "node_mapping_ids",
            "sync_pipeline_node_mapping_repeated",
        ),
        (
            "relationship_mapping_ids",
            "relationship_mapping_ids",
            "sync_pipeline_relationship_mapping_repeated",
        ),
    ],
)
def test_pipeline_rejects_execution_mapping_repeated_across_stages(
    first_field: str,
    second_field: str,
    error_type: str,
) -> None:
    """Execute each mapping once per pipeline definition."""
    first: dict[str, object] = {"stage_id": "stage.first"}
    second: dict[str, object] = {"stage_id": "stage.second"}
    first[first_field] = ["mapping.same"]
    second[second_field] = ["mapping.same"]

    with pytest.raises(ValidationError) as exc_info:
        SyncPipelineDefinition.model_validate(
            {
                "pipeline_id": "pipeline.graph_v1",
                "version": "1.0",
                "stages": [first, second],
            },
        )

    assert _error_type(exc_info) == error_type


def test_bundle_rejects_duplicate_source_ids() -> None:
    """Reject ambiguous source registry entries."""
    source = SourceAssetDefinition(
        source_id="source.customer_cdm",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system="CUSTOMER_CDM",
    )
    with pytest.raises(ValidationError) as exc_info:
        _bundle(source_assets=(source, source))

    assert _error_type(exc_info) == "mapping_bundle_duplicate_source"


def test_bundle_rejects_duplicate_canonical_mapping_ids() -> None:
    """Reject ambiguous canonical mapping identities."""
    mapping = _customer_mapping()
    with pytest.raises(ValidationError) as exc_info:
        _bundle(canonical_mappings=(mapping, mapping))

    assert _error_type(exc_info) == "mapping_bundle_duplicate_canonical_mapping"


def test_bundle_rejects_missing_source_reference() -> None:
    """Require every canonical mapping to resolve a configured source."""
    mapping = CanonicalEntityMapping(
        mapping_id="canonical.customer",
        version="1.0",
        source_id="source.missing",
        entity_type=CanonicalEntityType.CUSTOMER,
        fields=(_field("party_id", "partyId"),),
        identity=IdentityMapping(
            key_field="customer_key",
            handler="customer_key_v1",
            component_fields=("party_id",),
            identity_quality=IdentityQuality.VERIFIED,
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(
            canonical_mappings=(mapping,),
            graph_nodes=(_customer_node(),),
            graph_relationships=(),
            sync_pipelines=(
                SyncPipelineDefinition(
                    pipeline_id="pipeline.graph_v1",
                    version="1.0",
                    stages=(
                        SyncStageDefinition(
                            stage_id="stage.customer",
                            canonical_mapping_ids=("canonical.customer",),
                            node_mapping_ids=("graph.customer",),
                        ),
                    ),
                ),
            ),
        )

    assert _error_type(exc_info) == "mapping_bundle_source_reference_missing"


def test_bundle_rejects_missing_canonical_dependency() -> None:
    """Require every canonical dependency to resolve."""
    account = _account_mapping(depends_on=("canonical.missing",))

    with pytest.raises(ValidationError) as exc_info:
        _bundle(canonical_mappings=(_customer_mapping(), account))

    assert _error_type(exc_info) == "mapping_bundle_canonical_dependency_missing"


def test_bundle_rejects_indirect_canonical_dependency_cycle() -> None:
    """Reject multi-node dependency cycles before pipeline execution."""
    customer = _customer_mapping(depends_on=("canonical.customer_account",))
    account = _account_mapping(depends_on=("canonical.customer",))

    with pytest.raises(ValidationError) as exc_info:
        _bundle(canonical_mappings=(customer, account))

    assert _error_type(exc_info) == "mapping_bundle_canonical_dependency_cycle"


def test_bundle_rejects_graph_node_for_value_object() -> None:
    """Keep ContactPoint excluded from graph model v1."""
    contact_mapping = _customer_mapping(
        mapping_id="canonical.contact_point",
        entity_type=CanonicalEntityType.CONTACT_POINT,
    )
    contact_node = _customer_node(
        node_mapping_id="graph.contact_point",
        canonical_mapping_id="canonical.contact_point",
    )
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.contact_point",
                canonical_mapping_ids=("canonical.contact_point",),
                node_mapping_ids=("graph.contact_point",),
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(
            canonical_mappings=(contact_mapping,),
            graph_nodes=(contact_node,),
            graph_relationships=(),
            sync_pipelines=(pipeline,),
        )

    assert _error_type(exc_info) == "mapping_bundle_graph_entity_not_allowed"


def test_bundle_rejects_node_with_missing_canonical_mapping() -> None:
    """Require node projections to resolve canonical mapping contracts."""
    node = _customer_node(canonical_mapping_id="canonical.missing")

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_nodes=(node, _account_node()))

    assert _error_type(exc_info) == "mapping_bundle_node_canonical_reference_missing"


def test_bundle_rejects_graph_node_key_mismatch() -> None:
    """Use only the code-owned canonical identity as the node constraint key."""
    node = _customer_node(key_field="party_id")

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_nodes=(node, _account_node()))

    assert _error_type(exc_info) == "mapping_bundle_node_key_mismatch"


def test_bundle_rejects_graph_property_for_unmapped_field() -> None:
    """Reject graph properties absent from canonical normalization output."""
    node = GraphNodeMapping(
        node_mapping_id="graph.customer",
        canonical_mapping_id="canonical.customer",
        label="Customer",
        key_field="customer_key",
        properties=(
            GraphPropertyMapping(
                canonical_field="customer_key",
                graph_property="customer_key",
            ),
            GraphPropertyMapping(
                canonical_field="invented_field",
                graph_property="invented_field",
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_nodes=(node, _account_node()))

    assert _error_type(exc_info) == "mapping_bundle_node_property_field_missing"


def test_bundle_rejects_relationship_with_missing_endpoint() -> None:
    """Require both relationship endpoint node mappings."""
    relationship = GraphRelationshipMapping(
        relationship_mapping_id="graph.account.customer",
        relationship_type="BELONGS_TO_CUSTOMER",
        source_node_mapping_id="graph.missing",
        target_node_mapping_id="graph.customer",
        source_reference_field="customer_key",
        target_key_field="customer_key",
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_relationships=(relationship,))

    assert _error_type(exc_info) == "mapping_bundle_relationship_endpoint_missing"


def test_bundle_rejects_relationship_source_field_not_available() -> None:
    """Reject relationships built from nonexistent canonical references."""
    relationship = GraphRelationshipMapping(
        relationship_mapping_id="graph.account.customer",
        relationship_type="BELONGS_TO_CUSTOMER",
        source_node_mapping_id="graph.customer_account",
        target_node_mapping_id="graph.customer",
        source_reference_field="missing_customer_key",
        target_key_field="customer_key",
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_relationships=(relationship,))

    assert _error_type(exc_info) == "mapping_bundle_relationship_source_field_missing"


def test_bundle_rejects_relationship_target_key_mismatch() -> None:
    """Match relationships only against the target node constraint key."""
    relationship = GraphRelationshipMapping(
        relationship_mapping_id="graph.account.customer",
        relationship_type="BELONGS_TO_CUSTOMER",
        source_node_mapping_id="graph.customer_account",
        target_node_mapping_id="graph.customer",
        source_reference_field="customer_key",
        target_key_field="party_id",
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(graph_relationships=(relationship,))

    assert _error_type(exc_info) == "mapping_bundle_relationship_target_key_mismatch"


@pytest.mark.parametrize(
    ("stage_payload", "error_type"),
    [
        (
            {"canonical_mapping_ids": ("canonical.missing",)},
            "mapping_bundle_pipeline_canonical_reference_missing",
        ),
        (
            {"node_mapping_ids": ("graph.missing",)},
            "mapping_bundle_pipeline_node_reference_missing",
        ),
        (
            {"relationship_mapping_ids": ("graph.missing",)},
            "mapping_bundle_pipeline_relationship_reference_missing",
        ),
    ],
)
def test_bundle_rejects_undefined_pipeline_mapping_reference(
    stage_payload: Mapping[str, tuple[str, ...]],
    error_type: str,
) -> None:
    """Require every pipeline execution reference to resolve."""
    stage = SyncStageDefinition(
        stage_id="stage.invalid",
        **dict(stage_payload),
    )
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(stage,),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(sync_pipelines=(pipeline,))

    assert _error_type(exc_info) == error_type


def test_bundle_requires_canonical_dependency_in_earlier_pipeline_stage() -> None:
    """Reject same-stage execution of a dependent canonical mapping."""
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.customers",
                canonical_mapping_ids=(
                    "canonical.customer",
                    "canonical.customer_account",
                ),
                node_mapping_ids=("graph.customer", "graph.customer_account"),
            ),
            SyncStageDefinition(
                stage_id="stage.relationship",
                relationship_mapping_ids=("graph.customer_account.has_customer",),
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(sync_pipelines=(pipeline,))

    assert _error_type(exc_info) == "mapping_bundle_pipeline_canonical_order_invalid"


def test_bundle_rejects_node_projection_before_canonical_mapping() -> None:
    """Never project a node before its canonical record exists."""
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.node",
                node_mapping_ids=("graph.customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.canonical",
                canonical_mapping_ids=("canonical.customer",),
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(
            canonical_mappings=(_customer_mapping(),),
            graph_nodes=(_customer_node(),),
            graph_relationships=(),
            sync_pipelines=(pipeline,),
        )

    assert _error_type(exc_info) == "mapping_bundle_pipeline_node_order_invalid"


def test_bundle_rejects_relationship_before_endpoint_node() -> None:
    """Never create a relationship before both endpoint node stages."""
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.customer",
                canonical_mapping_ids=("canonical.customer",),
                node_mapping_ids=("graph.customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.relationship",
                relationship_mapping_ids=("graph.customer_account.has_customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.customer_account",
                canonical_mapping_ids=("canonical.customer_account",),
                node_mapping_ids=("graph.customer_account",),
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(sync_pipelines=(pipeline,))

    assert _error_type(exc_info) == "mapping_bundle_pipeline_relationship_order_invalid"


def test_same_stage_canonical_and_node_projection_is_allowed() -> None:
    """Allow code-owned within-stage ordering: normalize, then upsert node."""
    bundle = DataPlatformMappingBundle.model_validate(_valid_yaml_like_payload())

    assert bundle.sync_pipelines[0].stages[0].canonical_mapping_ids == ("canonical.customer",)
    assert bundle.sync_pipelines[0].stages[0].node_mapping_ids == ("graph.customer",)


def test_same_stage_endpoint_nodes_and_relationship_are_allowed() -> None:
    """Allow code-owned within-stage ordering: nodes before relationships."""
    pipeline = SyncPipelineDefinition(
        pipeline_id="pipeline.graph_v1",
        version="1.0",
        stages=(
            SyncStageDefinition(
                stage_id="stage.customer",
                canonical_mapping_ids=("canonical.customer",),
                node_mapping_ids=("graph.customer",),
            ),
            SyncStageDefinition(
                stage_id="stage.account",
                canonical_mapping_ids=("canonical.customer_account",),
                node_mapping_ids=("graph.customer_account",),
                relationship_mapping_ids=("graph.customer_account.has_customer",),
            ),
        ),
    )

    bundle = _bundle(sync_pipelines=(pipeline,))

    assert bundle.sync_pipelines[0].stages[1].relationship_mapping_ids == (
        "graph.customer_account.has_customer",
    )


def test_bundle_payload_is_not_mutated_during_validation() -> None:
    """Keep loader input reusable for digest calculation and diagnostics."""
    payload = _valid_yaml_like_payload()
    original = deepcopy(payload)

    DataPlatformMappingBundle.model_validate(payload)

    assert payload == original


def test_unordered_source_alias_set_is_rejected() -> None:
    """Reject sets because alias precedence is configuration evidence."""
    with pytest.raises(ValidationError) as exc_info:
        PhysicalFieldMapping.model_validate(
            {
                "canonical_field": "party_name",
                "source_paths": {"partyName", "organizationName"},
            },
        )

    assert _error_type(exc_info) == "mapping_ordered_sequence_required"


def test_unordered_pipeline_stage_set_is_rejected() -> None:
    """Reject unordered top-level stage collections."""
    stage = SyncStageDefinition(
        stage_id="stage.customer",
        canonical_mapping_ids=("canonical.customer",),
    )

    with pytest.raises(ValidationError) as exc_info:
        SyncPipelineDefinition.model_validate(
            {
                "pipeline_id": "pipeline.graph_v1",
                "version": "1.0",
                "stages": {stage},
            },
        )

    assert _error_type(exc_info) == "mapping_ordered_sequence_required"


@pytest.mark.parametrize("source_system", ["tds", "TDS:PRIMARY", "TDS PRIMARY"])
def test_source_system_is_allow_listed_uppercase_token(source_system: str) -> None:
    """Reject ambiguous source-system provenance names."""
    with pytest.raises(ValidationError):
        SourceAssetDefinition(
            source_id="source.sales_inv",
            catalog_asset_id="source.mongodb.sales_inv",
            source_system=source_system,
        )


def test_bundle_rejects_multiple_source_ids_for_same_catalog_asset() -> None:
    """Prevent duplicate physical-source bindings from diverging over time."""
    first = SourceAssetDefinition(
        source_id="source.customer_cdm",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system="CUSTOMER_CDM",
    )
    second = SourceAssetDefinition(
        source_id="source.customer_alias",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system="CUSTOMER_CDM",
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(source_assets=(first, second))

    assert _error_type(exc_info) == "mapping_bundle_duplicate_catalog_asset"


def test_bundle_rejects_two_node_projections_for_same_canonical_mapping() -> None:
    """Project each normalized mapping once to prevent duplicate graph facts."""
    duplicate_customer_node = GraphNodeMapping(
        node_mapping_id="graph.customer_duplicate",
        canonical_mapping_id="canonical.customer",
        label="CustomerDuplicate",
        key_field="customer_key",
        properties=(
            GraphPropertyMapping(
                canonical_field="customer_key",
                graph_property="customer_key",
            ),
        ),
    )

    with pytest.raises(ValidationError) as exc_info:
        _bundle(
            graph_nodes=(
                _customer_node(),
                duplicate_customer_node,
                _account_node(),
            ),
        )

    assert _error_type(exc_info) == "mapping_bundle_duplicate_canonical_node_projection"
