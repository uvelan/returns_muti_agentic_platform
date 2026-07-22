"""Deterministic in-memory graph projection materialization for Customer data."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Final, Never, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    Customer,
    CustomerAccount,
    GraphProjectionEvidence,
    GraphProjectionStatus,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.canonical.base import IdentityQuality, SourceProvenance
from return_platform.data_platform.mapping.compiler import (
    CompiledCanonicalMappingPlan,
    CompiledGraphNodePlan,
    CompiledGraphPropertyPlan,
    CompiledGraphPropertySource,
    CompiledGraphRelationshipPlan,
    MappingExecutionPlan,
)
from return_platform.data_platform.mapping.contracts import (
    CanonicalEntityType,
    GraphLabel,
    GraphPropertyName,
    GraphRelationshipType,
    MappingIdentifier,
)
from return_platform.data_platform.mapping.normalizer import (
    CustomerNormalizationResult,
    NormalizationRejection,
    NormalizationRejectionCode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CustomerGraphProjectionMaterialization",
    "GraphMaterializationError",
    "GraphMaterializationErrorCode",
    "GraphMaterializationRejectionCode",
    "GraphNodeUpsertParameters",
    "GraphParameterEntry",
    "GraphParameterMap",
    "GraphRelationshipUpsertParameters",
    "materialize_customer_graph_projection",
]

MATERIALIZER_VERSION: Final = "1.0"
_CUSTOMER_MAPPING_ID: Final = "canonical.customer.v1"
_CUSTOMER_ACCOUNT_MAPPING_ID: Final = "canonical.customer_account.v1"
_CUSTOMER_RELATIONSHIP_TYPE: Final = "HAS_ACCOUNT"
_REQUIRED_GRAPH_PROPERTIES: Final = frozenset(
    {
        "canonical_key",
        "configuration_digest",
        "graph_synced_at",
        "identity_quality",
        "mapping_version",
        "source_asset",
        "source_database",
        "source_record_id",
        "source_system",
        "source_updated_at",
        "sync_run_id",
    }
)

type GraphParameterValue = str | bool | int | float | datetime
"""Current Neo4j-safe scalar parameter values used by the Customer profile."""


class GraphMaterializationErrorCode(StrEnum):
    """Stable document-level graph-materialization failure codes."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    EXECUTION_PLAN_MISMATCH = "EXECUTION_PLAN_MISMATCH"
    PLAN_UNSUPPORTED = "PLAN_UNSUPPORTED"
    GRAPH_NODE_PLAN_INVALID = "GRAPH_NODE_PLAN_INVALID"
    GRAPH_RELATIONSHIP_PLAN_INVALID = "GRAPH_RELATIONSHIP_PLAN_INVALID"
    GRAPH_PROPERTY_PLAN_INVALID = "GRAPH_PROPERTY_PLAN_INVALID"
    GRAPH_PARAMETER_VALUE_INVALID = "GRAPH_PARAMETER_VALUE_INVALID"


_ERROR_MESSAGES: Final = {
    GraphMaterializationErrorCode.INVALID_INPUT_TYPE: (
        "Graph materialization inputs have invalid types."
    ),
    GraphMaterializationErrorCode.EXECUTION_PLAN_MISMATCH: (
        "Normalization evidence does not match the compiled execution plan."
    ),
    GraphMaterializationErrorCode.PLAN_UNSUPPORTED: (
        "The graph materializer supports only the Customer foundation profile."
    ),
    GraphMaterializationErrorCode.GRAPH_NODE_PLAN_INVALID: (
        "A compiled graph node plan is invalid for Customer projection."
    ),
    GraphMaterializationErrorCode.GRAPH_RELATIONSHIP_PLAN_INVALID: (
        "The compiled Customer relationship plan is invalid."
    ),
    GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID: (
        "A compiled graph property source is invalid."
    ),
    GraphMaterializationErrorCode.GRAPH_PARAMETER_VALUE_INVALID: (
        "A graph parameter value is unsupported."
    ),
}


class GraphMaterializationError(ValueError):
    """Safe public failure for corrupted plans or invalid runtime arguments."""

    def __init__(self, code: GraphMaterializationErrorCode) -> None:
        """Initialize one safe materialization error."""
        self.code = code
        self.safe_message = _ERROR_MESSAGES[code]
        super().__init__(self.safe_message)


