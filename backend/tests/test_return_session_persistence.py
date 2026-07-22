"""Contract tests for authoritative ReturnSession persistence and activities."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from pymongo.errors import AutoReconnect, DuplicateKeyError, OperationFailure
from temporalio.exceptions import ApplicationError

from return_platform.canonical.operations import (
    AuditEvent,
    ConfigurationVersionBinding,
    ContextSnapshot,
    ReturnSession,
    WorkflowStage,
)
from return_platform.workflows.activities import ReturnSessionActivities
from return_platform.workflows.persistence import (
    ReturnSessionDocument,
    ReturnSessionOutboxEvent,
    ReturnSessionPersistenceError,
    ReturnSessionPersistenceErrorCode,
    ReturnSessionPersistenceReceipt,
    ReturnSessionPersistenceStatus,
    ReturnSessionRepository,
    ReturnSessionTransition,
)
from return_platform.workflows.return_workflow import (
    ReturnSessionInitializeActivityInput,
    ReturnSessionTransitionActivityInput,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowInput,
    advance_return_workflow,
    start_return_workflow_execution,
)
from return_platform.workflows.stage_results import (
    IntakeActivityResult,
    IntakeChannel,
    bind_stage_activity_result,
)

_SESSION_ID = UUID("3e3d274b-229f-4f46-a7d1-f5d94bd9b53d")
_CORRELATION_ID = UUID("894231ea-f972-4a26-8944-420e3abcbd67")
_COMMAND_ID = UUID("00000000-0000-0000-0000-000000000001")
_CREATED_AT = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


class _FakePersistencePort:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.insert_calls = 0
        self.transition_calls = 0
        self.write_error: BaseException | None = None

    async def insert_bundle(
        self,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
    ) -> None:
        del audit, outbox
        self.insert_calls += 1
        if self.write_error is not None:
            raise self.write_error
        document_id = str(session["_id"])
        if document_id in self.sessions:
            raise DuplicateKeyError("duplicate")
        self.sessions[document_id] = dict(session)

    async def transition_bundle(
        self,
        *,
        document_id: str,
        expected_revision: int,
        completed_stage: str,
        command_id: str,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
        decision: dict[str, object] | None,
    ) -> tuple[Mapping[str, object], bool]:
        del audit, outbox, decision
        self.transition_calls += 1
        if self.write_error is not None:
            raise self.write_error
        current = self.sessions[document_id]
        current_session = current["session"]
        applied_commands = current["applied_commands"]
        assert isinstance(current_session, dict)
        assert isinstance(applied_commands, list)
        command_already_applied = any(
            isinstance(item, dict) and item.get("command_id") == command_id
            for item in applied_commands
        )
        if (
            current["revision"] != expected_revision
            or current_session["current_stage"] != completed_stage
            or command_already_applied
        ):
            return current, False
        self.sessions[document_id] = dict(session)
        return self.sessions[document_id], True

    async def find_session(self, document_id: str) -> Mapping[str, object] | None:
        value = self.sessions.get(document_id)
        return None if value is None else dict(value)


def _session() -> ReturnSession:
    return ReturnSession(
        session_id=_SESSION_ID,
        current_stage=WorkflowStage.INTAKE,
        status="RUNNING",
        workflow_id="return-workflow-1",
        workflow_run_id="return-run-1",
        configuration_versions=(
            ConfigurationVersionBinding(component="workflow", version="return-v1"),
        ),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _audit(*, transition: bool = False) -> AuditEvent:
    occurred_at = _CREATED_AT + (timedelta(minutes=1) if transition else timedelta())
    return AuditEvent(
        audit_event_id=UUID(int=12 if transition else 10),
        session_id=_SESSION_ID,
        correlation_id=_CORRELATION_ID,
        actor_type="WORKFLOW",
        actor_id="return-platform-return-v1",
        operation="TRANSITION_RETURN_SESSION_STAGE" if transition else "CREATE_RETURN_SESSION",
        entity_type="ReturnSession",
        entity_key=f"RETURN_SESSION:{_SESSION_ID}",
        after_summary="Stage ORDER_DISCOVERY revision 1"
        if transition
        else "Stage INTAKE revision 0",
        outcome="SUCCESS",
        occurred_at=occurred_at,
    )


def _outbox(*, transition: bool = False) -> ReturnSessionOutboxEvent:
    occurred_at = _CREATED_AT + (timedelta(minutes=1) if transition else timedelta())
    return ReturnSessionOutboxEvent(
        event_id=UUID(int=13 if transition else 11),
        session_id=_SESSION_ID,
        command_id=_COMMAND_ID if transition else None,
        event_type=(
            "RETURN_SESSION_STAGE_TRANSITIONED" if transition else "RETURN_SESSION_CREATED"
        ),
        revision=1 if transition else 0,
        current_stage=(WorkflowStage.ORDER_DISCOVERY if transition else WorkflowStage.INTAKE),
        status="RUNNING",
        occurred_at=occurred_at,
    )


def _transition() -> ReturnSessionTransition:
    return ReturnSessionTransition(
        command_id=_COMMAND_ID,
        expected_revision=0,
        completed_stage=WorkflowStage.INTAKE,
        resulting_stage=WorkflowStage.ORDER_DISCOVERY,
        context_snapshot=ContextSnapshot.from_mapping(
            schema_version="intake-v1",
            payload={"channel": "ASSOCIATE", "request_reference": "REQUEST-1"},
        ),
        updated_at=_CREATED_AT + timedelta(minutes=1),
        audit_event=_audit(transition=True),
        outbox_event=_outbox(transition=True),
    )


def _repository(port: _FakePersistencePort) -> ReturnSessionRepository:
    return ReturnSessionRepository(port, operation_timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_initialization_is_atomic_and_exact_replay_is_idempotent() -> None:
    port = _FakePersistencePort()
    repository = _repository(port)

    created = await repository.initialize(_session(), _audit(), _outbox())
    replayed = await repository.initialize(_session(), _audit(), _outbox())

    assert created.status is ReturnSessionPersistenceStatus.CREATED
    assert replayed.status is ReturnSessionPersistenceStatus.ALREADY_PRESENT
    assert created.state_digest == replayed.state_digest
    assert port.insert_calls == 2
    assert len(port.sessions) == 1


@pytest.mark.asyncio
async def test_initialization_rejects_conflicting_existing_session() -> None:
    port = _FakePersistencePort()
    repository = _repository(port)
    await repository.initialize(_session(), _audit(), _outbox())
    stored = port.sessions[f"RETURN_SESSION:{_SESSION_ID}"]
    stored["state_digest"] = "0" * 64

    with pytest.raises(ReturnSessionPersistenceError) as exc_info:
        await repository.initialize(_session(), _audit(), _outbox())

    assert exc_info.value.code is ReturnSessionPersistenceErrorCode.DOCUMENT_INVALID


def test_session_document_rejects_tampered_state() -> None:
    stored = ReturnSessionDocument.create(_session()).to_mongo_document()
    session = stored["session"]
    assert isinstance(session, dict)
    session["status"] = "TAMPERED"

    with pytest.raises(ReturnSessionPersistenceError) as exc_info:
        ReturnSessionDocument.from_mongo_document(stored)

    assert exc_info.value.code is ReturnSessionPersistenceErrorCode.DOCUMENT_INVALID


@pytest.mark.asyncio
async def test_transition_advances_revision_and_is_idempotent() -> None:
    port = _FakePersistencePort()
    repository = _repository(port)
    await repository.initialize(_session(), _audit(), _outbox())

    changed = await repository.transition(_SESSION_ID, _transition())
    replayed = await repository.transition(_SESSION_ID, _transition())

    assert changed.status is ReturnSessionPersistenceStatus.TRANSITIONED
    assert changed.revision == 1
    assert changed.current_stage is WorkflowStage.ORDER_DISCOVERY
    assert replayed.status is ReturnSessionPersistenceStatus.ALREADY_APPLIED
    assert port.transition_calls == 1
    stored = await repository.get(_SESSION_ID)
    assert stored is not None
    assert stored.session.intake_context == _transition().context_snapshot


@pytest.mark.asyncio
async def test_order_discovery_transition_persists_discovery_context() -> None:
    port = _FakePersistencePort()
    repository = _repository(port)
    await repository.initialize(_session(), _audit(), _outbox())
    await repository.transition(_SESSION_ID, _transition())
    occurred_at = _CREATED_AT + timedelta(minutes=2)
    command_id = UUID(int=2)
    discovery_context = ContextSnapshot.from_mapping(
        schema_version="order-discovery-v1",
        payload={
            "order_references": ["ORDER-1"],
            "request_reference": "REQUEST-1",
        },
    )
    transition = ReturnSessionTransition(
        command_id=command_id,
        expected_revision=1,
        completed_stage=WorkflowStage.ORDER_DISCOVERY,
        resulting_stage=WorkflowStage.ELIGIBILITY_EVALUATION,
        context_snapshot=discovery_context,
        updated_at=occurred_at,
        audit_event=AuditEvent(
            audit_event_id=UUID(int=40),
            session_id=_SESSION_ID,
            correlation_id=_CORRELATION_ID,
            actor_type="WORKFLOW",
            actor_id="return-platform-return-v1",
            operation="TRANSITION_RETURN_SESSION_STAGE",
            entity_type="ReturnSession",
            entity_key=f"RETURN_SESSION:{_SESSION_ID}",
            before_summary="Stage ORDER_DISCOVERY revision 1",
            after_summary="Stage ELIGIBILITY_EVALUATION revision 2",
            outcome="SUCCESS",
            occurred_at=occurred_at,
        ),
        outbox_event=ReturnSessionOutboxEvent(
            event_id=UUID(int=41),
            session_id=_SESSION_ID,
            command_id=command_id,
            event_type="RETURN_SESSION_STAGE_TRANSITIONED",
            revision=2,
            current_stage=WorkflowStage.ELIGIBILITY_EVALUATION,
            status="RUNNING",
            occurred_at=occurred_at,
        ),
    )

    receipt = await repository.transition(_SESSION_ID, transition)
    stored = await repository.get(_SESSION_ID)

    assert receipt.revision == 2
    assert stored is not None
    assert stored.session.intake_context is not None
    assert stored.session.discovery_context == discovery_context


@pytest.mark.asyncio
async def test_transition_rejects_stale_stage_without_a_write() -> None:
    port = _FakePersistencePort()
    repository = _repository(port)
    await repository.initialize(_session(), _audit(), _outbox())
    stale = _transition().model_copy(update={"expected_revision": 1})

    with pytest.raises(ReturnSessionPersistenceError) as exc_info:
        await repository.transition(_SESSION_ID, stale)

    assert exc_info.value.code is ReturnSessionPersistenceErrorCode.STALE_STAGE
    assert port.transition_calls == 0


@pytest.mark.asyncio
async def test_unknown_write_outcome_is_not_retried() -> None:
    port = _FakePersistencePort()
    port.write_error = AutoReconnect("connection lost")

    with pytest.raises(ReturnSessionPersistenceError) as exc_info:
        await _repository(port).initialize(_session(), _audit(), _outbox())

    assert exc_info.value.code is ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
    assert port.insert_calls == 1


@pytest.mark.asyncio
async def test_unknown_commit_result_label_is_preserved() -> None:
    port = _FakePersistencePort()
    port.write_error = OperationFailure(
        "commit result unavailable",
        code=91,
        details={"errorLabels": ["UnknownTransactionCommitResult"]},
    )

    with pytest.raises(ReturnSessionPersistenceError) as exc_info:
        await _repository(port).initialize(_session(), _audit(), _outbox())

    assert exc_info.value.code is ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN


class _ActivityRepository:
    def __init__(self) -> None:
        self.initialized: ReturnSession | None = None
        self.transitioned: ReturnSessionTransition | None = None

    async def initialize(
        self,
        session: ReturnSession,
        audit_event: AuditEvent,
        outbox_event: ReturnSessionOutboxEvent,
    ) -> ReturnSessionPersistenceReceipt:
        del audit_event, outbox_event
        self.initialized = session
        document = ReturnSessionDocument.create(session)
        return ReturnSessionPersistenceReceipt(
            status=ReturnSessionPersistenceStatus.CREATED,
            session_id=session.session_id,
            revision=0,
            current_stage=session.current_stage,
            state_digest=document.state_digest,
        )

    async def transition(
        self,
        session_id: UUID,
        transition: ReturnSessionTransition,
    ) -> ReturnSessionPersistenceReceipt:
        self.transitioned = transition
        return ReturnSessionPersistenceReceipt(
            status=ReturnSessionPersistenceStatus.TRANSITIONED,
            session_id=session_id,
            revision=transition.expected_revision + 1,
            current_stage=transition.resulting_stage,
            state_digest="0" * 64,
        )


def _workflow_input() -> ReturnWorkflowInput:
    return ReturnWorkflowInput(
        session_id=str(_SESSION_ID),
        correlation_id=str(_CORRELATION_ID),
        workflow_version="1.0",
        configuration_versions=(ReturnWorkflowConfigurationVersion("workflow", "return-v1"),),
    )


def _intake_result() -> IntakeActivityResult:
    return IntakeActivityResult(
        schema_version="intake-v1",
        request_reference="REQUEST-1",
        channel=IntakeChannel.ASSOCIATE,
        customer_reference="CUSTOMER-1",
        order_reference="ORDER-1",
        evidence_references=("FIXTURE:INTAKE-1",),
        observed_at=_CREATED_AT,
    )


@pytest.mark.asyncio
async def test_activities_construct_canonical_session_and_transition_evidence() -> None:
    repository = _ActivityRepository()
    activities = ReturnSessionActivities(repository)
    initial = start_return_workflow_execution(_workflow_input())
    initialized = await activities.initialize_return_session(
        ReturnSessionInitializeActivityInput(
            execution_state=initial,
            workflow_id="return-workflow-1",
            workflow_run_id="return-run-1",
            occurred_at=_CREATED_AT,
            audit_event_id=str(UUID(int=20)),
            outbox_event_id=str(UUID(int=21)),
        )
    )
    command = ReturnWorkflowAdvanceCommand(
        command_id=str(_COMMAND_ID),
        completed_stage=WorkflowStage.INTAKE,
        context_binding=bind_stage_activity_result(WorkflowStage.INTAKE, _intake_result()),
    )
    next_state = advance_return_workflow(initial, command)
    transitioned = await activities.transition_return_session(
        ReturnSessionTransitionActivityInput(
            previous_state=initial,
            next_state=next_state,
            command=command,
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            audit_event_id=str(UUID(int=22)),
            outbox_event_id=str(UUID(int=23)),
            decision_id=str(UUID(int=24)),
        )
    )

    assert initialized.revision == 0
    assert repository.initialized is not None
    assert repository.initialized.workflow_id == "return-workflow-1"
    assert transitioned.revision == 1
    assert repository.transitioned is not None
    assert repository.transitioned.completed_stage is WorkflowStage.INTAKE
    assert repository.transitioned.context_snapshot is not None
    assert repository.transitioned.context_snapshot.schema_version == "intake-v1"


@pytest.mark.asyncio
async def test_activity_rejects_inconsistent_transition_input() -> None:
    initial = start_return_workflow_execution(_workflow_input())
    command = ReturnWorkflowAdvanceCommand(
        command_id=str(_COMMAND_ID),
        completed_stage=WorkflowStage.INTAKE,
        context_binding=bind_stage_activity_result(WorkflowStage.INTAKE, _intake_result()),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await ReturnSessionActivities(_ActivityRepository()).transition_return_session(
            ReturnSessionTransitionActivityInput(
                previous_state=initial,
                next_state=initial,
                command=command,
                occurred_at=_CREATED_AT,
                audit_event_id=str(UUID(int=30)),
                outbox_event_id=str(UUID(int=31)),
                decision_id=str(UUID(int=32)),
            )
        )

    assert exc_info.value.type == "RETURN_SESSION_ACTIVITY_TRANSITION_INVALID"


def test_transition_model_rejects_non_successor_stage() -> None:
    payload = _transition().model_dump(mode="python")
    payload["resulting_stage"] = WorkflowStage.RETURN_REQUEST

    with pytest.raises(ValidationError):
        ReturnSessionTransition.model_validate(payload)


def test_production_adapter_uses_explicit_transaction_without_hidden_retries() -> None:
    source = __import__("inspect").getsource(
        __import__(
            "return_platform.workflows.persistence",
            fromlist=["_PyMongoReturnSessionPersistence"],
        )._PyMongoReturnSessionPersistence
    )

    assert "start_transaction" in source
    assert "with_transaction" not in source
