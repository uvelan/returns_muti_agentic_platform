"""Adversarial tests for deterministic in-memory Customer normalization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from pydantic import ValidationError

from return_platform.data_platform.mapping import (
    CompiledCanonicalMappingPlan,
    CompiledFieldMappingPlan,
    HandlerExecutionContext,
    HandlerOutputType,
    HandlerPurpose,
    HandlerResult,
    MappingExecutionPlan,
    MappingHandler,
    NormalizationExecutionError,
    NormalizationExecutionErrorCode,
    NormalizationRejectionCode,
    SingleStringHandler,
    SourceDocumentEvidence,
    build_customer_account_canonical_model_registry,
    build_customer_account_handler_registry,
    compile_customer_profile_mapping,
    load_data_platform_mapping_configuration,
    normalize_customer_source_document,
)
from return_platform.data_platform.mapping import normalizer as normalizer_module
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

if TYPE_CHECKING:
    from return_platform.canonical import Customer

_CONFIG_DIR = Path(__file__).parents[1] / "config" / "data_platform"
_SOURCE_HASH = "a" * 64
_EXPECTED_ACCEPTED_RECORDS = 3


class _MutableNormalizationResult(Protocol):
    customer: Customer | None


@dataclass(frozen=True, slots=True)
class _RuntimeHandler:
    """Test handler with compiler-compatible metadata and adversarial runtime."""

    name: str
    purpose: HandlerPurpose
    output_type: HandlerOutputType
    result: object | None = None
    raises: bool = False
    input_arity: int = 1
    contract_version: str = "1.0"
    deterministic: bool = True

    def invoke(
        self,
        values: tuple[object, ...],
        context: HandlerExecutionContext,
    ) -> HandlerResult:
        """Return a deliberately invalid result or raise a programming defect."""
        del values, context
        if self.raises:
            msg = "unexpected handler defect"
            raise RuntimeError(msg)
        return cast("HandlerResult", self.result)


def _asset() -> AssetCatalogEntry:
    """Create one valid governed Customer CDM source asset."""
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
    """Compile the approved Customer foundation execution plan."""
    loaded = load_data_platform_mapping_configuration(_CONFIG_DIR)
    return compile_customer_profile_mapping(
        loaded,
        AssetCatalog(version="1.0", assets=(_asset(),)),
        build_customer_account_handler_registry(),
        build_customer_account_canonical_model_registry(),
    )


def _evidence(
    *,
    source_document_id: str = "P100",
    source_updated_at: datetime | None = None,
) -> SourceDocumentEvidence:
    """Create deterministic source evidence with no clock access."""
    updated_at = source_updated_at or datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    return SourceDocumentEvidence(
        source_document_id=source_document_id,
        source_updated_at=updated_at,
        source_version="17",
        source_event_id="evt-100",
        source_hash=_SOURCE_HASH,
        observed_at=datetime(2026, 7, 21, 4, 1, tzinfo=UTC),
    )


def _document() -> dict[str, object]:
    """Create one valid Customer document with two ordered accounts."""
    return {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"accountNumber": "203*C002"},
        ],
    }


def _replace_mapping(
    plan: MappingExecutionPlan,
    mapping_id: str,
    replacement: CompiledCanonicalMappingPlan,
) -> MappingExecutionPlan:
    """Replace one compiled mapping in an otherwise valid immutable plan."""
    mappings = tuple(
        replacement if item.definition.mapping_id == mapping_id else item
        for item in plan.canonical_mappings
    )
    return replace(plan, canonical_mappings=mappings)


def _mapping(plan: MappingExecutionPlan, mapping_id: str) -> CompiledCanonicalMappingPlan:
    """Resolve one mapping with a shorter test helper."""
    return plan.resolve_canonical_mapping(mapping_id)


def _replace_field(
    plan: MappingExecutionPlan,
    mapping_id: str,
    canonical_field: str,
    replacement_field: CompiledFieldMappingPlan,
) -> MappingExecutionPlan:
    """Replace one compiled field while preserving every other plan contract."""
    mapping = _mapping(plan, mapping_id)
    fields = tuple(
        replacement_field if item.definition.canonical_field == canonical_field else item
        for item in mapping.fields
    )
    return _replace_mapping(plan, mapping_id, replace(mapping, fields=fields))


def _replace_field_handler(
    plan: MappingExecutionPlan,
    mapping_id: str,
    canonical_field: str,
    handler: MappingHandler,
) -> MappingExecutionPlan:
    """Replace one field handler after compilation for runtime adversarial tests."""
    mapping = _mapping(plan, mapping_id)
    field = next(
        item for item in mapping.fields if item.definition.canonical_field == canonical_field
    )
    return _replace_field(
        plan,
        mapping_id,
        canonical_field,
        replace(field, handler=handler),
    )


def _replace_identity_handler(
    plan: MappingExecutionPlan,
    mapping_id: str,
    handler: MappingHandler,
) -> MappingExecutionPlan:
    """Replace one identity handler after compilation for runtime checks."""
    mapping = _mapping(plan, mapping_id)
    identity = replace(mapping.identity, handler=handler)
    return _replace_mapping(plan, mapping_id, replace(mapping, identity=identity))


def _replace_source_paths(
    plan: MappingExecutionPlan,
    mapping_id: str,
    canonical_field: str,
    source_paths: tuple[str, ...],
) -> MappingExecutionPlan:
    """Change one already compiled physical alias list for path execution tests."""
    mapping = _mapping(plan, mapping_id)
    field = next(
        item for item in mapping.fields if item.definition.canonical_field == canonical_field
    )
    definition = field.definition.model_copy(update={"source_paths": source_paths})
    return _replace_field(
        plan,
        mapping_id,
        canonical_field,
        replace(field, definition=definition),
    )


def _wrong_customer_reference(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Return a valid but unrelated Customer reference."""
    del value, context
    return "CUSTOMER_CDM:OTHER"