class GraphMaterializationRejectionCode(StrEnum):
    """Stable entity-level graph projection rejection reasons."""

    NORMALIZATION_REJECTED = "NORMALIZATION_REJECTED"
    NORMALIZATION_DEPENDENCY_UNRESOLVED = "NORMALIZATION_DEPENDENCY_UNRESOLVED"
    SOURCE_EVIDENCE_MISMATCH = "SOURCE_EVIDENCE_MISMATCH"
    REQUIRED_PROPERTY_MISSING = "REQUIRED_PROPERTY_MISSING"
    KEY_PROPERTY_MISMATCH = "KEY_PROPERTY_MISMATCH"
    REQUIRED_RELATIONSHIP_UNRESOLVED = "REQUIRED_RELATIONSHIP_UNRESOLVED"


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


class GraphParameterEntry(CanonicalBaseModel):
    """One immutable named scalar parameter for a future Neo4j writer."""

    name: GraphPropertyName
    value: GraphParameterValue

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        """Reject non-finite floating-point values."""
        if isinstance(self.value, float) and not math.isfinite(self.value):
            _raise_model_error(
                "graph_parameter_non_finite",
                "graph parameter floats must be finite",
            )
        return self


class GraphParameterMap(CanonicalBaseModel):
    """Deterministically ordered immutable graph parameter map."""

    entries: Annotated[tuple[GraphParameterEntry, ...], Field(max_length=512)]

    @model_validator(mode="after")
    def validate_order_and_uniqueness(self) -> Self:
        """Require unique names in deterministic lexical order."""
        names = tuple(entry.name for entry in self.entries)
        if any(count > 1 for count in Counter(names).values()):
            _raise_model_error(
                "graph_parameter_duplicate_name",
                "graph parameter names must be unique",
            )
        if names != tuple(sorted(names)):
            _raise_model_error(
                "graph_parameter_order_invalid",
                "graph parameters must use deterministic lexical order",
            )
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, GraphParameterValue]) -> Self:
        """Create one immutable map from a caller-owned mapping."""
        return cls(
            entries=tuple(
                GraphParameterEntry(name=name, value=value)
                for name, value in sorted(values.items())
            )
        )

    def as_mapping(self) -> Mapping[str, GraphParameterValue]:
        """Return a detached read-only mapping for future driver invocation."""
        return MappingProxyType({entry.name: entry.value for entry in self.entries})

    def get(self, name: str) -> GraphParameterValue | None:
        """Resolve one parameter without exposing mutable storage."""
        for entry in self.entries:
            if entry.name == name:
                return entry.value
        return None


class GraphNodeUpsertParameters(CanonicalBaseModel):
    """One label/key/property parameter set without executable Cypher."""

    node_mapping_id: MappingIdentifier
    label: GraphLabel
    key_property: GraphPropertyName
    key_value: CanonicalIdentifier
    properties: GraphParameterMap

    @model_validator(mode="after")
    def validate_key_parameter(self) -> Self:
        """Require the constrained key inside the immutable property map."""
        if self.properties.get(self.key_property) != self.key_value:
            _raise_model_error(
                "graph_node_key_parameter_mismatch",
                "node key parameter must equal key_value",
            )
        return self


class GraphRelationshipUpsertParameters(CanonicalBaseModel):
    """One directed relationship match parameter set without Cypher."""

    relationship_mapping_id: MappingIdentifier
    relationship_type: GraphRelationshipType
    source_node_mapping_id: MappingIdentifier
    source_label: GraphLabel
    source_key_property: GraphPropertyName
    source_key_value: CanonicalIdentifier
    source_match: GraphParameterMap
    target_node_mapping_id: MappingIdentifier
    target_label: GraphLabel
    target_key_property: GraphPropertyName
    target_key_value: CanonicalIdentifier
    target_match: GraphParameterMap

    @model_validator(mode="after")
    def validate_endpoint_parameters(self) -> Self:
        """Require exactly one matching key parameter for each endpoint."""
        if len(self.source_match.entries) != 1 or len(self.target_match.entries) != 1:
            _raise_model_error(
                "graph_relationship_match_cardinality_invalid",
                "relationship endpoint matches require exactly one key",
            )
        if self.source_match.get(self.source_key_property) != self.source_key_value:
            _raise_model_error(
                "graph_relationship_source_match_invalid",
                "source match must contain the source key",
            )
        if self.target_match.get(self.target_key_property) != self.target_key_value:
            _raise_model_error(
                "graph_relationship_target_match_invalid",
                "target match must contain the target key",
            )
        return self


