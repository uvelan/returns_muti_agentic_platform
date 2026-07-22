"""Contracts for deterministic intake and order-discovery results."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from temporalio.converter import DataConverter

from return_platform.canonical.operations import WorkflowStage
from return_platform.workflows.return_workflow import ReturnWorkflowAdvanceCommand
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    StageResultValidationError,
    bind_stage_activity_result,
    eligibility_result_from_binding,
    validate_stage_context_binding,
)

_OBSERVED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)


def _intake() -> IntakeActivityResult:
    return IntakeActivityResult(
        schema_version="intake-v1",
        request_reference="REQUEST-1",
        channel=IntakeChannel.ASSOCIATE,
        customer_reference="CUSTOMER-1",
        order_reference="ORDER-1",
        evidence_references=("FIXTURE:INTAKE-1",),
        observed_at=_OBSERVED_AT,
    )


def _discovery() -> OrderDiscoveryActivityResult:
    return OrderDiscoveryActivityResult(
        schema_version="order-discovery-v1",
        request_reference="REQUEST-1",
        customer_reference="CUSTOMER-1",
        order_references=("ORDER-1", "ORDER-2"),
        source_asset_id="source.fixture.orders",
        source_document_references=("DOCUMENT:ORDER-1", "DOCUMENT:ORDER-2"),
        evidence_references=("FIXTURE:DISCOVERY-1",),
        observed_at=_OBSERVED_AT,
    )


def _eligibility() -> EligibilityActivityResult:
    return EligibilityActivityResult(
        schema_version="eligibility-v1",
        decision=EligibilityDecision.REVIEW_REQUIRED,
        explanation="Manual review is required.",
        confidence_millionths=250_000,
        evidence_references=("CONTEXT_SHA256:abc",),
        model_provider="CONTROLLED_FIXTURE",
        model_name="eligibility-v1",
        configuration_version="return-v1",
        observed_at=_OBSERVED_AT,
    )


def test_intake_result_produces_canonical_digest_bound_context() -> None:
    binding = bind_stage_activity_result(WorkflowStage.INTAKE, _intake())

    assert binding is not None
    assert binding.completed_stage is WorkflowStage.INTAKE
    assert binding.schema_version == "intake-v1"
    assert binding.payload_json.startswith('{"channel":"ASSOCIATE"')
    assert len(binding.payload_digest) == 64
    assert binding == bind_stage_activity_result(WorkflowStage.INTAKE, _intake())


def test_discovery_result_normalizes_observed_time_to_utc() -> None:
    result = _discovery()
    non_utc = OrderDiscoveryActivityResult(
        schema_version=result.schema_version,
        request_reference=result.request_reference,
        customer_reference=result.customer_reference,
        order_references=result.order_references,
        source_asset_id=result.source_asset_id,
        source_document_references=result.source_document_references,
        evidence_references=result.evidence_references,
        observed_at=result.observed_at.astimezone(timezone(timedelta(hours=5, minutes=30))),
    )

    assert bind_stage_activity_result(
        WorkflowStage.ORDER_DISCOVERY, non_utc
    ) == bind_stage_activity_result(WorkflowStage.ORDER_DISCOVERY, result)


def test_eligibility_result_round_trips_as_typed_evidence() -> None:
    binding = bind_stage_activity_result(WorkflowStage.ELIGIBILITY_EVALUATION, _eligibility())

    assert eligibility_result_from_binding(binding) == _eligibility()


@pytest.mark.parametrize(
    "result",
    (
        IntakeActivityResult(
            schema_version="wrong-v1",
            request_reference="REQUEST-1",
            channel=IntakeChannel.SYSTEM,
            customer_reference="CUSTOMER-1",
            order_reference=None,
            evidence_references=("FIXTURE:INTAKE-1",),
            observed_at=_OBSERVED_AT,
        ),
        IntakeActivityResult(
            schema_version="intake-v1",
            request_reference="REQUEST-1",
            channel=IntakeChannel.SYSTEM,
            customer_reference="CUSTOMER-1",
            order_reference=None,
            evidence_references=("DUPLICATE", "DUPLICATE"),
            observed_at=_OBSERVED_AT,
        ),
        IntakeActivityResult(
            schema_version="intake-v1",
            request_reference="REQUEST-1",
            channel=IntakeChannel.SYSTEM,
            customer_reference="CUSTOMER-1",
            order_reference=None,
            evidence_references=("FIXTURE:INTAKE-1",),
            observed_at=datetime(2026, 7, 22, 10, 0),
        ),
    ),
)
def test_rejects_invalid_intake_results(result: IntakeActivityResult) -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(WorkflowStage.INTAKE, result)


def test_rejects_result_bound_to_wrong_stage() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(WorkflowStage.ORDER_DISCOVERY, _intake())


def test_later_stage_rejects_unexpected_early_result() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(WorkflowStage.ELIGIBILITY_EVALUATION, _discovery())


def test_command_binding_rejects_cross_stage_result_field() -> None:
    with pytest.raises(StageResultValidationError):
        validate_stage_context_binding(
            WorkflowStage.INTAKE,
            bind_stage_activity_result(WorkflowStage.ORDER_DISCOVERY, _discovery()),
        )


@pytest.mark.asyncio
async def test_temporal_default_converter_round_trips_stage_results() -> None:
    values = [_intake(), _discovery()]

    payloads = await DataConverter.default.encode(values)
    decoded = await DataConverter.default.decode(
        payloads,
        [IntakeActivityResult, OrderDiscoveryActivityResult],
    )

    assert decoded == values


@pytest.mark.asyncio
async def test_temporal_default_converter_round_trips_nested_command_result() -> None:
    command = ReturnWorkflowAdvanceCommand(
        command_id="00000000-0000-0000-0000-000000000001",
        completed_stage=WorkflowStage.INTAKE,
        context_binding=bind_stage_activity_result(WorkflowStage.INTAKE, _intake()),
    )

    payloads = await DataConverter.default.encode([command])
    decoded = await DataConverter.default.decode(payloads, [ReturnWorkflowAdvanceCommand])

    assert decoded == [command]
