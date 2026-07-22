"""Pure compiler for immutable Customer foundation mapping execution plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never

from return_platform.canonical import CanonicalBaseModel, Customer, CustomerAccount
from return_platform.canonical.base import SourceProvenance
from return_platform.data_platform.mapping.contracts import (
    CanonicalEntityMapping,
    CanonicalEntityType,
    GraphNodeMapping,
    GraphRelationshipMapping,
    PhysicalFieldMapping,
    SourceAssetDefinition,
    SourceLifecycle,
    SyncPipelineDefinition,
)
from return_platform.data_platform.mapping.handlers import (
    HandlerInvocationError,
    HandlerOutputType,
    HandlerPurpose,
    HandlerRegistry,
    MappingHandler,
)
from return_platform.data_platform.mapping.loader import (
    LoadedDataPlatformConfiguration,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

__all__ = [
    "CanonicalModelRegistration",
    "CanonicalModelRegistry",
    "CompiledCanonicalMappingPlan",
    "CompiledFieldMappingPlan",
    "CompiledGraphNodePlan",
    "CompiledGraphPropertyPlan",
    "CompiledGraphPropertySource",
    "CompiledGraphRelationshipPlan",
    "CompiledIdentityMappingPlan",
    "CompiledSourceAssetPlan",
    "CompiledSyncPipelinePlan",
    "CompiledSyncStagePlan",
    "MappingCompilationError",
    "MappingCompilationErrorCode",
    "MappingExecutionPlan",
    "build_customer_account_canonical_model_registry",
    "compile_customer_profile_mapping",
]

COMPILER_VERSION: Final = "1.0"
_PLAN_DIGEST_DOMAIN: Final = "return-platform:mapping-execution-plan:v1"
_CUSTOMER_SOURCE_SYSTEM: Final = "CUSTOMER_CDM"
_SUPPORTED_ENTITY_TYPES: Final = frozenset(
    {
        CanonicalEntityType.CUSTOMER,
        CanonicalEntityType.CUSTOMER_ACCOUNT,
    }
)


type CanonicalModelType = type[CanonicalBaseModel]


class MappingCompilationErrorCode(StrEnum):
    """Stable safe error codes for pure mapping-plan compilation."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    CATALOG_ASSET_MISSING = "CATALOG_ASSET_MISSING"
    CATALOG_ASSET_OWNERSHIP_INVALID = "CATALOG_ASSET_OWNERSHIP_INVALID"
    CATALOG_ASSET_NOT_AUTHORITATIVE = "CATALOG_ASSET_NOT_AUTHORITATIVE"
    CATALOG_ASSET_OPERATION_INVALID = "CATALOG_ASSET_OPERATION_INVALID"
    CATALOG_ASSET_STORE_INVALID = "CATALOG_ASSET_STORE_INVALID"
    CATALOG_ASSET_KIND_INVALID = "CATALOG_ASSET_KIND_INVALID"
    SOURCE_SYSTEM_UNSUPPORTED = "SOURCE_SYSTEM_UNSUPPORTED"
    SOURCE_LIFECYCLE_INVALID = "SOURCE_LIFECYCLE_INVALID"
    CANONICAL_MODEL_REGISTRATION_INVALID = "CANONICAL_MODEL_REGISTRATION_INVALID"
    CANONICAL_MODEL_DUPLICATE = "CANONICAL_MODEL_DUPLICATE"
    CANONICAL_MODEL_MISSING = "CANONICAL_MODEL_MISSING"
    CANONICAL_ENTITY_UNSUPPORTED = "CANONICAL_ENTITY_UNSUPPORTED"
    CANONICAL_FIELD_MISSING = "CANONICAL_FIELD_MISSING"
    CANONICAL_FIELD_NESTING_UNSUPPORTED = "CANONICAL_FIELD_NESTING_UNSUPPORTED"
    CANONICAL_FIELD_TYPE_UNSUPPORTED = "CANONICAL_FIELD_TYPE_UNSUPPORTED"
    CANONICAL_REQUIRED_FIELD_UNMAPPED = "CANONICAL_REQUIRED_FIELD_UNMAPPED"
    HANDLER_NOT_REGISTERED = "HANDLER_NOT_REGISTERED"
    HANDLER_PURPOSE_MISMATCH = "HANDLER_PURPOSE_MISMATCH"
    HANDLER_ARITY_MISMATCH = "HANDLER_ARITY_MISMATCH"
    HANDLER_OUTPUT_TYPE_MISMATCH = "HANDLER_OUTPUT_TYPE_MISMATCH"
    HANDLER_VERSION_MISMATCH = "HANDLER_VERSION_MISMATCH"
    HANDLER_NOT_DETERMINISTIC = "HANDLER_NOT_DETERMINISTIC"
    GRAPH_PROPERTY_CONFLICT = "GRAPH_PROPERTY_CONFLICT"
    GRAPH_PROVENANCE_SOURCE_MISSING = "GRAPH_PROVENANCE_SOURCE_MISSING"


