"""Deterministic in-memory normalization for the Customer foundation profile."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Never, cast

from pydantic import Field, ValidationError

from return_platform.canonical import Customer, CustomerAccount
from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    Sha256Digest,
    SourceProvenance,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.mapping.compiler import (
    CompiledCanonicalMappingPlan,
    CompiledFieldMappingPlan,
    MappingExecutionPlan,
)
from return_platform.data_platform.mapping.contracts import (
    CanonicalEntityType,
    CanonicalFieldPath,
    MappingIdentifier,
    PhysicalPathScope,
)
from return_platform.data_platform.mapping.handlers import (
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerOutputType,
)

__all__ = [
    "CustomerNormalizationResult",
    "NormalizationExecutionError",
    "NormalizationExecutionErrorCode",
    "NormalizationRejection",
    "NormalizationRejectionCode",
    "SourceDocumentEvidence",
    "normalize_customer_source_document",
]

MAX_SOURCE_DOCUMENT_DEPTH: Final = 64
MAX_SOURCE_DOCUMENT_NODES: Final = 250_000
_NORMALIZER_VERSION: Final = "1.0"
_EXPECTED_MAPPING_IDS: Final = frozenset(
    {
        "canonical.customer.v1",
        "canonical.customer_account.v1",
    }
)
_MISSING: Final = object()

NonNegativeRecordIndex = Annotated[int, Field(strict=True, ge=0)]


type CanonicalCustomerRecord = Customer | CustomerAccount
type FrozenSourceValue = object
type FrozenSourceMapping = Mapping[str, FrozenSourceValue]


class NormalizationExecutionErrorCode(StrEnum):
    """Stable safe codes for document-level normalization failures."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    PLAN_UNSUPPORTED = "PLAN_UNSUPPORTED"
    SOURCE_DOCUMENT_LIMIT_EXCEEDED = "SOURCE_DOCUMENT_LIMIT_EXCEEDED"
    SOURCE_DOCUMENT_STRUCTURE_INVALID = "SOURCE_DOCUMENT_STRUCTURE_INVALID"
    HANDLER_CONTRACT_VIOLATION = "HANDLER_CONTRACT_VIOLATION"


_EXECUTION_SAFE_MESSAGES: Final = {
    NormalizationExecutionErrorCode.INVALID_INPUT_TYPE: (
        "Normalization inputs have invalid types."
    ),
    NormalizationExecutionErrorCode.PLAN_UNSUPPORTED: (
        "The execution plan is not the supported Customer foundation profile."
    ),
    NormalizationExecutionErrorCode.SOURCE_DOCUMENT_LIMIT_EXCEEDED: (
        "The source document exceeds bounded normalization limits."
    ),
    NormalizationExecutionErrorCode.SOURCE_DOCUMENT_STRUCTURE_INVALID: (
        "The source document structure is invalid."
    ),
    NormalizationExecutionErrorCode.HANDLER_CONTRACT_VIOLATION: (
        "A compiled mapping handler violated its runtime contract."
    ),
}


class NormalizationExecutionError(ValueError):
    """Safe document-level normalization failure."""

    def __init__(self, code: NormalizationExecutionErrorCode) -> None:
        """Initialize one bounded public execution error."""
        self.code = code
        self.safe_message = _EXECUTION_SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


