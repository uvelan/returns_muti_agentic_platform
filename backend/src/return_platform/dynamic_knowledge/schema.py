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


class SourceContractStatus(StrEnum):
    """Whether an entity's source field paths have been confirmed against a real document."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class EntitySourceAccess(StrEnum):
    """What an entity is allowed to do against its live source, independent of contract status.

    Capability matrix:

    | Access           | Seed projection      | Connected point read | Full/incremental sync | On-demand sync |
    |------------------|-----------------------|-----------------------|------------------------|----------------|
    | DISABLED         | No                    | No                    | No                     | No             |
    | SEED_ONLY        | Yes                   | No                    | No                     | No             |
    | CONNECTED_READ   | Optional test seed    | Yes                   | No                     | No             |
    | CONNECTED_SYNC   | Optional test seed    | Yes                   | Yes                    | Yes            |
    """

    DISABLED = "DISABLED"
    SEED_ONLY = "SEED_ONLY"
    CONNECTED_READ = "CONNECTED_READ"
    CONNECTED_SYNC = "CONNECTED_SYNC"


class RelationshipSourceAccess(StrEnum):
    """Access level for one relationship projection; independently capped by its endpoints."""

    DISABLED = "DISABLED"
    SEED_ONLY = "SEED_ONLY"
    CONNECTED_SYNC = "CONNECTED_SYNC"


#: For a pair of endpoint EntitySourceAccess values, the maximum RelationshipSourceAccess
#: a relationship between them may declare. Order-independent (checked both ways).
_ENTITY_ACCESS_RANK: dict[EntitySourceAccess, int] = {
    EntitySourceAccess.DISABLED: 0,
    EntitySourceAccess.SEED_ONLY: 1,
    EntitySourceAccess.CONNECTED_READ: 2,
    EntitySourceAccess.CONNECTED_SYNC: 3,
}


_RELATIONSHIP_ACCESS_RANK: dict[RelationshipSourceAccess, int] = {
    RelationshipSourceAccess.DISABLED: 0,
    RelationshipSourceAccess.SEED_ONLY: 1,
    RelationshipSourceAccess.CONNECTED_SYNC: 2,
}


def maximum_relationship_access(
    source_access: EntitySourceAccess, target_access: EntitySourceAccess
) -> RelationshipSourceAccess:
    """The ceiling a relationship's access may not exceed, given its two endpoints."""

    weakest = min(source_access, target_access, key=lambda value: _ENTITY_ACCESS_RANK[value])
    if weakest is EntitySourceAccess.DISABLED:
        return RelationshipSourceAccess.DISABLED
    if weakest is EntitySourceAccess.SEED_ONLY:
        return RelationshipSourceAccess.SEED_ONLY
    if weakest is EntitySourceAccess.CONNECTED_READ:
        return RelationshipSourceAccess.SEED_ONLY
    return RelationshipSourceAccess.CONNECTED_SYNC


class EntityDeletionPolicy(StrEnum):
    """How a source-side removal is reflected in the graph for one entity."""

    HARD_DELETE = "HARD_DELETE"
    TOMBSTONE = "TOMBSTONE"
    DETACH_ONLY = "DETACH_ONLY"


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


class PathOrigin(StrEnum):
    """Where a field's value is read from during a nested explosion (see EntityDefinition.explode)."""

    ROOT_DOCUMENT = "ROOT_DOCUMENT"
    CURRENT_RECORD = "CURRENT_RECORD"
    PARENT_RECORD = "PARENT_RECORD"


class DeriveOperation(StrEnum):
    """Allowlisted transformations available to a derived field. No others are supported."""

    SPLIT_PART = "SPLIT_PART"
    CONTACT_LOOKUP_DIGEST = "CONTACT_LOOKUP_DIGEST"
    COALESCE = "COALESCE"


class ContactDigestKind(StrEnum):
    """Which contact_evidence.py normalization/HMAC domain-separation rule applies."""

    PHONE = "PHONE"
    EMAIL = "EMAIL"