_SAFE_MESSAGES: Final = {
    MappingCompilationErrorCode.INVALID_INPUT_TYPE: ("Mapping compiler inputs have invalid types."),
    MappingCompilationErrorCode.CATALOG_ASSET_MISSING: (
        "A configured source asset is not declared in the governance catalog."
    ),
    MappingCompilationErrorCode.CATALOG_ASSET_OWNERSHIP_INVALID: (
        "A configured source asset has an incompatible ownership class."
    ),
    MappingCompilationErrorCode.CATALOG_ASSET_NOT_AUTHORITATIVE: (
        "A configured source asset is not declared authoritative."
    ),
    MappingCompilationErrorCode.CATALOG_ASSET_OPERATION_INVALID: (
        "A configured source asset does not permit the required read operation."
    ),
    MappingCompilationErrorCode.CATALOG_ASSET_STORE_INVALID: (
        "The Customer profile requires a MongoDB source asset."
    ),
    MappingCompilationErrorCode.CATALOG_ASSET_KIND_INVALID: (
        "The Customer profile requires a MongoDB collection asset."
    ),
    MappingCompilationErrorCode.SOURCE_SYSTEM_UNSUPPORTED: (
        "The compiler received an unsupported source-system profile."
    ),
    MappingCompilationErrorCode.SOURCE_LIFECYCLE_INVALID: (
        "A required source must be active before compilation."
    ),
    MappingCompilationErrorCode.CANONICAL_MODEL_REGISTRATION_INVALID: (
        "A canonical model registration is invalid."
    ),
    MappingCompilationErrorCode.CANONICAL_MODEL_DUPLICATE: (
        "Canonical model registrations must be unique."
    ),
    MappingCompilationErrorCode.CANONICAL_MODEL_MISSING: (
        "A configured canonical entity has no registered model."
    ),
    MappingCompilationErrorCode.CANONICAL_ENTITY_UNSUPPORTED: (
        "This compiler supports only the Customer foundation profile."
    ),
    MappingCompilationErrorCode.CANONICAL_FIELD_MISSING: (
        "A configured canonical field does not exist on its registered model."
    ),
    MappingCompilationErrorCode.CANONICAL_FIELD_NESTING_UNSUPPORTED: (
        "Nested canonical field compilation is not supported in this profile."
    ),
    MappingCompilationErrorCode.CANONICAL_FIELD_TYPE_UNSUPPORTED: (
        "A canonical field type is unsupported by the current handler contract."
    ),
    MappingCompilationErrorCode.CANONICAL_REQUIRED_FIELD_UNMAPPED: (
        "A required canonical model field is not produced by the mapping."
    ),
    MappingCompilationErrorCode.HANDLER_NOT_REGISTERED: (
        "A configured mapping handler is not registered."
    ),
    MappingCompilationErrorCode.HANDLER_PURPOSE_MISMATCH: (
        "A configured handler is registered for the wrong purpose."
    ),
    MappingCompilationErrorCode.HANDLER_ARITY_MISMATCH: (
        "A configured handler has an incompatible input contract."
    ),
    MappingCompilationErrorCode.HANDLER_OUTPUT_TYPE_MISMATCH: (
        "A configured handler has an incompatible output contract."
    ),
    MappingCompilationErrorCode.HANDLER_VERSION_MISMATCH: (
        "A configured handler contract version is incompatible."
    ),
    MappingCompilationErrorCode.HANDLER_NOT_DETERMINISTIC: (
        "Every compiled handler must declare deterministic execution."
    ),
    MappingCompilationErrorCode.GRAPH_PROPERTY_CONFLICT: (
        "A graph property conflicts with required projection evidence."
    ),
    MappingCompilationErrorCode.GRAPH_PROVENANCE_SOURCE_MISSING: (
        "A source-derived graph node lacks canonical provenance evidence."
    ),
}