class NormalizationRejectionCode(StrEnum):
    """Stable safe codes for one rejected canonical source record."""

    RECORD_PATH_INVALID = "RECORD_PATH_INVALID"
    RECORD_NOT_OBJECT = "RECORD_NOT_OBJECT"
    REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"
    FIELD_PATH_TYPE_MISMATCH = "FIELD_PATH_TYPE_MISMATCH"
    FIELD_PATH_CARDINALITY_INVALID = "FIELD_PATH_CARDINALITY_INVALID"
    ALIAS_CONFLICT = "ALIAS_CONFLICT"
    FIELD_VALUE_TYPE_INVALID = "FIELD_VALUE_TYPE_INVALID"
    HANDLER_REJECTED = "HANDLER_REJECTED"
    HANDLER_OUTPUT_INVALID = "HANDLER_OUTPUT_INVALID"
    IDENTITY_COMPONENT_MISSING = "IDENTITY_COMPONENT_MISSING"
    IDENTITY_HANDLER_REJECTED = "IDENTITY_HANDLER_REJECTED"
    IDENTITY_OUTPUT_INVALID = "IDENTITY_OUTPUT_INVALID"
    CANONICAL_VALIDATION_FAILED = "CANONICAL_VALIDATION_FAILED"
    DEPENDENCY_NOT_SATISFIED = "DEPENDENCY_NOT_SATISFIED"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"


_REJECTION_SAFE_MESSAGES: Final = {
    NormalizationRejectionCode.RECORD_PATH_INVALID: (
        "The configured nested record path is incompatible with the source document."
    ),
    NormalizationRejectionCode.RECORD_NOT_OBJECT: ("A selected source record is not an object."),
    NormalizationRejectionCode.REQUIRED_FIELD_MISSING: ("A required physical field is missing."),
    NormalizationRejectionCode.FIELD_PATH_TYPE_MISMATCH: (
        "A physical field path crosses an incompatible source value."
    ),
    NormalizationRejectionCode.FIELD_PATH_CARDINALITY_INVALID: (
        "A physical field path produced more than one value."
    ),
    NormalizationRejectionCode.ALIAS_CONFLICT: (
        "Multiple populated aliases contain conflicting values."
    ),
    NormalizationRejectionCode.FIELD_VALUE_TYPE_INVALID: (
        "A physical field value has an incompatible type."
    ),
    NormalizationRejectionCode.HANDLER_REJECTED: (
        "A code-owned field handler rejected the source value."
    ),
    NormalizationRejectionCode.HANDLER_OUTPUT_INVALID: (
        "A code-owned field handler returned an incompatible value."
    ),
    NormalizationRejectionCode.IDENTITY_COMPONENT_MISSING: (
        "A required identity component was not produced."
    ),
    NormalizationRejectionCode.IDENTITY_HANDLER_REJECTED: (
        "The code-owned identity handler rejected the normalized components."
    ),
    NormalizationRejectionCode.IDENTITY_OUTPUT_INVALID: (
        "The code-owned identity handler returned an incompatible value."
    ),
    NormalizationRejectionCode.CANONICAL_VALIDATION_FAILED: (
        "The normalized record failed canonical model validation."
    ),
    NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED: (
        "A required canonical parent record was not accepted."
    ),
    NormalizationRejectionCode.DUPLICATE_IDENTITY: (
        "Multiple source records produced the same canonical identity."
    ),
}


class SourceDocumentEvidence(CanonicalBaseModel):
    """Immutable evidence supplied with one already-fetched source document."""

    source_document_id: CanonicalIdentifier
    source_updated_at: UtcDateTime | None = None
    source_version: VersionReference | None = None
    source_event_id: NonBlankText | None = None
    source_hash: Sha256Digest | None = None
    observed_at: UtcDateTime


class NormalizationRejection(CanonicalBaseModel):
    """Safe evidence for one source record rejected before projection."""

    mapping_id: MappingIdentifier
    entity_type: CanonicalEntityType
    record_locator: NonBlankText
    record_index: NonNegativeRecordIndex | None = None
    canonical_field: CanonicalFieldPath | None = None
    code: NormalizationRejectionCode
    safe_message: NonBlankText
    cause_code: NonBlankText | None = None


class CustomerNormalizationResult(CanonicalBaseModel):
    """Immutable accepted Customer records and deterministic rejection evidence."""

    normalizer_version: VersionReference
    execution_plan_digest: Sha256Digest
    source_document_id: CanonicalIdentifier
    customer: Customer | None
    customer_accounts: tuple[CustomerAccount, ...]
    rejections: tuple[NormalizationRejection, ...]

    @property
    def accepted_count(self) -> int:
        """Return the number of accepted canonical records."""
        return int(self.customer is not None) + len(self.customer_accounts)

    @property
    def rejected_count(self) -> int:
        """Return the number of rejected source records."""
        return len(self.rejections)

    @property
    def complete(self) -> bool:
        """Return whether the document normalized without any rejection."""
        return self.customer is not None and not self.rejections


