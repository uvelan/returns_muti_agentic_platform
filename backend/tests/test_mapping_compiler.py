"""Adversarial tests for the pure Customer profile mapping compiler."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from typing import Protocol, cast

import pytest

from return_platform.canonical import Customer, CustomerAccount
from return_platform.canonical.base import SourceProvenance
from return_platform.data_platform.mapping import (
    CanonicalEntityType,
    CanonicalModelRegistration,
    CanonicalModelRegistry,
    CompiledGraphPropertySource,
    HandlerOutputType,
    HandlerPurpose,
    HandlerRegistry,
    HandlerResult,
    LoadedDataPlatformConfiguration,
    MappingCompilationError,
    MappingCompilationErrorCode,
    MappingExecutionPlan,
    MappingHandler,
    SingleStringHandler,
    build_customer_account_canonical_model_registry,
    build_customer_account_handler_registry,
    compile_customer_profile_mapping,
    load_data_platform_mapping_configuration,
)
from return_platform.data_platform.mapping.handlers import HandlerExecutionContext
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

_CONFIG_DIR = Path(__file__).parents[1] / "config" / "data_platform"
_SHA256_HEX_LENGTH = 64


class _MutableExecutionPlan(Protocol):
    sources: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _MetadataHandler:
    """Test handler exposing compiler-relevant static metadata."""

    name: str
    purpose: HandlerPurpose
    input_arity: int
    output_type: HandlerOutputType
    contract_version: str
    deterministic: bool

    def invoke(
        self,
        values: tuple[object, ...],
        context: HandlerExecutionContext,
    ) -> HandlerResult:
        """Return one deterministic placeholder without compiler invocation."""
        del context
        value = values[0]
        if not isinstance(value, str):
            msg = "test handler expected a string"
            raise TypeError(msg)
        return value


def _loaded() -> LoadedDataPlatformConfiguration:
    """Load the fixed profile through the production loader."""
    return load_data_platform_mapping_configuration(_CONFIG_DIR)


def _asset(
    *,
    store: DataStoreType = DataStoreType.MONGODB,
    object_kind: ObjectKind = ObjectKind.COLLECTION,
    ownership: OwnershipClass = OwnershipClass.SOURCE_SYSTEM,
    authoritative: bool = True,
    allowed_operations: tuple[AllowedOperation, ...] = (AllowedOperation.READ,),
) -> AssetCatalogEntry:
    """Create one governed Customer CDM source fixture."""
    return AssetCatalogEntry(
        asset_id="source.mongodb.customer_outbound_cdm",
        store=store,
        database="eventMessages",
        namespace="dbo" if store is DataStoreType.SQLSERVER else None,
        object_name="customerOutboundCDM",
        object_kind=object_kind,
        ownership=ownership,
        authoritative=authoritative,
        allowed_operations=allowed_operations,
    )


def _platform_asset() -> AssetCatalogEntry:
    """Create one governance-valid platform asset for ownership rejection."""
    return AssetCatalogEntry(
        asset_id="platform.mongodb.customer_outbound_cdm",
        store=DataStoreType.MONGODB,
        database="eventMessages",
        namespace=None,
        object_name="customerOutboundCDM",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.PLATFORM_OWNED,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def _asset_with_object_name(object_name: str) -> AssetCatalogEntry:
    """Create one valid source asset with altered physical-object evidence."""
    payload = _asset().model_dump(mode="python")
    payload["object_name"] = object_name
    return AssetCatalogEntry.model_validate(payload)


def _catalog(*, asset: AssetCatalogEntry | None = None) -> AssetCatalog:
    """Create one version-locked catalog with optional Customer source."""
    assets = () if asset is None else (asset,)
    return AssetCatalog(version="1.0", assets=assets)


def _compile(
    *,
    loaded: LoadedDataPlatformConfiguration | None = None,
    catalog: AssetCatalog | None = None,
    handlers: HandlerRegistry | None = None,
    models: CanonicalModelRegistry | None = None,
) -> MappingExecutionPlan:
    """Compile using approved defaults unless a dependency is overridden."""
    return compile_customer_profile_mapping(
        loaded or _loaded(),
        catalog or _catalog(asset=_asset()),
        handlers or build_customer_account_handler_registry(),
        models or build_customer_account_canonical_model_registry(),
    )


def _echo(value: str, context: HandlerExecutionContext) -> str:
    """Return one deterministic test value."""
    del context
    return value


def _provenance(
    value: str,
    context: HandlerExecutionContext,
) -> SourceProvenance:
    """Create deterministic provenance for metadata-only tests."""
    return SourceProvenance(
        source_system=context.source_system,
        source_database=context.source_database,
        source_asset=context.source_asset,
        source_record_id=value,
        source_updated_at=context.source_updated_at,
        source_version=context.source_version,
        source_event_id=context.source_event_id,
        source_hash=context.source_hash,
        observed_at=context.observed_at,
        mapping_version=context.mapping_version,
        configuration_version=context.configuration_version,
        configuration_digest=context.configuration_digest,
    )


def _registry_with_replacement(
    name: str,
    replacement: MappingHandler,
) -> HandlerRegistry:
    """Replace one built-in handler while preserving all other definitions."""
    current = build_customer_account_handler_registry()
    handlers = tuple(
        replacement if registered_name == name else current.resolve(registered_name)
        for registered_name in current.registered_names
    )
    return HandlerRegistry(handlers)


def _bundle_with_mutation(
    loaded: LoadedDataPlatformConfiguration,
    mutate: Callable[[dict[str, object]], None],
) -> LoadedDataPlatformConfiguration:
    """Revalidate one structurally valid altered bundle for compiler tests."""
    payload = loaded.bundle.model_dump(mode="python")
    mutate(payload)
    bundle = type(loaded.bundle).model_validate(payload)
    return replace(loaded, bundle=bundle)


def test_compiler_emits_deterministic_immutable_customer_plan() -> None:
    """Bind config, catalog, handlers, and models without external I/O."""
    first = _compile()
    second = _compile()

    assert first.execution_plan_digest == second.execution_plan_digest
    assert first.configuration_digest == _loaded().configuration_digest
    assert first.catalog_version == "1.0"
    assert len(first.sources) == 1
    assert first.sources[0].catalog_asset.database == "eventMessages"
    assert first.sources[0].catalog_asset.object_name == "customerOutboundCDM"
    assert tuple(mapping.definition.entity_type for mapping in first.canonical_mappings) == (
        CanonicalEntityType.CUSTOMER,
        CanonicalEntityType.CUSTOMER_ACCOUNT,
    )

    mutable_plan = cast("_MutableExecutionPlan", first)
    with pytest.raises(FrozenInstanceError):
        mutable_plan.sources = ()


def test_compiler_binds_registered_canonical_models_and_required_fields() -> None:
    """Verify field introspection against the actual Customer models."""
    plan = _compile()
    customer = plan.resolve_canonical_mapping("canonical.customer.v1")
    account = plan.resolve_canonical_mapping("canonical.customer_account.v1")

    assert customer.model_type is Customer
    assert account.model_type is CustomerAccount
    assert customer.required_model_fields == (
        "customer_key",
        "party_id",
        "provenance",
        "source_record_id",
        "source_system",
    )
    assert account.required_model_fields == (
        "account_key",
        "account_number",
        "customer_id",
        "customer_key",
        "provenance",
    )
    assert len(customer.model_schema_digest) == _SHA256_HEX_LENGTH
    assert len(account.model_schema_digest) == _SHA256_HEX_LENGTH


def test_compiler_resolves_handler_contracts_without_invoking_handlers() -> None:
    """Bind exact purpose, arity, output, version, and determinism metadata."""
    plan = _compile()
    customer = plan.resolve_canonical_mapping("canonical.customer.v1")
    fields = {field.definition.canonical_field: field for field in customer.fields}

    assert fields["party_id"].handler is None
    assert fields["party_id"].expected_output_type is HandlerOutputType.STRING
    assert fields["provenance"].handler is not None
    assert fields["provenance"].handler.output_type is HandlerOutputType.SOURCE_PROVENANCE
    assert customer.identity.handler.purpose is HandlerPurpose.IDENTITY
    assert customer.identity.handler.input_arity == 1
    assert customer.identity.handler.contract_version == "1.0"
    assert customer.identity.handler.deterministic is True


def test_compiler_injects_required_graph_provenance_and_runtime_properties() -> None:
    """Augment YAML properties with mandatory graph evidence sources."""
    plan = _compile()
    customer = plan.resolve_graph_node("graph.customer.v1")
    properties = {item.graph_property: item for item in customer.properties}

    expected = {
        "source_system",
        "source_database",
        "source_asset",
        "source_record_id",
        "source_updated_at",
        "canonical_key",
        "identity_quality",
        "mapping_version",
        "configuration_digest",
        "sync_run_id",
        "graph_synced_at",
        "source_version",
        "source_event_id",
        "source_hash",
    }
    assert expected <= set(properties)
    assert properties["source_database"].source is (CompiledGraphPropertySource.PROVENANCE_FIELD)
    assert properties["canonical_key"].source_path == "customer_key"
    assert properties["identity_quality"].static_value == "VERIFIED"
    assert properties["configuration_digest"].static_value == (plan.configuration_digest)
    assert properties["sync_run_id"].source is (CompiledGraphPropertySource.RUNTIME_VALUE)


def test_compiler_preserves_reference_match_and_emitted_edge_direction() -> None:
    """Compile HAS_ACCOUNT using child-held reference and parent-to-child edge."""
    relationship = _compile().graph_relationships[0]

    assert relationship.reference_holder_node_mapping_id == ("graph.customer_account.v1")
    assert relationship.referenced_node_mapping_id == "graph.customer.v1"
    assert relationship.edge_source_node_mapping_id == "graph.customer.v1"
    assert relationship.edge_target_node_mapping_id == ("graph.customer_account.v1")


def test_compiler_preserves_validated_pipeline_order() -> None:
    """Emit Customer before CustomerAccount and the relationship."""
    pipeline = _compile().sync_pipelines[0]

    assert tuple(stage.stage_id for stage in pipeline.stages) == (
        "stage.customer.v1",
        "stage.customer_account.v1",
    )
    assert pipeline.stages[1].depends_on == ("stage.customer.v1",)


def test_empty_catalog_fails_closed() -> None:
    """Do not compile a profile whose governed asset is absent."""
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(catalog=_catalog())

    assert exc_info.value.code is MappingCompilationErrorCode.CATALOG_ASSET_MISSING
    assert "customer_outbound_cdm" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("asset", "expected_code"),
    [
        (
            _asset(authoritative=False),
            MappingCompilationErrorCode.CATALOG_ASSET_NOT_AUTHORITATIVE,
        ),
        (
            _asset(
                store=DataStoreType.SQLSERVER,
                object_kind=ObjectKind.TABLE,
            ),
            MappingCompilationErrorCode.CATALOG_ASSET_STORE_INVALID,
        ),
    ],
)
def test_compiler_rejects_incompatible_governance_assets(
    asset: AssetCatalogEntry,
    expected_code: MappingCompilationErrorCode,
) -> None:
    """Enforce ownership, authoritativeness, store, and object-kind boundaries."""
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(catalog=_catalog(asset=asset))

    assert exc_info.value.code is expected_code


def test_compiler_rejects_platform_owned_governance_asset() -> None:
    """Resolve a valid platform asset, then reject it as an external source."""
    asset = _platform_asset()
    loaded = _loaded()

    def mutate(payload: dict[str, object]) -> None:
        source_assets = cast("list[dict[str, object]]", payload["source_assets"])
        source_assets[0]["catalog_asset_id"] = asset.asset_id

    changed = _bundle_with_mutation(loaded, mutate)
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(loaded=changed, catalog=_catalog(asset=asset))

    assert exc_info.value.code is (MappingCompilationErrorCode.CATALOG_ASSET_OWNERSHIP_INVALID)


def test_compiler_rejects_missing_registered_handler() -> None:
    """Fail before execution when YAML references an absent handler."""
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(handlers=HandlerRegistry(()))

    assert exc_info.value.code is MappingCompilationErrorCode.HANDLER_NOT_REGISTERED


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    [
        (
            SingleStringHandler(
                name="customer_key_v1",
                purpose=HandlerPurpose.FIELD,
                function=_echo,
            ),
            MappingCompilationErrorCode.HANDLER_PURPOSE_MISMATCH,
        ),
        (
            SingleStringHandler(
                name="customer_key_v1",
                purpose=HandlerPurpose.IDENTITY,
                function=_echo,
                output_type=HandlerOutputType.SOURCE_PROVENANCE,
            ),
            MappingCompilationErrorCode.HANDLER_OUTPUT_TYPE_MISMATCH,
        ),
        (
            SingleStringHandler(
                name="customer_key_v1",
                purpose=HandlerPurpose.IDENTITY,
                function=_echo,
                contract_version="2.0",
            ),
            MappingCompilationErrorCode.HANDLER_VERSION_MISMATCH,
        ),
    ],
)
def test_compiler_rejects_incompatible_identity_handler_metadata(
    replacement: MappingHandler,
    expected_code: MappingCompilationErrorCode,
) -> None:
    """Reject handler purpose, output, and version drift."""
    registry = _registry_with_replacement("customer_key_v1", replacement)

    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(handlers=registry)

    assert exc_info.value.code is expected_code


def test_compiler_rejects_identity_handler_arity_mismatch() -> None:
    """Require identity arity to equal configured component count."""
    replacement = _MetadataHandler(
        name="customer_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        input_arity=2,
        output_type=HandlerOutputType.STRING,
        contract_version="1.0",
        deterministic=True,
    )
    registry = _registry_with_replacement(
        "customer_key_v1",
        replacement,
    )

    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(handlers=registry)

    assert exc_info.value.code is MappingCompilationErrorCode.HANDLER_ARITY_MISMATCH


def test_compiler_rejects_handler_without_determinism_contract() -> None:
    """Do not compile handlers that can depend on clocks or randomness."""
    replacement = _MetadataHandler(
        name="customer_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        input_arity=1,
        output_type=HandlerOutputType.STRING,
        contract_version="1.0",
        deterministic=False,
    )
    registry = _registry_with_replacement(
        "customer_key_v1",
        replacement,
    )

    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(handlers=registry)

    assert exc_info.value.code is (MappingCompilationErrorCode.HANDLER_NOT_DETERMINISTIC)


def test_compiler_rejects_wrong_provenance_handler_output_type() -> None:
    """Require SourceProvenance metadata for provenance fields."""
    replacement = SingleStringHandler(
        name="customer_cdm_document_provenance_v1",
        purpose=HandlerPurpose.FIELD,
        function=_echo,
        output_type=HandlerOutputType.STRING,
    )
    registry = _registry_with_replacement(
        "customer_cdm_document_provenance_v1",
        replacement,
    )

    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(handlers=registry)

    assert exc_info.value.code is (MappingCompilationErrorCode.HANDLER_OUTPUT_TYPE_MISMATCH)


def test_compiler_rejects_missing_canonical_model() -> None:
    """Do not infer model classes from configuration strings."""
    registry = CanonicalModelRegistry(
        (CanonicalModelRegistration(CanonicalEntityType.CUSTOMER, Customer),)
    )

    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(models=registry)

    assert exc_info.value.code is MappingCompilationErrorCode.CANONICAL_MODEL_MISSING


def test_model_registry_rejects_duplicate_model_or_entity_registration() -> None:
    """Prevent ambiguous entity-to-model resolution."""
    with pytest.raises(MappingCompilationError) as exc_info:
        CanonicalModelRegistry(
            (
                CanonicalModelRegistration(CanonicalEntityType.CUSTOMER, Customer),
                CanonicalModelRegistration(CanonicalEntityType.CUSTOMER, Customer),
            )
        )

    assert exc_info.value.code is MappingCompilationErrorCode.CANONICAL_MODEL_DUPLICATE


def test_compiler_rejects_unmapped_required_model_field() -> None:
    """Fail when YAML cannot construct a valid canonical model."""
    loaded = _loaded()

    def mutate(payload: dict[str, object]) -> None:
        mappings = cast("list[dict[str, object]]", payload["canonical_mappings"])
        customer = mappings[0]
        fields = cast("list[dict[str, object]]", customer["fields"])
        customer["fields"] = [field for field in fields if field["canonical_field"] != "provenance"]

    changed = _bundle_with_mutation(loaded, mutate)
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(loaded=changed)

    assert exc_info.value.code is (MappingCompilationErrorCode.CANONICAL_REQUIRED_FIELD_UNMAPPED)


def test_compiler_rejects_graph_property_conflicting_with_injected_evidence() -> None:
    """Prevent YAML from overwriting mandatory source provenance semantics."""
    loaded = _loaded()

    def mutate(payload: dict[str, object]) -> None:
        nodes = cast("list[dict[str, object]]", payload["graph_nodes"])
        properties = cast("list[dict[str, object]]", nodes[0]["properties"])
        properties[1]["graph_property"] = "source_database"

    changed = _bundle_with_mutation(loaded, mutate)
    with pytest.raises(MappingCompilationError) as exc_info:
        _compile(loaded=changed)

    assert exc_info.value.code is MappingCompilationErrorCode.GRAPH_PROPERTY_CONFLICT


def test_plan_digest_changes_with_catalog_or_configuration_evidence() -> None:
    """Bind execution evidence to catalog version and exact config digest."""
    baseline = _compile()
    catalog_changed = _compile(
        catalog=_catalog(asset=_asset_with_object_name("customerOutboundCDM_v2"))
    )
    config_changed = _compile(loaded=replace(_loaded(), configuration_digest="f" * 64))

    assert baseline.execution_plan_digest != catalog_changed.execution_plan_digest
    assert baseline.execution_plan_digest != config_changed.execution_plan_digest


def test_compiler_rejects_invalid_dependency_types() -> None:
    """Reject duck-typed or mutable compiler dependencies."""
    with pytest.raises(MappingCompilationError) as exc_info:
        compile_customer_profile_mapping(
            cast("LoadedDataPlatformConfiguration", object()),
            _catalog(asset=_asset()),
            build_customer_account_handler_registry(),
            build_customer_account_canonical_model_registry(),
        )

    assert exc_info.value.code is MappingCompilationErrorCode.INVALID_INPUT_TYPE
