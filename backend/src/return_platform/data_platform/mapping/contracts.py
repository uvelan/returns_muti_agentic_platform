"""Strict versioned source, canonical, graph, and pipeline mapping contracts."""

from collections import Counter
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Never, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    IdentityQuality,
    VersionReference,
)

__all__ = [
    "CanonicalEntityMapping",
    "CanonicalEntityType",
    "DataPlatformMappingBundle",
    "GraphNodeMapping",
    "GraphPropertyMapping",
    "GraphRelationshipMapping",
    "HandlerName",
    "IdentityMapping",
    "MappingBaseModel",
    "MappingIdentifier",
    "PhysicalFieldMapping",
    "PhysicalPathScope",
    "RelationshipDirection",
    "SourceAssetDefinition",
    "SourceLifecycle",
    "SourceSystemName",
    "SyncPipelineDefinition",
    "SyncStageDefinition",
]

MappingIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$",
    ),
]
"""Stable lowercase dotted identifier for mapping configuration objects."""

SourceSystemName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=2,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    ),
]
"""Allow-listed source-system token used in provenance."""

PhysicalFieldPath = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=512,
        pattern=(
            r"^[A-Za-z_][A-Za-z0-9_-]*(?:\[\])?"
            r"(?:\.[A-Za-z_][A-Za-z0-9_-]*(?:\[\])?)*$"
        ),
    ),
]
"""Allow-listed physical path with optional whole-array traversal markers."""

CanonicalFieldPath = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
    ),
]
"""Canonical snake-case field path."""

HandlerName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
"""Code-owned handler registry key, never an import path or expression."""

GraphLabel = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Za-z0-9_]*$",
    ),
]
"""Allow-listed Neo4j label token."""

GraphPropertyName = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    ),
]
"""Allow-listed Neo4j property token."""

GraphRelationshipType = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    ),
]
"""Allow-listed uppercase Neo4j relationship token."""


def _require_ordered_sequence(value: object) -> object:
    """Reject sets and iterators whose order is not configuration evidence."""
    if not isinstance(value, (list, tuple)):
        _raise_validation_error(
            "mapping_ordered_sequence_required",
            "configuration sequences must be YAML lists or tuples",
        )
    return value