@dataclass(slots=True)
class _SnapshotBudget:
    """Mutable internal counter used only while copying source structure."""

    nodes: int = 0

    def consume(self) -> None:
        """Consume one bounded source-structure node."""
        self.nodes += 1
        if self.nodes > MAX_SOURCE_DOCUMENT_NODES:
            _raise_execution_error(NormalizationExecutionErrorCode.SOURCE_DOCUMENT_LIMIT_EXCEEDED)


@dataclass(frozen=True, slots=True)
class _PathResolution:
    """Internal deterministic path-resolution result."""

    values: tuple[object, ...]
    type_mismatch: bool = False


@dataclass(frozen=True, slots=True)
class _SelectedRecord:
    """One deterministic source record selected for canonical normalization."""

    record: FrozenSourceMapping
    record_locator: str
    record_index: int | None


@dataclass(frozen=True, slots=True)
class _NormalizedCandidate:
    """One canonical candidate before duplicate and dependency checks."""

    record: CanonicalCustomerRecord
    record_locator: str
    record_index: int | None


@dataclass(frozen=True, slots=True)
class _AliasSelection:
    """One ordered physical alias selection or safe failure description."""

    value: object = _MISSING
    code: NormalizationRejectionCode | None = None


@dataclass(frozen=True, slots=True)
class _RejectionLocation:
    """Structural source location used to create safe rejection evidence."""

    locator: str
    record_index: int | None


@dataclass(frozen=True, slots=True)
class _FieldOutcome:
    """One canonical field value or one safe record rejection."""

    value: object = _MISSING
    rejection: NormalizationRejection | None = None


class _SourceStructureError(ValueError):
    """Internal marker for malformed or cyclic source structure."""


def _raise_execution_error(code: NormalizationExecutionErrorCode) -> Never:
    """Raise one safe document-level normalization error."""
    raise NormalizationExecutionError(code)


def _snapshot_source_value(
    value: object,
    *,
    depth: int,
    active_containers: set[int],
    budget: _SnapshotBudget,
) -> object:
    """Copy source container structure while preserving scalar BSON values."""
    budget.consume()
    if depth > MAX_SOURCE_DOCUMENT_DEPTH:
        _raise_execution_error(NormalizationExecutionErrorCode.SOURCE_DOCUMENT_LIMIT_EXCEEDED)

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_containers:
            raise _SourceStructureError
        active_containers.add(container_id)
        try:
            items = tuple(value.items())
            copied: dict[str, object] = {}
            for key, nested_value in items:
                if not isinstance(key, str):
                    raise _SourceStructureError
                copied[key] = _snapshot_source_value(
                    nested_value,
                    depth=depth + 1,
                    active_containers=active_containers,
                    budget=budget,
                )
            return MappingProxyType(copied)
        finally:
            active_containers.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            raise _SourceStructureError
        active_containers.add(container_id)
        try:
            return tuple(
                _snapshot_source_value(
                    nested_value,
                    depth=depth + 1,
                    active_containers=active_containers,
                    budget=budget,
                )
                for nested_value in tuple(value)
            )
        finally:
            active_containers.remove(container_id)

    return value


def _snapshot_source_document(document: Mapping[str, object]) -> FrozenSourceMapping:
    """Create one bounded structural snapshot without retaining caller containers."""
    try:
        snapshot = _snapshot_source_value(
            document,
            depth=0,
            active_containers=set(),
            budget=_SnapshotBudget(),
        )
    except NormalizationExecutionError:
        raise
    except (RuntimeError, _SourceStructureError) as error:
        raise NormalizationExecutionError(
            NormalizationExecutionErrorCode.SOURCE_DOCUMENT_STRUCTURE_INVALID
        ) from error

    if not isinstance(snapshot, Mapping):
        _raise_execution_error(NormalizationExecutionErrorCode.SOURCE_DOCUMENT_STRUCTURE_INVALID)
    return cast("FrozenSourceMapping", snapshot)


