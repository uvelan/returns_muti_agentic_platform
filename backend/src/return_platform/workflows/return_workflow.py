"""Deterministic execution-state core for the durable Return workflow."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Final, Never
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

from return_platform.canonical.operations import WorkflowStage
from return_platform.workflows.stage_results import (
    StageContextBinding,
    StageResultValidationError,
    validate_stage_context_binding,
)

__all__ = [
    "DEFAULT_STAGE_SEQUENCE",
    "AppliedStageCommand",
    "ReturnSessionActivityResult",
    "ReturnSessionInitializeActivityInput",
    "ReturnSessionTransitionActivityInput",
    "ReturnWorkflow",
    "ReturnWorkflowAdvanceCommand",
    "ReturnWorkflowConfigurationVersion",
    "ReturnWorkflowErrorCode",
    "ReturnWorkflowExecutionState",
    "ReturnWorkflowInput",
    "ReturnWorkflowStatus",
    "ReturnWorkflowTransitionError",
    "advance_return_workflow",
    "start_return_workflow_execution",
]

_WORKFLOW_VERSION: Final = "1.0"
_ACTIVITY_TIMEOUT: Final = timedelta(seconds=10)
_MAX_CONFIGURATION_VERSIONS: Final = 128
_MAX_STAGE_SEQUENCE_LENGTH: Final = 32
_REFERENCE_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")

# Default sequencing when a workflow input doesn't pin one explicitly (e.g. tests,
# or a caller that hasn't wired the config-driven path yet). The authoritative
# sequence for real return sessions is `config/workflows/return_session.yaml`,
# pinned per configuration release -- see operations/orchestrator.py::_handle().
DEFAULT_STAGE_SEQUENCE: Final[tuple[WorkflowStage, ...]] = (
    WorkflowStage.INTAKE,
    WorkflowStage.ORDER_DISCOVERY,
    WorkflowStage.ELIGIBILITY_EVALUATION,
    WorkflowStage.RETURN_REQUEST,
    WorkflowStage.FULFILLMENT_TRACKING,
    WorkflowStage.BAY_ASSIGNMENT,
    WorkflowStage.FEEDBACK_LEARNING,
    WorkflowStage.COMPLETED,
)


class ReturnWorkflowStatus(StrEnum):
    """Code-owned Temporal execution statuses, not business return statuses."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class ReturnWorkflowErrorCode(StrEnum):
    """Stable safe errors for deterministic workflow transition rejection."""

    INVALID_INPUT = "RETURN_WORKFLOW_INVALID_INPUT"
    INVALID_IDENTITY = "RETURN_WORKFLOW_INVALID_IDENTITY"
    INVALID_CONFIGURATION = "RETURN_WORKFLOW_INVALID_CONFIGURATION"
    INVALID_STAGE_RESULT = "RETURN_WORKFLOW_INVALID_STAGE_RESULT"
    NOT_STARTED = "RETURN_WORKFLOW_NOT_STARTED"
    STAGE_OUT_OF_ORDER = "RETURN_WORKFLOW_STAGE_OUT_OF_ORDER"
    COMMAND_CONFLICT = "RETURN_WORKFLOW_COMMAND_CONFLICT"
    ALREADY_COMPLETED = "RETURN_WORKFLOW_ALREADY_COMPLETED"
    PERSISTENCE_MISMATCH = "RETURN_WORKFLOW_PERSISTENCE_MISMATCH"


_SAFE_MESSAGES: Final = {
    ReturnWorkflowErrorCode.INVALID_INPUT: "The workflow input is invalid.",
    ReturnWorkflowErrorCode.INVALID_IDENTITY: "A workflow identity is invalid.",
    ReturnWorkflowErrorCode.INVALID_CONFIGURATION: (
        "The workflow configuration binding is invalid."
    ),
    ReturnWorkflowErrorCode.INVALID_STAGE_RESULT: (
        "The workflow stage activity result is invalid."
    ),
    ReturnWorkflowErrorCode.NOT_STARTED: "The workflow execution has not started.",
    ReturnWorkflowErrorCode.STAGE_OUT_OF_ORDER: ("The stage-completion command is out of order."),
    ReturnWorkflowErrorCode.COMMAND_CONFLICT: (
        "The command identity is already bound to different stage evidence."
    ),
    ReturnWorkflowErrorCode.ALREADY_COMPLETED: "The workflow execution is complete.",
    ReturnWorkflowErrorCode.PERSISTENCE_MISMATCH: (
        "Persisted session state does not match workflow execution state."
    ),
}


