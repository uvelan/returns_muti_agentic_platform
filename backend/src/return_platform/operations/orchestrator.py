"""Durable product orchestrator that drives the canonical Temporal workflow."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError

from return_platform.ai_gateway.service import AIGatewayService
from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.configuration.settings import Settings
from return_platform.operations.models import (
    AIDecision,
    AIRequestStatus,
    ReturnSessionView,
    ReturnStatus,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.sql_business_state import SQLBusinessStateRepository
from return_platform.workflows.bay_assignment import build_bay_assignment_result
from return_platform.workflows.feedback_learning import build_feedback_learning_result
from return_platform.workflows.fulfillment_tracking import build_fulfillment_tracking_result
from return_platform.workflows.return_request import build_return_request_result
from return_platform.workflows.return_workflow import (
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


def _command_id(session_id: str, stage: WorkflowStage) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"return-platform:{session_id}:{stage.value}"))


def _binding_snapshot(binding: StageContextBinding) -> ContextSnapshot:
    return ContextSnapshot(
        schema_version=binding.schema_version,
        payload_json=binding.payload_json,
        payload_digest=binding.payload_digest,
    )


def _observed_at(session: ReturnSessionView) -> datetime:
    return session.createdAt.astimezone(UTC)


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
    ) -> None:
        self._repository = repository
        self._temporal = temporal
        self._settings = settings
        self._worker_id = worker_id
        self._ai = AIGatewayService(repository, settings)
        self._business_state = SQLBusinessStateRepository(settings)

    async def run_forever(self) -> None:
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
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._fail(session, error)
                await self._repository.release_return(session.id, "FAILED")

    async def _fail(self, session: ReturnSessionView, error: Exception) -> None:
        code = type(error).__name__.upper()[:100]
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

    async def _handle(
        self, session: ReturnSessionView
    ) -> WorkflowHandle[Any, Any]:
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
        from typing import cast
        state = cast(ReturnWorkflowExecutionState, await handle.execute_update(ReturnWorkflow.complete_stage, command))
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
        from typing import cast
        state = cast(ReturnWorkflowExecutionState, await handle.query(ReturnWorkflow.execution_state))
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
                    days_since_delivery = max(0, (observed_at - delivered_at.astimezone(UTC)).days)
                evaluation = await self._ai.evaluate(
                    session_id=session.id,
                    redacted_input={
                        "requestReference": f"REQ-{session.id}",
                        "customerReference": session.customerReference,
                        "orderReferences": [session.orderReference],
                        "itemReferences": session.itemReferences,
                        "reasonCode": session.reasonCode,
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
        return_reference = (
            f"RMA-{session.id[:8].upper()}" if trace.decision is AIDecision.APPROVE else None
        )
        return_result = build_return_request_result(
            eligibility=_binding_snapshot(eligibility),
            request_reference=f"REQ-{session.id}",
            return_reference=return_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        return_binding = bind_stage_activity_result(WorkflowStage.RETURN_REQUEST, return_result)
        if state.current_stage is WorkflowStage.RETURN_REQUEST:
            await self._business_state.record_return_decision(
                session,
                decision=trace.decision.value,
                return_reference=return_reference,
                status=(
                    ReturnStatus.APPROVED.value
                    if trace.decision is AIDecision.APPROVE
                    else ReturnStatus.REJECTED.value
                ),
            )
            state = await self._complete(
                handle,
                session,
                WorkflowStage.RETURN_REQUEST,
                return_binding,
                event_type="RETURN_REQUEST_PROCESSED",
                event_payload={
                    "outcome": return_result.outcome.value,
                    "returnReference": return_reference,
                },
                updates={"returnReference": return_reference},
            )

        fulfillment_reference = f"FUL-{session.id[:8].upper()}" if return_reference else None
        tracking_reference = f"TRK-{session.id[:8].upper()}" if return_reference else None
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
            await self._business_state.record_fulfillment(
                session.id,
                fulfillment_reference=fulfillment_reference,
                tracking_reference=tracking_reference,
                warehouse_reference=None,
                bay_reference=None,
                status=fulfillment_result.status.value,
            )
            state = await self._complete(
                handle,
                session,
                WorkflowStage.FULFILLMENT_TRACKING,
                fulfillment_binding,
                event_type="FULFILLMENT_TRACKING_CREATED",
                event_payload={
                    "status": fulfillment_result.status.value,
                    "trackingReference": tracking_reference,
                },
                updates={"trackingReference": tracking_reference},
            )

        warehouse_reference = "WH-CHENNAI-01" if return_reference else None
        bay_reference = "BAY-A1" if return_reference else None
        bay_result = build_bay_assignment_result(
            fulfillment=_binding_snapshot(fulfillment_binding),
            warehouse_reference=warehouse_reference,
            bay_reference=bay_reference,
            configuration_version=_CONFIGURATION_VERSION,
            observed_at=observed_at,
        )
        bay_binding = bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, bay_result)
        if state.current_stage is WorkflowStage.BAY_ASSIGNMENT:
            await self._business_state.record_fulfillment(
                session.id,
                fulfillment_reference=fulfillment_reference,
                tracking_reference=tracking_reference,
                warehouse_reference=warehouse_reference,
                bay_reference=bay_reference,
                status=bay_result.status.value,
            )
            state = await self._complete(
                handle,
                session,
                WorkflowStage.BAY_ASSIGNMENT,
                bay_binding,
                event_type="BAY_ASSIGNMENT_UPDATED",
                event_payload={"status": bay_result.status.value, "bayReference": bay_reference},
                updates={"bayReference": bay_reference},
            )

        feedback_reference = f"FDB-{session.id[:8].upper()}" if return_reference else None
        learning_reference = f"LRN-{session.id[:8].upper()}" if return_reference else None
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
                event_payload={"status": feedback_result.status.value},
                updates={},
            )

        if state.current_stage is WorkflowStage.COMPLETED:
            terminal_status = (
                ReturnStatus.COMPLETED
                if trace.decision is AIDecision.APPROVE
                else ReturnStatus.REJECTED
            )
            await self._business_state.mark_return_status(session.id, terminal_status.value)
            await self._repository.update_return(
                session.id,
                {
                    "currentStage": WorkflowStage.COMPLETED.value,
                    "status": terminal_status.value,
                    "progressPercentage": 100,
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
                },
                deduplication_key="workflow-terminal",
            )
        return "COMPLETED"