def _path_segments(path: str) -> tuple[tuple[str, bool], ...]:
    """Split one already contract-validated physical field path."""
    return tuple(
        (segment[:-2], True) if segment.endswith("[]") else (segment, False)
        for segment in path.split(".")
    )


def _resolve_path(root: object, path: str) -> _PathResolution:
    """Resolve one bounded field path with deterministic array expansion."""
    current: tuple[object, ...] = (root,)
    for key, expands_array in _path_segments(path):
        next_values: list[object] = []
        for current_value in current:
            if not isinstance(current_value, Mapping):
                return _PathResolution((), type_mismatch=True)
            if key not in current_value:
                continue
            nested_value = current_value[key]
            if expands_array:
                if nested_value is None:
                    continue
                if not isinstance(nested_value, tuple):
                    return _PathResolution((), type_mismatch=True)
                next_values.extend(nested_value)
            else:
                next_values.append(nested_value)
        current = tuple(next_values)
        if not current:
            break
    return _PathResolution(current)


def _record_locator(record_path: str | None, record_index: int | None) -> str:
    """Build a deterministic safe structural locator without source values."""
    if record_path is None:
        return "$"
    rendered = record_path.replace("[]", "")
    if record_index is None:
        return f"$.{record_path}"
    return f"$.{rendered}[{record_index}]"


def _rejection(
    mapping: CompiledCanonicalMappingPlan,
    location: _RejectionLocation,
    code: NormalizationRejectionCode,
    canonical_field: str | None = None,
    cause_code: str | None = None,
) -> NormalizationRejection:
    """Build one safe rejection without echoing physical source values."""
    return NormalizationRejection(
        mapping_id=mapping.definition.mapping_id,
        entity_type=mapping.definition.entity_type,
        record_locator=location.locator,
        record_index=location.record_index,
        canonical_field=canonical_field,
        code=code,
        safe_message=_REJECTION_SAFE_MESSAGES[code],
        cause_code=cause_code,
    )


def _selected_location(selected: _SelectedRecord) -> _RejectionLocation:
    """Return the safe structural location for one selected source record."""
    return _RejectionLocation(selected.record_locator, selected.record_index)


def _select_records(
    mapping: CompiledCanonicalMappingPlan,
    document: FrozenSourceMapping,
) -> tuple[tuple[_SelectedRecord, ...], tuple[NormalizationRejection, ...]]:
    """Select root or nested source records in deterministic source order."""
    record_path = mapping.definition.record_path
    if record_path is None:
        return (
            (_SelectedRecord(document, _record_locator(None, None), None),),
            (),
        )

    resolved = _resolve_path(document, record_path)
    if resolved.type_mismatch:
        location = _RejectionLocation(_record_locator(record_path, None), None)
        return (
            (),
            (
                _rejection(
                    mapping,
                    location,
                    NormalizationRejectionCode.RECORD_PATH_INVALID,
                ),
            ),
        )

    records: list[_SelectedRecord] = []
    rejections: list[NormalizationRejection] = []
    for record_index, value in enumerate(resolved.values):
        locator = _record_locator(record_path, record_index)
        location = _RejectionLocation(locator, record_index)
        if not isinstance(value, Mapping):
            rejections.append(
                _rejection(
                    mapping,
                    location,
                    NormalizationRejectionCode.RECORD_NOT_OBJECT,
                )
            )
            continue
        records.append(
            _SelectedRecord(
                record=cast("FrozenSourceMapping", value),
                record_locator=locator,
                record_index=record_index,
            )
        )
    return tuple(records), tuple(rejections)