class ReturnWorkflowTransitionError(RuntimeError):
    """Deterministic safe workflow rejection without raw business data."""

    def __init__(self, code: ReturnWorkflowErrorCode) -> None:
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: ReturnWorkflowErrorCode) -> Never:
    raise ReturnWorkflowTransitionError(code)


@dataclass(frozen=True, slots=True)
class ReturnWorkflowConfigurationVersion:
    """One immutable component/version binding copied into workflow input."""

    component: str
    version: str


@dataclass(frozen=True, slots=True)
class ReturnWorkflowInput:
    """Business-data-free input for one Return workflow execution."""

    session_id: str
    correlation_id: str
    workflow_version: str
    configuration_versions: tuple[ReturnWorkflowConfigurationVersion, ...]
    stage_sequence: tuple[WorkflowStage, ...] = DEFAULT_STAGE_SEQUENCE


@dataclass(frozen=True, slots=True)
class ReturnWorkflowAdvanceCommand:
    """Idempotent evidence that the current execution stage completed."""

    command_id: str
    completed_stage: WorkflowStage
    context_binding: StageContextBinding


@dataclass(frozen=True, slots=True)
class AppliedStageCommand:
    """Bound command identity retained for deterministic replay and deduplication."""

    command_id: str
    completed_stage: WorkflowStage
    context_digest: str | None


@dataclass(frozen=True, slots=True)
class ReturnWorkflowExecutionState:
    """Temporal-owned coordination state; never authoritative business state."""

    session_id: str
    correlation_id: str
    workflow_version: str
    configuration_versions: tuple[ReturnWorkflowConfigurationVersion, ...]
    stage_sequence: tuple[WorkflowStage, ...]
    current_stage: WorkflowStage
    status: ReturnWorkflowStatus
    completed_stages: tuple[WorkflowStage, ...]
    applied_commands: tuple[AppliedStageCommand, ...]


@dataclass(frozen=True, slots=True)
class ReturnSessionInitializeActivityInput:
    """Serializable request to create authoritative session state."""

    execution_state: ReturnWorkflowExecutionState
    workflow_id: str
    workflow_run_id: str
    occurred_at: datetime
    audit_event_id: str
    outbox_event_id: str


@dataclass(frozen=True, slots=True)
class ReturnSessionTransitionActivityInput:
    """Serializable request to persist one workflow stage transition."""

    previous_state: ReturnWorkflowExecutionState
    next_state: ReturnWorkflowExecutionState
    command: ReturnWorkflowAdvanceCommand
    occurred_at: datetime
    audit_event_id: str
    outbox_event_id: str
    decision_id: str


@dataclass(frozen=True, slots=True)
class ReturnSessionActivityResult:
    """Minimal persisted-state receipt returned to deterministic workflow code."""

    revision: int
    current_stage: WorkflowStage
    state_digest: str


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        _raise_error(ReturnWorkflowErrorCode.INVALID_IDENTITY)
    try:
        parsed = UUID(value)
    except ValueError:
        _raise_error(ReturnWorkflowErrorCode.INVALID_IDENTITY)
    if str(parsed) != value:
        _raise_error(ReturnWorkflowErrorCode.INVALID_IDENTITY)
    return value


def _valid_reference(value: object) -> bool:
    return isinstance(value, str) and _REFERENCE_PATTERN.fullmatch(value) is not None


def _stage_context_binding(command: ReturnWorkflowAdvanceCommand) -> StageContextBinding:
    try:
        return validate_stage_context_binding(
            command.completed_stage,
            command.context_binding,
        )
    except StageResultValidationError:
        _raise_error(ReturnWorkflowErrorCode.INVALID_STAGE_RESULT)


