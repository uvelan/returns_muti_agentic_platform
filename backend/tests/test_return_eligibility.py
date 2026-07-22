"""Provider-neutral eligibility boundary and fail-safe tests."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.eligibility import (
    EligibilityEvaluationInput,
    EligibilityGatewayError,
    EligibilityGatewayErrorCode,
    EligibilityGatewayService,
    build_eligibility_input,
)
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    bind_stage_activity_result,
)

_AT = datetime(2026, 7, 22, 12, tzinfo=UTC)


def _snapshot(stage: WorkflowStage, result: object) -> ContextSnapshot:
    binding = bind_stage_activity_result(stage, result)  # type: ignore[arg-type]
    return ContextSnapshot(
        schema_version=binding.schema_version,
        payload_json=binding.payload_json,
        payload_digest=binding.payload_digest,
    )


def _request() -> EligibilityEvaluationInput:
    intake = _snapshot(
        WorkflowStage.INTAKE,
        IntakeActivityResult(
            "intake-v1",
            "REQUEST-1",
            IntakeChannel.SYSTEM,
            "CUSTOMER-1",
            "ORDER-1",
            ("FIXTURE:INTAKE",),
            _AT,
        ),
    )
    discovery = _snapshot(
        WorkflowStage.ORDER_DISCOVERY,
        OrderDiscoveryActivityResult(
            "order-discovery-v1",
            "REQUEST-1",
            "CUSTOMER-1",
            ("ORDER-1",),
            "source.orders",
            ("DOCUMENT:ORDER-1",),
            ("FIXTURE:DISCOVERY",),
            _AT,
        ),
    )
    return build_eligibility_input(
        session_id=UUID(int=1),
        intake=intake,
        discovery=discovery,
        configuration_version="return-v1",
        requested_at=_AT,
    )


class _Gateway:
    def __init__(
        self,
        result: Any = None,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = 0

    async def evaluate(self, request: EligibilityEvaluationInput) -> Any:
        del request
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def _result() -> EligibilityActivityResult:
    return EligibilityActivityResult(
        "eligibility-v1",
        EligibilityDecision.APPROVE,
        "Policy evidence matched.",
        900_000,
        ("POLICY:RETURN-1",),
        "AI_GATEWAY",
        "controlled-model",
        "return-v1",
        _AT,
    )


def test_input_is_derived_from_persisted_contexts() -> None:
    request = _request()
    assert request.order_references == ("ORDER-1",)
    assert (
        len([value for value in request.evidence_references if value.startswith("CONTEXT_SHA256:")])
        == 2
    )


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_a_business_decision() -> None:
    gateway = _Gateway(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await EligibilityGatewayService(gateway, timeout_seconds=1.0).evaluate_return_eligibility(
            _request()
        )

    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_valid_gateway_result_is_returned_after_one_attempt() -> None:
    gateway = _Gateway(result=_result())
    result = await EligibilityGatewayService(
        gateway, timeout_seconds=1.0
    ).evaluate_return_eligibility(_request())
    assert result.decision is EligibilityDecision.APPROVE
    assert gateway.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway", "safe_code"),
    [
        (_Gateway(delay=0.1), EligibilityGatewayErrorCode.TIMEOUT),
        (
            _Gateway(error=EligibilityGatewayError(EligibilityGatewayErrorCode.UNAVAILABLE)),
            EligibilityGatewayErrorCode.UNAVAILABLE,
        ),
        (_Gateway(result=object()), EligibilityGatewayErrorCode.RESPONSE_INVALID),
    ],
)
async def test_failures_fall_back_to_review_required(
    gateway: _Gateway, safe_code: EligibilityGatewayErrorCode
) -> None:
    result = await EligibilityGatewayService(
        gateway, timeout_seconds=0.05
    ).evaluate_return_eligibility(_request())
    assert result.decision is EligibilityDecision.REVIEW_REQUIRED
    assert result.confidence_millionths == 0
    assert f"SAFE_ERROR:{safe_code.value}" in result.evidence_references
    assert gateway.calls == 1