def _strictly_equal(left: object, right: object) -> bool:
    """Compare aliases without Python's cross-type equality coercion."""
    if type(left) is not type(right):
        return False
    try:
        return bool(left == right)
    except (RuntimeError, TypeError, ValueError):
        return False


def _select_alias(
    field: CompiledFieldMappingPlan,
    *,
    document: FrozenSourceMapping,
    record: FrozenSourceMapping,
) -> _AliasSelection:
    """Select the first populated alias while rejecting conflicting evidence."""
    root: object = document if field.definition.path_scope is PhysicalPathScope.DOCUMENT else record
    populated: list[object] = []
    for source_path in field.definition.source_paths:
        resolved = _resolve_path(root, source_path)
        if resolved.type_mismatch:
            return _AliasSelection(code=NormalizationRejectionCode.FIELD_PATH_TYPE_MISMATCH)
        if len(resolved.values) > 1:
            return _AliasSelection(code=NormalizationRejectionCode.FIELD_PATH_CARDINALITY_INVALID)
        if not resolved.values:
            continue
        value = resolved.values[0]
        if value is None:
            continue
        populated.append(value)

    if not populated:
        return _AliasSelection()
    first = populated[0]
    if any(not _strictly_equal(first, other) for other in populated[1:]):
        return _AliasSelection(code=NormalizationRejectionCode.ALIAS_CONFLICT)
    return _AliasSelection(value=first)


def _handler_context(
    plan: MappingExecutionPlan,
    mapping: CompiledCanonicalMappingPlan,
    evidence: SourceDocumentEvidence,
) -> HandlerExecutionContext:
    """Build immutable runtime evidence for one compiled canonical mapping."""
    asset = mapping.source.catalog_asset
    source_definition = mapping.source.definition
    return HandlerExecutionContext(
        source_id=source_definition.source_id,
        catalog_asset_id=source_definition.catalog_asset_id,
        source_system=source_definition.source_system,
        source_database=asset.database,
        source_asset=asset.object_name,
        source_document_id=evidence.source_document_id,
        source_updated_at=evidence.source_updated_at,
        source_version=evidence.source_version,
        source_event_id=evidence.source_event_id,
        source_hash=evidence.source_hash,
        observed_at=evidence.observed_at,
        mapping_version=mapping.definition.version,
        configuration_version=plan.schema_version,
        configuration_digest=plan.configuration_digest,
    )


def _handler_output_is_valid(
    field: CompiledFieldMappingPlan,
    value: object,
) -> bool:
    """Verify runtime output against metadata already checked by the compiler."""
    if field.expected_output_type is HandlerOutputType.STRING:
        return isinstance(value, str)
    if field.expected_output_type is HandlerOutputType.SOURCE_PROVENANCE:
        return isinstance(value, SourceProvenance)
    return False


def _synchronize_provenance_metadata(
    mapping: CompiledCanonicalMappingPlan,
    payload: dict[str, object],
) -> None:
    """Populate code-owned duplicate metadata fields from provenance evidence."""
    provenance = payload.get("provenance")
    if not isinstance(provenance, SourceProvenance):
        return
    if (
        "source_updated_at" in mapping.model_type.model_fields
        and "source_updated_at" not in payload
    ):
        payload["source_updated_at"] = provenance.source_updated_at


def _field_outcome(
    field: CompiledFieldMappingPlan,
    *,
    mapping: CompiledCanonicalMappingPlan,
    selected: _SelectedRecord,
    document: FrozenSourceMapping,
    context: HandlerExecutionContext,
) -> _FieldOutcome:
    """Resolve and transform one physical field into one canonical value."""
    location = _selected_location(selected)
    selection = _select_alias(field, document=document, record=selected.record)
    outcome = _FieldOutcome()

    if selection.code is not None:
        outcome = _FieldOutcome(
            rejection=_rejection(
                mapping,
                location,
                selection.code,
                field.definition.canonical_field,
            )
        )
    elif selection.value is _MISSING:
        if field.definition.required:
            outcome = _FieldOutcome(
                rejection=_rejection(
                    mapping,
                    location,
                    NormalizationRejectionCode.REQUIRED_FIELD_MISSING,
                    field.definition.canonical_field,
                )
            )
    elif field.handler is None:
        if isinstance(selection.value, str):
            outcome = _FieldOutcome(value=selection.value)
        else:
            outcome = _FieldOutcome(
                rejection=_rejection(
                    mapping,
                    location,
                    NormalizationRejectionCode.FIELD_VALUE_TYPE_INVALID,
                    field.definition.canonical_field,
                )
            )
    else:
        outcome = _invoke_field_handler(
            field,
            mapping=mapping,
            location=location,
            value=selection.value,
            context=context,
        )
    return outcome