class MappingCompilationError(ValueError):
    """Safe public error raised when pure plan compilation fails."""

    def __init__(self, code: MappingCompilationErrorCode) -> None:
        """Initialize one bounded compilation failure."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_compilation_error(code: MappingCompilationErrorCode) -> Never:
    """Raise one safe compilation error."""
    raise MappingCompilationError(code)


@dataclass(frozen=True, slots=True)
class CanonicalModelRegistration:
    """One code-owned canonical entity-to-Pydantic-model registration."""

    entity_type: CanonicalEntityType
    model_type: CanonicalModelType


class CanonicalModelRegistry:
    """Immutable registry of approved canonical Pydantic model classes."""

    __slots__ = ("_models", "_registered_entity_types")

    def __init__(
        self,
        registrations: tuple[CanonicalModelRegistration, ...],
    ) -> None:
        """Validate and index one ordered immutable registration collection."""
        if not isinstance(registrations, tuple):
            _raise_compilation_error(
                MappingCompilationErrorCode.CANONICAL_MODEL_REGISTRATION_INVALID
            )

        indexed: dict[CanonicalEntityType, CanonicalModelType] = {}
        seen_models: set[CanonicalModelType] = set()
        for registration in registrations:
            if not isinstance(registration, CanonicalModelRegistration):
                _raise_compilation_error(
                    MappingCompilationErrorCode.CANONICAL_MODEL_REGISTRATION_INVALID
                )
            model_type = registration.model_type
            if not isinstance(model_type, type) or not issubclass(
                model_type,
                CanonicalBaseModel,
            ):
                _raise_compilation_error(
                    MappingCompilationErrorCode.CANONICAL_MODEL_REGISTRATION_INVALID
                )
            if registration.entity_type in indexed or model_type in seen_models:
                _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_MODEL_DUPLICATE)
            indexed[registration.entity_type] = model_type
            seen_models.add(model_type)

        self._models = MappingProxyType(indexed)
        self._registered_entity_types = tuple(
            sorted(indexed, key=lambda entity_type: entity_type.value)
        )

    @property
    def registered_entity_types(self) -> tuple[CanonicalEntityType, ...]:
        """Return deterministic registry inventory."""
        return self._registered_entity_types

    def resolve(self, entity_type: CanonicalEntityType) -> CanonicalModelType:
        """Resolve one model or fail with a safe compiler error."""
        model_type = self._models.get(entity_type)
        if model_type is None:
            _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_MODEL_MISSING)
        return model_type


def build_customer_account_canonical_model_registry() -> CanonicalModelRegistry:
    """Build the code-owned canonical registry for the first profile."""
    return CanonicalModelRegistry(
        (
            CanonicalModelRegistration(CanonicalEntityType.CUSTOMER, Customer),
            CanonicalModelRegistration(
                CanonicalEntityType.CUSTOMER_ACCOUNT,
                CustomerAccount,
            ),
        )
    )


class CompiledGraphPropertySource(StrEnum):
    """Safe value origin for one compiled Neo4j node property."""

    CANONICAL_FIELD = "CANONICAL_FIELD"
    PROVENANCE_FIELD = "PROVENANCE_FIELD"
    STATIC_VALUE = "STATIC_VALUE"
    RUNTIME_VALUE = "RUNTIME_VALUE"


@dataclass(frozen=True, slots=True)
class CompiledSourceAssetPlan:
    """Resolved source definition bound to an approved catalog asset."""

    definition: SourceAssetDefinition
    catalog_asset: AssetCatalogEntry


@dataclass(frozen=True, slots=True)
class CompiledFieldMappingPlan:
    """Validated physical-to-canonical field execution metadata."""

    definition: PhysicalFieldMapping
    expected_output_type: HandlerOutputType
    handler: MappingHandler | None


@dataclass(frozen=True, slots=True)
class CompiledIdentityMappingPlan:
    """Validated canonical identity execution metadata."""

    key_field: str
    component_fields: tuple[str, ...]
    handler: MappingHandler


@dataclass(frozen=True, slots=True)
class CompiledCanonicalMappingPlan:
    """Canonical mapping bound to source, model, fields, and identity handler."""

    definition: CanonicalEntityMapping
    source: CompiledSourceAssetPlan
    model_type: CanonicalModelType
    model_schema_digest: str
    required_model_fields: tuple[str, ...]
    fields: tuple[CompiledFieldMappingPlan, ...]
    identity: CompiledIdentityMappingPlan


@dataclass(frozen=True, slots=True)
class CompiledGraphPropertyPlan:
    """One parameterized graph property value source."""

    graph_property: str
    source: CompiledGraphPropertySource
    source_path: str | None = None
    static_value: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledGraphNodePlan:
    """Graph node mapping augmented with mandatory provenance properties."""

    definition: GraphNodeMapping
    canonical_mapping_id: str
    properties: tuple[CompiledGraphPropertyPlan, ...]


@dataclass(frozen=True, slots=True)
class _GraphPropertyCompilationContext:
    """Internal immutable context for generated graph properties."""

    canonical: CompiledCanonicalMappingPlan
    available_fields: frozenset[str]
    configuration_digest: str


@dataclass(frozen=True, slots=True)
class CompiledGraphRelationshipPlan:
    """Resolved graph relationship with explicit emitted edge endpoints."""

    definition: GraphRelationshipMapping
    reference_holder_node_mapping_id: str
    referenced_node_mapping_id: str
    edge_source_node_mapping_id: str
    edge_target_node_mapping_id: str


@dataclass(frozen=True, slots=True)
class CompiledSyncStagePlan:
    """One ordered immutable execution stage."""

    stage_id: str
    canonical_mapping_ids: tuple[str, ...]
    node_mapping_ids: tuple[str, ...]
    relationship_mapping_ids: tuple[str, ...]
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledSyncPipelinePlan:
    """One versioned immutable ordered synchronization pipeline."""

    definition: SyncPipelineDefinition
    stages: tuple[CompiledSyncStagePlan, ...]


@dataclass(frozen=True, slots=True)
class MappingExecutionPlan:
    """Immutable, digest-bound, I/O-free mapping execution plan."""

    compiler_version: str
    schema_version: str
    configuration_digest: str
    catalog_version: str
    execution_plan_digest: str
    sources: tuple[CompiledSourceAssetPlan, ...]
    canonical_mappings: tuple[CompiledCanonicalMappingPlan, ...]
    graph_nodes: tuple[CompiledGraphNodePlan, ...]
    graph_relationships: tuple[CompiledGraphRelationshipPlan, ...]
    sync_pipelines: tuple[CompiledSyncPipelinePlan, ...]

    def resolve_canonical_mapping(
        self,
        mapping_id: str,
    ) -> CompiledCanonicalMappingPlan:
        """Resolve one compiled canonical mapping by stable ID."""
        for mapping in self.canonical_mappings:
            if mapping.definition.mapping_id == mapping_id:
                return mapping
        raise KeyError(mapping_id)

    def resolve_graph_node(self, node_mapping_id: str) -> CompiledGraphNodePlan:
        """Resolve one compiled graph node by stable ID."""
        for node in self.graph_nodes:
            if node.definition.node_mapping_id == node_mapping_id:
                return node
        raise KeyError(node_mapping_id)


_REQUIRED_GRAPH_PROPERTIES: Final = (
    ("source_system", CompiledGraphPropertySource.CANONICAL_FIELD, "source_system"),
    (
        "source_database",
        CompiledGraphPropertySource.PROVENANCE_FIELD,
        "source_database",
    ),
    ("source_asset", CompiledGraphPropertySource.PROVENANCE_FIELD, "source_asset"),
    (
        "source_record_id",
        CompiledGraphPropertySource.CANONICAL_FIELD,
        "source_record_id",
    ),
    (
        "source_updated_at",
        CompiledGraphPropertySource.CANONICAL_FIELD,
        "source_updated_at",
    ),
    ("canonical_key", CompiledGraphPropertySource.CANONICAL_FIELD, "$identity"),
    ("identity_quality", CompiledGraphPropertySource.STATIC_VALUE, "$identity_quality"),
    ("mapping_version", CompiledGraphPropertySource.STATIC_VALUE, "$mapping_version"),
    (
        "configuration_digest",
        CompiledGraphPropertySource.STATIC_VALUE,
        "$configuration_digest",
    ),
    ("sync_run_id", CompiledGraphPropertySource.RUNTIME_VALUE, "$sync_run_id"),
    (
        "graph_synced_at",
        CompiledGraphPropertySource.RUNTIME_VALUE,
        "$graph_synced_at",
    ),
)

_OPTIONAL_GRAPH_PROPERTIES: Final = (
    ("source_version", "source_version"),
    ("source_event_id", "source_event_id"),
    ("source_hash", "source_hash"),
)


def _catalog_index(catalog: AssetCatalog) -> dict[str, AssetCatalogEntry]:
    """Build one exact catalog asset index."""
    return {asset.asset_id: asset for asset in catalog.assets}


def _compile_source(
    definition: SourceAssetDefinition,
    catalog_index: dict[str, AssetCatalogEntry],
) -> CompiledSourceAssetPlan:
    """Resolve one source definition against governance boundaries."""
    asset = catalog_index.get(definition.catalog_asset_id)
    if asset is None:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_MISSING)
    if definition.source_system != _CUSTOMER_SOURCE_SYSTEM:
        _raise_compilation_error(MappingCompilationErrorCode.SOURCE_SYSTEM_UNSUPPORTED)
    if definition.required_for_sync and definition.lifecycle is not SourceLifecycle.ACTIVE:
        _raise_compilation_error(MappingCompilationErrorCode.SOURCE_LIFECYCLE_INVALID)
    if asset.ownership is not OwnershipClass.SOURCE_SYSTEM:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_OWNERSHIP_INVALID)
    if not asset.authoritative:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_NOT_AUTHORITATIVE)
    if set(asset.allowed_operations) != {AllowedOperation.READ}:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_OPERATION_INVALID)
    if asset.store is not DataStoreType.MONGODB:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_STORE_INVALID)
    if asset.object_kind is not ObjectKind.COLLECTION or asset.namespace is not None:
        _raise_compilation_error(MappingCompilationErrorCode.CATALOG_ASSET_KIND_INVALID)
    return CompiledSourceAssetPlan(definition=definition, catalog_asset=asset)


def _resolve_model_field(
    model_type: CanonicalModelType,
    field_path: str,
) -> object:
    """Resolve one top-level canonical model field annotation."""
    if "." in field_path:
        _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_FIELD_NESTING_UNSUPPORTED)
    model_field = model_type.model_fields.get(field_path)
    if model_field is None:
        _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_FIELD_MISSING)
    return model_field.annotation


def _expected_output_type(annotation: object) -> HandlerOutputType:
    """Map supported canonical annotations to handler output categories."""
    if annotation is str:
        return HandlerOutputType.STRING
    if annotation is SourceProvenance:
        return HandlerOutputType.SOURCE_PROVENANCE
    _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_FIELD_TYPE_UNSUPPORTED)


def _resolve_handler(
    registry: HandlerRegistry,
    name: str,
) -> MappingHandler:
    """Resolve one configured handler without leaking registry input."""
    try:
        return registry.resolve(name)
    except HandlerInvocationError:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_NOT_REGISTERED)


def _validate_handler_contract(
    handler: MappingHandler,
    *,
    purpose: HandlerPurpose,
    input_arity: int,
    output_type: HandlerOutputType,
    contract_version: str,
) -> None:
    """Validate static handler metadata before any invocation is possible."""
    if handler.purpose is not purpose:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_PURPOSE_MISMATCH)
    if handler.input_arity != input_arity:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_ARITY_MISMATCH)
    if handler.output_type is not output_type:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_OUTPUT_TYPE_MISMATCH)
    if handler.contract_version != contract_version:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_VERSION_MISMATCH)
    if handler.deterministic is not True:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_NOT_DETERMINISTIC)


def _compile_field(
    field: PhysicalFieldMapping,
    *,
    mapping_version: str,
    model_type: CanonicalModelType,
    handler_registry: HandlerRegistry,
) -> CompiledFieldMappingPlan:
    """Compile one field mapping against model and handler contracts."""
    annotation = _resolve_model_field(model_type, field.canonical_field)
    expected_output_type = _expected_output_type(annotation)
    if field.handler is None:
        if expected_output_type is not HandlerOutputType.STRING:
            _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_FIELD_TYPE_UNSUPPORTED)
        return CompiledFieldMappingPlan(field, expected_output_type, None)

    handler = _resolve_handler(handler_registry, field.handler)
    _validate_handler_contract(
        handler,
        purpose=HandlerPurpose.FIELD,
        input_arity=1,
        output_type=expected_output_type,
        contract_version=mapping_version,
    )
    return CompiledFieldMappingPlan(field, expected_output_type, handler)


def _model_schema_digest(model_type: CanonicalModelType) -> str:
    """Calculate a deterministic digest of the registered Pydantic schema."""
    schema_bytes = json.dumps(
        model_type.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(schema_bytes).hexdigest()


def _compile_canonical_mapping(
    mapping: CanonicalEntityMapping,
    *,
    source_index: dict[str, CompiledSourceAssetPlan],
    handler_registry: HandlerRegistry,
    model_registry: CanonicalModelRegistry,
) -> CompiledCanonicalMappingPlan:
    """Bind one canonical mapping to source, model, and handlers."""
    if mapping.entity_type not in _SUPPORTED_ENTITY_TYPES:
        _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_ENTITY_UNSUPPORTED)

    model_type = model_registry.resolve(mapping.entity_type)
    source = source_index[mapping.source_id]
    compiled_fields = tuple(
        _compile_field(
            field,
            mapping_version=mapping.version,
            model_type=model_type,
            handler_registry=handler_registry,
        )
        for field in mapping.fields
    )

    identity_annotation = _resolve_model_field(model_type, mapping.identity.key_field)
    if _expected_output_type(identity_annotation) is not HandlerOutputType.STRING:
        _raise_compilation_error(MappingCompilationErrorCode.HANDLER_OUTPUT_TYPE_MISMATCH)
    identity_handler = _resolve_handler(handler_registry, mapping.identity.handler)
    _validate_handler_contract(
        identity_handler,
        purpose=HandlerPurpose.IDENTITY,
        input_arity=len(mapping.identity.component_fields),
        output_type=HandlerOutputType.STRING,
        contract_version=mapping.version,
    )

    required_fields = tuple(
        sorted(
            field_name
            for field_name, field_info in model_type.model_fields.items()
            if field_info.is_required()
        )
    )
    produced_fields = {field.definition.canonical_field for field in compiled_fields}
    produced_fields.add(mapping.identity.key_field)
    if any(field_name not in produced_fields for field_name in required_fields):
        _raise_compilation_error(MappingCompilationErrorCode.CANONICAL_REQUIRED_FIELD_UNMAPPED)

    return CompiledCanonicalMappingPlan(
        definition=mapping,
        source=source,
        model_type=model_type,
        model_schema_digest=_model_schema_digest(model_type),
        required_model_fields=required_fields,
        fields=compiled_fields,
        identity=CompiledIdentityMappingPlan(
            key_field=mapping.identity.key_field,
            component_fields=mapping.identity.component_fields,
            handler=identity_handler,
        ),
    )


def _configured_graph_properties(
    node: GraphNodeMapping,
) -> list[CompiledGraphPropertyPlan]:
    """Compile explicitly configured canonical graph properties."""
    return [
        CompiledGraphPropertyPlan(
            graph_property=property_mapping.graph_property,
            source=CompiledGraphPropertySource.CANONICAL_FIELD,
            source_path=property_mapping.canonical_field,
        )
        for property_mapping in node.properties
    ]


def _append_graph_property(
    properties: list[CompiledGraphPropertyPlan],
    property_plan: CompiledGraphPropertyPlan,
) -> None:
    """Append one generated property or reject a conflicting definition."""
    for existing in properties:
        if existing.graph_property != property_plan.graph_property:
            continue
        if existing != property_plan:
            _raise_compilation_error(MappingCompilationErrorCode.GRAPH_PROPERTY_CONFLICT)
        return
    properties.append(property_plan)


def _resolve_generated_graph_property(
    graph_property: str,
    source: CompiledGraphPropertySource,
    source_path: str,
    context: _GraphPropertyCompilationContext,
) -> CompiledGraphPropertyPlan:
    """Resolve one required system-property value source."""
    resolved_path: str | None = source_path
    static_value: str | None = None
    if source_path == "$identity":
        resolved_path = context.canonical.definition.identity.key_field
    elif source_path == "$identity_quality":
        resolved_path = None
        static_value = context.canonical.definition.identity.identity_quality.value
    elif source_path == "$mapping_version":
        resolved_path = None
        static_value = context.canonical.definition.version
    elif source_path == "$configuration_digest":
        resolved_path = None
        static_value = context.configuration_digest
    elif source in {
        CompiledGraphPropertySource.RUNTIME_VALUE,
        CompiledGraphPropertySource.STATIC_VALUE,
    }:
        resolved_path = None

    actual_source = source
    if (
        source is CompiledGraphPropertySource.CANONICAL_FIELD
        and resolved_path not in context.available_fields
    ):
        provenance_fallback_fields = {
            "source_system",
            "source_record_id",
            "source_updated_at",
        }
        if resolved_path in provenance_fallback_fields:
            actual_source = CompiledGraphPropertySource.PROVENANCE_FIELD
        else:
            _raise_compilation_error(MappingCompilationErrorCode.GRAPH_PROVENANCE_SOURCE_MISSING)

    return CompiledGraphPropertyPlan(
        graph_property=graph_property,
        source=actual_source,
        source_path=resolved_path,
        static_value=static_value,
    )


def _compile_graph_node(
    node: GraphNodeMapping,
    canonical_index: dict[str, CompiledCanonicalMappingPlan],
    configuration_digest: str,
) -> CompiledGraphNodePlan:
    """Augment one node mapping with required provenance and runtime evidence."""
    canonical = canonical_index[node.canonical_mapping_id]
    available_fields = set(canonical.model_type.model_fields)
    if "provenance" not in available_fields:
        _raise_compilation_error(MappingCompilationErrorCode.GRAPH_PROVENANCE_SOURCE_MISSING)

    properties = _configured_graph_properties(node)
    property_context = _GraphPropertyCompilationContext(
        canonical=canonical,
        available_fields=frozenset(available_fields),
        configuration_digest=configuration_digest,
    )
    for graph_property, source, source_path in _REQUIRED_GRAPH_PROPERTIES:
        _append_graph_property(
            properties,
            _resolve_generated_graph_property(
                graph_property,
                source,
                source_path,
                property_context,
            ),
        )

    for graph_property, provenance_field in _OPTIONAL_GRAPH_PROPERTIES:
        _append_graph_property(
            properties,
            CompiledGraphPropertyPlan(
                graph_property=graph_property,
                source=CompiledGraphPropertySource.PROVENANCE_FIELD,
                source_path=provenance_field,
            ),
        )

    return CompiledGraphNodePlan(
        definition=node,
        canonical_mapping_id=node.canonical_mapping_id,
        properties=tuple(properties),
    )


def _compile_relationship(
    relationship: GraphRelationshipMapping,
) -> CompiledGraphRelationshipPlan:
    """Resolve one relationship's match and emitted edge directions."""
    return CompiledGraphRelationshipPlan(
        definition=relationship,
        reference_holder_node_mapping_id=relationship.source_node_mapping_id,
        referenced_node_mapping_id=relationship.target_node_mapping_id,
        edge_source_node_mapping_id=relationship.edge_source_node_mapping_id,
        edge_target_node_mapping_id=relationship.edge_target_node_mapping_id,
    )


