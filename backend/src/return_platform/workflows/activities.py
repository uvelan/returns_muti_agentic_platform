"""Temporal activities for authoritative ReturnSession persistence only."""

from decimal import Decimal
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from return_platform.canonical.operations import (
    AgentDecision,
    AuditEvent,
    ConfigurationVersionBinding,
    ContextSnapshot,
    ReturnSession,
    WorkflowStage,
)
from return_platform.workflows.persistence import (
    ReturnSessionOutboxEvent,
    ReturnSessionPersistenceError,
    ReturnSessionPersistenceStatus,
    ReturnSessionRepositoryPort,
    ReturnSessionTransition,
)
from return_platform.workflows.return_workflow import (
    ReturnSessionActivityResult,
    ReturnSessionInitializeActivityInput,
    ReturnSessionTransitionActivityInput,
    advance_return_workflow,
)
from return_platform.workflows.stage_results import (
    StageResultValidationError,
    eligibility_result_from_binding,
    validate_stage_context_binding,
)

__all__ = ["ReturnSessionActivities"]


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ApplicationError(
            "A workflow activity identity is invalid.",
            type="RETURN_SESSION_ACTIVITY_INVALID_IDENTITY",
            non_retryable=True,
        )
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ApplicationError(
            "A workflow activity identity is invalid.",
            type="RETURN_SESSION_ACTIVITY_INVALID_IDENTITY",
            non_retryable=True,
        ) from error
    if str(parsed) != value:
        raise ApplicationError(
            "A workflow activity identity is invalid.",
            type="RETURN_SESSION_ACTIVITY_INVALID_IDENTITY",
            non_retryable=True,
        )
    return parsed


def _map_persistence_error(error: ReturnSessionPersistenceError) -> ApplicationError:
    return ApplicationError(
        error.safe_message,
        type=error.code.value,
        non_retryable=True,
    )