def _invoke_field_handler(
    field: CompiledFieldMappingPlan,
    *,
    mapping: CompiledCanonicalMappingPlan,
    location: _RejectionLocation,
    value: object,
    context: HandlerExecutionContext,
) -> _FieldOutcome:
    """Invoke one compiled field handler and enforce its declared output type."""
    handler = field.handler
    if handler is None:
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    try:
        handled = handler.invoke((value,), context)
    except HandlerInvocationError as error:
        return _FieldOutcome(
            rejection=_rejection(
                mapping,
                location,
                NormalizationRejectionCode.HANDLER_REJECTED,
                field.definition.canonical_field,
                error.code.value,
            )
        )
    except Exception as error:
        raise NormalizationExecutionError(
            NormalizationExecutionErrorCode.HANDLER_CONTRACT_VIOLATION
        ) from error

    if _handler_output_is_valid(field, handled):
        return _FieldOutcome(value=handled)
    return _FieldOutcome(
        rejection=_rejection(
            mapping,
            location,
            NormalizationRejectionCode.HANDLER_OUTPUT_INVALID,
            field.definition.canonical_field,
        )
    )


def _build_payload(
    plan: MappingExecutionPlan,
    mapping: CompiledCanonicalMappingPlan,
    selected: _SelectedRecord,
    document: FrozenSourceMapping,
    evidence: SourceDocumentEvidence,
) -> tuple[dict[str, object] | None, NormalizationRejection | None]:
    """Build normalized canonical fields for one selected source record."""
    payload: dict[str, object] = {}
    context = _handler_context(plan, mapping, evidence)
    for field in mapping.fields:
        outcome = _field_outcome(
            field,
            mapping=mapping,
            selected=selected,
            document=document,
            context=context,
        )
        if outcome.rejection is not None:
            return None, outcome.rejection
        if outcome.value is not _MISSING:
            payload[field.definition.canonical_field] = outcome.value
    _synchronize_provenance_metadata(mapping, payload)
    return payload, None


def _build_identity(
    mapping: CompiledCanonicalMappingPlan,
    selected: _SelectedRecord,
    payload: dict[str, object],
    context: HandlerExecutionContext,
) -> NormalizationRejection | None:
    """Invoke the compiled identity handler using normalized components."""
    location = _selected_location(selected)
    identity_values: list[object] = []
    for component_field in mapping.identity.component_fields:
        component_value = payload.get(component_field, _MISSING)
        if component_value is _MISSING:
            return _rejection(
                mapping,
                location,
                NormalizationRejectionCode.IDENTITY_COMPONENT_MISSING,
                component_field,
            )
        identity_values.append(component_value)

    try:
        identity_value = mapping.identity.handler.invoke(
            tuple(identity_values),
            context,
        )
    except HandlerInvocationError as error:
        return _rejection(
            mapping,
            location,
            NormalizationRejectionCode.IDENTITY_HANDLER_REJECTED,
            mapping.identity.key_field,
            error.code.value,
        )
    except Exception as error:
        raise NormalizationExecutionError(
            NormalizationExecutionErrorCode.HANDLER_CONTRACT_VIOLATION
        ) from error

    if not isinstance(identity_value, str):
        return _rejection(
            mapping,
            location,
            NormalizationRejectionCode.IDENTITY_OUTPUT_INVALID,
            mapping.identity.key_field,
        )
    payload[mapping.identity.key_field] = identity_value
    return None