class CustomerGraphProjectionMaterialization(CanonicalBaseModel):
    """Immutable graph parameters; PROJECTED means materialized, not persisted."""

    materializer_version: VersionReference
    execution_plan_digest: Sha256Digest
    sync_run_id: UUID
    graph_synced_at: UtcDateTime
    customer_node: GraphNodeUpsertParameters | None
    customer_account_nodes: tuple[GraphNodeUpsertParameters, ...]
    has_account_relationships: tuple[GraphRelationshipUpsertParameters, ...]
    projection_evidence: tuple[GraphProjectionEvidence, ...]

    @property
    def node_count(self) -> int:
        """Return the number of materialized node upserts."""
        return int(self.customer_node is not None) + len(self.customer_account_nodes)

    @property
    def relationship_count(self) -> int:
        """Return the number of materialized relationship upserts."""
        return len(self.has_account_relationships)

    @property
    def rejected_count(self) -> int:
        """Return rejected or unresolved projection evidence count."""
        return sum(
            evidence.projection_status is not GraphProjectionStatus.PROJECTED
            for evidence in self.projection_evidence
        )


class _RecordMaterializationError(ValueError):
    """Internal entity-level failure converted into safe projection evidence."""

    def __init__(self, code: GraphMaterializationRejectionCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class _ProfilePlans:
    """Exact compiled plans required by the Customer graph profile."""

    customer_mapping: CompiledCanonicalMappingPlan
    account_mapping: CompiledCanonicalMappingPlan
    customer_node: CompiledGraphNodePlan
    account_node: CompiledGraphNodePlan
    relationship: CompiledGraphRelationshipPlan


@dataclass(frozen=True, slots=True)
class _RuntimeContext:
    """Runtime evidence injected into compiled graph properties."""

    sync_run_id: UUID
    graph_synced_at: datetime


@dataclass(frozen=True, slots=True)
class _NodeContext:
    """Inputs required to materialize one canonical graph node."""

    plan: MappingExecutionPlan
    canonical: CompiledCanonicalMappingPlan
    node: CompiledGraphNodePlan
    runtime: _RuntimeContext


@dataclass(frozen=True, slots=True)
class _EvidenceSpec:
    """All deterministic inputs for one projection evidence record."""

    runtime: _RuntimeContext
    mapping: CompiledCanonicalMappingPlan
    node: CompiledGraphNodePlan
    source_asset: str
    source_record_id: str
    entity_key: str
    status: GraphProjectionStatus
    rejection_reason: str | None
    discriminator: str


@dataclass(frozen=True, slots=True)
class _RejectionContext:
    """Shared inputs for translating normalization rejection evidence."""

    plan: MappingExecutionPlan
    normalization: CustomerNormalizationResult
    plans: _ProfilePlans
    runtime: _RuntimeContext


def _raise_error(code: GraphMaterializationErrorCode) -> Never:
    """Raise one safe document-level materialization error."""
    raise GraphMaterializationError(code)


def _runtime_context(sync_run_id: object, graph_synced_at: object) -> _RuntimeContext:
    """Validate and normalize runtime evidence without coercion."""
    if not isinstance(sync_run_id, UUID):
        _raise_error(GraphMaterializationErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(graph_synced_at, datetime) or graph_synced_at.tzinfo is None:
        _raise_error(GraphMaterializationErrorCode.INVALID_INPUT_TYPE)
    return _RuntimeContext(sync_run_id, graph_synced_at.astimezone(UTC))


def _canonical_plan(
    plan: MappingExecutionPlan,
    mapping_id: str,
) -> CompiledCanonicalMappingPlan:
    """Resolve one required canonical mapping or reject a corrupted plan."""
    try:
        return plan.resolve_canonical_mapping(mapping_id)
    except KeyError:
        _raise_error(GraphMaterializationErrorCode.PLAN_UNSUPPORTED)


def _node_index(
    plan: MappingExecutionPlan,
) -> dict[CanonicalEntityType, CompiledGraphNodePlan]:
    """Resolve the exact supported node plans by canonical entity type."""
    indexed: dict[CanonicalEntityType, CompiledGraphNodePlan] = {}
    for node in plan.graph_nodes:
        try:
            canonical = plan.resolve_canonical_mapping(node.canonical_mapping_id)
        except KeyError:
            _raise_error(GraphMaterializationErrorCode.GRAPH_NODE_PLAN_INVALID)
        entity_type = canonical.definition.entity_type
        if entity_type in indexed:
            _raise_error(GraphMaterializationErrorCode.GRAPH_NODE_PLAN_INVALID)
        indexed[entity_type] = node
    return indexed


def _relationship_plan(
    plan: MappingExecutionPlan,
    customer_node: CompiledGraphNodePlan,
    account_node: CompiledGraphNodePlan,
) -> CompiledGraphRelationshipPlan:
    """Validate the exact required HAS_ACCOUNT edge contract."""
    if len(plan.graph_relationships) != 1:
        _raise_error(GraphMaterializationErrorCode.GRAPH_RELATIONSHIP_PLAN_INVALID)
    relationship = plan.graph_relationships[0]
    definition = relationship.definition
    valid = (
        definition.relationship_type == _CUSTOMER_RELATIONSHIP_TYPE
        and definition.required is True
        and relationship.reference_holder_node_mapping_id == account_node.definition.node_mapping_id
        and relationship.referenced_node_mapping_id == customer_node.definition.node_mapping_id
        and relationship.edge_source_node_mapping_id == customer_node.definition.node_mapping_id
        and relationship.edge_target_node_mapping_id == account_node.definition.node_mapping_id
        and definition.source_reference_field == "customer_key"
        and definition.target_key_field == customer_node.definition.key_field
    )
    if not valid:
        _raise_error(GraphMaterializationErrorCode.GRAPH_RELATIONSHIP_PLAN_INVALID)
    return relationship


def _validate_profile_plan(plan: MappingExecutionPlan) -> _ProfilePlans:
    """Validate profile-level plan invariants before processing records."""
    customer_mapping = _canonical_plan(plan, _CUSTOMER_MAPPING_ID)
    account_mapping = _canonical_plan(plan, _CUSTOMER_ACCOUNT_MAPPING_ID)
    if (
        customer_mapping.definition.entity_type is not CanonicalEntityType.CUSTOMER
        or account_mapping.definition.entity_type is not CanonicalEntityType.CUSTOMER_ACCOUNT
    ):
        _raise_error(GraphMaterializationErrorCode.PLAN_UNSUPPORTED)

    nodes = _node_index(plan)
    if set(nodes) != {
        CanonicalEntityType.CUSTOMER,
        CanonicalEntityType.CUSTOMER_ACCOUNT,
    }:
        _raise_error(GraphMaterializationErrorCode.PLAN_UNSUPPORTED)
    customer_node = nodes[CanonicalEntityType.CUSTOMER]
    account_node = nodes[CanonicalEntityType.CUSTOMER_ACCOUNT]
    return _ProfilePlans(
        customer_mapping=customer_mapping,
        account_mapping=account_mapping,
        customer_node=customer_node,
        account_node=account_node,
        relationship=_relationship_plan(plan, customer_node, account_node),
    )


def _validate_record_source(
    record: Customer | CustomerAccount,
    context: _NodeContext,
) -> None:
    """Ensure canonical provenance is bound to the compiled governed source."""
    provenance = record.provenance
    source = context.canonical.source
    valid = (
        provenance.source_system == source.definition.source_system
        and provenance.source_database == source.catalog_asset.database
        and provenance.source_asset == source.catalog_asset.object_name
        and provenance.mapping_version == context.canonical.definition.version
        and provenance.configuration_version == context.plan.schema_version
        and provenance.configuration_digest == context.plan.configuration_digest
    )
    if not valid:
        raise _RecordMaterializationError(
            GraphMaterializationRejectionCode.SOURCE_EVIDENCE_MISMATCH
        )


def _graph_value(value: object) -> GraphParameterValue:
    """Convert one compiled property value into a bounded Neo4j-safe scalar."""
    converted: object = value
    if isinstance(value, IdentityQuality):
        converted = value.value
    elif isinstance(value, UUID):
        converted = str(value)
    elif isinstance(value, datetime):
        if value.tzinfo is None:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PARAMETER_VALUE_INVALID)
        converted = value.astimezone(UTC)

    if isinstance(converted, bool | str | int):
        return converted
    if isinstance(converted, float) and math.isfinite(converted):
        return converted
    if isinstance(converted, datetime):
        return converted
    _raise_error(GraphMaterializationErrorCode.GRAPH_PARAMETER_VALUE_INVALID)


def _resolve_property(
    property_plan: CompiledGraphPropertyPlan,
    record: Customer | CustomerAccount,
    runtime: _RuntimeContext,
) -> object:
    """Resolve one value exclusively from a compiled safe property source."""
    source_path = property_plan.source_path
    if property_plan.source is CompiledGraphPropertySource.CANONICAL_FIELD:
        if source_path is None or source_path not in record.__class__.model_fields:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)
        return getattr(record, source_path)
    if property_plan.source is CompiledGraphPropertySource.PROVENANCE_FIELD:
        if source_path is None or source_path not in SourceProvenance.model_fields:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)
        return getattr(record.provenance, source_path)
    if property_plan.source is CompiledGraphPropertySource.STATIC_VALUE:
        if property_plan.static_value is None:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)
        return property_plan.static_value
    if property_plan.source is CompiledGraphPropertySource.RUNTIME_VALUE:
        runtime_values: Mapping[str, object] = {
            "graph_synced_at": runtime.graph_synced_at,
            "sync_run_id": runtime.sync_run_id,
        }
        try:
            return runtime_values[property_plan.graph_property]
        except KeyError:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)
    _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)