def _wrong_customer_key(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Return a syntactically valid identity that violates model semantics."""
    del value, context
    return "CUSTOMER_CDM:WRONG"


def test_normalizer_builds_customer_and_accounts_in_source_order() -> None:
    """Execute DOCUMENT and RECORD paths with deterministic handler invocation."""
    result = normalize_customer_source_document(_plan(), _evidence(), _document())

    assert result.complete is True
    assert result.accepted_count == _EXPECTED_ACCEPTED_RECORDS
    assert result.rejected_count == 0
    assert result.customer is not None
    assert result.customer.customer_key == "CUSTOMER_CDM:P100"
    assert result.customer.source_updated_at == datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    assert result.customer.provenance.source_database == "eventMessages"
    assert result.customer.provenance.source_asset == "customerOutboundCDM"
    assert tuple(account.account_number for account in result.customer_accounts) == (
        "202*C001",
        "203*C002",
    )
    assert tuple(account.customer_id for account in result.customer_accounts) == (
        "C001",
        "C002",
    )
    assert all(account.customer_key == "CUSTOMER_CDM:P100" for account in result.customer_accounts)
    assert tuple(account.provenance.source_record_id for account in result.customer_accounts) == (
        "202*C001",
        "203*C002",
    )


def test_normalizer_is_deterministic_and_does_not_mutate_input() -> None:
    """Return equal immutable output while leaving caller containers unchanged."""
    plan = _plan()
    evidence = _evidence()
    document = _document()
    original = {
        "partyId": document["partyId"],
        "custAccts": [dict(item) for item in cast("list[dict[str, str]]", document["custAccts"])],
    }

    first = normalize_customer_source_document(plan, evidence, document)
    second = normalize_customer_source_document(plan, evidence, document)

    assert first == second
    assert document == original
    mutable = cast("_MutableNormalizationResult", first)
    with pytest.raises(ValidationError):
        mutable.customer = None


def test_result_is_detached_from_subsequent_input_mutation() -> None:
    """Snapshot source structure before returning canonical records."""
    document = _document()
    result = normalize_customer_source_document(_plan(), _evidence(), document)

    document["partyId"] = "MUTATED"
    accounts = cast("list[dict[str, str]]", document["custAccts"])
    accounts[0]["accountNumber"] = "999*CHANGED"

    assert result.customer is not None
    assert result.customer.party_id == "P100"
    assert result.customer_accounts[0].account_number == "202*C001"


@pytest.mark.parametrize("account_container", [None, []])
def test_missing_or_empty_account_collection_is_valid(
    account_container: object,
) -> None:
    """Normalize a Customer without fabricating CustomerAccount records."""
    document: dict[str, object] = {"partyId": "P100"}
    if account_container is not None:
        document["custAccts"] = account_container

    result = normalize_customer_source_document(_plan(), _evidence(), document)

    assert result.customer is not None
    assert result.customer_accounts == ()
    assert result.rejections == ()


def test_mixed_valid_and_invalid_nested_records_are_isolated() -> None:
    """Keep healthy accounts visible while recording safe per-record failures."""
    document = {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"missing": "value"},
            17,
            {"accountNumber": "203*C002"},
        ],
    }

    result = normalize_customer_source_document(_plan(), _evidence(), document)

    assert tuple(account.account_number for account in result.customer_accounts) == (
        "202*C001",
        "203*C002",
    )
    assert tuple(rejection.record_index for rejection in result.rejections) == (1, 2)
    assert tuple(rejection.code for rejection in result.rejections) == (
        NormalizationRejectionCode.REQUIRED_FIELD_MISSING,
        NormalizationRejectionCode.RECORD_NOT_OBJECT,
    )


def test_duplicate_account_identity_rejects_every_colliding_record() -> None:
    """Never keep the first account silently when identity evidence collides."""
    document = {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"accountNumber": "202*C001"},
            {"accountNumber": "203*C002"},
        ],
    }

    result = normalize_customer_source_document(_plan(), _evidence(), document)

    assert tuple(account.account_number for account in result.customer_accounts) == ("203*C002",)
    duplicate_rejections = tuple(
        rejection
        for rejection in result.rejections
        if rejection.code is NormalizationRejectionCode.DUPLICATE_IDENTITY
    )
    assert tuple(item.record_index for item in duplicate_rejections) == (0, 1)


def test_document_scope_customer_reference_is_enforced() -> None:
    """Reject an account whose handler produces a different canonical parent."""
    handler = SingleStringHandler(
        name="customer_reference_key_v1",
        purpose=HandlerPurpose.FIELD,
        function=_wrong_customer_reference,
    )
    plan = _replace_field_handler(
        _plan(),
        "canonical.customer_account.v1",
        "customer_key",
        handler,
    )

    result = normalize_customer_source_document(plan, _evidence(), _document())

    assert result.customer is not None
    assert result.customer_accounts == ()
    assert tuple(rejection.code for rejection in result.rejections) == (
        NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED,
        NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED,
    )


def test_record_path_type_mismatch_is_safe_mapping_rejection() -> None:
    """Do not iterate a scalar as nested CustomerAccount records."""
    result = normalize_customer_source_document(
        _plan(),
        _evidence(),
        {"partyId": "P100", "custAccts": {"accountNumber": "202*C001"}},
    )

    assert result.customer is not None
    assert result.customer_accounts == ()
    assert result.rejections[0].code is (NormalizationRejectionCode.RECORD_PATH_INVALID)
    assert result.rejections[0].record_locator == "$.custAccts[]"


def test_ordered_aliases_accept_equal_values_and_reject_conflicts() -> None:
    """Use explicit precedence without silently discarding divergent evidence."""
    plan = _replace_source_paths(
        _plan(),
        "canonical.customer.v1",
        "party_id",
        ("partyPrimary", "partyId"),
    )
    equal_document = _document() | {"partyPrimary": "P100"}
    conflicting_document = _document() | {"partyPrimary": "P999"}

    accepted = normalize_customer_source_document(plan, _evidence(), equal_document)
    rejected = normalize_customer_source_document(
        plan,
        _evidence(),
        conflicting_document,
    )

    assert accepted.customer is not None
    assert rejected.customer is None
    assert rejected.rejections[0].code is NormalizationRejectionCode.ALIAS_CONFLICT


def test_null_primary_alias_falls_back_to_next_populated_alias() -> None:
    """Treat null as absent rather than blocking an approved fallback path."""
    plan = _replace_source_paths(
        _plan(),
        "canonical.customer.v1",
        "party_id",
        ("partyPrimary", "partyId"),
    )
    document = _document() | {"partyPrimary": None}

    result = normalize_customer_source_document(plan, _evidence(), document)

    assert result.customer is not None
    assert result.customer.party_id == "P100"


def test_alias_comparison_is_type_strict() -> None:
    """Treat Python-equal cross-type values such as True and 1 as conflicting."""
    plan = _replace_source_paths(
        _plan(),
        "canonical.customer.v1",
        "party_id",
        ("partyPrimary", "partyId"),
    )
    document = {"partyPrimary": True, "partyId": 1, "custAccts": []}

    result = normalize_customer_source_document(plan, _evidence(), document)

    assert result.customer is None
    assert result.rejections[0].code is NormalizationRejectionCode.ALIAS_CONFLICT


def test_field_path_type_mismatch_and_cardinality_are_distinct() -> None:
    """Differentiate malformed traversal from a multi-value scalar mapping."""
    mismatch_plan = _replace_source_paths(
        _plan(),
        "canonical.customer.v1",
        "party_id",
        ("identity.partyId",),
    )
    cardinality_plan = _replace_source_paths(
        _plan(),
        "canonical.customer.v1",
        "party_id",
        ("partyIds[]",),
    )

    mismatch = normalize_customer_source_document(
        mismatch_plan,
        _evidence(),
        {"identity": "P100", "partyId": "P100", "custAccts": []},
    )
    cardinality = normalize_customer_source_document(
        cardinality_plan,
        _evidence(),
        {"partyIds": ["P100", "P100"], "partyId": "P100", "custAccts": []},
    )

    assert mismatch.rejections[0].code is (NormalizationRejectionCode.FIELD_PATH_TYPE_MISMATCH)
    assert cardinality.rejections[0].code is (
        NormalizationRejectionCode.FIELD_PATH_CARDINALITY_INVALID
    )


def test_direct_field_rejects_non_string_without_pydantic_coercion() -> None:
    """Reject integer party identity before canonical model construction."""
    result = normalize_customer_source_document(
        _plan(),
        _evidence(source_document_id="100"),
        {"partyId": 100, "custAccts": []},
    )

    assert result.customer is None
    assert result.rejections[0].code is (NormalizationRejectionCode.FIELD_VALUE_TYPE_INVALID)


def test_handler_rejection_records_stable_cause_without_source_value() -> None:
    """Preserve safe handler code while masking the rejected identity value."""
    sensitive_value = "confidential party value"
    result = normalize_customer_source_document(
        _plan(),
        _evidence(source_document_id="P100"),
        {"partyId": sensitive_value, "custAccts": []},
    )

    dumped = result.model_dump_json()
    assert result.customer is None
    assert result.rejections[0].code is NormalizationRejectionCode.HANDLER_REJECTED
    assert result.rejections[0].cause_code == "INVALID_INPUT_VALUE"
    assert sensitive_value not in dumped


def test_handler_runtime_output_type_is_enforced() -> None:
    """Reject a handler implementation that lies about its declared output."""
    handler = _RuntimeHandler(
        name="customer_cdm_source_system_v1",
        purpose=HandlerPurpose.FIELD,
        output_type=HandlerOutputType.STRING,
        result=17,
    )
    plan = _replace_field_handler(
        _plan(),
        "canonical.customer.v1",
        "source_system",
        handler,
    )

    result = normalize_customer_source_document(plan, _evidence(), _document())

    assert result.customer is None
    assert result.rejections[0].code is (NormalizationRejectionCode.HANDLER_OUTPUT_INVALID)


def test_unexpected_handler_exception_aborts_with_safe_global_error() -> None:
    """Treat programming defects as global failures, not bad source records."""
    handler = _RuntimeHandler(
        name="customer_cdm_source_system_v1",
        purpose=HandlerPurpose.FIELD,
        output_type=HandlerOutputType.STRING,
        raises=True,
    )
    plan = _replace_field_handler(
        _plan(),
        "canonical.customer.v1",
        "source_system",
        handler,
    )

    with pytest.raises(NormalizationExecutionError) as exc_info:
        normalize_customer_source_document(plan, _evidence(), _document())

    assert exc_info.value.code is (NormalizationExecutionErrorCode.HANDLER_CONTRACT_VIOLATION)
    assert "unexpected handler defect" not in str(exc_info.value)


def test_identity_handler_rejection_and_invalid_output_are_distinct() -> None:
    """Separate bad identity input from a broken handler implementation."""
    rejecting = SingleStringHandler(
        name="customer_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        function=_wrong_customer_key,
    )
    invalid_output = _RuntimeHandler(
        name="customer_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        output_type=HandlerOutputType.STRING,
        result=17,
    )
    rejected_result = normalize_customer_source_document(
        _replace_identity_handler(_plan(), "canonical.customer.v1", rejecting),
        _evidence(),
        _document(),
    )
    invalid_result = normalize_customer_source_document(
        _replace_identity_handler(_plan(), "canonical.customer.v1", invalid_output),
        _evidence(),
        _document(),
    )

    assert rejected_result.rejections[0].code is (
        NormalizationRejectionCode.CANONICAL_VALIDATION_FAILED
    )
    assert invalid_result.rejections[0].code is (NormalizationRejectionCode.IDENTITY_OUTPUT_INVALID)


def test_source_evidence_mismatch_fails_closed_per_record() -> None:
    """Require partyId to match source adapter document identity evidence."""
    result = normalize_customer_source_document(
        _plan(),
        _evidence(source_document_id="OTHER"),
        _document(),
    )

    assert result.customer is None
    assert result.customer_accounts == ()
    assert all(
        rejection.code is NormalizationRejectionCode.HANDLER_REJECTED
        for rejection in result.rejections
    )
    assert all(rejection.cause_code == "SOURCE_CONTEXT_MISMATCH" for rejection in result.rejections)


def test_source_structure_cycle_and_non_string_key_are_rejected() -> None:
    """Reject cyclic or non-document mapping structures before field execution."""
    cyclic: dict[str, object] = {"partyId": "P100"}
    cyclic["loop"] = cyclic

    with pytest.raises(NormalizationExecutionError) as cycle_error:
        normalize_customer_source_document(_plan(), _evidence(), cyclic)
    with pytest.raises(NormalizationExecutionError) as key_error:
        normalize_customer_source_document(
            _plan(),
            _evidence(),
            cast("dict[str, object]", {1: "invalid"}),
        )

    assert cycle_error.value.code is (
        NormalizationExecutionErrorCode.SOURCE_DOCUMENT_STRUCTURE_INVALID
    )
    assert key_error.value.code is (
        NormalizationExecutionErrorCode.SOURCE_DOCUMENT_STRUCTURE_INVALID
    )


def test_source_document_node_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound structural copying before any canonical handler is invoked."""
    monkeypatch.setattr(normalizer_module, "MAX_SOURCE_DOCUMENT_NODES", 4)

    with pytest.raises(NormalizationExecutionError) as exc_info:
        normalize_customer_source_document(_plan(), _evidence(), _document())

    assert exc_info.value.code is (NormalizationExecutionErrorCode.SOURCE_DOCUMENT_LIMIT_EXCEEDED)


def test_invalid_inputs_and_unsupported_plan_fail_before_execution() -> None:
    """Reject wrong dependency types and incomplete manually altered plans."""
    plan = _plan()
    unsupported = replace(plan, canonical_mappings=plan.canonical_mappings[:1])

    with pytest.raises(NormalizationExecutionError) as plan_error:
        normalize_customer_source_document(
            unsupported,
            _evidence(),
            _document(),
        )
    with pytest.raises(NormalizationExecutionError) as evidence_error:
        normalize_customer_source_document(
            plan,
            cast("SourceDocumentEvidence", object()),
            _document(),
        )
    with pytest.raises(NormalizationExecutionError) as document_error:
        normalize_customer_source_document(
            plan,
            _evidence(),
            cast("dict[str, object]", object()),
        )

    assert plan_error.value.code is NormalizationExecutionErrorCode.PLAN_UNSUPPORTED
    assert evidence_error.value.code is (NormalizationExecutionErrorCode.INVALID_INPUT_TYPE)
    assert document_error.value.code is (NormalizationExecutionErrorCode.INVALID_INPUT_TYPE)


def test_result_binds_plan_and_source_evidence() -> None:
    """Carry immutable plan identity and source evidence into output."""
    plan = _plan()
    evidence = _evidence()

    result = normalize_customer_source_document(plan, evidence, _document())

    assert result.normalizer_version == "1.0"
    assert result.execution_plan_digest == plan.execution_plan_digest
    assert result.source_document_id == evidence.source_document_id
    assert result.customer is not None
    assert result.customer.provenance.source_version == "17"
    assert result.customer.provenance.source_event_id == "evt-100"
    assert result.customer.provenance.source_hash == _SOURCE_HASH
    assert result.customer.provenance.observed_at == evidence.observed_at
