"""Durable product orchestrator that drives the canonical Temporal workflow."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Final, cast

import httpx
from temporalio.client import Client, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from return_platform.ai_gateway.configuration import LoadedAIGatewayConfiguration
from return_platform.ai_gateway.routing import AIRoutePool
from return_platform.ai_gateway.service import AIGatewayService
from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.configuration.domain.workflow import WorkflowDefinition
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.operations.feedback_service import FeedbackLearningService
from return_platform.operations.models import (
    AIDecision,
    AIRequestStatus,
    ReturnSessionView,
    ReturnStatus,
    normalize_utc_datetime,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.providers.factory import (
    build_return_support_provider,
)
from return_platform.operations.return_support.service import (
    CreateSupportWorkItemRequest,
    ReturnSupportService,
)
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.workflows.bay_assignment import build_bay_assignment_result
from return_platform.workflows.feedback_learning import build_feedback_learning_result
from return_platform.workflows.fulfillment_tracking import build_fulfillment_tracking_result
from return_platform.workflows.return_request import build_return_request_result
from return_platform.workflows.return_workflow import (
    DEFAULT_STAGE_SEQUENCE,
    ReturnWorkflow,
    ReturnWorkflowAdvanceCommand,
    ReturnWorkflowConfigurationVersion,
    ReturnWorkflowExecutionState,
    ReturnWorkflowInput,
)
from return_platform.workflows.stage_results import (
    EligibilityActivityResult,
    EligibilityDecision,
    IntakeActivityResult,
    IntakeChannel,
    OrderDiscoveryActivityResult,
    StageContextBinding,
    bind_stage_activity_result,
)

_CONFIGURATION_VERSION: Final = "e2e-v1"
_STAGE_PROGRESS: Final = {
    WorkflowStage.INTAKE: 5,
    WorkflowStage.ORDER_DISCOVERY: 20,
    WorkflowStage.ELIGIBILITY_EVALUATION: 40,
    WorkflowStage.RETURN_REQUEST: 60,
    WorkflowStage.FULFILLMENT_TRACKING: 75,
    WorkflowStage.BAY_ASSIGNMENT: 88,
    WorkflowStage.FEEDBACK_LEARNING: 96,
    WorkflowStage.COMPLETED: 100,
}


def _stage_sequence_from_definition(
    definition: WorkflowDefinition,
) -> tuple[WorkflowStage, ...]:
    """Resolve a pinned WorkflowDefinition's stage_ids() into WorkflowStage order.

    Callers that hold a pinned RuntimeConfigurationView (the manifest/release
    system `config/workflows/return_session.yaml` belongs to) can pass its
    resolved `workflow.workflow["return_session"]` here. Nothing in this
    codebase resolves that bridge today -- ReturnOrchestrator's other
    dependencies (self._return_configuration, AgentRegistry construction) still
    come from the legacy single-file ReturnPlatformConfiguration -- so this
    stays an explicit opt-in rather than a default.
    """
    try:
        return tuple(WorkflowStage(stage_id) for stage_id in definition.stage_ids())
    except ValueError as exc:
        raise ValueError(
            f"workflow definition contains a stage ID that is not a WorkflowStage: {exc}"
        ) from exc


def _command_id(session_id: str, stage: WorkflowStage) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"return-platform:{session_id}:{stage.value}"))


def _binding_snapshot(binding: StageContextBinding) -> ContextSnapshot:
    return ContextSnapshot(
        schema_version=binding.schema_version,
        payload_json=binding.payload_json,
        payload_digest=binding.payload_digest,
    )


def _observed_at(session: ReturnSessionView) -> datetime:
    return normalize_utc_datetime(session.createdAt)


def _eligibility_decision(value: AIDecision) -> EligibilityDecision:
    return EligibilityDecision(value.value)


class ReturnOrchestrator:
    """Resume-safe stage driver. Temporal owns ordering; MongoDB owns business projections."""

    def __init__(
        self,
        *,
        repository: OperationalRepository,
        temporal: Client,
        settings: Settings,
        worker_id: str,
        return_configuration: ReturnPlatformConfiguration | None = None,
        ai_gateway_configuration: LoadedAIGatewayConfiguration | None = None,
        ai_gateway_route_pool: AIRoutePool | None = None,
        workflow_definition: WorkflowDefinition | None = None,
    ) -> None:
        self._repository = repository
        self._temporal = temporal
        self._settings = settings
        self._worker_id = worker_id
        self._stage_sequence = (
            _stage_sequence_from_definition(workflow_definition)
            if workflow_definition is not None
            else DEFAULT_STAGE_SEQUENCE
        )
        from return_platform.ai_gateway.service import AIGatewayRepository

        self._ai = AIGatewayService(
            cast(AIGatewayRepository, repository),
            settings,
            loaded_configuration=ai_gateway_configuration,
            route_pool=ai_gateway_route_pool,
        )
        self._business_state = SQLBusinessStateRepository(settings)
        self._http_client = httpx.AsyncClient()
        if return_configuration is None:
            if settings.environment not in {"development", "test"}:
                raise RuntimeError(
                    "Production orchestrator behavior must come from the active graph release"
                )
            return_configuration = load_return_configuration(
                settings.return_configuration_path
            ).configuration
        self._return_configuration = return_configuration
        self._internal_support = ReturnSupportService(
            client=repository.platform_client,
            settings=settings,
            configuration=self._return_configuration,
            operational_repository=repository,
        )
        self._support = (
            build_return_support_provider(
                settings=settings,
                repository=self._business_state,
                http_client=self._http_client,
            )
            if settings.support_ticket_mode == "EXTERNAL_AUTHORITY"
            else None
        )
        self._feedback = FeedbackLearningService(
            repository.platform_client,
            settings,
        )

    async def run_forever(self) -> None:
        async with self._http_client:
            await self._repository.ensure_indexes()
            while True:
                await self._repository.heartbeat(
                    "return-orchestrator",
                    self._worker_id,
                    ttl_seconds=self._settings.worker_readiness_ttl_seconds,
                )
                session = await self._repository.claim_next_return(self._worker_id)
                if session is None:
                    await asyncio.sleep(self._settings.orchestration_poll_seconds)
                    continue
                try:
                    final_state = await self.process(session)
                    await self._repository.release_return(session.id, final_state)
                    if final_state in {"COMPLETED", "CANCELLED", "FAILED"}:
                        await self._repository.release_discovery_lock(
                            session.id, reason=final_state
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self._fail(session, error)
                    await self._repository.release_return(session.id, "FAILED")
                    await self._repository.release_discovery_lock(session.id, reason="FAILED")

    @staticmethod
    def _failure_code(error: Exception) -> str:
        """Name the actual verdict, not the transport that carried it.

        A rejected stage command arrives as `WorkflowUpdateFailedError` wrapping
        an `ApplicationError` whose `type` is the stable
        `ReturnWorkflowErrorCode`. Using the outer class name would stamp every
        distinct rejection -- out-of-order, command conflict, already-completed
        -- with the same opaque `WORKFLOWUPDATEFAILEDERROR`, and that code is
        what lands on the return record and on the HIGH-priority support case an
        operator has to triage.
        """
        if isinstance(error, WorkflowUpdateFailedError):
            cause = error.cause
            if isinstance(cause, ApplicationError) and cause.type:
                return cause.type.upper()[:100]
        return type(error).__name__.upper()[:100]

    async def _fail(self, session: ReturnSessionView, error: Exception) -> None:
        code = self._failure_code(error)
        await self._repository.update_return(
            session.id,
            {
                "status": ReturnStatus.FAILED.value,
                "failureCode": code,
                "failureMessage": "The return flow failed and requires operator review.",
            },
        )
        support_case = await self._repository.create_support_case(
            session_id=session.id,
            case_type="FLOW_FAILURE",
            priority="HIGH",
            reason="The return flow failed and requires operator review or retry.",
        )
        await self._repository.append_event(
            session.id,
            event_type="RETURN_FLOW_FAILED",
            actor_type="SYSTEM",
            actor_id=self._worker_id,
            payload={"failureCode": code, "caseId": support_case.id},
            deduplication_key=f"flow-failed:{session.id}:{code}",
        )

    async def _handle(self, session: ReturnSessionView) -> WorkflowHandle[Any, Any]:
        workflow_id = session.workflowId or f"return-{session.id}"
        handle = self._temporal.get_workflow_handle(workflow_id)
        if session.workflowId is None:
            workflow_input = ReturnWorkflowInput(
                session_id=session.id,
                correlation_id=session.correlationId,
                workflow_version="1.0",
                configuration_versions=(
                    ReturnWorkflowConfigurationVersion(
                        component="return-orchestrator",
                        version=_CONFIGURATION_VERSION,
                    ),
                ),
                stage_sequence=self._stage_sequence,
            )
            try:
                handle = await self._temporal.start_workflow(
                    ReturnWorkflow.run,
                    workflow_input,
                    id=workflow_id,
                    task_queue=self._settings.return_workflow_task_queue,
                )
            except WorkflowAlreadyStartedError:
                handle = self._temporal.get_workflow_handle(workflow_id)
            await self._repository.update_return(
                session.id,
                {
                    "workflowId": workflow_id,
                    "status": ReturnStatus.RUNNING.value,
                    "orchestrationState": "RUNNING",
                },
            )
            await self._repository.append_event(
                session.id,
                event_type="TEMPORAL_WORKFLOW_STARTED",
                actor_type="SYSTEM",
                actor_id=self._worker_id,
                payload={"workflowId": workflow_id},
            )
        return handle

    async def _complete(
        self,
        handle: WorkflowHandle[Any, Any],
        session: ReturnSessionView,
        stage: WorkflowStage,
        binding: StageContextBinding,
        *,
        event_type: str,
        event_payload: dict[str, object],
        updates: dict[str, object],
    ) -> ReturnWorkflowExecutionState:
        command = ReturnWorkflowAdvanceCommand(
            command_id=_command_id(session.id, stage),
            completed_stage=stage,
            context_binding=binding,
        )
        state = cast(
            ReturnWorkflowExecutionState,
            await handle.execute_update(ReturnWorkflow.complete_stage, command),
        )
        await self._repository.update_return(
            session.id,
            {
                "currentStage": state.current_stage.value,
                "progressPercentage": _STAGE_PROGRESS.get(state.current_stage, 0),
                **updates,
            },
        )
        await self._repository.append_event(
            session.id,
            event_type=event_type,
            actor_type="SYSTEM",
            actor_id=self._worker_id,
            payload=event_payload,
            deduplication_key=f"workflow-stage:{stage.value}",
        )
        return state

    async def process(self, claimed: ReturnSessionView) -> str:
        session = await self._repository.get_return(claimed.id)
        if session is None:
            return "FAILED"
        if session.status is ReturnStatus.CANCELLED:
            return "CANCELLED"
        handle = await self._handle(session)
        state = cast(
            ReturnWorkflowExecutionState,
            await handle.query(ReturnWorkflow.execution_state),
        )
        observed_at = _observed_at(session)

        intake = bind_stage_activity_result(
            WorkflowStage.INTAKE,
            IntakeActivityResult(
                schema_version="intake-v1",
                request_reference=f"REQ-{session.id}",
                channel=IntakeChannel(session.channel),
                customer_reference=session.customerReference,
                order_reference=session.orderReference,
                evidence_references=(f"RETURN_REQUEST:{session.id}",),
                observed_at=observed_at,
            ),
        )
        if state.current_stage is WorkflowStage.INTAKE:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.INTAKE,
                intake,
                event_type="INTAKE_COMPLETED",
                event_payload={"channel": session.channel},
                updates={"status": ReturnStatus.RUNNING.value},
            )

        order = await self._repository.source_order(session.orderReference)
        if order is None:
            raise ValueError("ORDER_NOT_FOUND")
        if order.get("customerReference") != session.customerReference:
            raise ValueError("ORDER_CUSTOMER_MISMATCH")
        order_items = {
            str(item.get("itemReference"))
            for item in order.get("items", [])
            if isinstance(item, dict)
        }
        if not set(session.itemReferences).issubset(order_items):
            raise ValueError("ORDER_ITEM_MISMATCH")
        discovery = bind_stage_activity_result(
            WorkflowStage.ORDER_DISCOVERY,
            OrderDiscoveryActivityResult(
                schema_version="order-discovery-v1",
                request_reference=f"REQ-{session.id}",
                customer_reference=session.customerReference,
                order_references=(session.orderReference,),
                source_asset_id="SOURCE_MONGODB_ORDERS",
                source_document_references=(f"ORDER:{session.orderReference}",),
                evidence_references=(f"SOURCE_ORDER:{session.orderReference}",),
                observed_at=observed_at,
            ),
        )
        if state.current_stage is WorkflowStage.ORDER_DISCOVERY:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.ORDER_DISCOVERY,
                discovery,
                event_type="ORDER_DISCOVERED",
                event_payload={
                    "orderReference": session.orderReference,
                    "itemCount": len(session.itemReferences),
                },
                updates={},
            )

        session = await self._repository.get_return(session.id) or session
        if state.current_stage is WorkflowStage.ELIGIBILITY_EVALUATION:
            trace = (
                await self._repository.get_ai_trace(session.aiRequestId)
                if session.aiRequestId
                else None
            )
            if trace is None:
                delivered_at = order.get("deliveredAt")
                days_since_delivery = None
                if isinstance(delivered_at, datetime):
                    days_since_delivery = max(
                        0,
                        (observed_at - normalize_utc_datetime(delivered_at)).days,
                    )
                evaluation = await self._ai.evaluate(
                    session_id=session.id,
                    redacted_input={
                        "requestReference": f"REQ-{session.id}",
                        "customerReference": session.customerReference,
                        "orderReferences": [session.orderReference],
                        "itemReferences": session.itemReferences,
                        "reasonCode": session.reasonCode,
                        "returnQuantity": session.returnQuantity,
                        "packageCount": session.packageCount,
                        "shippingPathExpectation": session.shippingPathExpectation,
                        "orderStatus": order.get("status"),
                        "daysSinceDelivery": days_since_delivery,
                    },
                )
                trace = evaluation.trace
                session = await self._repository.update_return(
                    session.id,
                    {
                        "aiRequestId": trace.id,
                        "status": (
                            ReturnStatus.INTERCEPTION_PENDING.value
                            if evaluation.pending_interception
                            else ReturnStatus.RUNNING.value
                        ),
                    },
                )
                await self._repository.append_event(
                    session.id,
                    event_type=(
                        "AI_INTERCEPTION_REQUIRED"
                        if evaluation.pending_interception
                        else "AI_ELIGIBILITY_EVALUATED"
                    ),
                    actor_type="AI_GATEWAY",
                    actor_id=trace.provider or "gateway",
                    payload={"aiRequestId": trace.id, "status": trace.status.value},
                )
            if trace.status is AIRequestStatus.INTERCEPTION_PENDING:
                case = await self._repository.create_support_case(
                    session_id=session.id,
                    case_type="AI_INTERCEPTION",
                    priority="HIGH",
                    reason="AI dispatch is paused for operator interception.",
                )
                await self._repository.update_return(
                    session.id,
                    {
                        "status": ReturnStatus.INTERCEPTION_PENDING.value,
                        "supportCaseId": case.id,
                    },
                )
                return "WAITING_INTERCEPTION"
            if trace.decision is None:
                return "WAITING_INTERCEPTION"
            if (
                trace.decision is AIDecision.REVIEW_REQUIRED
                and trace.status is not AIRequestStatus.MANUAL_OVERRIDE
            ):
                case = await self._repository.create_support_case(
                    session_id=session.id,
                    case_type="ELIGIBILITY_REVIEW",
                    priority="NORMAL",
                    reason=trace.explanation or "Eligibility requires manual review.",
                )
                await self._repository.update_return(
                    session.id,
                    {
                        "status": ReturnStatus.REVIEW_REQUIRED.value,
                        "supportCaseId": case.id,
                    },
                )
                await self._repository.append_event(
                    session.id,
                    event_type="SUPPORT_REVIEW_REQUIRED",
                    actor_type="AI_GATEWAY",
                    actor_id=trace.provider or "gateway",
                    payload={"caseId": case.id, "aiRequestId": trace.id},
                )
                return "WAITING_SUPPORT"
            eligibility = bind_stage_activity_result(
                WorkflowStage.ELIGIBILITY_EVALUATION,
                EligibilityActivityResult(
                    schema_version="eligibility-v1",
                    decision=_eligibility_decision(trace.decision),
                    explanation=trace.explanation or "Eligibility decision recorded.",
                    confidence_millionths=trace.confidenceMillionths or 0,
                    evidence_references=(
                        f"AI_TRACE:{trace.id}",
                        f"AI_REQUEST_SHA256:{trace.requestDigest}",
                    ),
                    model_provider=trace.provider or "MANUAL",
                    model_name=trace.model or "manual-override-v1",
                    configuration_version=_CONFIGURATION_VERSION,
                    observed_at=observed_at,
                ),
            )
            state = await self._complete(
                handle,
                session,
                WorkflowStage.ELIGIBILITY_EVALUATION,
                eligibility,
                event_type="ELIGIBILITY_DECISION_PERSISTED",
                event_payload={"decision": trace.decision.value, "aiRequestId": trace.id},
                updates={
                    "eligibilityDecision": trace.decision.value,
                    "status": (
                        ReturnStatus.APPROVED.value
                        if trace.decision is AIDecision.APPROVE
                        else ReturnStatus.REJECTED.value
                    ),
                },
            )
        else:
            trace = (
                await self._repository.get_ai_trace(session.aiRequestId)
                if session.aiRequestId
                else None
            )
            if trace is None or trace.decision is None:
                raise ValueError("ELIGIBILITY_TRACE_MISSING")
            eligibility = bind_stage_activity_result(
                WorkflowStage.ELIGIBILITY_EVALUATION,
                EligibilityActivityResult(
                    schema_version="eligibility-v1",
                    decision=_eligibility_decision(trace.decision),
                    explanation=trace.explanation or "Eligibility decision recorded.",
                    confidence_millionths=trace.confidenceMillionths or 0,
                    evidence_references=(
                        f"AI_TRACE:{trace.id}",
                        f"AI_REQUEST_SHA256:{trace.requestDigest}",
                    ),
                    model_provider=trace.provider or "MANUAL",
                    model_name=trace.model or "manual-override-v1",
                    configuration_version=_CONFIGURATION_VERSION,
                    observed_at=observed_at,
                ),
            )

        assert trace is not None and trace.decision is not None
        if self._support is None:
            if session.supportWorkItemId is None:
                support_draft = (
                    f"Return request for order {session.orderReference}\n"
                    f"Customer reference: {session.customerReference}\n"
                    f"Items: {', '.join(session.itemReferences)}\n"
                    f"Reason: {session.reasonCode}\n"
                    f"Product presence: {session.productPresence or 'UNKNOWN'}\n"
                    f"Proposed return method: {session.shippingPathExpectation}"
                )
                work_item = await self._internal_support.create_work_item(
                    CreateSupportWorkItemRequest(
                        sessionId=session.id,
                        subject=f"Return request for order {session.orderReference}",
                        supportDraft=support_draft,
                        requestSnapshotDigest=trace.requestDigest,
                        idempotencyKey=f"support:{session.id}:{trace.requestDigest}",
                    ),
                    actor_id=self._worker_id,
                    correlation_id=session.correlationId,
                )
                await self._repository.append_event(
                    session.id,
                    event_type="RETURN_WORKFLOW_WAITING_FOR_SUPPORT",
                    actor_type="SYSTEM",
                    actor_id=self._worker_id,
                    payload={"workItemId": work_item.id},
                    deduplication_key=f"waiting-support:{work_item.id}",
                )
            return "WAITING_SUPPORT"
        support_result = await self._support.submit(
            session,
            decision=trace.decision.value,
            request_digest=trace.requestDigest,
        )
        return_reference = support_result.return_reference
        return_result = build_return_request_result(
            eligibility=_binding_snapshot(eligibility),
            request_reference=support_result.external_reference,
            return_reference=return_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        return_binding = bind_stage_activity_result(WorkflowStage.RETURN_REQUEST, return_result)
        if state.current_stage is WorkflowStage.RETURN_REQUEST:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.RETURN_REQUEST,
                return_binding,
                event_type="RETURN_SUPPORT_TICKET_UPDATED",
                event_payload={
                    "outcome": return_result.outcome.value,
                    "ticketReference": support_result.external_reference,
                    "ticketStatus": support_result.ticket_status,
                    "returnReference": return_reference,
                },
                updates={
                    "returnReference": return_reference,
                    "supportTicketReference": support_result.external_reference,
                },
            )

        fulfillment_reference = support_result.fulfillment_reference
        tracking_reference = support_result.tracking_reference
        fulfillment_result = build_fulfillment_tracking_result(
            return_request=_binding_snapshot(return_binding),
            fulfillment_reference=fulfillment_reference,
            tracking_reference=tracking_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        fulfillment_binding = bind_stage_activity_result(
            WorkflowStage.FULFILLMENT_TRACKING,
            fulfillment_result,
        )
        if state.current_stage is WorkflowStage.FULFILLMENT_TRACKING:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.FULFILLMENT_TRACKING,
                fulfillment_binding,
                event_type="AUTHORITATIVE_FULFILLMENT_TRACKING_READ",
                event_payload={
                    "status": fulfillment_result.status.value,
                    "trackingReference": tracking_reference,
                    "supportTicketReference": support_result.external_reference,
                },
                updates={"trackingReference": tracking_reference},
            )

        warehouse_reference: str | None = None
        bay_reference: str | None = None
        if return_reference is not None and support_result.shipping_path is not None:
            warehouse_reference, bay_reference = await self._business_state.assign_bay(
                session,
                return_reference=return_reference,
                shipping_path=support_result.shipping_path,
                package_count=session.packageCount,
            )
        bay_result = build_bay_assignment_result(
            fulfillment=_binding_snapshot(fulfillment_binding),
            warehouse_reference=warehouse_reference,
            bay_reference=bay_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        bay_binding = bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, bay_result)
        if state.current_stage is WorkflowStage.BAY_ASSIGNMENT:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.BAY_ASSIGNMENT,
                bay_binding,
                event_type="BAY_ASSIGNMENT_UPDATED",
                event_payload={
                    "status": bay_result.status.value,
                    "bayReference": bay_reference,
                    "warehouseReference": warehouse_reference,
                },
                updates={"bayReference": bay_reference},
            )

        projected_session = await self._repository.get_return(session.id) or session
        projected_events = await self._repository.list_events(session.id)
        intended_terminal_status = (
            ReturnStatus.COMPLETED
            if trace.decision is AIDecision.APPROVE
            else ReturnStatus.REJECTED
        )
        feedback_record = await self._feedback.record(
            projected_session,
            projected_events,
            support_ticket_reference=support_result.external_reference,
            final_outcome=intended_terminal_status.value,
        )
        feedback_reference = feedback_record.id if return_reference else None
        learning_reference = feedback_record.evidenceDigest if return_reference else None
        feedback_result = build_feedback_learning_result(
            bay_assignment=_binding_snapshot(bay_binding),
            feedback_reference=feedback_reference,
            learning_signal_reference=learning_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        feedback_binding = bind_stage_activity_result(
            WorkflowStage.FEEDBACK_LEARNING, feedback_result
        )
        if state.current_stage is WorkflowStage.FEEDBACK_LEARNING:
            state = await self._complete(
                handle,
                session,
                WorkflowStage.FEEDBACK_LEARNING,
                feedback_binding,
                event_type="FEEDBACK_LEARNING_RECORDED",
                event_payload={
                    "status": feedback_result.status.value,
                    "feedbackReference": feedback_record.id,
                    "reviewStatus": feedback_record.reviewStatus,
                },
                updates={"feedbackReference": feedback_record.id},
            )

        if state.current_stage is WorkflowStage.COMPLETED:
            terminal_status = intended_terminal_status
            await self._business_state.mark_return_status(session.id, terminal_status.value)
            await self._repository.update_return(
                session.id,
                {
                    "currentStage": WorkflowStage.COMPLETED.value,
                    "status": terminal_status.value,
                    "progressPercentage": 100,
                    "supportTicketReference": support_result.external_reference,
                    "feedbackReference": feedback_record.id,
                },
            )
            await self._repository.append_event(
                session.id,
                event_type="RETURN_FLOW_COMPLETED",
                actor_type="SYSTEM",
                actor_id=self._worker_id,
                payload={
                    "workflowStatus": "COMPLETED",
                    "returnStatus": terminal_status.value,
                    "supportTicketReference": support_result.external_reference,
                    "feedbackReference": feedback_record.id,
                },
                deduplication_key="workflow-terminal",
            )
        return "COMPLETED"
