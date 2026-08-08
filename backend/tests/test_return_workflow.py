"""Contract tests for deterministic Temporal Return workflow coordination."""

from dataclasses import fields
from datetime import UTC, datetime
from uuid import UUID

import pytest
from temporalio import workflow
from temporalio.converter import DataConverter
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from return_platform.canonical.operations import WorkflowStage
from return_platform.workflows.return_workflow import (
    ReturnWorkflow,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowErrorCode,
    ReturnWorkflowExecutionState,
    ReturnWorkflowInput,
    ReturnWorkflowStatus,
    ReturnWorkflowTransitionError,
    advance_return_workflow,
    start_return_workflow_execution,
)
from return_platform.workflows.stage_results import (
    BayAssignmentActivityResult,
    BayAssignmentStatus,
    EligibilityActivityResult,
    EligibilityDecision,
    FeedbackLearningActivityResult,
    FeedbackLearningStatus,
    FulfillmentTrackingActivityResult,
    FulfillmentTrackingStatus,
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    ReturnRequestActivityResult,
    ReturnRequestOutcome,
    bind_stage_activity_result,
    empty_stage_context_binding,
)

_SESSION_ID = "3e3d274b-229f-4f46-a7d1-f5d94bd9b53d"
_CORRELATION_ID = "894231ea-f972-4a26-8944-420e3abcbd67"
_OBSERVED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
_STAGES = (
    WorkflowStage.INTAKE,
    WorkflowStage.ORDER_DISCOVERY,
    WorkflowStage.ELIGIBILITY_EVALUATION,
    WorkflowStage.RETURN_REQUEST,
    WorkflowStage.FULFILLMENT_TRACKING,
    WorkflowStage.BAY_ASSIGNMENT,
    WorkflowStage.FEEDBACK_LEARNING,
)


def _input() -> ReturnWorkflowInput:
    return ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(
            ReturnWorkflowConfigurationVersion("workflow", "return-v1"),
            ReturnWorkflowConfigurationVersion("eligibility-policy", "policy-v1"),
        ),
    )