def _node_parameters(
    record: Customer | CustomerAccount,
    context: _NodeContext,
) -> GraphNodeUpsertParameters:
    """Materialize one canonical record using compiled property sources only."""
    _validate_record_source(record, context)
    values: dict[str, GraphParameterValue] = {}
    seen_properties: set[str] = set()
    for property_plan in context.node.properties:
        if property_plan.graph_property in seen_properties:
            _raise_error(GraphMaterializationErrorCode.GRAPH_PROPERTY_PLAN_INVALID)
        seen_properties.add(property_plan.graph_property)
        resolved = _resolve_property(property_plan, record, context.runtime)
        if resolved is None:
            if property_plan.graph_property in _REQUIRED_GRAPH_PROPERTIES:
                raise _RecordMaterializationError(
                    GraphMaterializationRejectionCode.REQUIRED_PROPERTY_MISSING
                )
            continue
        values[property_plan.graph_property] = _graph_value(resolved)

    if not _REQUIRED_GRAPH_PROPERTIES.issubset(values):
        raise _RecordMaterializationError(
            GraphMaterializationRejectionCode.REQUIRED_PROPERTY_MISSING
        )

    key_field = context.node.definition.key_field
    key_value = getattr(record, key_field, None)
    if (
        not isinstance(key_value, str)
        or values.get(key_field) != key_value
        or values.get("canonical_key") != key_value
    ):
        raise _RecordMaterializationError(GraphMaterializationRejectionCode.KEY_PROPERTY_MISMATCH)

    return GraphNodeUpsertParameters(
        node_mapping_id=context.node.definition.node_mapping_id,
        label=context.node.definition.label,
        key_property=key_field,
        key_value=key_value,
        properties=GraphParameterMap.from_mapping(values),
    )


