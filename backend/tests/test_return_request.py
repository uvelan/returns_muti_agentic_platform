"""Deterministic return-request construction tests."""

from datetime import UTC, datetime

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.return_request import build_return_request_result
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    ReturnRequestActivityResult,
    ReturnRequestOutcome,
    StageResultValidationError,
    bind_stage_activity_result,
    return_request_result_from_binding,
)

_AT = datetime(2026, 7, 22, 13, tzinfo=UTC)


def _eligibility(decision: EligibilityDecision) -> ContextSnapshot:
    binding = bind_stage_activity_result(
        WorkflowStage.ELIGIBILITY_EVALUATION,
        EligibilityActivityResult(
            schema_version="eligibility-v1",
            decision=decision,
            explanation="Controlled eligibility evidence.",
            confidence_millionths=800_000,
            evidence_references=("FIXTURE:ELIGIBILITY",),
            model_provider="CONTROLLED_FIXTURE",
            model_name="eligibility-v1",
            configuration_version="policy-v1",
            observed_at=_AT,
        ),
    )
    return ContextSnapshot(
        schema_version=binding.schema_version,
        payload_json=binding.payload_json,
        payload_digest=binding.payload_digest,
    )


@pytest.mark.parametrize(
    ("decision", "return_reference", "outcome"),
    (
        (EligibilityDecision.APPROVE, "RETURN-1", ReturnRequestOutcome.CREATED),
        (EligibilityDecision.REJECT, None, ReturnRequestOutcome.DECLINED),
        (
            EligibilityDecision.REVIEW_REQUIRED,
            None,
            ReturnRequestOutcome.REVIEW_PENDING,
        ),
    ),
)
def test_builder_maps_persisted_eligibility_to_consistent_outcome(
    decision: EligibilityDecision,
    return_reference: str | None,
    outcome: ReturnRequestOutcome,
) -> None:
    eligibility = _eligibility(decision)

    result = build_return_request_result(
        eligibility=eligibility,
        request_reference="REQUEST-1",
        return_reference=return_reference,
        configuration_version="return-v1",
        observed_at=_AT,
    )
    binding = bind_stage_activity_result(WorkflowStage.RETURN_REQUEST, result)

    assert result.outcome is outcome
    assert result.eligibility_context_digest == eligibility.payload_digest
    assert return_request_result_from_binding(binding) == result


@pytest.mark.parametrize(
    ("decision", "return_reference"),
    (
        (EligibilityDecision.APPROVE, None),
        (EligibilityDecision.REJECT, "RETURN-1"),
        (EligibilityDecision.REVIEW_REQUIRED, "RETURN-1"),
    ),
)
def test_builder_rejects_inconsistent_reference_creation(
    decision: EligibilityDecision, return_reference: str | None
) -> None:
    with pytest.raises(ValueError):
        build_return_request_result(
            eligibility=_eligibility(decision),
            request_reference="REQUEST-1",
            return_reference=return_reference,
            configuration_version="return-v1",
            observed_at=_AT,
        )


def test_binding_rejects_claimed_outcome_inconsistent_with_decision() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(
            WorkflowStage.RETURN_REQUEST,
            ReturnRequestActivityResult(
                schema_version="return-request-v1",
                eligibility_decision=EligibilityDecision.REJECT,
                outcome=ReturnRequestOutcome.CREATED,
                request_reference="REQUEST-1",
                return_reference="RETURN-1",
                eligibility_context_digest="0" * 64,
                evidence_references=("FIXTURE:RETURN",),
                configuration_version="return-v1",
                observed_at=_AT,
            ),
        )