def _validate_canonical_record(
    mapping: CompiledCanonicalMappingPlan,
    selected: _SelectedRecord,
    payload: dict[str, object],
) -> tuple[CanonicalCustomerRecord | None, NormalizationRejection | None]:
    """Validate one complete payload through its registered canonical model."""
    try:
        normalized = mapping.model_type.model_validate(payload)
    except ValidationError as error:
        errors = error.errors()
        cause_code = str(errors[0]["type"]) if errors else None
        return None, _rejection(
            mapping,
            _selected_location(selected),
            NormalizationRejectionCode.CANONICAL_VALIDATION_FAILED,
            cause_code=cause_code,
        )

    if not isinstance(normalized, (Customer, CustomerAccount)):
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    return normalized, None


def _normalize_selected_record(
    plan: MappingExecutionPlan,
    mapping: CompiledCanonicalMappingPlan,
    selected: _SelectedRecord,
    document: FrozenSourceMapping,
    evidence: SourceDocumentEvidence,
) -> tuple[_NormalizedCandidate | None, NormalizationRejection | None]:
    """Normalize one source record through fields, identity, and model validation."""
    payload, rejection = _build_payload(
        plan,
        mapping,
        selected,
        document,
        evidence,
    )
    if rejection is not None or payload is None:
        return None, rejection

    context = _handler_context(plan, mapping, evidence)
    rejection = _build_identity(mapping, selected, payload, context)
    if rejection is not None:
        return None, rejection

    normalized, rejection = _validate_canonical_record(mapping, selected, payload)
    if normalized is None:
        return None, rejection
    return (
        _NormalizedCandidate(
            record=normalized,
            record_locator=selected.record_locator,
            record_index=selected.record_index,
        ),
        None,
    )


def _validate_plan(plan: MappingExecutionPlan) -> None:
    """Require the exact compiled Customer foundation plan."""
    mapping_ids = frozenset(mapping.definition.mapping_id for mapping in plan.canonical_mappings)
    entity_types = Counter(mapping.definition.entity_type for mapping in plan.canonical_mappings)
    if mapping_ids != _EXPECTED_MAPPING_IDS:
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    if entity_types != Counter(
        {
            CanonicalEntityType.CUSTOMER: 1,
            CanonicalEntityType.CUSTOMER_ACCOUNT: 1,
        }
    ):
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    customer_mapping = plan.resolve_canonical_mapping("canonical.customer.v1")
    account_mapping = plan.resolve_canonical_mapping("canonical.customer_account.v1")
    if customer_mapping.model_type is not Customer:
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    if account_mapping.model_type is not CustomerAccount:
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)
    if account_mapping.definition.depends_on != ("canonical.customer.v1",):
        _raise_execution_error(NormalizationExecutionErrorCode.PLAN_UNSUPPORTED)


def _normalize_mapping(
    plan: MappingExecutionPlan,
    mapping: CompiledCanonicalMappingPlan,
    document: FrozenSourceMapping,
    evidence: SourceDocumentEvidence,
) -> tuple[tuple[_NormalizedCandidate, ...], tuple[NormalizationRejection, ...]]:
    """Normalize every selected record for one canonical mapping."""
    selected_records, selection_rejections = _select_records(mapping, document)
    candidates: list[_NormalizedCandidate] = []
    rejections = list(selection_rejections)
    for selected in selected_records:
        candidate, rejection = _normalize_selected_record(
            plan,
            mapping,
            selected,
            document,
            evidence,
        )
        if candidate is not None:
            candidates.append(candidate)
        if rejection is not None:
            rejections.append(rejection)
    return tuple(candidates), tuple(rejections)


