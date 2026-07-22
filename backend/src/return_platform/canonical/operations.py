"""Canonical platform operational-state and evidence contracts."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Never, Self
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)

__all__ = [
    "AgentDecision",
    "AuditEvent",
    "ConfigurationVersionBinding",
    "ContextSnapshot",
    "GraphProjectionEvidence",
    "GraphProjectionStatus",
    "GraphSyncRun",
    "GraphSyncSafeError",
    "GraphValidationResult",
    "ReturnSession",
    "WorkflowStage",
]

NonNegativeCounter = Annotated[
    int,
    Field(strict=True, ge=0, le=9_223_372_036_854_775_807),
]
"""Bounded non-negative operational counter."""

DecisionConfidence = Annotated[
    Decimal,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=0,
        le=1,
        max_digits=8,
        decimal_places=7,
    ),
]
"""Strict confidence value in the closed interval [0, 1]."""

ContextJsonText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=2,
        max_length=1_000_000,
    ),
]
"""Bounded canonical JSON object text for immutable context snapshots."""


class WorkflowStage(StrEnum):
    """Code-owned mandatory Return workflow stages."""

    INTAKE = "INTAKE"
    ORDER_DISCOVERY = "ORDER_DISCOVERY"
    ELIGIBILITY_EVALUATION = "ELIGIBILITY_EVALUATION"
    RETURN_REQUEST = "RETURN_REQUEST"
    FULFILLMENT_TRACKING = "FULFILLMENT_TRACKING"
    BAY_ASSIGNMENT = "BAY_ASSIGNMENT"
    FEEDBACK_LEARNING = "FEEDBACK_LEARNING"
    COMPLETED = "COMPLETED"


class GraphProjectionStatus(StrEnum):
    """Outcome of projecting one canonical entity into Neo4j."""

    PROJECTED = "PROJECTED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


def _raise_validation_error(error_type: str, message: str) -> Never:
    """Raise a stable Pydantic validation error."""
    raise PydanticCustomError(error_type, message)


def _reject_duplicates(values: tuple[str, ...], *, error_type: str) -> None:
    """Reject duplicate immutable references."""
    if any(count > 1 for count in Counter(values).values()):
        _raise_validation_error(error_type, "duplicate values are not allowed")


class _DuplicateJsonKeyError(ValueError):
    """Raised when serialized context contains an ambiguous duplicate key."""


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate names."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> Never:
    """Reject NaN and infinity tokens, which are not valid JSON."""
    raise ValueError(value)


def _canonicalize_json_object(payload: Mapping[str, JsonValue]) -> str:
    """Serialize a JSON object deterministically without non-standard numbers."""
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _context_digest(payload_json: str) -> str:
    """Return the lowercase SHA-256 digest of canonical UTF-8 JSON."""
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


class ContextSnapshot(CanonicalBaseModel):
    """Immutable deterministic snapshot of one workflow-stage context."""

    schema_version: VersionReference
    payload_json: ContextJsonText
    payload_digest: Sha256Digest

    @classmethod
    def from_mapping(
        cls,
        *,
        schema_version: VersionReference,
        payload: Mapping[str, JsonValue],
    ) -> Self:
        """Create a canonical context snapshot from an in-memory JSON object."""
        payload_json = _canonicalize_json_object(payload)
        return cls(
            schema_version=schema_version,
            payload_json=payload_json,
            payload_digest=_context_digest(payload_json),
        )

    @model_validator(mode="after")
    def validate_serialization_and_digest(self) -> Self:
        """Reject duplicate, non-object, non-canonical, or tampered JSON."""
        try:
            parsed: object = json.loads(
                self.payload_json,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_non_finite_json_constant,
            )
        except (ValueError, RecursionError):
            _raise_validation_error(
                "context_snapshot_json_invalid",
                "payload_json must be valid JSON with unique object keys",
            )

        if not isinstance(parsed, dict):
            _raise_validation_error(
                "context_snapshot_root_invalid",
                "payload_json root must be a JSON object",
            )

        canonical_json = json.dumps(
            parsed,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if self.payload_json != canonical_json:
            _raise_validation_error(
                "context_snapshot_json_not_canonical",
                "payload_json must use deterministic canonical serialization",
            )

        if self.payload_digest != _context_digest(self.payload_json):
            _raise_validation_error(
                "context_snapshot_digest_mismatch",
                "payload_digest does not match payload_json",
            )

        return self


class ConfigurationVersionBinding(CanonicalBaseModel):
    """Immutable component-to-configuration-version binding."""

    component: CanonicalIdentifier
    version: VersionReference


class ReturnSession(CanonicalBaseModel):
    """Platform MongoDB authoritative state for one Return workflow session."""

    session_id: UUID
    current_stage: WorkflowStage
    status: NonBlankText
    intake_context: ContextSnapshot | None = None
    discovery_context: ContextSnapshot | None = None
    return_request_context: ContextSnapshot | None = None
    fulfillment_tracking_context: ContextSnapshot | None = None
    bay_staging_context: ContextSnapshot | None = None
    learning_feedback_context: ContextSnapshot | None = None
    workflow_id: CanonicalIdentifier | None = None
    workflow_run_id: CanonicalIdentifier | None = None
    configuration_versions: Annotated[
        tuple[ConfigurationVersionBinding, ...],
        Field(min_length=1, max_length=128),
    ]
    created_at: UtcDateTime
    updated_at: UtcDateTime
    completed_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_state_and_timeline(self) -> Self:
        """Validate workflow references, contexts, versions, and timestamps."""
        if self.updated_at < self.created_at:
            _raise_validation_error(
                "return_session_updated_at_invalid",
                "updated_at cannot precede created_at",
            )

        if (self.workflow_id is None) != (self.workflow_run_id is None):
            _raise_validation_error(
                "return_session_workflow_reference_pair_invalid",
                "workflow_id and workflow_run_id must be set together",
            )

        if self.current_stage is WorkflowStage.COMPLETED:
            if self.completed_at is None:
                _raise_validation_error(
                    "return_session_completed_at_required",
                    "completed_at is required for the COMPLETED stage",
                )
        elif self.completed_at is not None:
            _raise_validation_error(
                "return_session_completed_stage_required",
                "completed_at is allowed only for the COMPLETED stage",
            )

        if self.completed_at is not None and self.completed_at < self.updated_at:
            _raise_validation_error(
                "return_session_completed_at_invalid",
                "completed_at cannot precede updated_at",
            )

        version_components = tuple(binding.component for binding in self.configuration_versions)
        _reject_duplicates(
            version_components,
            error_type="return_session_duplicate_configuration_component",
        )

        return self


class AgentDecision(CanonicalBaseModel):
    """Traceable AI or deterministic-agent decision evidence."""

    decision_id: UUID
    session_id: UUID
    agent_name: NonBlankText
    stage: WorkflowStage
    decision_type: NonBlankText
    decision: NonBlankText
    explanation: NonBlankText
    confidence: DecisionConfidence
    evidence_references: Annotated[
        tuple[NonBlankText, ...],
        Field(min_length=1, max_length=1_000),
    ]
    model_provider: NonBlankText | None = None
    model_name: NonBlankText | None = None
    configuration_version: VersionReference
    created_at: UtcDateTime
    reviewed_by: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_model_and_evidence(self) -> Self:
        """Validate model-reference pairing and evidence uniqueness."""
        if (self.model_provider is None) != (self.model_name is None):
            _raise_validation_error(
                "agent_decision_model_reference_pair_invalid",
                "model_provider and model_name must be set together",
            )
        _reject_duplicates(
            self.evidence_references,
            error_type="agent_decision_duplicate_evidence",
        )
        return self


class AuditEvent(CanonicalBaseModel):
    """Immutable audit evidence for a platform or workflow operation."""

    audit_event_id: UUID
    session_id: UUID
    correlation_id: UUID
    actor_type: NonBlankText
    actor_id: NonBlankText
    operation: NonBlankText
    entity_type: NonBlankText
    entity_key: CanonicalIdentifier
    before_summary: NonBlankText | None = None
    after_summary: NonBlankText | None = None
    outcome: NonBlankText
    safe_error_code: CanonicalIdentifier | None = None
    occurred_at: UtcDateTime
    evidence_references: tuple[NonBlankText, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_evidence(self) -> Self:
        """Reject duplicate evidence references."""
        _reject_duplicates(
            self.evidence_references,
            error_type="audit_event_duplicate_evidence",
        )
        return self


class GraphValidationResult(CanonicalBaseModel):
    """One deterministic graph-sync validation result."""

    validation_code: CanonicalIdentifier
    passed: bool
    safe_message: NonBlankText


class GraphSyncSafeError(CanonicalBaseModel):
    """Sanitized graph-sync error evidence."""

    error_code: CanonicalIdentifier
    safe_message: NonBlankText
    source_asset: NonBlankText | None = None
    source_record_id: NonBlankText | None = None


class GraphSyncRun(CanonicalBaseModel):
    """Platform-owned execution evidence for one graph synchronization run."""

    sync_run_id: UUID
    pipeline_id: CanonicalIdentifier
    mapping_version: VersionReference
    configuration_digest: Sha256Digest
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    status: NonBlankText
    source_assets: Annotated[
        tuple[NonBlankText, ...],
        Field(min_length=1, max_length=1_000),
    ]
    records_read: NonNegativeCounter
    nodes_created: NonNegativeCounter
    nodes_updated: NonNegativeCounter
    relationships_created: NonNegativeCounter
    relationships_updated: NonNegativeCounter
    records_rejected: NonNegativeCounter
    validation_results: tuple[GraphValidationResult, ...] = ()
    safe_errors: tuple[GraphSyncSafeError, ...] = ()

    @model_validator(mode="after")
    def validate_run_evidence(self) -> Self:
        """Validate timeline and unique source/validation evidence."""
        if self.completed_at is not None and self.completed_at < self.started_at:
            _raise_validation_error(
                "graph_sync_completed_at_invalid",
                "completed_at cannot precede started_at",
            )

        _reject_duplicates(
            self.source_assets,
            error_type="graph_sync_duplicate_source_asset",
        )
        _reject_duplicates(
            tuple(result.validation_code for result in self.validation_results),
            error_type="graph_sync_duplicate_validation_code",
        )
        return self


class GraphProjectionEvidence(CanonicalBaseModel):
    """Entity-level evidence for one Neo4j graph projection attempt."""

    evidence_id: UUID
    sync_run_id: UUID
    source_asset: NonBlankText
    source_record_id: NonBlankText
    canonical_entity_type: NonBlankText
    canonical_entity_key: CanonicalIdentifier
    graph_label: CanonicalIdentifier
    graph_key: CanonicalIdentifier
    mapping_version: VersionReference
    projection_status: GraphProjectionStatus
    rejection_reason: NonBlankText | None = None
    projected_at: UtcDateTime

    @model_validator(mode="after")
    def validate_projection_outcome(self) -> Self:
        """Require rejection evidence only for non-projected outcomes."""
        if self.projection_status is GraphProjectionStatus.PROJECTED:
            if self.rejection_reason is not None:
                _raise_validation_error(
                    "graph_projection_rejection_reason_forbidden",
                    "rejection_reason is forbidden for PROJECTED evidence",
                )
        elif self.rejection_reason is None:
            _raise_validation_error(
                "graph_projection_rejection_reason_required",
                "rejection_reason is required for rejected or unresolved evidence",
            )
        return self