def _command(index: int, stage: WorkflowStage) -> ReturnWorkflowAdvanceCommand:
    intake_result: IntakeActivityResult | None = None
    discovery_result: OrderDiscoveryActivityResult | None = None
    eligibility_result: EligibilityActivityResult | None = None
    return_request_result: ReturnRequestActivityResult | None = None
    fulfillment_result: FulfillmentTrackingActivityResult | None = None
    bay_assignment_result: BayAssignmentActivityResult | None = None
    feedback_result: FeedbackLearningActivityResult | None = None
    if stage is WorkflowStage.INTAKE:
        intake_result = IntakeActivityResult(
            schema_version="intake-v1",
            request_reference="REQUEST-1",
            channel=IntakeChannel.ASSOCIATE,
            customer_reference="CUSTOMER-1",
            order_reference="ORDER-1",
            evidence_references=("FIXTURE:INTAKE-1",),
            observed_at=_OBSERVED_AT,
        )
    elif stage is WorkflowStage.ORDER_DISCOVERY:
        discovery_result = OrderDiscoveryActivityResult(
            schema_version="order-discovery-v1",
            request_reference="REQUEST-1",
            customer_reference="CUSTOMER-1",
            order_references=("ORDER-1",),
            source_asset_id="source.fixture.orders",
            source_document_references=("DOCUMENT:ORDER-1",),
            evidence_references=("FIXTURE:DISCOVERY-1",),
            observed_at=_OBSERVED_AT,
        )
    elif stage is WorkflowStage.ELIGIBILITY_EVALUATION:
        eligibility_result = EligibilityActivityResult(
            schema_version="eligibility-v1",
            decision=EligibilityDecision.REVIEW_REQUIRED,
            explanation="Manual review is required.",
            confidence_millionths=0,
            evidence_references=("FIXTURE:ELIGIBILITY-1",),
            model_provider="DETERMINISTIC",
            model_name="eligibility-fallback-v1",
            configuration_version="policy-v1",
            observed_at=_OBSERVED_AT,
        )
    elif stage is WorkflowStage.RETURN_REQUEST:
        eligibility_binding = bind_stage_activity_result(
            WorkflowStage.ELIGIBILITY_EVALUATION,
            EligibilityActivityResult(
                schema_version="eligibility-v1",
                decision=EligibilityDecision.REVIEW_REQUIRED,
                explanation="Manual review is required.",
                confidence_millionths=0,
                evidence_references=("FIXTURE:ELIGIBILITY-1",),
                model_provider="DETERMINISTIC",
                model_name="eligibility-fallback-v1",
                configuration_version="policy-v1",
                observed_at=_OBSERVED_AT,
            ),
        )
        return_request_result = ReturnRequestActivityResult(
            schema_version="return-request-v1",
            eligibility_decision=EligibilityDecision.REVIEW_REQUIRED,
            outcome=ReturnRequestOutcome.REVIEW_PENDING,
            request_reference="REQUEST-1",
            return_reference=None,
            eligibility_context_digest=eligibility_binding.payload_digest,
            evidence_references=(f"CONTEXT_SHA256:{eligibility_binding.payload_digest}",),
            configuration_version="return-v1",
            observed_at=_OBSERVED_AT,
        )
    elif stage is WorkflowStage.FULFILLMENT_TRACKING:
        eligibility_binding = bind_stage_activity_result(
            WorkflowStage.ELIGIBILITY_EVALUATION,
            EligibilityActivityResult(
                "eligibility-v1",
                EligibilityDecision.REVIEW_REQUIRED,
                "Manual review is required.",
                0,
                ("FIXTURE:ELIGIBILITY-1",),
                "DETERMINISTIC",
                "eligibility-fallback-v1",
                "policy-v1",
                _OBSERVED_AT,
            ),
        )
        request_binding = bind_stage_activity_result(
            WorkflowStage.RETURN_REQUEST,
            ReturnRequestActivityResult(
                "return-request-v1",
                EligibilityDecision.REVIEW_REQUIRED,
                ReturnRequestOutcome.REVIEW_PENDING,
                "REQUEST-1",
                None,
                eligibility_binding.payload_digest,
                (f"CONTEXT_SHA256:{eligibility_binding.payload_digest}",),
                "return-v1",
                _OBSERVED_AT,
            ),
        )
        fulfillment_result = FulfillmentTrackingActivityResult(
            "fulfillment-tracking-v1",
            ReturnRequestOutcome.REVIEW_PENDING,
            FulfillmentTrackingStatus.NOT_APPLICABLE,
            "REQUEST-1",
            None,
            None,
            None,
            request_binding.payload_digest,
            (f"CONTEXT_SHA256:{request_binding.payload_digest}",),
            "return-v1",
            _OBSERVED_AT,
        )
    elif stage is WorkflowStage.BAY_ASSIGNMENT:
        fulfillment_binding = bind_stage_activity_result(
            WorkflowStage.FULFILLMENT_TRACKING,
            FulfillmentTrackingActivityResult(
                "fulfillment-tracking-v1",
                ReturnRequestOutcome.REVIEW_PENDING,
                FulfillmentTrackingStatus.NOT_APPLICABLE,
                "REQUEST-1",
                None,
                None,
                None,
                "a" * 64,
                ("FIXTURE:FULFILLMENT-1",),
                "return-v1",
                _OBSERVED_AT,
            ),
        )
        bay_assignment_result = BayAssignmentActivityResult(
            "bay-assignment-v1",
            FulfillmentTrackingStatus.NOT_APPLICABLE,
            BayAssignmentStatus.NOT_APPLICABLE,
            "REQUEST-1",
            None,
            None,
            None,
            fulfillment_binding.payload_digest,
            (f"CONTEXT_SHA256:{fulfillment_binding.payload_digest}",),
            "return-v1",
            _OBSERVED_AT,
        )
    elif stage is WorkflowStage.FEEDBACK_LEARNING:
        bay_binding = bind_stage_activity_result(
            WorkflowStage.BAY_ASSIGNMENT,
            BayAssignmentActivityResult(
                "bay-assignment-v1",
                FulfillmentTrackingStatus.NOT_APPLICABLE,
                BayAssignmentStatus.NOT_APPLICABLE,
                "REQUEST-1",
                None,
                None,
                None,
                "b" * 64,
                ("FIXTURE:BAY-1",),
                "return-v1",
                _OBSERVED_AT,
            ),
        )
        feedback_result = FeedbackLearningActivityResult(
            "feedback-learning-v1",
            BayAssignmentStatus.NOT_APPLICABLE,
            FeedbackLearningStatus.NOT_APPLICABLE,
            "REQUEST-1",
            None,
            None,
            None,
            None,
            None,
            bay_binding.payload_digest,
            (f"CONTEXT_SHA256:{bay_binding.payload_digest}",),
            "return-v1",
            _OBSERVED_AT,
        )
    return ReturnWorkflowAdvanceCommand(
        command_id=str(UUID(int=index + 1)),
        completed_stage=stage,
        context_binding=(
            bind_stage_activity_result(
                stage,
                intake_result
                or discovery_result
                or eligibility_result
                or return_request_result
                or fulfillment_result
                or bay_assignment_result
                or feedback_result,
            )
            if any(
                result is not None
                for result in (
                    intake_result,
                    discovery_result,
                    eligibility_result,
                    return_request_result,
                    fulfillment_result,
                    bay_assignment_result,
                    feedback_result,
                )
            )
            else empty_stage_context_binding(stage)
        ),
    )


