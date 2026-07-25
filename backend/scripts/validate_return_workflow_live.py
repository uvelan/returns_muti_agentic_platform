"""Validate one live Temporal Return workflow with MongoDB read-back."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pymongo import AsyncMongoClient
from temporalio.client import Client, WorkflowHandle

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.bay_assignment import build_bay_assignment_result
from return_platform.workflows.feedback_learning import build_feedback_learning_result
from return_platform.workflows.fulfillment_tracking import (
    build_fulfillment_tracking_result,
)
from return_platform.workflows.persistence import ReturnSessionRepository
from return_platform.workflows.return_request import build_return_request_result
from return_platform.workflows.return_workflow import (
    ReturnWorkflow,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowExecutionState,
    ReturnWorkflowInput,
    ReturnWorkflowStatus,
)
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    bind_stage_activity_result,
)
from return_platform.workflows.worker import (
    create_return_workflow_worker,
)

_DATABASE = "return_workflow_live_validation"
_SESSIONS = "return_sessions"
_AUDITS = "return_session_audit_events"
_OUTBOX = "return_session_outbox_events"
_DECISIONS = "return_session_agent_decisions"
_SESSION_ID = UUID("dfe8dbce-8f2a-4f3b-b7de-9e94d698ca11")
_CORRELATION_ID = UUID("56804dd3-a7fe-41cb-89a9-28161d2cb107")
_OBSERVED_AT = datetime(2026, 7, 22, 10, 30, tzinfo=UTC)
_STAGES = (
    WorkflowStage.INTAKE,
    WorkflowStage.ORDER_DISCOVERY,
    WorkflowStage.ELIGIBILITY_EVALUATION,
    WorkflowStage.RETURN_REQUEST,
    WorkflowStage.FULFILLMENT_TRACKING,
    WorkflowStage.BAY_ASSIGNMENT,
    WorkflowStage.FEEDBACK_LEARNING,
)


async def _validate() -> None:
    mongo_dsn = os.environ.get("PLATFORM_MONGO_DSN")
    temporal_target = os.environ.get("PLATFORM_TEMPORAL_TARGET")
    if mongo_dsn is None or temporal_target is None:
        raise RuntimeError("Platform MongoDB and Temporal configuration is required")
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(mongo_dsn)
    temporal = await Client.connect(temporal_target)
    repository = ReturnSessionRepository.from_client(
        mongo,
        database=_DATABASE,
        sessions_collection=_SESSIONS,
        audits_collection=_AUDITS,
        outbox_collection=_OUTBOX,
        decisions_collection=_DECISIONS,
        operation_timeout_seconds=5.0,
    )
    worker = create_return_workflow_worker(temporal, repository, task_queue="live-validation-tq")
    worker_task = asyncio.create_task(worker.run())
    handle: WorkflowHandle[ReturnWorkflow, ReturnWorkflowExecutionState] | None = None
    await mongo.drop_database(_DATABASE)
    database = mongo[_DATABASE]
    await database.create_collection("return_sessions")
    await database.create_collection("audit_events")
    await database.create_collection("outbox_events")
    await database.create_collection("agent_decisions")
    try:
        async with asyncio.timeout(45.0):
            handle = await temporal.start_workflow(
                ReturnWorkflow.run,
                ReturnWorkflowInput(
                    session_id=str(_SESSION_ID),
                    correlation_id=str(_CORRELATION_ID),
                    workflow_version="1.0",
                    configuration_versions=(
                        ReturnWorkflowConfigurationVersion("workflow", "return-v1"),
                    ),
                ),
                id=f"return-live-validation-{uuid4()}",
                task_queue="live-validation-tq",
            )
            first_command = ReturnWorkflowAdvanceCommand(
                command_id=str(UUID(int=1)),
                completed_stage=WorkflowStage.INTAKE,
                context_binding=bind_stage_activity_result(
                    WorkflowStage.INTAKE,
                    IntakeActivityResult(
                        schema_version="intake-v1",
                        request_reference="LIVE-VALIDATION-REQUEST",
                        channel=IntakeChannel.SYSTEM,
                        customer_reference="CUSTOMER-FIXTURE-1",
                        order_reference="ORDER-FIXTURE-1",
                        evidence_references=("FIXTURE:INTAKE-1",),
                        observed_at=_OBSERVED_AT,
                    ),
                ),
            )
            first = await handle.execute_update(
                "complete_stage",
                first_command,
                id="complete-intake",
                result_type=ReturnWorkflowExecutionState,
            )
            queried = await handle.query(
                "execution_state",
                result_type=ReturnWorkflowExecutionState,
            )
            replayed = await handle.execute_update(
                "complete_stage",
                first_command,
                id="replay-intake-command",
                result_type=ReturnWorkflowExecutionState,
            )
            if first != queried or replayed != first:
                raise RuntimeError("Temporal update/query/replay state mismatch")
            for index, stage in enumerate(_STAGES[1:], start=2):
                eligibility_fixture = EligibilityActivityResult(
                    schema_version="eligibility-v1",
                    decision=EligibilityDecision.REVIEW_REQUIRED,
                    explanation="Controlled live validation requires manual review.",
                    confidence_millionths=750_000,
                    evidence_references=("FIXTURE:ELIGIBILITY-1",),
                    model_provider="CONTROLLED_FIXTURE",
                    model_name="eligibility-contract-v1",
                    configuration_version="return-v1",
                    observed_at=_OBSERVED_AT,
                )
                eligibility_binding = bind_stage_activity_result(
                    WorkflowStage.ELIGIBILITY_EVALUATION, eligibility_fixture
                )
                return_request_fixture = build_return_request_result(
                    eligibility=ContextSnapshot(
                        schema_version=eligibility_binding.schema_version,
                        payload_json=eligibility_binding.payload_json,
                        payload_digest=eligibility_binding.payload_digest,
                    ),
                    request_reference="LIVE-VALIDATION-REQUEST",
                    return_reference=None,
                    configuration_version="return-v1",
                    observed_at=_OBSERVED_AT,
                )
                return_request_binding = bind_stage_activity_result(
                    WorkflowStage.RETURN_REQUEST, return_request_fixture
                )
                fulfillment_fixture = build_fulfillment_tracking_result(
                    return_request=ContextSnapshot(
                        schema_version=return_request_binding.schema_version,
                        payload_json=return_request_binding.payload_json,
                        payload_digest=return_request_binding.payload_digest,
                    ),
                    fulfillment_reference=None,
                    tracking_reference=None,
                    configuration_version="return-v1",
                    observed_at=_OBSERVED_AT,
                )
                fulfillment_binding = bind_stage_activity_result(
                    WorkflowStage.FULFILLMENT_TRACKING, fulfillment_fixture
                )
                bay_fixture = build_bay_assignment_result(
                    fulfillment=ContextSnapshot(
                        schema_version=fulfillment_binding.schema_version,
                        payload_json=fulfillment_binding.payload_json,
                        payload_digest=fulfillment_binding.payload_digest,
                    ),
                    warehouse_reference=None,
                    bay_reference=None,
                    configuration_version="return-v1",
                    observed_at=_OBSERVED_AT,
                )
                bay_binding = bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, bay_fixture)
                stage_result = (
                    OrderDiscoveryActivityResult(
                        schema_version="order-discovery-v1",
                        request_reference="LIVE-VALIDATION-REQUEST",
                        customer_reference="CUSTOMER-FIXTURE-1",
                        order_references=("ORDER-FIXTURE-1",),
                        source_asset_id="source.fixture.orders",
                        source_document_references=("DOCUMENT:ORDER-FIXTURE-1",),
                        evidence_references=("FIXTURE:DISCOVERY-1",),
                        observed_at=_OBSERVED_AT,
                    )
                    if stage is WorkflowStage.ORDER_DISCOVERY
                    else eligibility_fixture
                    if stage is WorkflowStage.ELIGIBILITY_EVALUATION
                    else return_request_fixture
                    if stage is WorkflowStage.RETURN_REQUEST
                    else fulfillment_fixture
                    if stage is WorkflowStage.FULFILLMENT_TRACKING
                    else bay_fixture
                    if stage is WorkflowStage.BAY_ASSIGNMENT
                    else build_feedback_learning_result(
                        bay_assignment=ContextSnapshot(
                            schema_version=bay_binding.schema_version,
                            payload_json=bay_binding.payload_json,
                            payload_digest=bay_binding.payload_digest,
                        ),
                        feedback_reference=None,
                        learning_signal_reference=None,
                        configuration_version="return-v1",
                        observed_at=_OBSERVED_AT,
                    )
                    if stage is WorkflowStage.FEEDBACK_LEARNING
                    else None
                )
                await handle.execute_update(
                    "complete_stage",
                    ReturnWorkflowAdvanceCommand(
                        command_id=str(UUID(int=index)),
                        completed_stage=stage,
                        context_binding=bind_stage_activity_result(stage, stage_result),
                    ),
                    id=f"complete-{stage.value.lower()}",
                    result_type=ReturnWorkflowExecutionState,
                )
            result = await handle.result()
            stored = await repository.get(_SESSION_ID)
            counts = (
                await mongo[_DATABASE][_SESSIONS].count_documents({}),
                await mongo[_DATABASE][_AUDITS].count_documents({}),
                await mongo[_DATABASE][_OUTBOX].count_documents({}),
                await mongo[_DATABASE][_DECISIONS].count_documents({}),
            )
            if (
                result.status is not ReturnWorkflowStatus.COMPLETED
                or result.current_stage is not WorkflowStage.COMPLETED
                or stored is None
                or stored.revision != len(_STAGES)
                or stored.session.current_stage is not WorkflowStage.COMPLETED
                or stored.session.intake_context is None
                or stored.session.discovery_context is None
                or stored.session.eligibility_context is None
                or stored.session.return_request_context is None
                or stored.session.fulfillment_tracking_context is None
                or stored.session.bay_staging_context is None
                or stored.session.learning_feedback_context is None
                or counts != (1, 8, 8, 1)
            ):
                raise RuntimeError("Temporal or MongoDB live validation failed")
            print("Live Temporal Return workflow: PASS")
            print("Ordered updates: 7/7; query: PASS; command replay: PASS")
            print(
                "Context snapshots: intake=PASS discovery=PASS eligibility=PASS "
                "return_request=PASS fulfillment_tracking=PASS bay_assignment=PASS"
                " feedback_learning=PASS"
            )
            print("MongoDB read-back: sessions=1 audit_events=8 outbox_events=8 agent_decisions=1")
    except BaseException:
        if handle is not None:
            await handle.terminate(reason="live validation cleanup after failure")
        raise
    finally:
        await worker.shutdown()
        await worker_task
        await mongo.drop_database(_DATABASE)
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_validate())
