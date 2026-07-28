"""Deterministic bounded activity-result contracts for early Return stages."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never

from return_platform.canonical.operations import WorkflowStage

__all__ = [
    "BayAssignmentActivityResult",
    "BayAssignmentStatus",
    "EligibilityActivityResult",
    "EligibilityDecision",
    "FeedbackLearningActivityResult",
    "FeedbackLearningStatus",
    "FulfillmentTrackingActivityResult",
    "FulfillmentTrackingStatus",
    "IntakeActivityResult",
    "IntakeChannel",
    "OrderDiscoveryActivityResult",
    "ReturnRequestActivityResult",
    "ReturnRequestOutcome",
    "StageContextBinding",
    "StageResultValidationError",
    "bay_assignment_result_from_binding",
    "bind_stage_activity_result",
    "eligibility_result_from_binding",
    "empty_stage_context_binding",
    "feedback_learning_result_from_binding",
    "fulfillment_tracking_result_from_binding",
    "return_request_result_from_binding",
    "validate_stage_context_binding",
]

_INTAKE_SCHEMA: Final = "intake-v1"
_DISCOVERY_SCHEMA: Final = "order-discovery-v1"
_ELIGIBILITY_SCHEMA: Final = "eligibility-v1"
_RETURN_REQUEST_SCHEMA: Final = "return-request-v1"
_FULFILLMENT_TRACKING_SCHEMA: Final = "fulfillment-tracking-v1"
_BAY_ASSIGNMENT_SCHEMA: Final = "bay-assignment-v1"
_FEEDBACK_LEARNING_SCHEMA: Final = "feedback-learning-v1"
_IDENTIFIER_PATTERN: Final = re.compile(r"^[^\s\x00-\x1f\x7f]{1,512}$")
_MAX_REFERENCES: Final = 128


class IntakeChannel(StrEnum):
    """Code-owned channels accepted by the intake contract."""

    ASSOCIATE = "ASSOCIATE"
    CUSTOMER = "CUSTOMER"
    SYSTEM = "SYSTEM"


class EligibilityDecision(StrEnum):
    """Fail-safe eligibility outcomes."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReturnRequestOutcome(StrEnum):
    """Code-owned outcomes consistent with eligibility evidence."""

    CREATED = "CREATED"
    DECLINED = "DECLINED"
    REVIEW_PENDING = "REVIEW_PENDING"


class FulfillmentTrackingStatus(StrEnum):
    """Code-owned fulfillment states for the bounded tracking context."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    AWAITING_HANDOFF = "AWAITING_HANDOFF"
    IN_TRANSIT = "IN_TRANSIT"


class BayAssignmentStatus(StrEnum):
    """Code-owned bay assignment outcomes."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"