class FieldDerivation(BaseModel):
    """Compute a field's value from other already-extracted fields, never from a raw source path.

    CONTACT_LOOKUP_DIGEST never carries the HMAC secret itself -- only a
    vault:// key_reference, resolved once per sync run by the caller and
    supplied to the extractor as an already-resolved value, the same
    never-log-the-secret posture as KeyResolution's DETERMINISTIC_HMAC.

    SPLIT_PART and CONTACT_LOOKUP_DIGEST take one source_field; COALESCE takes
    an ordered list of candidate fields and resolves to the first non-null one
    -- the two shapes are mutually exclusive per operation, never combined.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: DeriveOperation
    source_field: Identifier | None = None
    fields: tuple[Identifier, ...] = ()
    delimiter: str | None = Field(default=None, min_length=1)
    index: int | None = Field(default=None, ge=0)
    contact_kind: ContactDigestKind | None = None
    key_reference: str | None = None
    key_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> FieldDerivation:
        if self.operation is DeriveOperation.COALESCE:
            if self.source_field is not None:
                raise ValueError("COALESCE derive uses fields, not source_field")
            if len(self.fields) < 2:
                raise ValueError("COALESCE derive requires at least two candidate fields")
            return self
        if self.fields:
            raise ValueError(f"{self.operation!r} derive uses source_field, not fields")
        if self.source_field is None:
            raise ValueError(f"{self.operation!r} derive requires source_field")
        if self.operation is DeriveOperation.SPLIT_PART:
            if self.delimiter is None or self.index is None:
                raise ValueError("SPLIT_PART derive requires delimiter and index")
        else:
            if self.contact_kind is None:
                raise ValueError("CONTACT_LOOKUP_DIGEST derive requires contact_kind")
            if not self.key_reference or not self.key_reference.startswith("vault://"):
                raise ValueError(
                    "CONTACT_LOOKUP_DIGEST derive requires a vault:// key_reference; "
                    "the HMAC secret itself must never appear in configuration"
                )
            if self.key_version is None:
                raise ValueError("CONTACT_LOOKUP_DIGEST derive requires key_version")
        return self

    @property
    def referenced_fields(self) -> tuple[str, ...]:
        """Every sibling field this derivation reads, regardless of operation shape."""

        if self.operation is DeriveOperation.COALESCE:
            return self.fields
        assert self.source_field is not None
        return (self.source_field,)


class FieldDefinition(BaseModel):
    """Logical field mapped to a configured physical path (or derivation) and graph property."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: Identifier
    description: str = ""
    physical_path: tuple[str, ...] | None = None
    path_origin: PathOrigin = PathOrigin.CURRENT_RECORD
    derive: FieldDerivation | None = None
    graph_property: GraphIdentifier
    data_type: FieldType
    nullable: bool = True
    capabilities: FieldCapabilities = Field(default_factory=FieldCapabilities)
    permissions: FieldPermissions = Field(default_factory=FieldPermissions)

    @model_validator(mode="after")
    def validate_path(self) -> FieldDefinition:
        if (self.physical_path is None) == (self.derive is None):
            raise ValueError(
                f"field {self.field_id!r} must set exactly one of physical_path or derive"
            )
        if self.physical_path is not None and (
            not self.physical_path or any(not segment.strip() for segment in self.physical_path)
        ):
            raise ValueError("physical_path must contain non-empty segments")
        return self


class WhereSelector(BaseModel):
    """Restrict an exploded entity's records to those where a raw source path equals a literal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    physical_path: tuple[str, ...]
    equals: str

    @model_validator(mode="after")
    def validate_path(self) -> WhereSelector:
        if not self.physical_path or any(not segment.strip() for segment in self.physical_path):
            raise ValueError("physical_path must contain non-empty segments")
        return self


class KeyResolutionStrategy(StrEnum):
    """How an entity's key is derived when the source has no obviously-stable identifier field."""

    SOURCE_FIELD = "SOURCE_FIELD"
    DETERMINISTIC_HMAC = "DETERMINISTIC_HMAC"