def _evidence_id(spec: _EvidenceSpec) -> UUID:
    """Create one deterministic idempotent evidence identifier."""
    name = (
        f"{MATERIALIZER_VERSION}|{spec.mapping.definition.mapping_id}|"
        f"{spec.mapping.definition.entity_type.value}|{spec.entity_key}|"
        f"{spec.status.value}|{spec.discriminator}"
    )
    return uuid5(spec.runtime.sync_run_id, name)


def _projection_evidence(spec: _EvidenceSpec) -> GraphProjectionEvidence:
    """Create one deterministic canonical projection-evidence record."""
    return GraphProjectionEvidence(
        evidence_id=_evidence_id(spec),
        sync_run_id=spec.runtime.sync_run_id,
        source_asset=spec.source_asset,
        source_record_id=spec.source_record_id,
        canonical_entity_type=spec.mapping.definition.entity_type.value,
        canonical_entity_key=spec.entity_key,
        graph_label=spec.node.definition.label,
        graph_key=spec.entity_key,
        mapping_version=spec.mapping.definition.version,
        projection_status=spec.status,
        rejection_reason=spec.rejection_reason,
        projected_at=spec.runtime.graph_synced_at,
    )


def _synthetic_rejection_key(
    plan: MappingExecutionPlan,
    rejection: NormalizationRejection,
) -> str:
    """Create a non-authoritative deterministic evidence key for rejected input."""
    raw = (
        f"{plan.execution_plan_digest}|{rejection.mapping_id}|"
        f"{rejection.entity_type.value}|{rejection.record_locator}|"
        f"{rejection.record_index}|{rejection.code.value}"
    ).encode()
    return f"EVIDENCE:{hashlib.sha256(raw).hexdigest()}"


