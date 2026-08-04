"""Configuration-owned external schema and graph projection contracts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
GraphIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    ),
]


class ConnectorType(StrEnum):
    """Supported infrastructure connectors."""

    MONGODB = "MONGODB"
    MSSQL = "MSSQL"
    POSTGRESQL = "POSTGRESQL"
    NEO4J = "NEO4J"


class RuntimeMode(StrEnum):
    """Runtime connectivity mode."""

    KNOWLEDGE_ONLY = "KNOWLEDGE_ONLY"
    CONNECTED_READ = "CONNECTED_READ"
    CONNECTED_SYNC = "CONNECTED_SYNC"


class FieldType(StrEnum):
    """Portable field types understood by validators and query compilers."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    DATETIME = "DATETIME"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"


class FieldCapabilities(BaseModel):
    """Operations explicitly enabled for a logical field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    searchable: bool = False
    filterable: bool = False
    distinct: bool = False
    aggregatable: bool = False
    displayable: bool = False
    on_demand_sync_anchor: bool = False
    operators: frozenset[str] = frozenset()
    aggregations: frozenset[str] = frozenset()


class FieldPermissions(BaseModel):
    """Role-based field permissions; empty sets deny and ``*`` explicitly allows all roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    searchable_by: frozenset[str] = frozenset()
    displayable_by: frozenset[str] = frozenset()
    on_demand_sync_by: frozenset[str] = frozenset()
    masking: str | None = None


class FieldDefinition(BaseModel):
    """Logical field mapped to a configured physical path and graph property."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: Identifier
    description: str = ""
    physical_path: tuple[str, ...]
    graph_property: GraphIdentifier
    data_type: FieldType
    nullable: bool = True
    capabilities: FieldCapabilities = Field(default_factory=FieldCapabilities)
    permissions: FieldPermissions = Field(default_factory=FieldPermissions)

    @model_validator(mode="after")
    def validate_path(self) -> FieldDefinition:
        if not self.physical_path or any(not segment.strip() for segment in self.physical_path):
            raise ValueError("physical_path must contain non-empty segments")
        return self


class AnchorFieldDefinition(BaseModel):
    """One field/operator requirement in a strong-anchor definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: Identifier
    allowed_operators: frozenset[str]
    required: bool = True