class ReturnSessionActivities:
    """Narrow injected activity surface with no source-system access."""

    def __init__(self, repository: ReturnSessionRepositoryPort) -> None:
        self._repository = repository

    @activity.defn(name="initialize_return_session")
    async def initialize_return_session(
        self,
        request: ReturnSessionInitializeActivityInput,
    ) -> ReturnSessionActivityResult:
        """Create authoritative session, audit, and outbox state atomically."""
        state = request.execution_state
        session_id = _uuid(state.session_id)
        correlation_id = _uuid(state.correlation_id)
        session = ReturnSession(
            session_id=session_id,
            current_stage=state.current_stage,
            status=state.status.value,
            workflow_id=request.workflow_id,
            workflow_run_id=request.workflow_run_id,
            configuration_versions=tuple(
                ConfigurationVersionBinding(
                    component=value.component,
                    version=value.version,
                )
                for value in state.configuration_versions
            ),
            created_at=request.occurred_at,
            updated_at=request.occurred_at,
        )
        audit_event = AuditEvent(
            audit_event_id=_uuid(request.audit_event_id),
            session_id=session_id,
            correlation_id=correlation_id,
            actor_type="WORKFLOW",
            actor_id="return-platform-return-v1",
            operation="CREATE_RETURN_SESSION",
            entity_type="ReturnSession",
            entity_key=f"RETURN_SESSION:{session_id}",
            after_summary="Stage INTAKE revision 0",
            outcome="SUCCESS",
            occurred_at=request.occurred_at,
        )
        outbox = ReturnSessionOutboxEvent(
            event_id=_uuid(request.outbox_event_id),
            session_id=session_id,
            command_id=None,
            event_type="RETURN_SESSION_CREATED",
            revision=0,
            current_stage=WorkflowStage.INTAKE,
            status="RUNNING",
            occurred_at=request.occurred_at,
        )
        try:
            receipt = await self._repository.initialize(session, audit_event, outbox)
        except ReturnSessionPersistenceError as error:
            raise _map_persistence_error(error) from error
        if receipt.status not in {
            ReturnSessionPersistenceStatus.CREATED,
            ReturnSessionPersistenceStatus.ALREADY_PRESENT,
        }:
            raise ApplicationError(
                "The session initialization receipt is invalid.",
                type="RETURN_SESSION_ACTIVITY_RECEIPT_INVALID",
                non_retryable=True,
            )
        return ReturnSessionActivityResult(
            revision=receipt.revision,
            current_stage=receipt.current_stage,
            state_digest=receipt.state_digest,
        )

    @activity.defn(name="transition_return_session")
    async def transition_return_session(
        self,
        request: ReturnSessionTransitionActivityInput,
    ) -> ReturnSessionActivityResult:
        """Persist one stage transition and its audit/outbox evidence."""
        expected_next = advance_return_workflow(request.previous_state, request.command)
        if expected_next != request.next_state:
            raise ApplicationError(
                "The workflow transition activity input is inconsistent.",
                type="RETURN_SESSION_ACTIVITY_TRANSITION_INVALID",
                non_retryable=True,
            )
        session_id = _uuid(request.previous_state.session_id)
        correlation_id = _uuid(request.previous_state.correlation_id)
        command_id = _uuid(request.command.command_id)
        try:
            expected_binding = validate_stage_context_binding(
                request.command.completed_stage,
                request.command.context_binding,
            )
        except StageResultValidationError as error:
            raise ApplicationError(
                "The workflow stage activity result is invalid.",
                type="RETURN_SESSION_ACTIVITY_STAGE_RESULT_INVALID",
                non_retryable=True,
            ) from error
        context_snapshot = (
            None
            if not expected_binding.payload_json
            else ContextSnapshot(
                schema_version=expected_binding.schema_version,
                payload_json=expected_binding.payload_json,
                payload_digest=expected_binding.payload_digest,
            )
        )
        agent_decision = None
        if request.command.completed_stage is WorkflowStage.ELIGIBILITY_EVALUATION:
            try:
                result = eligibility_result_from_binding(expected_binding)
            except StageResultValidationError as error:
                raise ApplicationError(
                    "The workflow stage activity result is invalid.",
                    type="RETURN_SESSION_ACTIVITY_STAGE_RESULT_INVALID",
                    non_retryable=True,
                ) from error
            agent_decision = AgentDecision(
                decision_id=_uuid(request.decision_id),
                session_id=session_id,
                agent_name="eligibility-agent",
                stage=WorkflowStage.ELIGIBILITY_EVALUATION,
                decision_type="RETURN_ELIGIBILITY",
                decision=result.decision.value,
                explanation=result.explanation,
                confidence=Decimal(result.confidence_millionths) / Decimal(1_000_000),
                evidence_references=result.evidence_references,
                model_provider=result.model_provider,
                model_name=result.model_name,
                configuration_version=result.configuration_version,
                created_at=request.occurred_at,
            )
        audit_event = AuditEvent(
            audit_event_id=_uuid(request.audit_event_id),
            session_id=session_id,
            correlation_id=correlation_id,
            actor_type="WORKFLOW",
            actor_id="return-platform-return-v1",
            operation="TRANSITION_RETURN_SESSION_STAGE",
            entity_type="ReturnSession",
            entity_key=f"RETURN_SESSION:{session_id}",
            before_summary=(
                f"Stage {request.previous_state.current_stage.value} "
                f"revision {len(request.previous_state.applied_commands)}"
            ),
            after_summary=(
                f"Stage {request.next_state.current_stage.value} "
                f"revision {len(request.next_state.applied_commands)}"
            ),
            outcome="SUCCESS",
            occurred_at=request.occurred_at,
            evidence_references=(
                (f"COMMAND:{command_id}",)
                if context_snapshot is None
                else (
                    f"COMMAND:{command_id}",
                    f"CONTEXT_SHA256:{context_snapshot.payload_digest}",
                )
            ),
        )
        outbox = ReturnSessionOutboxEvent(
            event_id=_uuid(request.outbox_event_id),
            session_id=session_id,
            command_id=command_id,
            event_type="RETURN_SESSION_STAGE_TRANSITIONED",
            revision=len(request.next_state.applied_commands),
            current_stage=request.next_state.current_stage,
            status=request.next_state.status.value,
            occurred_at=request.occurred_at,
        )
        transition = ReturnSessionTransition(
            command_id=command_id,
            expected_revision=len(request.previous_state.applied_commands),
            completed_stage=request.previous_state.current_stage,
            resulting_stage=request.next_state.current_stage,
            context_snapshot=context_snapshot,
            agent_decision=agent_decision,
            updated_at=request.occurred_at,
            audit_event=audit_event,
            outbox_event=outbox,
        )
        try:
            receipt = await self._repository.transition(session_id, transition)
        except ReturnSessionPersistenceError as error:
            raise _map_persistence_error(error) from error
        return ReturnSessionActivityResult(
            revision=receipt.revision,
            current_stage=receipt.current_stage,
            state_digest=receipt.state_digest,
        )