class FeedbackLearningStatus(StrEnum):
    """Code-owned bounded feedback dispositions."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEFERRED = "DEFERRED"
    RECORDED = "RECORDED"


class StageResultValidationError(ValueError):
    """Safe validation failure for an early-stage activity result."""


def _invalid() -> Never:
    raise StageResultValidationError("The stage activity result is invalid.")


@dataclass(frozen=True, slots=True)
class IntakeActivityResult:
    """Bounded successful result from the INTAKE activity."""

    schema_version: str
    request_reference: str
    channel: IntakeChannel
    customer_reference: str
    order_reference: str | None
    evidence_references: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class OrderDiscoveryActivityResult:
    """Bounded successful result from governed order discovery."""

    schema_version: str
    request_reference: str
    customer_reference: str
    order_references: tuple[str, ...]
    source_asset_id: str
    source_document_references: tuple[str, ...]
    evidence_references: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class EligibilityActivityResult:
    """Validated provider-neutral eligibility result."""

    schema_version: str
    decision: EligibilityDecision
    explanation: str
    confidence_millionths: int
    evidence_references: tuple[str, ...]
    model_provider: str
    model_name: str
    configuration_version: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnRequestActivityResult:
    """Deterministic return-request result bound to eligibility evidence."""

    schema_version: str
    eligibility_decision: EligibilityDecision
    outcome: ReturnRequestOutcome
    request_reference: str
    return_reference: str | None
    eligibility_context_digest: str
    evidence_references: tuple[str, ...]
    configuration_version: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class FulfillmentTrackingActivityResult:
    """Deterministic fulfillment result bound to return-request evidence."""

    schema_version: str
    return_request_outcome: ReturnRequestOutcome
    status: FulfillmentTrackingStatus
    request_reference: str
    return_reference: str | None
    fulfillment_reference: str | None
    tracking_reference: str | None
    return_request_context_digest: str
    evidence_references: tuple[str, ...]
    configuration_version: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BayAssignmentActivityResult:
    """Deterministic bay assignment bound to fulfillment evidence."""

    schema_version: str
    fulfillment_status: FulfillmentTrackingStatus
    status: BayAssignmentStatus
    request_reference: str
    return_reference: str | None
    warehouse_reference: str | None
    bay_reference: str | None
    fulfillment_context_digest: str
    evidence_references: tuple[str, ...]
    configuration_version: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class FeedbackLearningActivityResult:
    """Deterministic feedback evidence bound to bay-assignment context."""

    schema_version: str
    bay_assignment_status: BayAssignmentStatus
    status: FeedbackLearningStatus
    request_reference: str
    return_reference: str | None
    warehouse_reference: str | None
    bay_reference: str | None
    feedback_reference: str | None
    learning_signal_reference: str | None
    bay_assignment_context_digest: str
    evidence_references: tuple[str, ...]
    configuration_version: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class StageContextBinding:
    """Canonical JSON and digest passed from workflow to persistence activity."""

    completed_stage: WorkflowStage
    schema_version: str
    payload_json: str
    payload_digest: str


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER_PATTERN.fullmatch(value) is not None


def _valid_references(values: object, *, minimum: int) -> bool:
    return (
        isinstance(values, tuple)
        and minimum <= len(values) <= _MAX_REFERENCES
        and all(_valid_identifier(value) for value in values)
        and len(set(values)) == len(values)
    )


def _utc_text(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _invalid()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _binding(
    stage: WorkflowStage,
    schema_version: str,
    payload: dict[str, object],
) -> StageContextBinding:
    payload_json = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return StageContextBinding(
        completed_stage=stage,
        schema_version=schema_version,
        payload_json=payload_json,
        payload_digest=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    )


def _bind_intake(result: IntakeActivityResult) -> StageContextBinding:
    if (
        result.schema_version != _INTAKE_SCHEMA
        or not _valid_identifier(result.request_reference)
        or not isinstance(result.channel, IntakeChannel)
        or not _valid_identifier(result.customer_reference)
        or (result.order_reference is not None and not _valid_identifier(result.order_reference))
        or not _valid_references(result.evidence_references, minimum=1)
    ):
        _invalid()
    return _binding(
        WorkflowStage.INTAKE,
        result.schema_version,
        {
            "channel": result.channel.value,
            "customer_reference": result.customer_reference,
            "evidence_references": list(result.evidence_references),
            "observed_at": _utc_text(result.observed_at),
            "order_reference": result.order_reference,
            "request_reference": result.request_reference,
        },
    )


def _bind_discovery(result: OrderDiscoveryActivityResult) -> StageContextBinding:
    if (
        result.schema_version != _DISCOVERY_SCHEMA
        or not _valid_identifier(result.request_reference)
        or not _valid_identifier(result.customer_reference)
        or not _valid_references(result.order_references, minimum=1)
        or not _valid_identifier(result.source_asset_id)
        or not _valid_references(result.source_document_references, minimum=1)
        or not _valid_references(result.evidence_references, minimum=1)
    ):
        _invalid()
    return _binding(
        WorkflowStage.ORDER_DISCOVERY,
        result.schema_version,
        {
            "customer_reference": result.customer_reference,
            "evidence_references": list(result.evidence_references),
            "observed_at": _utc_text(result.observed_at),
            "order_references": list(result.order_references),
            "request_reference": result.request_reference,
            "source_asset_id": result.source_asset_id,
            "source_document_references": list(result.source_document_references),
        },
    )


def _bind_eligibility(result: EligibilityActivityResult) -> StageContextBinding:
    if (
        result.schema_version != _ELIGIBILITY_SCHEMA
        or not isinstance(result.decision, EligibilityDecision)
        or not isinstance(result.explanation, str)
        or not 1 <= len(result.explanation.strip()) <= 1_024
        or not isinstance(result.confidence_millionths, int)
        or isinstance(result.confidence_millionths, bool)
        or not 0 <= result.confidence_millionths <= 1_000_000
        or not _valid_references(result.evidence_references, minimum=1)
        or not _valid_identifier(result.model_provider)
        or not _valid_identifier(result.model_name)
        or not _valid_identifier(result.configuration_version)
    ):
        _invalid()
    return _binding(
        WorkflowStage.ELIGIBILITY_EVALUATION,
        result.schema_version,
        {
            "confidence_millionths": result.confidence_millionths,
            "configuration_version": result.configuration_version,
            "decision": result.decision.value,
            "evidence_references": list(result.evidence_references),
            "explanation": result.explanation.strip(),
            "model_name": result.model_name,
            "model_provider": result.model_provider,
            "observed_at": _utc_text(result.observed_at),
        },
    )


def _bind_return_request(result: ReturnRequestActivityResult) -> StageContextBinding:
    consistent = (
        (
            result.eligibility_decision is EligibilityDecision.APPROVE
            and result.outcome is ReturnRequestOutcome.CREATED
            and _valid_identifier(result.return_reference)
        )
        or (
            result.eligibility_decision is EligibilityDecision.REJECT
            and result.outcome is ReturnRequestOutcome.DECLINED
            and result.return_reference is None
        )
        or (
            result.eligibility_decision is EligibilityDecision.REVIEW_REQUIRED
            and result.outcome is ReturnRequestOutcome.REVIEW_PENDING
            and result.return_reference is None
        )
    )
    if (
        result.schema_version != _RETURN_REQUEST_SCHEMA
        or not isinstance(result.eligibility_decision, EligibilityDecision)
        or not isinstance(result.outcome, ReturnRequestOutcome)
        or not consistent
        or not _valid_identifier(result.request_reference)
        or re.fullmatch(r"[0-9a-f]{64}", result.eligibility_context_digest) is None
        or not _valid_references(result.evidence_references, minimum=1)
        or not _valid_identifier(result.configuration_version)
    ):
        _invalid()
    return _binding(
        WorkflowStage.RETURN_REQUEST,
        result.schema_version,
        {
            "configuration_version": result.configuration_version,
            "eligibility_context_digest": result.eligibility_context_digest,
            "eligibility_decision": result.eligibility_decision.value,
            "evidence_references": list(result.evidence_references),
            "observed_at": _utc_text(result.observed_at),
            "outcome": result.outcome.value,
            "request_reference": result.request_reference,
            "return_reference": result.return_reference,
        },
    )


def _bind_fulfillment_tracking(
    result: FulfillmentTrackingActivityResult,
) -> StageContextBinding:
    active = (
        result.return_request_outcome is ReturnRequestOutcome.CREATED
        and result.status
        in {FulfillmentTrackingStatus.AWAITING_HANDOFF, FulfillmentTrackingStatus.IN_TRANSIT}
        and _valid_identifier(result.return_reference)
        and _valid_identifier(result.fulfillment_reference)
        and (
            result.tracking_reference is None
            if result.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
            else _valid_identifier(result.tracking_reference)
        )
    )
    inactive = (
        result.return_request_outcome
        in {ReturnRequestOutcome.DECLINED, ReturnRequestOutcome.REVIEW_PENDING}
        and result.status is FulfillmentTrackingStatus.NOT_APPLICABLE
        and result.return_reference is None
        and result.fulfillment_reference is None
        and result.tracking_reference is None
    )
    if (
        result.schema_version != _FULFILLMENT_TRACKING_SCHEMA
        or not isinstance(result.return_request_outcome, ReturnRequestOutcome)
        or not isinstance(result.status, FulfillmentTrackingStatus)
        or not (active or inactive)
        or not _valid_identifier(result.request_reference)
        or re.fullmatch(r"[0-9a-f]{64}", result.return_request_context_digest) is None
        or not _valid_references(result.evidence_references, minimum=1)
        or not _valid_identifier(result.configuration_version)
    ):
        _invalid()
    return _binding(
        WorkflowStage.FULFILLMENT_TRACKING,
        result.schema_version,
        {
            "configuration_version": result.configuration_version,
            "evidence_references": list(result.evidence_references),
            "fulfillment_reference": result.fulfillment_reference,
            "observed_at": _utc_text(result.observed_at),
            "request_reference": result.request_reference,
            "return_reference": result.return_reference,
            "return_request_context_digest": result.return_request_context_digest,
            "return_request_outcome": result.return_request_outcome.value,
            "status": result.status.value,
            "tracking_reference": result.tracking_reference,
        },
    )


def _bind_bay_assignment(result: BayAssignmentActivityResult) -> StageContextBinding:
    consistent = (
        (
            result.fulfillment_status is FulfillmentTrackingStatus.NOT_APPLICABLE
            and result.status is BayAssignmentStatus.NOT_APPLICABLE
            and result.return_reference is None
            and result.warehouse_reference is None
            and result.bay_reference is None
        )
        or (
            result.fulfillment_status is FulfillmentTrackingStatus.AWAITING_HANDOFF
            and result.status is BayAssignmentStatus.PENDING
            and _valid_identifier(result.return_reference)
            and result.warehouse_reference is None
            and result.bay_reference is None
        )
        or (
            result.fulfillment_status is FulfillmentTrackingStatus.IN_TRANSIT
            and result.status is BayAssignmentStatus.ASSIGNED
            and _valid_identifier(result.return_reference)
            and _valid_identifier(result.warehouse_reference)
            and _valid_identifier(result.bay_reference)
        )
    )
    if (
        result.schema_version != _BAY_ASSIGNMENT_SCHEMA
        or not isinstance(result.fulfillment_status, FulfillmentTrackingStatus)
        or not isinstance(result.status, BayAssignmentStatus)
        or not consistent
        or not _valid_identifier(result.request_reference)
        or re.fullmatch(r"[0-9a-f]{64}", result.fulfillment_context_digest) is None
        or not _valid_references(result.evidence_references, minimum=1)
        or not _valid_identifier(result.configuration_version)
    ):
        _invalid()
    return _binding(
        WorkflowStage.BAY_ASSIGNMENT,
        result.schema_version,
        {
            "bay_reference": result.bay_reference,
            "configuration_version": result.configuration_version,
            "evidence_references": list(result.evidence_references),
            "fulfillment_context_digest": result.fulfillment_context_digest,
            "fulfillment_status": result.fulfillment_status.value,
            "observed_at": _utc_text(result.observed_at),
            "request_reference": result.request_reference,
            "return_reference": result.return_reference,
            "status": result.status.value,
            "warehouse_reference": result.warehouse_reference,
        },
    )


def _bind_feedback_learning(
    result: FeedbackLearningActivityResult,
) -> StageContextBinding:
    consistent = (
        (
            result.bay_assignment_status is BayAssignmentStatus.NOT_APPLICABLE
            and result.status is FeedbackLearningStatus.NOT_APPLICABLE
            and result.return_reference is None
            and result.warehouse_reference is None
            and result.bay_reference is None
            and result.feedback_reference is None
            and result.learning_signal_reference is None
        )
        or (
            result.bay_assignment_status is BayAssignmentStatus.PENDING
            and result.status is FeedbackLearningStatus.DEFERRED
            and _valid_identifier(result.return_reference)
            and result.warehouse_reference is None
            and result.bay_reference is None
            and result.feedback_reference is None
            and result.learning_signal_reference is None
        )
        or (
            result.bay_assignment_status is BayAssignmentStatus.ASSIGNED
            and result.status is FeedbackLearningStatus.RECORDED
            and _valid_identifier(result.return_reference)
            and _valid_identifier(result.warehouse_reference)
            and _valid_identifier(result.bay_reference)
            and _valid_identifier(result.feedback_reference)
            and _valid_identifier(result.learning_signal_reference)
        )
    )
    if (
        result.schema_version != _FEEDBACK_LEARNING_SCHEMA
        or not isinstance(result.bay_assignment_status, BayAssignmentStatus)
        or not isinstance(result.status, FeedbackLearningStatus)
        or not consistent
        or not _valid_identifier(result.request_reference)
        or re.fullmatch(r"[0-9a-f]{64}", result.bay_assignment_context_digest) is None
        or not _valid_references(result.evidence_references, minimum=1)
        or not _valid_identifier(result.configuration_version)
    ):
        _invalid()
    return _binding(
        WorkflowStage.FEEDBACK_LEARNING,
        result.schema_version,
        {
            "bay_assignment_context_digest": result.bay_assignment_context_digest,
            "bay_assignment_status": result.bay_assignment_status.value,
            "bay_reference": result.bay_reference,
            "configuration_version": result.configuration_version,
            "evidence_references": list(result.evidence_references),
            "feedback_reference": result.feedback_reference,
            "learning_signal_reference": result.learning_signal_reference,
            "observed_at": _utc_text(result.observed_at),
            "request_reference": result.request_reference,
            "return_reference": result.return_reference,
            "status": result.status.value,
            "warehouse_reference": result.warehouse_reference,
        },
    )


def bind_stage_activity_result(
    completed_stage: WorkflowStage,
    result: (
        IntakeActivityResult
        | OrderDiscoveryActivityResult
        | EligibilityActivityResult
        | ReturnRequestActivityResult
        | FulfillmentTrackingActivityResult
        | BayAssignmentActivityResult
        | FeedbackLearningActivityResult
        | None
    ),
) -> StageContextBinding:
    """Validate one result and produce its deterministic context binding."""
    if completed_stage is WorkflowStage.INTAKE:
        if not isinstance(result, IntakeActivityResult):
            _invalid()
        return _bind_intake(result)
    if completed_stage is WorkflowStage.ORDER_DISCOVERY:
        if not isinstance(result, OrderDiscoveryActivityResult):
            _invalid()
        return _bind_discovery(result)
    if completed_stage is WorkflowStage.ELIGIBILITY_EVALUATION:
        if not isinstance(result, EligibilityActivityResult):
            _invalid()
        return _bind_eligibility(result)
    if completed_stage is WorkflowStage.RETURN_REQUEST:
        if not isinstance(result, ReturnRequestActivityResult):
            _invalid()
        return _bind_return_request(result)
    if completed_stage is WorkflowStage.FULFILLMENT_TRACKING:
        if not isinstance(result, FulfillmentTrackingActivityResult):
            _invalid()
        return _bind_fulfillment_tracking(result)
    if completed_stage is WorkflowStage.BAY_ASSIGNMENT:
        if not isinstance(result, BayAssignmentActivityResult):
            _invalid()
        return _bind_bay_assignment(result)
    if completed_stage is WorkflowStage.FEEDBACK_LEARNING:
        if not isinstance(result, FeedbackLearningActivityResult):
            _invalid()
        return _bind_feedback_learning(result)
    if result is not None:
        _invalid()
    return empty_stage_context_binding(completed_stage)


def empty_stage_context_binding(completed_stage: WorkflowStage) -> StageContextBinding:
    """Create the explicit no-context binding used by later unimplemented stages."""
    if not isinstance(completed_stage, WorkflowStage):
        _invalid()
    return StageContextBinding(
        completed_stage=completed_stage,
        schema_version="",
        payload_json="",
        payload_digest="",
    )


def validate_stage_context_binding(
    completed_stage: WorkflowStage,
    binding: StageContextBinding,
) -> StageContextBinding:
    """Fail closed on a malformed, cross-stage, or tampered context binding."""
    if (
        not isinstance(binding, StageContextBinding)
        or binding.completed_stage is not completed_stage
    ):
        _invalid()
    expected_schema = {
        WorkflowStage.INTAKE: _INTAKE_SCHEMA,
        WorkflowStage.ORDER_DISCOVERY: _DISCOVERY_SCHEMA,
        WorkflowStage.ELIGIBILITY_EVALUATION: _ELIGIBILITY_SCHEMA,
        WorkflowStage.RETURN_REQUEST: _RETURN_REQUEST_SCHEMA,
        WorkflowStage.FULFILLMENT_TRACKING: _FULFILLMENT_TRACKING_SCHEMA,
        WorkflowStage.BAY_ASSIGNMENT: _BAY_ASSIGNMENT_SCHEMA,
        WorkflowStage.FEEDBACK_LEARNING: _FEEDBACK_LEARNING_SCHEMA,
    }.get(completed_stage)
    if expected_schema is None:
        if binding.schema_version or binding.payload_json or binding.payload_digest:
            _invalid()
        return binding
    if binding.schema_version != expected_schema or not re.fullmatch(
        r"[0-9a-f]{64}", binding.payload_digest
    ):
        _invalid()
    try:
        parsed = json.loads(binding.payload_json)
    except (ValueError, TypeError, RecursionError):
        _invalid()
    if (
        not isinstance(parsed, dict)
        or json.dumps(
            parsed,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        != binding.payload_json
    ):
        _invalid()
    if hashlib.sha256(binding.payload_json.encode("utf-8")).hexdigest() != binding.payload_digest:
        _invalid()
    return binding


def eligibility_result_from_binding(
    binding: StageContextBinding,
) -> EligibilityActivityResult:
    """Reconstruct and revalidate typed eligibility evidence from its wire binding."""
    validate_stage_context_binding(WorkflowStage.ELIGIBILITY_EVALUATION, binding)
    try:
        payload = json.loads(binding.payload_json)
        if not isinstance(payload, dict) or set(payload) != {
            "confidence_millionths",
            "configuration_version",
            "decision",
            "evidence_references",
            "explanation",
            "model_name",
            "model_provider",
            "observed_at",
        }:
            _invalid()
        result = EligibilityActivityResult(
            schema_version=_ELIGIBILITY_SCHEMA,
            decision=EligibilityDecision(payload["decision"]),
            explanation=payload["explanation"],
            confidence_millionths=payload["confidence_millionths"],
            evidence_references=tuple(payload["evidence_references"]),
            model_provider=payload["model_provider"],
            model_name=payload["model_name"],
            configuration_version=payload["configuration_version"],
            observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        _invalid()
    if bind_stage_activity_result(WorkflowStage.ELIGIBILITY_EVALUATION, result) != binding:
        _invalid()
    return result


def return_request_result_from_binding(
    binding: StageContextBinding,
) -> ReturnRequestActivityResult:
    """Reconstruct and revalidate typed return-request evidence."""
    validate_stage_context_binding(WorkflowStage.RETURN_REQUEST, binding)
    try:
        payload = json.loads(binding.payload_json)
        if not isinstance(payload, dict) or set(payload) != {
            "configuration_version",
            "eligibility_context_digest",
            "eligibility_decision",
            "evidence_references",
            "observed_at",
            "outcome",
            "request_reference",
            "return_reference",
        }:
            _invalid()
        result = ReturnRequestActivityResult(
            schema_version=_RETURN_REQUEST_SCHEMA,
            eligibility_decision=EligibilityDecision(payload["eligibility_decision"]),
            outcome=ReturnRequestOutcome(payload["outcome"]),
            request_reference=payload["request_reference"],
            return_reference=payload["return_reference"],
            eligibility_context_digest=payload["eligibility_context_digest"],
            evidence_references=tuple(payload["evidence_references"]),
            configuration_version=payload["configuration_version"],
            observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        _invalid()
    if bind_stage_activity_result(WorkflowStage.RETURN_REQUEST, result) != binding:
        _invalid()
    return result


def fulfillment_tracking_result_from_binding(
    binding: StageContextBinding,
) -> FulfillmentTrackingActivityResult:
    """Reconstruct and revalidate typed fulfillment evidence."""
    validate_stage_context_binding(WorkflowStage.FULFILLMENT_TRACKING, binding)
    try:
        payload = json.loads(binding.payload_json)
        if not isinstance(payload, dict) or set(payload) != {
            "configuration_version",
            "evidence_references",
            "fulfillment_reference",
            "observed_at",
            "request_reference",
            "return_reference",
            "return_request_context_digest",
            "return_request_outcome",
            "status",
            "tracking_reference",
        }:
            _invalid()
        result = FulfillmentTrackingActivityResult(
            schema_version=_FULFILLMENT_TRACKING_SCHEMA,
            return_request_outcome=ReturnRequestOutcome(payload["return_request_outcome"]),
            status=FulfillmentTrackingStatus(payload["status"]),
            request_reference=payload["request_reference"],
            return_reference=payload["return_reference"],
            fulfillment_reference=payload["fulfillment_reference"],
            tracking_reference=payload["tracking_reference"],
            return_request_context_digest=payload["return_request_context_digest"],
            evidence_references=tuple(payload["evidence_references"]),
            configuration_version=payload["configuration_version"],
            observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        _invalid()
    if bind_stage_activity_result(WorkflowStage.FULFILLMENT_TRACKING, result) != binding:
        _invalid()
    return result


def bay_assignment_result_from_binding(
    binding: StageContextBinding,
) -> BayAssignmentActivityResult:
    """Reconstruct and revalidate typed bay-assignment evidence."""
    validate_stage_context_binding(WorkflowStage.BAY_ASSIGNMENT, binding)
    try:
        payload = json.loads(binding.payload_json)
        if not isinstance(payload, dict) or set(payload) != {
            "bay_reference",
            "configuration_version",
            "evidence_references",
            "fulfillment_context_digest",
            "fulfillment_status",
            "observed_at",
            "request_reference",
            "return_reference",
            "status",
            "warehouse_reference",
        }:
            _invalid()
        result = BayAssignmentActivityResult(
            schema_version=_BAY_ASSIGNMENT_SCHEMA,
            fulfillment_status=FulfillmentTrackingStatus(payload["fulfillment_status"]),
            status=BayAssignmentStatus(payload["status"]),
            request_reference=payload["request_reference"],
            return_reference=payload["return_reference"],
            warehouse_reference=payload["warehouse_reference"],
            bay_reference=payload["bay_reference"],
            fulfillment_context_digest=payload["fulfillment_context_digest"],
            evidence_references=tuple(payload["evidence_references"]),
            configuration_version=payload["configuration_version"],
            observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        _invalid()
    if bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, result) != binding:
        _invalid()
    return result


def feedback_learning_result_from_binding(
    binding: StageContextBinding,
) -> FeedbackLearningActivityResult:
    """Reconstruct and revalidate typed feedback-learning evidence."""
    validate_stage_context_binding(WorkflowStage.FEEDBACK_LEARNING, binding)
    try:
        payload = json.loads(binding.payload_json)
        if not isinstance(payload, dict) or set(payload) != {
            "bay_assignment_context_digest",
            "bay_assignment_status",
            "bay_reference",
            "configuration_version",
            "evidence_references",
            "feedback_reference",
            "learning_signal_reference",
            "observed_at",
            "request_reference",
            "return_reference",
            "status",
            "warehouse_reference",
        }:
            _invalid()
        result = FeedbackLearningActivityResult(
            schema_version=_FEEDBACK_LEARNING_SCHEMA,
            bay_assignment_status=BayAssignmentStatus(payload["bay_assignment_status"]),
            status=FeedbackLearningStatus(payload["status"]),
            request_reference=payload["request_reference"],
            return_reference=payload["return_reference"],
            warehouse_reference=payload["warehouse_reference"],
            bay_reference=payload["bay_reference"],
            feedback_reference=payload["feedback_reference"],
            learning_signal_reference=payload["learning_signal_reference"],
            bay_assignment_context_digest=payload["bay_assignment_context_digest"],
            evidence_references=tuple(payload["evidence_references"]),
            configuration_version=payload["configuration_version"],
            observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        _invalid()
    if bind_stage_activity_result(WorkflowStage.FEEDBACK_LEARNING, result) != binding:
        _invalid()
    return result