def _normalization_rejection_evidence(
    rejection: NormalizationRejection,
    context: _RejectionContext,
) -> GraphProjectionEvidence:
    """Translate safe normalization evidence into graph projection evidence."""
    if rejection.entity_type is CanonicalEntityType.CUSTOMER:
        mapping = context.plans.customer_mapping
        node = context.plans.customer_node
    elif rejection.entity_type is CanonicalEntityType.CUSTOMER_ACCOUNT:
        mapping = context.plans.account_mapping
        node = context.plans.account_node
    else:
        _raise_error(GraphMaterializationErrorCode.PLAN_UNSUPPORTED)

    status = (
        GraphProjectionStatus.UNRESOLVED
        if rejection.code is NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED
        else GraphProjectionStatus.REJECTED
    )
    reason_code = (
        GraphMaterializationRejectionCode.NORMALIZATION_DEPENDENCY_UNRESOLVED
        if status is GraphProjectionStatus.UNRESOLVED
        else GraphMaterializationRejectionCode.NORMALIZATION_REJECTED
    )
    source_record_id = context.normalization.source_document_id
    if rejection.record_locator != "$":
        source_record_id = f"{source_record_id}#{rejection.record_locator}"
    return _projection_evidence(
        _EvidenceSpec(
            runtime=context.runtime,
            mapping=mapping,
            node=node,
            source_asset=mapping.source.catalog_asset.object_name,
            source_record_id=source_record_id,
            entity_key=_synthetic_rejection_key(context.plan, rejection),
            status=status,
            rejection_reason=f"{reason_code.value}:{rejection.code.value}",
            discriminator=rejection.record_locator,
        )
    )


def _record_failure_evidence(
    error: _RecordMaterializationError,
    record: Customer | CustomerAccount,
    context: _NodeContext,
) -> GraphProjectionEvidence:
    """Create evidence for one canonical record that cannot materialize."""
    key = getattr(record, context.canonical.definition.identity.key_field)
    return _projection_evidence(
        _EvidenceSpec(
            runtime=context.runtime,
            mapping=context.canonical,
            node=context.node,
            source_asset=record.provenance.source_asset,
            source_record_id=record.provenance.source_record_id,
            entity_key=key,
            status=GraphProjectionStatus.REJECTED,
            rejection_reason=error.code.value,
            discriminator=error.code.value,
        )
    )


def _unresolved_account_evidence(
    account: CustomerAccount,
    context: _NodeContext,
) -> GraphProjectionEvidence:
    """Reject an account when its required Customer endpoint is unavailable."""
    reason = GraphMaterializationRejectionCode.REQUIRED_RELATIONSHIP_UNRESOLVED
    return _projection_evidence(
        _EvidenceSpec(
            runtime=context.runtime,
            mapping=context.canonical,
            node=context.node,
            source_asset=account.provenance.source_asset,
            source_record_id=account.provenance.source_record_id,
            entity_key=account.account_key,
            status=GraphProjectionStatus.UNRESOLVED,
            rejection_reason=reason.value,
            discriminator="required-customer-endpoint",
        )
    )


def _projected_evidence(
    record: Customer | CustomerAccount,
    context: _NodeContext,
    *,
    discriminator: str,
) -> GraphProjectionEvidence:
    """Create evidence for one successfully materialized canonical record."""
    key = getattr(record, context.canonical.definition.identity.key_field)
    return _projection_evidence(
        _EvidenceSpec(
            runtime=context.runtime,
            mapping=context.canonical,
            node=context.node,
            source_asset=record.provenance.source_asset,
            source_record_id=record.provenance.source_record_id,
            entity_key=key,
            status=GraphProjectionStatus.PROJECTED,
            rejection_reason=None,
            discriminator=discriminator,
        )
    )