def _compile_pipeline(
    pipeline: SyncPipelineDefinition,
) -> CompiledSyncPipelinePlan:
    """Copy one already validated ordered pipeline into immutable plan form."""
    return CompiledSyncPipelinePlan(
        definition=pipeline,
        stages=tuple(
            CompiledSyncStagePlan(
                stage_id=stage.stage_id,
                canonical_mapping_ids=stage.canonical_mapping_ids,
                node_mapping_ids=stage.node_mapping_ids,
                relationship_mapping_ids=stage.relationship_mapping_ids,
                depends_on=stage.depends_on,
            )
            for stage in pipeline.stages
        ),
    )


def _plan_digest_payload(
    *,
    loaded: LoadedDataPlatformConfiguration,
    catalog: AssetCatalog,
    sources: tuple[CompiledSourceAssetPlan, ...],
    canonical_mappings: tuple[CompiledCanonicalMappingPlan, ...],
) -> dict[str, object]:
    """Build deterministic compiler evidence without executable objects."""
    used_handlers: dict[str, MappingHandler] = {}
    for mapping in canonical_mappings:
        used_handlers[mapping.identity.handler.name] = mapping.identity.handler
        for field in mapping.fields:
            if field.handler is not None:
                used_handlers[field.handler.name] = field.handler

    return {
        "domain": _PLAN_DIGEST_DOMAIN,
        "compiler_version": COMPILER_VERSION,
        "schema_version": loaded.bundle.schema_version,
        "configuration_digest": loaded.configuration_digest,
        "catalog_version": catalog.version,
        "sources": [
            {
                "source_id": source.definition.source_id,
                "catalog_asset_id": source.catalog_asset.asset_id,
                "store": source.catalog_asset.store.value,
                "database": source.catalog_asset.database,
                "namespace": source.catalog_asset.namespace,
                "object_name": source.catalog_asset.object_name,
                "object_kind": source.catalog_asset.object_kind.value,
                "ownership": source.catalog_asset.ownership.value,
                "authoritative": source.catalog_asset.authoritative,
                "allowed_operations": sorted(
                    operation.value for operation in source.catalog_asset.allowed_operations
                ),
            }
            for source in sources
        ],
        "models": [
            {
                "entity_type": mapping.definition.entity_type.value,
                "model": (f"{mapping.model_type.__module__}.{mapping.model_type.__qualname__}"),
                "schema_digest": mapping.model_schema_digest,
            }
            for mapping in canonical_mappings
        ],
        "handlers": [
            {
                "name": handler.name,
                "purpose": handler.purpose.value,
                "input_arity": handler.input_arity,
                "output_type": handler.output_type.value,
                "contract_version": handler.contract_version,
                "deterministic": handler.deterministic,
            }
            for handler in sorted(used_handlers.values(), key=lambda item: item.name)
        ],
    }