def _remove_duplicate_accounts(
    mapping: CompiledCanonicalMappingPlan,
    candidates: tuple[_NormalizedCandidate, ...],
) -> tuple[tuple[_NormalizedCandidate, ...], tuple[NormalizationRejection, ...]]:
    """Reject every account participating in one canonical identity collision."""
    key_counts = Counter(
        cast("CustomerAccount", candidate.record).account_key for candidate in candidates
    )
    accepted: list[_NormalizedCandidate] = []
    rejected: list[NormalizationRejection] = []
    for candidate in candidates:
        account = cast("CustomerAccount", candidate.record)
        if key_counts[account.account_key] == 1:
            accepted.append(candidate)
            continue
        rejected.append(
            _rejection(
                mapping,
                _RejectionLocation(candidate.record_locator, candidate.record_index),
                NormalizationRejectionCode.DUPLICATE_IDENTITY,
                "account_key",
            )
        )
    return tuple(accepted), tuple(rejected)


def _rejection_sort_key(
    rejection: NormalizationRejection,
) -> tuple[int, int, str, str]:
    """Sort safe evidence by canonical stage and source-record order."""
    mapping_rank = 0 if rejection.entity_type is CanonicalEntityType.CUSTOMER else 1
    record_rank = -1 if rejection.record_index is None else rejection.record_index
    field_rank = rejection.canonical_field or ""
    return mapping_rank, record_rank, field_rank, rejection.code.value


def normalize_customer_source_document(
    plan: MappingExecutionPlan,
    evidence: SourceDocumentEvidence,
    document: Mapping[str, object],
) -> CustomerNormalizationResult:
    """Normalize one supplied Customer CDM document without external I/O."""
    if not isinstance(plan, MappingExecutionPlan):
        _raise_execution_error(NormalizationExecutionErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(evidence, SourceDocumentEvidence):
        _raise_execution_error(NormalizationExecutionErrorCode.INVALID_INPUT_TYPE)
    if not isinstance(document, Mapping):
        _raise_execution_error(NormalizationExecutionErrorCode.INVALID_INPUT_TYPE)

    _validate_plan(plan)
    frozen_document = _snapshot_source_document(document)
    customer_mapping = plan.resolve_canonical_mapping("canonical.customer.v1")
    account_mapping = plan.resolve_canonical_mapping("canonical.customer_account.v1")

    customer_candidates, customer_rejections = _normalize_mapping(
        plan,
        customer_mapping,
        frozen_document,
        evidence,
    )
    customer: Customer | None = None
    rejections = list(customer_rejections)
    if len(customer_candidates) == 1:
        customer = cast("Customer", customer_candidates[0].record)
    elif len(customer_candidates) > 1:
        rejections.extend(
            _rejection(
                customer_mapping,
                _RejectionLocation(candidate.record_locator, candidate.record_index),
                NormalizationRejectionCode.DUPLICATE_IDENTITY,
                "customer_key",
            )
            for candidate in customer_candidates
        )

    account_candidates, account_rejections = _normalize_mapping(
        plan,
        account_mapping,
        frozen_document,
        evidence,
    )
    rejections.extend(account_rejections)
    account_candidates, duplicate_rejections = _remove_duplicate_accounts(
        account_mapping,
        account_candidates,
    )
    rejections.extend(duplicate_rejections)

    accepted_accounts: list[CustomerAccount] = []
    for candidate in account_candidates:
        account = cast("CustomerAccount", candidate.record)
        if customer is None or account.customer_key != customer.customer_key:
            rejections.append(
                _rejection(
                    account_mapping,
                    _RejectionLocation(candidate.record_locator, candidate.record_index),
                    NormalizationRejectionCode.DEPENDENCY_NOT_SATISFIED,
                    "customer_key",
                )
            )
            continue
        accepted_accounts.append(account)

    return CustomerNormalizationResult(
        normalizer_version=_NORMALIZER_VERSION,
        execution_plan_digest=plan.execution_plan_digest,
        source_document_id=evidence.source_document_id,
        customer=customer,
        customer_accounts=tuple(accepted_accounts),
        rejections=tuple(sorted(rejections, key=_rejection_sort_key)),
    )