class MappingBaseModel(BaseModel):
    """Immutable configuration model that accepts YAML sequences and enum text."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class SourceLifecycle(StrEnum):
    """Operational lifecycle of a configured physical source asset."""

    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class PhysicalPathScope(StrEnum):
    """Resolution origin for physical field paths in nested source records."""

    RECORD = "RECORD"
    DOCUMENT = "DOCUMENT"


class RelationshipDirection(StrEnum):
    """Direction of the emitted graph edge relative to match endpoints."""

    SOURCE_TO_TARGET = "SOURCE_TO_TARGET"
    TARGET_TO_SOURCE = "TARGET_TO_SOURCE"


class CanonicalEntityType(StrEnum):
    """Code-owned canonical entity types available to mapping profiles."""

    CUSTOMER = "Customer"
    CUSTOMER_ACCOUNT = "CustomerAccount"
    CONTACT_POINT = "ContactPoint"
    ADDRESS = "Address"
    SALES_ORDER = "SalesOrder"
    ORDER_LINE = "OrderLine"
    PRODUCT = "Product"
    WAREHOUSE = "Warehouse"
    WAREHOUSE_PRODUCT = "WarehouseProduct"
    SHIPMENT = "Shipment"
    SHIPMENT_ITEM = "ShipmentItem"
    TRACKING_EVENT = "TrackingEvent"
    CARRIER_TRACKING_REFERENCE = "CarrierTrackingReference"
    RETURN = "Return"
    RETURN_ITEM = "ReturnItem"
    FREIGHT_SHIPMENT = "FreightShipment"
    BAY = "Bay"
    BAY_ASSIGNMENT = "BayAssignment"
    RETURN_SESSION = "ReturnSession"
    AGENT_DECISION = "AgentDecision"
    AUDIT_EVENT = "AuditEvent"
    GRAPH_SYNC_RUN = "GraphSyncRun"
    GRAPH_PROJECTION_EVIDENCE = "GraphProjectionEvidence"


_GRAPH_V1_NODE_ENTITIES = frozenset(
    {
        CanonicalEntityType.CUSTOMER,
        CanonicalEntityType.CUSTOMER_ACCOUNT,
        CanonicalEntityType.SALES_ORDER,
        CanonicalEntityType.ORDER_LINE,
        CanonicalEntityType.PRODUCT,
        CanonicalEntityType.WAREHOUSE,
        CanonicalEntityType.WAREHOUSE_PRODUCT,
        CanonicalEntityType.SHIPMENT,
        CanonicalEntityType.SHIPMENT_ITEM,
        CanonicalEntityType.TRACKING_EVENT,
        CanonicalEntityType.RETURN,
        CanonicalEntityType.RETURN_ITEM,
        CanonicalEntityType.FREIGHT_SHIPMENT,
        CanonicalEntityType.BAY,
        CanonicalEntityType.BAY_ASSIGNMENT,
        CanonicalEntityType.RETURN_SESSION,
    },
)


def _raise_validation_error(error_type: str, message: str) -> Never:
    """Raise a stable mapping-contract validation error."""
    raise PydanticCustomError(error_type, message)


def _reject_duplicates(
    values: tuple[str, ...],
    *,
    error_type: str,
    message: str,
) -> None:
    """Reject duplicate immutable identifiers or paths."""
    if any(count > 1 for count in Counter(values).values()):
        _raise_validation_error(error_type, message)


def _index_unique_models[T: MappingBaseModel](
    models: tuple[T, ...],
    *,
    identifier: Callable[[T], str],
    error_type: str,
) -> dict[str, T]:
    """Build a stable identifier index while rejecting duplicates."""
    result: dict[str, T] = {}
    for model in models:
        model_id = identifier(model)
        if model_id in result:
            _raise_validation_error(
                error_type,
                "duplicate configuration identifiers are not allowed",
            )
        result[model_id] = model
    return result


class SourceAssetDefinition(MappingBaseModel):
    """Mapping-profile binding to one approved governance catalog asset."""

    source_id: MappingIdentifier
    catalog_asset_id: MappingIdentifier
    source_system: SourceSystemName
    lifecycle: SourceLifecycle = SourceLifecycle.ACTIVE
    required_for_sync: StrictBool = True


class PhysicalFieldMapping(MappingBaseModel):
    """Ordered physical aliases feeding one canonical field.

    ``RECORD`` paths resolve relative to the record selected by
    ``CanonicalEntityMapping.record_path``. When no record path is configured,
    the source document itself is the current record. ``DOCUMENT`` paths always
    resolve from the physical source document root, allowing nested records to
    reference stable parent identity without inventing denormalized fields.
    """

    canonical_field: CanonicalFieldPath
    path_scope: PhysicalPathScope = PhysicalPathScope.RECORD
    source_paths: Annotated[
        tuple[PhysicalFieldPath, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=16),
    ]
    required: StrictBool = True
    handler: HandlerName | None = None

    @model_validator(mode="after")
    def validate_aliases(self) -> Self:
        """Reject repeated aliases while preserving explicit precedence order."""
        _reject_duplicates(
            self.source_paths,
            error_type="physical_field_mapping_duplicate_source_path",
            message="source_paths must not contain duplicates",
        )
        return self


class IdentityMapping(MappingBaseModel):
    """Code-owned canonical identity handler and its normalized inputs."""

    key_field: CanonicalFieldPath
    handler: HandlerName
    component_fields: Annotated[
        tuple[CanonicalFieldPath, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=32),
    ]
    identity_quality: IdentityQuality

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        """Reject duplicate or recursively self-referential key components."""
        _reject_duplicates(
            self.component_fields,
            error_type="identity_mapping_duplicate_component",
            message="component_fields must not contain duplicates",
        )
        if self.key_field in self.component_fields:
            _raise_validation_error(
                "identity_mapping_key_is_component",
                "key_field cannot be one of its own component_fields",
            )
        return self


class CanonicalEntityMapping(MappingBaseModel):
    """Versioned source-record mapping into one canonical entity contract."""

    mapping_id: MappingIdentifier
    version: VersionReference
    source_id: MappingIdentifier
    entity_type: CanonicalEntityType
    record_path: PhysicalFieldPath | None = None
    fields: Annotated[
        tuple[PhysicalFieldMapping, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=256),
    ]
    identity: IdentityMapping
    depends_on: Annotated[
        tuple[MappingIdentifier, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=64),
    ] = ()

    @model_validator(mode="after")
    def validate_fields_and_dependencies(self) -> Self:
        """Validate field uniqueness, identity inputs, and dependency shape."""
        canonical_fields = tuple(field.canonical_field for field in self.fields)
        _reject_duplicates(
            canonical_fields,
            error_type="canonical_entity_mapping_duplicate_field",
            message="canonical fields must be mapped at most once",
        )
        _reject_duplicates(
            self.depends_on,
            error_type="canonical_entity_mapping_duplicate_dependency",
            message="depends_on must not contain duplicates",
        )
        if self.mapping_id in self.depends_on:
            _raise_validation_error(
                "canonical_entity_mapping_self_dependency",
                "a canonical mapping cannot depend on itself",
            )

        available_fields = frozenset(canonical_fields)
        missing_components = tuple(
            component
            for component in self.identity.component_fields
            if component not in available_fields
        )
        if missing_components:
            _raise_validation_error(
                "canonical_entity_mapping_identity_component_missing",
                "every identity component must have a physical field mapping",
            )
        return self


class GraphPropertyMapping(MappingBaseModel):
    """Canonical-to-Neo4j property mapping without arbitrary Cypher."""

    canonical_field: CanonicalFieldPath
    graph_property: GraphPropertyName


class GraphNodeMapping(MappingBaseModel):
    """Neo4j node projection metadata for one canonical mapping."""

    node_mapping_id: MappingIdentifier
    canonical_mapping_id: MappingIdentifier
    label: GraphLabel
    key_field: CanonicalFieldPath
    properties: Annotated[
        tuple[GraphPropertyMapping, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=256),
    ]

    @model_validator(mode="after")
    def validate_properties(self) -> Self:
        """Reject duplicate properties and require projection of the node key."""
        canonical_fields = tuple(
            property_mapping.canonical_field for property_mapping in self.properties
        )
        graph_properties = tuple(
            property_mapping.graph_property for property_mapping in self.properties
        )
        _reject_duplicates(
            canonical_fields,
            error_type="graph_node_mapping_duplicate_canonical_field",
            message="a canonical field can map to only one node property",
        )
        _reject_duplicates(
            graph_properties,
            error_type="graph_node_mapping_duplicate_graph_property",
            message="graph property names must be unique within a node mapping",
        )
        if self.key_field not in canonical_fields:
            _raise_validation_error(
                "graph_node_mapping_key_property_missing",
                "key_field must be projected as a graph property",
            )
        return self


class GraphRelationshipMapping(MappingBaseModel):
    """Parameterized relationship projection between two configured nodes.

    ``source_node_mapping_id`` identifies the node holding
    ``source_reference_field``. ``target_node_mapping_id`` identifies the node
    whose constrained ``target_key_field`` is referenced. ``direction`` then
    determines the emitted Neo4j edge direction without changing how the
    foreign-key match is resolved.
    """

    relationship_mapping_id: MappingIdentifier
    relationship_type: GraphRelationshipType
    source_node_mapping_id: MappingIdentifier
    target_node_mapping_id: MappingIdentifier
    source_reference_field: CanonicalFieldPath
    target_key_field: CanonicalFieldPath
    direction: RelationshipDirection = RelationshipDirection.SOURCE_TO_TARGET
    required: StrictBool = True

    @property
    def edge_source_node_mapping_id(self) -> str:
        """Return the configured source endpoint of the emitted edge."""
        if self.direction is RelationshipDirection.SOURCE_TO_TARGET:
            return self.source_node_mapping_id
        return self.target_node_mapping_id

    @property
    def edge_target_node_mapping_id(self) -> str:
        """Return the configured target endpoint of the emitted edge."""
        if self.direction is RelationshipDirection.SOURCE_TO_TARGET:
            return self.target_node_mapping_id
        return self.source_node_mapping_id

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        """Reject accidental self-endpoint configuration."""
        if self.source_node_mapping_id == self.target_node_mapping_id:
            _raise_validation_error(
                "graph_relationship_mapping_same_endpoint",
                "source and target node mappings must differ",
            )
        return self


class SyncStageDefinition(MappingBaseModel):
    """One ordered, idempotent stage in a code-owned synchronization pipeline."""

    stage_id: MappingIdentifier
    canonical_mapping_ids: Annotated[
        tuple[MappingIdentifier, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=128),
    ] = ()
    node_mapping_ids: Annotated[
        tuple[MappingIdentifier, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=128),
    ] = ()
    relationship_mapping_ids: Annotated[
        tuple[MappingIdentifier, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=128),
    ] = ()
    depends_on: Annotated[
        tuple[MappingIdentifier, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=64),
    ] = ()

    @model_validator(mode="after")
    def validate_stage(self) -> Self:
        """Require work and reject duplicate or self-referential stage inputs."""
        if not (
            self.canonical_mapping_ids or self.node_mapping_ids or self.relationship_mapping_ids
        ):
            _raise_validation_error(
                "sync_stage_empty",
                "a sync stage must reference at least one execution mapping",
            )

        for values, error_type in (
            (
                self.canonical_mapping_ids,
                "sync_stage_duplicate_canonical_mapping",
            ),
            (self.node_mapping_ids, "sync_stage_duplicate_node_mapping"),
            (
                self.relationship_mapping_ids,
                "sync_stage_duplicate_relationship_mapping",
            ),
            (self.depends_on, "sync_stage_duplicate_dependency"),
        ):
            _reject_duplicates(
                values,
                error_type=error_type,
                message="stage references must not contain duplicates",
            )

        if self.stage_id in self.depends_on:
            _raise_validation_error(
                "sync_stage_self_dependency",
                "a sync stage cannot depend on itself",
            )
        return self


class SyncPipelineDefinition(MappingBaseModel):
    """Versioned ordered synchronization pipeline definition."""

    pipeline_id: MappingIdentifier
    version: VersionReference
    stages: Annotated[
        tuple[SyncStageDefinition, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=256),
    ]

    @model_validator(mode="after")
    def validate_stage_order(self) -> Self:
        """Reject duplicate stages, forward dependencies, and repeated work."""
        stage_ids = tuple(stage.stage_id for stage in self.stages)
        _reject_duplicates(
            stage_ids,
            error_type="sync_pipeline_duplicate_stage",
            message="stage_id values must be unique within a pipeline",
        )

        seen_stages: set[str] = set()
        seen_canonical_mappings: set[str] = set()
        seen_node_mappings: set[str] = set()
        seen_relationship_mappings: set[str] = set()

        for stage in self.stages:
            if any(dependency not in seen_stages for dependency in stage.depends_on):
                _raise_validation_error(
                    "sync_pipeline_stage_dependency_order_invalid",
                    "stage dependencies must reference earlier stages",
                )

            for values, seen, error_type in (
                (
                    stage.canonical_mapping_ids,
                    seen_canonical_mappings,
                    "sync_pipeline_canonical_mapping_repeated",
                ),
                (
                    stage.node_mapping_ids,
                    seen_node_mappings,
                    "sync_pipeline_node_mapping_repeated",
                ),
                (
                    stage.relationship_mapping_ids,
                    seen_relationship_mappings,
                    "sync_pipeline_relationship_mapping_repeated",
                ),
            ):
                if any(value in seen for value in values):
                    _raise_validation_error(
                        error_type,
                        "an execution mapping may appear in only one pipeline stage",
                    )
                seen.update(values)

            seen_stages.add(stage.stage_id)
        return self


class DataPlatformMappingBundle(MappingBaseModel):
    """Merged, structurally validated configuration from the versioned files."""

    schema_version: VersionReference
    source_assets: Annotated[
        tuple[SourceAssetDefinition, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=128),
    ]
    canonical_mappings: Annotated[
        tuple[CanonicalEntityMapping, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=512),
    ]
    graph_nodes: Annotated[
        tuple[GraphNodeMapping, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=512),
    ]
    graph_relationships: Annotated[
        tuple[GraphRelationshipMapping, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(max_length=1_024),
    ] = ()
    sync_pipelines: Annotated[
        tuple[SyncPipelineDefinition, ...],
        BeforeValidator(_require_ordered_sequence),
        Field(min_length=1, max_length=64),
    ]

    @model_validator(mode="after")
    def validate_cross_references(self) -> Self:
        """Validate all safe structural references and dependency order."""
        source_index = _index_unique_models(
            self.source_assets,
            identifier=lambda source: source.source_id,
            error_type="mapping_bundle_duplicate_source",
        )
        canonical_index = _index_unique_models(
            self.canonical_mappings,
            identifier=lambda mapping: mapping.mapping_id,
            error_type="mapping_bundle_duplicate_canonical_mapping",
        )
        node_index = _index_unique_models(
            self.graph_nodes,
            identifier=lambda node: node.node_mapping_id,
            error_type="mapping_bundle_duplicate_node_mapping",
        )
        relationship_index = _index_unique_models(
            self.graph_relationships,
            identifier=lambda relationship: relationship.relationship_mapping_id,
            error_type="mapping_bundle_duplicate_relationship_mapping",
        )
        _index_unique_models(
            self.sync_pipelines,
            identifier=lambda pipeline: pipeline.pipeline_id,
            error_type="mapping_bundle_duplicate_pipeline",
        )
        _reject_duplicates(
            tuple(source.catalog_asset_id for source in self.source_assets),
            error_type="mapping_bundle_duplicate_catalog_asset",
            message="a catalog asset may have only one source definition",
        )
        _reject_duplicates(
            tuple(node.canonical_mapping_id for node in self.graph_nodes),
            error_type="mapping_bundle_duplicate_canonical_node_projection",
            message="a canonical mapping may have only one node projection",
        )

        self._validate_canonical_references(source_index, canonical_index)
        self._validate_canonical_dependency_cycles(canonical_index)
        self._validate_graph_nodes(canonical_index, node_index)
        self._validate_graph_relationships(
            canonical_index,
            node_index,
            relationship_index,
        )
        self._validate_pipeline_references(
            canonical_index,
            node_index,
            relationship_index,
        )
        return self

    def _validate_canonical_references(
        self,
        source_index: dict[str, SourceAssetDefinition],
        canonical_index: dict[str, CanonicalEntityMapping],
    ) -> None:
        """Validate source and canonical dependency references."""
        for mapping in canonical_index.values():
            if mapping.source_id not in source_index:
                _raise_validation_error(
                    "mapping_bundle_source_reference_missing",
                    "canonical mapping references an undefined source_id",
                )
            if any(dependency not in canonical_index for dependency in mapping.depends_on):
                _raise_validation_error(
                    "mapping_bundle_canonical_dependency_missing",
                    "canonical mapping references an undefined dependency",
                )

    def _validate_canonical_dependency_cycles(
        self,
        canonical_index: dict[str, CanonicalEntityMapping],
    ) -> None:
        """Reject cyclic canonical mapping dependencies using Kahn ordering."""
        remaining_dependencies = {
            mapping_id: set(mapping.depends_on) for mapping_id, mapping in canonical_index.items()
        }
        ready = sorted(
            mapping_id
            for mapping_id, dependencies in remaining_dependencies.items()
            if not dependencies
        )
        processed: set[str] = set()

        while ready:
            mapping_id = ready.pop(0)
            if mapping_id in processed:
                continue
            processed.add(mapping_id)
            self._release_dependency(
                mapping_id,
                remaining_dependencies,
                ready,
                processed,
            )

        if len(processed) != len(canonical_index):
            _raise_validation_error(
                "mapping_bundle_canonical_dependency_cycle",
                "canonical mapping dependencies must be acyclic",
            )

    @staticmethod
    def _release_dependency(
        mapping_id: str,
        remaining_dependencies: dict[str, set[str]],
        ready: list[str],
        processed: set[str],
    ) -> None:
        """Release one dependency from deterministic Kahn traversal state."""
        for dependent_id, dependencies in remaining_dependencies.items():
            if mapping_id not in dependencies:
                continue
            dependencies.remove(mapping_id)
            if not dependencies and dependent_id not in processed:
                ready.append(dependent_id)
        ready.sort()

    def _validate_graph_nodes(
        self,
        canonical_index: dict[str, CanonicalEntityMapping],
        node_index: dict[str, GraphNodeMapping],
    ) -> None:
        """Validate graph node entities, identities, and property references."""
        for node in node_index.values():
            canonical = canonical_index.get(node.canonical_mapping_id)
            if canonical is None:
                _raise_validation_error(
                    "mapping_bundle_node_canonical_reference_missing",
                    "graph node references an undefined canonical mapping",
                )
            if canonical.entity_type not in _GRAPH_V1_NODE_ENTITIES:
                _raise_validation_error(
                    "mapping_bundle_graph_entity_not_allowed",
                    "canonical entity is excluded from graph model v1",
                )
            if node.key_field != canonical.identity.key_field:
                _raise_validation_error(
                    "mapping_bundle_node_key_mismatch",
                    "graph node key_field must equal canonical identity key_field",
                )

            available_fields = {field.canonical_field for field in canonical.fields}
            available_fields.add(canonical.identity.key_field)
            if any(
                property_mapping.canonical_field not in available_fields
                for property_mapping in node.properties
            ):
                _raise_validation_error(
                    "mapping_bundle_node_property_field_missing",
                    "graph property references an unmapped canonical field",
                )

    def _validate_graph_relationships(
        self,
        canonical_index: dict[str, CanonicalEntityMapping],
        node_index: dict[str, GraphNodeMapping],
        relationship_index: dict[str, GraphRelationshipMapping],
    ) -> None:
        """Validate graph relationship endpoints and safe match fields."""
        for relationship in relationship_index.values():
            source_node = node_index.get(relationship.source_node_mapping_id)
            target_node = node_index.get(relationship.target_node_mapping_id)
            if source_node is None or target_node is None:
                _raise_validation_error(
                    "mapping_bundle_relationship_endpoint_missing",
                    "graph relationship references an undefined endpoint",
                )

            source_mapping = canonical_index[source_node.canonical_mapping_id]
            source_fields = {field.canonical_field for field in source_mapping.fields}
            source_fields.add(source_mapping.identity.key_field)
            if relationship.source_reference_field not in source_fields:
                _raise_validation_error(
                    "mapping_bundle_relationship_source_field_missing",
                    "relationship source_reference_field is not available",
                )
            if relationship.target_key_field != target_node.key_field:
                _raise_validation_error(
                    "mapping_bundle_relationship_target_key_mismatch",
                    "relationship target_key_field must equal target node key_field",
                )

    def _validate_pipeline_references(
        self,
        canonical_index: dict[str, CanonicalEntityMapping],
        node_index: dict[str, GraphNodeMapping],
        relationship_index: dict[str, GraphRelationshipMapping],
    ) -> None:
        """Validate pipeline references and safe execution dependency order."""
        for pipeline in self.sync_pipelines:
            stage_indexes = self._build_pipeline_stage_indexes(
                pipeline,
                canonical_index,
                node_index,
                relationship_index,
            )
            canonical_stage, node_stage, relationship_stage = stage_indexes
            self._validate_pipeline_canonical_order(
                canonical_stage,
                canonical_index,
            )
            self._validate_pipeline_node_order(
                canonical_stage,
                node_stage,
                node_index,
            )
            self._validate_pipeline_relationship_order(
                node_stage,
                relationship_stage,
                relationship_index,
            )

    @staticmethod
    def _build_pipeline_stage_indexes(
        pipeline: SyncPipelineDefinition,
        canonical_index: dict[str, CanonicalEntityMapping],
        node_index: dict[str, GraphNodeMapping],
        relationship_index: dict[str, GraphRelationshipMapping],
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """Resolve pipeline mapping references into deterministic stage indexes."""
        canonical_stage: dict[str, int] = {}
        node_stage: dict[str, int] = {}
        relationship_stage: dict[str, int] = {}

        for stage_number, stage in enumerate(pipeline.stages):
            DataPlatformMappingBundle._index_stage_references(
                stage.canonical_mapping_ids,
                stage_number,
                canonical_index,
                canonical_stage,
                error_type="mapping_bundle_pipeline_canonical_reference_missing",
            )
            DataPlatformMappingBundle._index_stage_references(
                stage.node_mapping_ids,
                stage_number,
                node_index,
                node_stage,
                error_type="mapping_bundle_pipeline_node_reference_missing",
            )
            DataPlatformMappingBundle._index_stage_references(
                stage.relationship_mapping_ids,
                stage_number,
                relationship_index,
                relationship_stage,
                error_type="mapping_bundle_pipeline_relationship_reference_missing",
            )
        return canonical_stage, node_stage, relationship_stage

    @staticmethod
    def _index_stage_references[T: MappingBaseModel](
        identifiers: tuple[str, ...],
        stage_number: int,
        available: dict[str, T],
        result: dict[str, int],
        *,
        error_type: str,
    ) -> None:
        """Index one stage reference group after validating existence."""
        for identifier in identifiers:
            if identifier not in available:
                _raise_validation_error(
                    error_type,
                    "pipeline references an undefined execution mapping",
                )
            result[identifier] = stage_number

    @staticmethod
    def _validate_pipeline_canonical_order(
        canonical_stage: dict[str, int],
        canonical_index: dict[str, CanonicalEntityMapping],
    ) -> None:
        """Require canonical dependencies to execute in earlier stages."""
        for mapping_id, stage_number in canonical_stage.items():
            mapping = canonical_index[mapping_id]
            for dependency in mapping.depends_on:
                dependency_stage = canonical_stage.get(dependency)
                if dependency_stage is None or dependency_stage >= stage_number:
                    _raise_validation_error(
                        "mapping_bundle_pipeline_canonical_order_invalid",
                        "canonical dependencies must execute in earlier stages",
                    )

    @staticmethod
    def _validate_pipeline_node_order(
        canonical_stage: dict[str, int],
        node_stage: dict[str, int],
        node_index: dict[str, GraphNodeMapping],
    ) -> None:
        """Prevent graph node projection before canonical normalization."""
        for node_mapping_id, stage_number in node_stage.items():
            node = node_index[node_mapping_id]
            canonical_stage_number = canonical_stage.get(node.canonical_mapping_id)
            if canonical_stage_number is None or canonical_stage_number > stage_number:
                _raise_validation_error(
                    "mapping_bundle_pipeline_node_order_invalid",
                    "node projection cannot precede canonical normalization",
                )

    @staticmethod
    def _validate_pipeline_relationship_order(
        node_stage: dict[str, int],
        relationship_stage: dict[str, int],
        relationship_index: dict[str, GraphRelationshipMapping],
    ) -> None:
        """Prevent relationship projection before either endpoint node."""
        for relationship_mapping_id, stage_number in relationship_stage.items():
            relationship = relationship_index[relationship_mapping_id]
            source_stage = node_stage.get(relationship.source_node_mapping_id)
            target_stage = node_stage.get(relationship.target_node_mapping_id)
            if (
                source_stage is None
                or target_stage is None
                or source_stage > stage_number
                or target_stage > stage_number
            ):
                _raise_validation_error(
                    "mapping_bundle_pipeline_relationship_order_invalid",
                    "relationship projection cannot precede either endpoint node",
                )