def test_configured_stage_sequence_overrides_the_default() -> None:
    """Stage sequencing is a first-class input, not a hardcoded module constant --
    a shorter, differently-configured sequence produces genuinely different
    real behavior (fewer required stages, an earlier COMPLETED transition)."""
    custom_sequence = (
        WorkflowStage.INTAKE,
        WorkflowStage.ORDER_DISCOVERY,
        WorkflowStage.COMPLETED,
    )
    workflow_input = ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
        stage_sequence=custom_sequence,
    )
    state = start_return_workflow_execution(workflow_input)
    assert state.current_stage is WorkflowStage.INTAKE

    state = advance_return_workflow(state, _command(0, WorkflowStage.INTAKE))
    assert state.current_stage is WorkflowStage.ORDER_DISCOVERY
    assert state.status is ReturnWorkflowStatus.RUNNING

    state = advance_return_workflow(state, _command(1, WorkflowStage.ORDER_DISCOVERY))
    assert state.current_stage is WorkflowStage.COMPLETED
    assert state.status is ReturnWorkflowStatus.COMPLETED


def test_empty_stage_sequence_is_rejected() -> None:
    workflow_input = ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
        stage_sequence=(),
    )
    with pytest.raises(ReturnWorkflowTransitionError) as excinfo:
        start_return_workflow_execution(workflow_input)
    assert excinfo.value.code is ReturnWorkflowErrorCode.INVALID_CONFIGURATION


def test_duplicate_stage_in_sequence_is_rejected() -> None:
    workflow_input = ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
        stage_sequence=(WorkflowStage.INTAKE, WorkflowStage.INTAKE),
    )
    with pytest.raises(ReturnWorkflowTransitionError) as excinfo:
        start_return_workflow_execution(workflow_input)
    assert excinfo.value.code is ReturnWorkflowErrorCode.INVALID_CONFIGURATION


def test_starts_with_execution_only_intake_state() -> None:
    state = start_return_workflow_execution(_input())

    assert state.current_stage is WorkflowStage.INTAKE
    assert state.status is ReturnWorkflowStatus.RUNNING
    assert state.completed_stages == ()
    assert {field.name for field in fields(state)} == {
        "session_id",
        "correlation_id",
        "workflow_version",
        "configuration_versions",
        "stage_sequence",
        "current_stage",
        "status",
        "completed_stages",
        "applied_commands",
    }


def test_advances_only_through_the_fixed_stage_sequence() -> None:
    state = start_return_workflow_execution(_input())

    for index, stage in enumerate(_STAGES):
        state = advance_return_workflow(state, _command(index, stage))

    assert state.current_stage is WorkflowStage.COMPLETED
    assert state.status is ReturnWorkflowStatus.COMPLETED
    assert state.completed_stages == _STAGES
    assert len(state.applied_commands) == len(_STAGES)


def test_identical_command_replay_is_idempotent() -> None:
    initial = start_return_workflow_execution(_input())
    command = _command(0, WorkflowStage.INTAKE)
    advanced = advance_return_workflow(initial, command)

    assert advance_return_workflow(advanced, command) is advanced