def _relationship_parameters(
    relationship: CompiledGraphRelationshipPlan,
    customer_node: GraphNodeUpsertParameters,
    account_node: GraphNodeUpsertParameters,
) -> GraphRelationshipUpsertParameters:
    """Materialize the pre-resolved Customer -> CustomerAccount edge."""
    return GraphRelationshipUpsertParameters(
        relationship_mapping_id=relationship.definition.relationship_mapping_id,
        relationship_type=relationship.definition.relationship_type,
        source_node_mapping_id=customer_node.node_mapping_id,
        source_label=customer_node.label,
        source_key_property=customer_node.key_property,
        source_key_value=customer_node.key_value,
        source_match=GraphParameterMap.from_mapping(
            {customer_node.key_property: customer_node.key_value}
        ),
        target_node_mapping_id=account_node.node_mapping_id,
        target_label=account_node.label,
        target_key_property=account_node.key_property,
        target_key_value=account_node.key_value,
        target_match=GraphParameterMap.from_mapping(
            {account_node.key_property: account_node.key_value}
        ),
    )


def materialize_customer_graph_projection(
    plan: MappingExecutionPlan,
    normalization: CustomerNormalizationResult,
    *,
    sync_run_id: UUID,
    graph_synced_at: datetime,
) -> CustomerGraphProjectionMaterialization:
    """Materialize Customer graph parameters without Cypher or Neo4j I/O."""
    if not isinstance(plan, MappingExecutionPlan) or not isinstance(
        normalization, CustomerNormalizationResult
    ):
        _raise_error(GraphMaterializationErrorCode.INVALID_INPUT_TYPE)
    runtime = _runtime_context(sync_run_id, graph_synced_at)
    if normalization.execution_plan_digest != plan.execution_plan_digest:
        _raise_error(GraphMaterializationErrorCode.EXECUTION_PLAN_MISMATCH)

    plans = _validate_profile_plan(plan)
    customer_context = _NodeContext(
        plan,
        plans.customer_mapping,
        plans.customer_node,
        runtime,
    )
    account_context = _NodeContext(
        plan,
        plans.account_mapping,
        plans.account_node,
        runtime,
    )
    rejection_context = _RejectionContext(plan, normalization, plans, runtime)
    evidence = [
        _normalization_rejection_evidence(rejection, rejection_context)
        for rejection in normalization.rejections
    ]

    customer_node: GraphNodeUpsertParameters | None = None
    if normalization.customer is not None:
        try:
            customer_node = _node_parameters(
                normalization.customer,
                customer_context,
            )
        except _RecordMaterializationError as error:
            evidence.append(
                _record_failure_evidence(
                    error,
                    normalization.customer,
                    customer_context,
                )
            )
        else:
            evidence.append(
                _projected_evidence(
                    normalization.customer,
                    customer_context,
                    discriminator="node",
                )
            )

    account_nodes: list[GraphNodeUpsertParameters] = []
    relationships: list[GraphRelationshipUpsertParameters] = []
    for account in normalization.customer_accounts:
        if customer_node is None or account.customer_key != customer_node.key_value:
            evidence.append(_unresolved_account_evidence(account, account_context))
            continue
        try:
            account_node = _node_parameters(account, account_context)
        except _RecordMaterializationError as error:
            evidence.append(_record_failure_evidence(error, account, account_context))
            continue

        account_nodes.append(account_node)
        relationships.append(
            _relationship_parameters(
                plans.relationship,
                customer_node,
                account_node,
            )
        )
        evidence.append(
            _projected_evidence(
                account,
                account_context,
                discriminator="node-and-required-relationship",
            )
        )

    return CustomerGraphProjectionMaterialization(
        materializer_version=MATERIALIZER_VERSION,
        execution_plan_digest=plan.execution_plan_digest,
        sync_run_id=runtime.sync_run_id,
        graph_synced_at=runtime.graph_synced_at,
        customer_node=customer_node,
        customer_account_nodes=tuple(account_nodes),
        has_account_relationships=tuple(relationships),
        projection_evidence=tuple(evidence),
    )