class KeyResolution(BaseModel):
    """Configuration-owned identity derivation; never a plain unkeyed hash of raw source values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: KeyResolutionStrategy
    source_field: Identifier | None = None
    fields: tuple[Identifier, ...] = ()
    key_reference: str | None = None
    key_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_strategy_fields(self) -> KeyResolution:
        if self.strategy is KeyResolutionStrategy.SOURCE_FIELD:
            if self.source_field is None:
                raise ValueError("SOURCE_FIELD key_resolution requires source_field")
        else:
            if not self.fields:
                raise ValueError("DETERMINISTIC_HMAC key_resolution requires at least one field")
            if not self.key_reference or not self.key_reference.startswith("vault://"):
                raise ValueError(
                    "DETERMINISTIC_HMAC key_resolution requires a vault:// key_reference; "
                    "the HMAC secret itself must never appear in configuration"
                )
            if self.key_version is None:
                raise ValueError("DETERMINISTIC_HMAC key_resolution requires key_version")
        return self


class OwnershipMode(StrEnum):
    """How an entity's identity lifecycle is reconciled against its owning
    source document(s). REPLACE_CHILD_SET is the only mode today: an entity
    exploded from a parent document's array, whose current membership is
    exactly the array's current contents on every COMPLETE_SOURCE_DOCUMENT
    read -- see ProjectionOwnership."""

    REPLACE_CHILD_SET = "REPLACE_CHILD_SET"


class OwnershipOwnerIdentity(StrEnum):
    """What identifies one "owner" of a business node for ownership counting.
    SOURCE_DOCUMENT: one owner per distinct (source_asset_id, source_identity)
    parent document -- the only strategy implemented today."""

    SOURCE_DOCUMENT = "SOURCE_DOCUMENT"


class OwnershipPolicy(BaseModel):
    """Configuration-owned replace-child-set reconciliation policy.

    Never applied from a PARTIAL_TARGETED_READ -- only a COMPLETE_SOURCE_DOCUMENT
    read may conclude a previously-seen child is now absent (see
    ProjectionReadScope). Enforcement of that boundary lives in the writer
    orchestration that calls reconcile_child_ownership, not here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: OwnershipMode = OwnershipMode.REPLACE_CHILD_SET
    owner_identity: OwnershipOwnerIdentity = OwnershipOwnerIdentity.SOURCE_DOCUMENT


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
    source_contract_status: SourceContractStatus = SourceContractStatus.VERIFIED
    source_access: EntitySourceAccess = EntitySourceAccess.CONNECTED_SYNC
    deletion_policy: EntityDeletionPolicy = EntityDeletionPolicy.HARD_DELETE
    record_path: tuple[str, ...] = ()
    explode: bool = False
    where: tuple[WhereSelector, ...] = ()
    distinct: bool = False
    key_resolution: KeyResolution | None = None
    ownership_policy: OwnershipPolicy | None = None

    @model_validator(mode="after")
    def validate_references(self) -> EntityDefinition:
        for key, field in self.fields.items():
            if key != field.field_id:
                raise ValueError(
                    f"field map key {key!r} does not match field_id {field.field_id!r}"
                )
        missing_keys = set(self.natural_key).difference(self.fields)
        if missing_keys:
            raise ValueError(f"unknown natural-key fields: {sorted(missing_keys)}")
        for key, anchor in self.strong_anchors.items():
            if key != anchor.anchor_id:
                raise ValueError(
                    f"anchor map key {key!r} does not match anchor_id {anchor.anchor_id!r}"
                )
            missing = {item.field_id for item in anchor.fields}.difference(self.fields)
            if missing:
                raise ValueError(
                    f"anchor {anchor.anchor_id!r} references unknown fields: {sorted(missing)}"
                )
        if self.source_contract_status is SourceContractStatus.UNVERIFIED and self.source_access in {
            EntitySourceAccess.CONNECTED_READ,
            EntitySourceAccess.CONNECTED_SYNC,
        }:
            raise ValueError(
                f"entity {self.entity_id!r} has an UNVERIFIED source contract and cannot "
                f"declare source_access {self.source_access!r}; use SEED_ONLY or DISABLED"
            )
        if self.explode and not self.record_path:
            raise ValueError(f"entity {self.entity_id!r} sets explode=True but no record_path")
        if self.record_path and any(not segment.strip() for segment in self.record_path):
            raise ValueError(f"entity {self.entity_id!r} has an empty record_path segment")
        if self.ownership_policy is not None and not self.explode:
            raise ValueError(
                f"entity {self.entity_id!r} declares an ownership_policy but is not exploded "
                "from a parent document -- REPLACE_CHILD_SET only applies to exploded children"
            )
        for field_id, field in self.fields.items():
            if field.derive is None:
                continue
            for source in field.derive.referenced_fields:
                source_field = self.fields.get(source)
                if source_field is None:
                    raise ValueError(
                        f"field {field_id!r} derives from unknown field {source!r}"
                    )
                if source == field_id:
                    raise ValueError(f"field {field_id!r} cannot derive from itself")
                if source_field.derive is not None:
                    raise ValueError(
                        f"field {field_id!r} derives from {source!r}, which is itself derived; "
                        "chained derivations are not supported"
                    )
        if self.key_resolution is not None:
            resolution = self.key_resolution
            if resolution.strategy is KeyResolutionStrategy.SOURCE_FIELD:
                if resolution.source_field not in self.fields:
                    raise ValueError(
                        f"entity {self.entity_id!r} key_resolution references unknown field "
                        f"{resolution.source_field!r}"
                    )
            else:
                missing = set(resolution.fields).difference(self.fields)
                if missing:
                    raise ValueError(
                        f"entity {self.entity_id!r} key_resolution references unknown fields: "
                        f"{sorted(missing)}"
                    )
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
        if not self.object_ref or any(
            not key.strip() or not value.strip() for key, value in self.object_ref.items()
        ):
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