def _calculate_plan_digest(payload: dict[str, object]) -> str:
    """Calculate one deterministic SHA-256 digest for compiler evidence."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_customer_profile_mapping(
    loaded: LoadedDataPlatformConfiguration,
    catalog: AssetCatalog,
    handler_registry: HandlerRegistry,
    model_registry: CanonicalModelRegistry,
) -> MappingExecutionPlan:
    """Compile the Customer foundation profile without source or graph I/O."""
    if not isinstance(loaded, LoadedDataPlatformConfiguration):
        _raise_compilation_error(MappingCompilationErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(catalog, AssetCatalog):
        _raise_compilation_error(MappingCompilationErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(handler_registry, HandlerRegistry):
        _raise_compilation_error(MappingCompilationErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(model_registry, CanonicalModelRegistry):
        _raise_compilation_error(MappingCompilationErrorCode.INVALID_INPUT_TYPE)

    bundle = loaded.bundle
    catalog_index = _catalog_index(catalog)
    sources = tuple(
        _compile_source(definition, catalog_index) for definition in bundle.source_assets
    )
    source_index = {source.definition.source_id: source for source in sources}

    canonical_mappings = tuple(
        _compile_canonical_mapping(
            mapping,
            source_index=source_index,
            handler_registry=handler_registry,
            model_registry=model_registry,
        )
        for mapping in bundle.canonical_mappings
    )
    canonical_index = {mapping.definition.mapping_id: mapping for mapping in canonical_mappings}
    graph_nodes = tuple(
        _compile_graph_node(
            node,
            canonical_index,
            loaded.configuration_digest,
        )
        for node in bundle.graph_nodes
    )
    graph_relationships = tuple(
        _compile_relationship(relationship) for relationship in bundle.graph_relationships
    )
    sync_pipelines = tuple(_compile_pipeline(pipeline) for pipeline in bundle.sync_pipelines)

    plan_digest = _calculate_plan_digest(
        _plan_digest_payload(
            loaded=loaded,
            catalog=catalog,
            sources=sources,
            canonical_mappings=canonical_mappings,
        )
    )
    return MappingExecutionPlan(
        compiler_version=COMPILER_VERSION,
        schema_version=bundle.schema_version,
        configuration_digest=loaded.configuration_digest,
        catalog_version=catalog.version,
        execution_plan_digest=plan_digest,
        sources=sources,
        canonical_mappings=canonical_mappings,
        graph_nodes=graph_nodes,
        graph_relationships=graph_relationships,
        sync_pipelines=sync_pipelines,
    )