class StrongAnchorDefinition(BaseModel):
    """Configuration-owned selective source lookup contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: Identifier
    fields: tuple[AnchorFieldDefinition, ...]
    minimum_fields_present: int = Field(ge=1)
    maximum_expected_matches: int = Field(ge=1)
    on_demand_sync_allowed: bool = True

    @model_validator(mode="after")
    def validate_anchor(self) -> StrongAnchorDefinition:
        field_ids = [item.field_id for item in self.fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("strong anchor contains duplicate fields")
        if self.minimum_fields_present > len(self.fields):
            raise ValueError("minimum_fields_present exceeds configured fields")
        required = sum(1 for item in self.fields if item.required)
        if self.minimum_fields_present < required:
            raise ValueError("minimum_fields_present cannot be lower than required field count")
        return self


class EntityDefinition(BaseModel):
    """Dynamic external entity; business names exist only in configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: Identifier
    description: str = ""
    source_asset_id: Identifier
    fields: dict[str, FieldDefinition]
    natural_key: tuple[Identifier, ...]
    strong_anchors: dict[str, StrongAnchorDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> EntityDefinition:
        for key, field in self.fields.items():
            if key != field.field_id:
                raise ValueError(f"field map key {key!r} does not match field_id {field.field_id!r}")
        missing_keys = set(self.natural_key).difference(self.fields)
        if missing_keys:
            raise ValueError(f"unknown natural-key fields: {sorted(missing_keys)}")
        for key, anchor in self.strong_anchors.items():
            if key != anchor.anchor_id:
                raise ValueError(f"anchor map key {key!r} does not match anchor_id {anchor.anchor_id!r}")
            missing = {item.field_id for item in anchor.fields}.difference(self.fields)
            if missing:
                raise ValueError(f"anchor {anchor.anchor_id!r} references unknown fields: {sorted(missing)}")
        return self


class SourceAssetDefinition(BaseModel):
    """Configured external object with no code-owned business columns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_asset_id: Identifier
    connector_type: ConnectorType
    connection_ref: str
    object_ref: dict[str, str]
    incremental_cursor_field: Identifier | None = None

    @model_validator(mode="after")
    def validate_object_ref(self) -> SourceAssetDefinition:
        if not self.object_ref or any(not key.strip() or not value.strip() for key, value in self.object_ref.items()):
            raise ValueError("object_ref must contain non-empty keys and values")
        return self


class NodeProjection(BaseModel):
    """Schema-owned projection from one entity into one graph node label."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    projection_id: Identifier
    entity_id: Identifier
    label: GraphIdentifier
    key_fields: tuple[Identifier, ...]
    property_fields: tuple[Identifier, ...]


class RelationshipProjection(BaseModel):
    """Schema-owned relationship projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: Identifier
    relationship_type: GraphIdentifier
    source_entity_id: Identifier
    target_entity_id: Identifier
    source_match_fields: tuple[Identifier, ...]
    target_match_fields: tuple[Identifier, ...]
    property_fields: tuple[Identifier, ...] = ()


class GraphDefinition(BaseModel):
    """Business graph projection and isolation boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: Identifier
    nodes: dict[str, NodeProjection]
    relationships: dict[str, RelationshipProjection] = Field(default_factory=dict)
    constraints: tuple[dict[str, Any], ...] = ()
    indexes: tuple[dict[str, Any], ...] = ()


class AgentPolicy(BaseModel):
    """Independent agent capability and safety policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: Identifier
    task_queue: Identifier
    allowed_business_capabilities: frozenset[str]
    allowed_roles: frozenset[str]
    allowed_entity_ids: frozenset[str]
    standard_model_refs: tuple[str, ...]
    max_reasoning_steps: int = Field(default=8, ge=1, le=32)
    max_graph_queries_per_turn: int = Field(default=12, ge=1, le=64)
    max_correction_attempts: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def require_standard_models(self) -> AgentPolicy:
        if not self.standard_model_refs or any(not item.strip() for item in self.standard_model_refs):
            raise ValueError("at least one standard reasoning model is required")
        return self


class ActiveSchema(BaseModel):
    """Immutable active configuration release used by sync and every agent turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    configuration_release_id: Identifier
    configuration_checksum: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    release_status: str = Field(pattern=r"^ACTIVE$")
    approved_by: Identifier
    approved_at: datetime
    schema_version: Identifier
    policy_version: Identifier
    prompt_version: Identifier
    compiler_version: Identifier
    runtime_mode: RuntimeMode
    sources: dict[str, SourceAssetDefinition]
    entities: dict[str, EntityDefinition]
    graph: GraphDefinition
    agent_policies: dict[str, AgentPolicy]

    @model_validator(mode="after")
    def validate_cross_references(self) -> ActiveSchema:
        for key, source in self.sources.items():
            if key != source.source_asset_id:
                raise ValueError(f"source map key {key!r} does not match source_asset_id")
        for key, entity in self.entities.items():
            if key != entity.entity_id:
                raise ValueError(f"entity map key {key!r} does not match entity_id")
            if entity.source_asset_id not in self.sources:
                raise ValueError(f"entity {entity.entity_id!r} references unknown source asset")
            source = self.sources[entity.source_asset_id]
            if source.incremental_cursor_field and source.incremental_cursor_field not in entity.fields:
                raise ValueError(
                    f"source {source.source_asset_id!r} incremental cursor is not defined on entity {entity.entity_id!r}"
                )
        for key, node in self.graph.nodes.items():
            if key != node.projection_id:
                raise ValueError(f"node map key {key!r} does not match projection_id")
            entity = self.entities.get(node.entity_id)
            if entity is None:
                raise ValueError(f"node {node.projection_id!r} references unknown entity")
            unknown = set(node.key_fields + node.property_fields).difference(entity.fields)
            if unknown:
                raise ValueError(f"node {node.projection_id!r} references unknown fields: {sorted(unknown)}")
        node_entities = {node.entity_id for node in self.graph.nodes.values()}
        if len(node_entities) != len(self.graph.nodes):
            raise ValueError("only one node projection per entity is supported in this compiler version")
        for key, relationship in self.graph.relationships.items():
            if key != relationship.relationship_id:
                raise ValueError(f"relationship map key {key!r} does not match relationship_id")
            for entity_id, field_ids in (
                (relationship.source_entity_id, relationship.source_match_fields),
                (relationship.target_entity_id, relationship.target_match_fields),
            ):
                entity = self.entities.get(entity_id)
                if entity is None or entity_id not in node_entities:
                    raise ValueError(f"relationship {relationship.relationship_id!r} references unprojected entity")
                unknown = set(field_ids).difference(entity.fields)
                if unknown:
                    raise ValueError(
                        f"relationship {relationship.relationship_id!r} references unknown fields: {sorted(unknown)}"
                    )
            property_unknown = set(relationship.property_fields).difference(
                self.entities[relationship.source_entity_id].fields
            )
            if property_unknown:
                raise ValueError(
                    f"relationship {relationship.relationship_id!r} references unknown property fields: "
                    f"{sorted(property_unknown)}"
                )
        for key, policy in self.agent_policies.items():
            if key != policy.agent_id:
                raise ValueError(f"agent policy map key {key!r} does not match agent_id")
            unknown = set(policy.allowed_entity_ids).difference(self.entities)
            if unknown:
                raise ValueError(f"agent {policy.agent_id!r} references unknown entities: {sorted(unknown)}")
        return self

    def entity_node(self, entity_id: str) -> NodeProjection:
        """Return the active node projection for a logical entity."""

        for node in self.graph.nodes.values():
            if node.entity_id == entity_id:
                return node
        raise KeyError(f"entity {entity_id!r} has no graph projection")


def validate_graph_identifier(value: str) -> str:
    """Validate an identifier independently of Pydantic model construction."""

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"unsafe graph identifier: {value!r}")
    return value