def _validate_configuration_versions(
    values: object,
) -> tuple[ReturnWorkflowConfigurationVersion, ...]:
    if (
        not isinstance(values, tuple)
        or not 1 <= len(values) <= _MAX_CONFIGURATION_VERSIONS
        or any(not isinstance(value, ReturnWorkflowConfigurationVersion) for value in values)
    ):
        _raise_error(ReturnWorkflowErrorCode.INVALID_CONFIGURATION)
    components: set[str] = set()
    for value in values:
        if not _valid_reference(value.component) or not _valid_reference(value.version):
            _raise_error(ReturnWorkflowErrorCode.INVALID_CONFIGURATION)
        if value.component in components:
            _raise_error(ReturnWorkflowErrorCode.INVALID_CONFIGURATION)
        components.add(value.component)
    return values


def _validate_stage_sequence(value: object) -> tuple[WorkflowStage, ...]:
    if (
        not isinstance(value, tuple)
        or not 1 <= len(value) <= _MAX_STAGE_SEQUENCE_LENGTH
        or any(not isinstance(stage, WorkflowStage) for stage in value)
        or len(set(value)) != len(value)
    ):
        _raise_error(ReturnWorkflowErrorCode.INVALID_CONFIGURATION)
    return value


def start_return_workflow_execution(
    workflow_input: ReturnWorkflowInput,
) -> ReturnWorkflowExecutionState:
    """Validate input and create the initial deterministic execution state."""
    if not isinstance(workflow_input, ReturnWorkflowInput):
        _raise_error(ReturnWorkflowErrorCode.INVALID_INPUT)
    session_id = _canonical_uuid(workflow_input.session_id)
    correlation_id = _canonical_uuid(workflow_input.correlation_id)
    if workflow_input.workflow_version != _WORKFLOW_VERSION:
        _raise_error(ReturnWorkflowErrorCode.INVALID_CONFIGURATION)
    configuration_versions = _validate_configuration_versions(workflow_input.configuration_versions)
    stage_sequence = _validate_stage_sequence(workflow_input.stage_sequence)
    return ReturnWorkflowExecutionState(
        session_id=session_id,
        correlation_id=correlation_id,
        workflow_version=workflow_input.workflow_version,
        configuration_versions=configuration_versions,
        stage_sequence=stage_sequence,
        current_stage=stage_sequence[0],
        status=ReturnWorkflowStatus.RUNNING,
        completed_stages=(),
        applied_commands=(),
    )


def advance_return_workflow(
    state: ReturnWorkflowExecutionState,
    command: ReturnWorkflowAdvanceCommand,
) -> ReturnWorkflowExecutionState:
    """Apply one ordered idempotent stage-completion command."""
    if not isinstance(state, ReturnWorkflowExecutionState) or not isinstance(
        command, ReturnWorkflowAdvanceCommand
    ):
        _raise_error(ReturnWorkflowErrorCode.INVALID_INPUT)
    command_id = _canonical_uuid(command.command_id)
    for applied in state.applied_commands:
        if applied.command_id == command_id:
            if applied.completed_stage is not command.completed_stage:
                _raise_error(ReturnWorkflowErrorCode.COMMAND_CONFLICT)
            binding = _stage_context_binding(command)
            digest = binding.payload_digest or None
            if applied.context_digest != digest:
                _raise_error(ReturnWorkflowErrorCode.COMMAND_CONFLICT)
            return state
    if state.status is ReturnWorkflowStatus.COMPLETED:
        _raise_error(ReturnWorkflowErrorCode.ALREADY_COMPLETED)
    if command.completed_stage is not state.current_stage:
        _raise_error(ReturnWorkflowErrorCode.STAGE_OUT_OF_ORDER)
    binding = _stage_context_binding(command)
    next_stage_map = dict(pairwise(state.stage_sequence))
    next_stage = next_stage_map.get(state.current_stage)
    if next_stage is None:
        _raise_error(ReturnWorkflowErrorCode.ALREADY_COMPLETED)
    status = (
        ReturnWorkflowStatus.COMPLETED
        if next_stage is state.stage_sequence[-1]
        else ReturnWorkflowStatus.RUNNING
    )
    return ReturnWorkflowExecutionState(
        session_id=state.session_id,
        correlation_id=state.correlation_id,
        workflow_version=state.workflow_version,
        configuration_versions=state.configuration_versions,
        stage_sequence=state.stage_sequence,
        current_stage=next_stage,
        status=status,
        completed_stages=(*state.completed_stages, command.completed_stage),
        applied_commands=(
            *state.applied_commands,
            AppliedStageCommand(
                command_id=command_id,
                completed_stage=command.completed_stage,
                context_digest=binding.payload_digest or None,
            ),
        ),
    )