def test_rejects_command_identity_reuse_with_different_evidence() -> None:
    initial = start_return_workflow_execution(_input())
    advanced = advance_return_workflow(initial, _command(0, WorkflowStage.INTAKE))
    conflicting = _command(0, WorkflowStage.ORDER_DISCOVERY)

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        advance_return_workflow(advanced, conflicting)

    assert exc_info.value.code is ReturnWorkflowErrorCode.COMMAND_CONFLICT


def test_rejects_out_of_order_stage_without_mutating_state() -> None:
    state = start_return_workflow_execution(_input())

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        advance_return_workflow(state, _command(0, WorkflowStage.ORDER_DISCOVERY))

    assert exc_info.value.code is ReturnWorkflowErrorCode.STAGE_OUT_OF_ORDER
    assert state.current_stage is WorkflowStage.INTAKE


def test_rejects_missing_required_intake_result() -> None:
    state = start_return_workflow_execution(_input())
    command = ReturnWorkflowAdvanceCommand(
        command_id=str(UUID(int=1)),
        completed_stage=WorkflowStage.INTAKE,
        context_binding=empty_stage_context_binding(WorkflowStage.INTAKE),
    )

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        advance_return_workflow(state, command)

    assert exc_info.value.code is ReturnWorkflowErrorCode.INVALID_STAGE_RESULT


def test_replay_rejects_changed_context_evidence() -> None:
    state = start_return_workflow_execution(_input())
    command = _command(0, WorkflowStage.INTAKE)
    advanced = advance_return_workflow(state, command)
    result = IntakeActivityResult(
        schema_version="intake-v1",
        request_reference="REQUEST-1",
        channel=IntakeChannel.ASSOCIATE,
        customer_reference="CUSTOMER-1",
        order_reference="ORDER-2",
        evidence_references=("FIXTURE:INTAKE-1",),
        observed_at=_OBSERVED_AT,
    )
    conflicting = ReturnWorkflowAdvanceCommand(
        command_id=command.command_id,
        completed_stage=command.completed_stage,
        context_binding=bind_stage_activity_result(WorkflowStage.INTAKE, result),
    )

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        advance_return_workflow(advanced, conflicting)

    assert exc_info.value.code is ReturnWorkflowErrorCode.COMMAND_CONFLICT


def test_rejects_noncanonical_identity() -> None:
    workflow_input = _input()
    invalid = ReturnWorkflowInput(
        session_id=workflow_input.session_id.upper(),
        correlation_id=workflow_input.correlation_id,
        workflow_version=workflow_input.workflow_version,
        configuration_versions=workflow_input.configuration_versions,
    )

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        start_return_workflow_execution(invalid)

    assert exc_info.value.code is ReturnWorkflowErrorCode.INVALID_IDENTITY


def test_rejects_duplicate_configuration_components() -> None:
    invalid = ReturnWorkflowInput(
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        workflow_version="1.0",
        configuration_versions=(
            ReturnWorkflowConfigurationVersion("workflow", "return-v1"),
            ReturnWorkflowConfigurationVersion("workflow", "return-v2"),
        ),
    )

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        start_return_workflow_execution(invalid)

    assert exc_info.value.code is ReturnWorkflowErrorCode.INVALID_CONFIGURATION


def test_rejects_advancement_after_completion() -> None:
    state: ReturnWorkflowExecutionState = start_return_workflow_execution(_input())
    for index, stage in enumerate(_STAGES):
        state = advance_return_workflow(state, _command(index, stage))

    with pytest.raises(ReturnWorkflowTransitionError) as exc_info:
        advance_return_workflow(state, _command(99, WorkflowStage.COMPLETED))

    assert exc_info.value.code is ReturnWorkflowErrorCode.ALREADY_COMPLETED


@pytest.mark.asyncio
async def test_temporal_default_converter_round_trips_workflow_contracts() -> None:
    workflow_input = _input()

    payloads = await DataConverter.default.encode([workflow_input])
    decoded = await DataConverter.default.decode(payloads, [ReturnWorkflowInput])

    assert decoded == [workflow_input]


@pytest.mark.asyncio
async def test_temporal_sandbox_prepares_workflow_definition() -> None:
    SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(ReturnWorkflow))