class RelationshipCardinality(StrEnum):
    """Declared shape of one relationship projection, enforced at projection time.

    A violation (e.g. more matched targets per source than declared) fails
    the projection loudly rather than silently truncating or exploding into
    an unbounded number of edges.
    """

    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"


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
    access: RelationshipSourceAccess = RelationshipSourceAccess.CONNECTED_SYNC
    cardinality: RelationshipCardinality = RelationshipCardinality.MANY_TO_MANY
    maximum_targets_per_source: int | None = Field(default=None, ge=1)
    maximum_sources_per_target: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_cardinality(self) -> RelationshipProjection:
        if (
            self.cardinality in {RelationshipCardinality.ONE_TO_ONE, RelationshipCardinality.MANY_TO_ONE}
            and self.maximum_targets_per_source not in (None, 1)
        ):
            raise ValueError(
                f"relationship {self.relationship_id!r} declares {self.cardinality!r} but "
                f"maximum_targets_per_source={self.maximum_targets_per_source!r} allows more than one"
            )
        if (
            self.cardinality in {RelationshipCardinality.ONE_TO_ONE, RelationshipCardinality.ONE_TO_MANY}
            and self.maximum_sources_per_target not in (None, 1)
        ):
            raise ValueError(
                f"relationship {self.relationship_id!r} declares {self.cardinality!r} but "
                f"maximum_sources_per_target={self.maximum_sources_per_target!r} allows more than one"
            )
        return self


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
    max_clarifications: int = Field(default=3, ge=0, le=10)
    max_replans: int = Field(default=2, ge=0, le=10)
    max_targeted_syncs_per_turn: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def require_standard_models(self) -> AgentPolicy:
        if not self.standard_model_refs or any(
            not item.strip() for item in self.standard_model_refs
        ):
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
            if (
                source.incremental_cursor_field
                and source.incremental_cursor_field not in entity.fields
            ):
                raise ValueError(
                    f"source {source.source_asset_id!r} incremental cursor is not defined on entity {entity.entity_id!r}"
                )
        for key, node in self.graph.nodes.items():
            if key != node.projection_id:
                raise ValueError(f"node map key {key!r} does not match projection_id")
            node_entity = self.entities.get(node.entity_id)
            if node_entity is None:
                raise ValueError(f"node {node.projection_id!r} references unknown entity")
            unknown = set(node.key_fields + node.property_fields).difference(node_entity.fields)
            if unknown:
                raise ValueError(
                    f"node {node.projection_id!r} references unknown fields: {sorted(unknown)}"
                )
        node_entities = {node.entity_id for node in self.graph.nodes.values()}
        if len(node_entities) != len(self.graph.nodes):
            raise ValueError(
                "only one node projection per entity is supported in this compiler version"
            )
        for key, relationship in self.graph.relationships.items():
            if key != relationship.relationship_id:
                raise ValueError(f"relationship map key {key!r} does not match relationship_id")
            for entity_id, field_ids in (
                (relationship.source_entity_id, relationship.source_match_fields),
                (relationship.target_entity_id, relationship.target_match_fields),
            ):
                relationship_entity = self.entities.get(entity_id)
                if relationship_entity is None or entity_id not in node_entities:
                    raise ValueError(
                        f"relationship {relationship.relationship_id!r} references unprojected entity"
                    )
                unknown = set(field_ids).difference(relationship_entity.fields)
                if unknown:
                    raise ValueError(
                        f"relationship {relationship.relationship_id!r} references unknown fields: {sorted(unknown)}"
                    )
            source_entity_access = self.entities[relationship.source_entity_id].source_access
            target_entity_access = self.entities[relationship.target_entity_id].source_access
            ceiling = maximum_relationship_access(source_entity_access, target_entity_access)
            if _RELATIONSHIP_ACCESS_RANK[relationship.access] > _RELATIONSHIP_ACCESS_RANK[ceiling]:
                raise ValueError(
                    f"relationship {relationship.relationship_id!r} declares access "
                    f"{relationship.access!r}, which exceeds the maximum {ceiling!r} allowed by "
                    f"its endpoints ({relationship.source_entity_id!r}={source_entity_access!r}, "
                    f"{relationship.target_entity_id!r}={target_entity_access!r})"
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
                raise ValueError(
                    f"agent {policy.agent_id!r} references unknown entities: {sorted(unknown)}"
                )
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