@workflow.defn(name="return-platform-return-v1")
class ReturnWorkflow:
    """Durably coordinate ordered stages without owning business state."""

    def __init__(self) -> None:
        self._state: ReturnWorkflowExecutionState | None = None
        self._persistence_ready = False
        self._transition_in_progress = False

    def _require_state(self) -> ReturnWorkflowExecutionState:
        if self._state is None:
            _raise_error(ReturnWorkflowErrorCode.NOT_STARTED)
        return self._state

    @workflow.run
    async def run(self, workflow_input: ReturnWorkflowInput) -> ReturnWorkflowExecutionState:
        """Wait durably until every code-owned workflow stage is completed."""
        self._state = start_return_workflow_execution(workflow_input)
        info = workflow.info()
        initialized = await workflow.execute_activity(
            "initialize_return_session",
            ReturnSessionInitializeActivityInput(
                execution_state=self._state,
                workflow_id=info.workflow_id,
                workflow_run_id=info.run_id,
                occurred_at=workflow.now(),
                audit_event_id=str(workflow.uuid4()),
                outbox_event_id=str(workflow.uuid4()),
            ),
            result_type=ReturnSessionActivityResult,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        if (
            not isinstance(initialized, ReturnSessionActivityResult)
            or initialized.revision != 0
            or initialized.current_stage is not self._state.current_stage
        ):
            _raise_error(ReturnWorkflowErrorCode.PERSISTENCE_MISMATCH)
        self._persistence_ready = True
        await workflow.wait_condition(
            lambda: self._state is not None and self._state.status is ReturnWorkflowStatus.COMPLETED
        )
        await workflow.wait_condition(lambda: workflow.all_handlers_finished())
        return self._require_state()

    @workflow.query(name="execution_state")
    def execution_state(self) -> ReturnWorkflowExecutionState:
        """Return execution-only coordination state for observability."""
        return self._require_state()

    @workflow.update(name="complete_stage")
    async def complete_stage(
        self,
        command: ReturnWorkflowAdvanceCommand,
    ) -> ReturnWorkflowExecutionState:
        """Apply one ordered idempotent stage-completion command."""
        await workflow.wait_condition(
            lambda: self._persistence_ready and not self._transition_in_progress
        )
        self._transition_in_progress = True
        try:
            previous_state = self._require_state()
            next_state = advance_return_workflow(previous_state, command)
            workflow.logger.info(
                "Return workflow stage transition: status=%s from=%s to=%s",
                next_state.status,
                previous_state.current_stage,
                next_state.current_stage,
            )
            if next_state is previous_state:
                return previous_state
            persisted = await workflow.execute_activity(
                "transition_return_session",
                ReturnSessionTransitionActivityInput(
                    previous_state=previous_state,
                    next_state=next_state,
                    command=command,
                    occurred_at=workflow.now(),
                    audit_event_id=str(workflow.uuid4()),
                    outbox_event_id=str(workflow.uuid4()),
                    decision_id=str(workflow.uuid4()),
                ),
                result_type=ReturnSessionActivityResult,
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            if (
                not isinstance(persisted, ReturnSessionActivityResult)
                or persisted.revision != len(next_state.applied_commands)
                or persisted.current_stage is not next_state.current_stage
            ):
                _raise_error(ReturnWorkflowErrorCode.PERSISTENCE_MISMATCH)
            self._state = next_state
            return next_state
        finally:
            self._transition_in_progress = False
