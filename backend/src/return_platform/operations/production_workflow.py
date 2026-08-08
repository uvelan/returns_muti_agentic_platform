"""Application service coordinating the durable production return workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from return_platform.agents.contracts import (
    FeedbackAssessmentRequest,
    FulfillmentAssessmentRequest,
    FulfillmentFact,
)
from return_platform.agents.registry import AgentRegistry
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.operations.models import ReturnSessionView
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.production_return_workflow import (
    ProductionReturnEvent,
    ProductionReturnEventType,
    ProductionReturnWorkflow,
    ProductionReturnWorkflowInput,
    ProductionReturnWorkflowState,
)


def production_workflow_id(session_id: str) -> str:
    return f"production-return-{session_id}"


class ProductionWorkflowCoordinator:
    """Start, update, and project one Temporal return workflow."""

    def __init__(
        self,
        *,
        temporal: Client,
        repository: OperationalRepository,
        configuration: ReturnPlatformConfiguration,
        task_queue: str,
    ) -> None:
        self._temporal = temporal
        self._repository = repository
        self._configuration = configuration
        self._task_queue = task_queue
        registry = AgentRegistry.build(configuration)
        self._fulfillment_agent = registry.return_fulfillment
        self._feedback_agent = registry.feedback_learning

    async def ensure_started(
        self,
        session: ReturnSessionView,
        *,
        actor_id: str,
    ) -> str:
        workflow_id = production_workflow_id(session.id)
        try:
            await self._temporal.start_workflow(
                ProductionReturnWorkflow.run,
                ProductionReturnWorkflowInput(
                    session_id=session.id,
                    correlation_id=session.correlationId,
                    workflow_version=self._configuration.workflow.version,
                    assumption_set_version=self._configuration.assumption_set_version,
                ),
                id=workflow_id,
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        await self._repository.update_return(
            session.id,
            {
                "workflowId": workflow_id,
                "productionWorkflowVersion": self._configuration.workflow.version,
                "assumptionSetVersion": self._configuration.assumption_set_version,
            },
        )
        await self._repository.append_event(
            session.id,
            event_type="PRODUCTION_WORKFLOW_STARTED",
            actor_type="SYSTEM",
            actor_id=actor_id,
            payload={"workflowId": workflow_id},
            deduplication_key=f"production-workflow:{workflow_id}",
        )
        return workflow_id

    async def query_state(self, session_id: str) -> ProductionReturnWorkflowState:
        handle = self._temporal.get_workflow_handle(production_workflow_id(session_id))
        return cast(
            ProductionReturnWorkflowState,
            await handle.query(ProductionReturnWorkflow.production_state),
        )

    async def _persist_agent_assessments(
        self,
        session_id: str,
        state: ProductionReturnWorkflowState,
        *,
        event_id: str,
        evidence_reference: str,
        actor_id: str,
    ) -> None:
        session = await self._repository.get_return(session_id)
        if session is None:
            return
        facts: list[FulfillmentFact] = []
        fact_values = (
            (state.bol_tendered, "BOL_TENDERED", "TENDERED"),
            (state.carrier_booking_confirmed, "CARRIER_BOOKED", "CONFIRMED"),
            (state.physical_return_complete, "PICKUP_CONFIRMED", "CONFIRMED"),
            (state.receipt_confirmed, "WAREHOUSE_RECEIPT", "RECEIVED"),
            (state.license_plate_assigned, "LICENSE_PLATE", "ASSIGNED"),
            (state.vendor_recovery_complete, "VENDOR_CREDIT", "RECEIVED"),
        )
        for enabled, fact_type, fact_status in fact_values:
            if enabled:
                facts.append(
                    FulfillmentFact(
                        factType=fact_type,
                        status=fact_status,
                        evidenceReference=evidence_reference,
                    )
                )
        raw_status = (
            "REFUNDED"
            if state.customer_resolution_complete
            else "PROCESSED"
            if state.receipt_confirmed
            else "REQUESTED"
            if state.return_created
            else "UNKNOWN"
        )
        raw_product_resolution = (
            "RTV"
            if state.vendor_recovery_required
            else "RESOLVED"
            if state.product_disposition_complete
            else None
        )
        fulfillment = self._fulfillment_agent.assess(
            FulfillmentAssessmentRequest(
                returnVersion=session.omcReturnVersion or "UNKNOWN",
                rawReturnStatus=raw_status,
                rawCustomerResolution=("REFUNDED" if state.customer_resolution_complete else None),
                rawProductResolution=raw_product_resolution,
                facts=tuple(facts),
            )
        )
        await self._repository.persist_agent_decision(
            aggregate_id=session_id,
            session_id=session_id,
            decision=fulfillment.decision.model_dump(mode="json"),
            decision_key=f"fulfillment:{event_id}",
            actor_id=actor_id,
        )
        if state.case_fully_closed:
            events = await self._repository.list_events(session_id, limit=1_000)
            event_types = [item.eventType for item in events]
            feedback = self._feedback_agent.assess(
                FeedbackAssessmentRequest(
                    sessionId=session_id,
                    metrics={
                        "supportClarificationCount": event_types.count(
                            "SUPPORT_CLARIFICATION_REQUESTED"
                        ),
                        "pickupFailureCount": event_types.count("PICKUP_FAILED"),
                        "discoveryAmbiguityCount": event_types.count("DISCOVERY_AMBIGUOUS"),
                        "assumptionMismatch": ("BUSINESS_ASSUMPTION_MISMATCH" in event_types),
                    },
                    evidenceReferences=(evidence_reference,),
                )
            )
            await self._repository.persist_agent_decision(
                aggregate_id=session_id,
                session_id=session_id,
                decision=feedback.decision.model_dump(mode="json"),
                decision_key=f"feedback:{event_id}",
                actor_id=actor_id,
            )

    async def _project_business_event(
        self,
        session_id: str,
        *,
        event_type: ProductionReturnEventType,
        evidence_reference: str,
        business_payload: dict[str, Any],
        actor_id: str,
    ) -> None:
        handling_unit_id = business_payload.get("handlingUnitId")
        handling_units = await self._repository.list_handling_units(session_id)
        selected_units = [
            item
            for item in handling_units
            if handling_unit_id is None or str(item.get("handlingUnitId")) == str(handling_unit_id)
        ]
        if event_type in {
            ProductionReturnEventType.BOL_TENDERED,
            ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
            ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
            ProductionReturnEventType.RECEIPT_CONFIRMED,
        }:
            event_codes = {
                ProductionReturnEventType.BOL_TENDERED: "BOL_TENDERED",
                ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED: ("CARRIER_BOOKING_CONFIRMED"),
                ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED: (
                    "PHYSICAL_HANDOFF_CONFIRMED"
                ),
                ProductionReturnEventType.RECEIPT_CONFIRMED: "RECEIPT_CONFIRMED",
            }
            await self._repository.record_shipment_event(
                session_id=session_id,
                source_system=str(business_payload.get("sourceSystem") or "PLATFORM_EVIDENCE"),
                source_event_id=str(
                    business_payload.get("sourceEventId")
                    or f"{event_type.value}:{evidence_reference}"
                ),
                event_code=event_codes[event_type],
                event_time=datetime.now(UTC),
                handling_unit_id=(str(handling_unit_id) if handling_unit_id is not None else None),
                tracking_number=cast(str | None, business_payload.get("trackingNumber")),
                bol_reference=cast(str | None, business_payload.get("bolReference")),
                carrier=cast(str | None, business_payload.get("carrier")),
                location=cast(str | None, business_payload.get("location")),
                payload_digest=cast(str | None, business_payload.get("payloadDigest")),
            )
        handling_statuses = {
            ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED: "IN_TRANSIT",
            ProductionReturnEventType.RECEIPT_CONFIRMED: "WAREHOUSE_RECEIVED",
            ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED: ("WAREHOUSE_STAGED"),
        }
        target_status = handling_statuses.get(event_type)
        if target_status is not None:
            for handling in selected_units:
                await self._repository.update_handling_unit(
                    str(handling["handlingUnitId"]),
                    {"physicalStatus": target_status},
                    expected_version=int(handling.get("version", 0)),
                )
        if event_type is ProductionReturnEventType.LICENSE_PLATE_ASSIGNED:
            license_plate_id = business_payload.get("licensePlateId")
            if not license_plate_id:
                raise ValueError("LICENSE_PLATE_ASSIGNED requires licensePlateId")
            if not selected_units:
                raise ValueError("LICENSE_PLATE_ASSIGNED requires a valid handling unit")
            for handling in selected_units:
                current_ids = [str(value) for value in handling.get("licensePlateIds", [])]
                updated_ids = list(dict.fromkeys([*current_ids, str(license_plate_id)]))
                await self._repository.update_handling_unit(
                    str(handling["handlingUnitId"]),
                    {
                        "licensePlateIds": updated_ids,
                        "physicalStatus": "LICENSE_PLATE_ASSIGNED",
                    },
                    expected_version=int(handling.get("version", 0)),
                )
        if event_type is ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED:
            disposition = str(business_payload.get("productResolution") or "RESOLVED")
            for item in await self._repository.list_return_items(session_id):
                await self._repository.update_return_item(
                    str(item["returnItemId"]),
                    {
                        "disposition": disposition,
                        "omcProductResolutionId": business_payload.get("omcProductResolutionId"),
                    },
                    expected_version=int(item.get("version", 0)),
                )
        if event_type in {
            ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED,
            ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED,
        }:
            rga_id = business_payload.get("omcRgaId")
            if rga_id:
                await self._repository.upsert_vendor_return_link(
                    session_id=session_id,
                    omc_rga_id=str(rga_id),
                    omc_rga_number=cast(str | None, business_payload.get("omcRgaNumber")),
                    omc_cart_item_ids=[
                        str(value) for value in business_payload.get("omcCartItemIds", [])
                    ],
                    po_numbers=[str(value) for value in business_payload.get("poNumbers", [])],
                    vendor_id=cast(str | None, business_payload.get("vendorId")),
                    status=(
                        "CLOSED"
                        if event_type is ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED
                        else "ACTIVE"
                    ),
                    credit_memo_ids=[
                        str(value) for value in business_payload.get("creditMemoIds", [])
                    ],
                    actor_id=actor_id,
                )

    async def record_event(
        self,
        session_id: str,
        *,
        event_id: str,
        event_type: ProductionReturnEventType,
        evidence_reference: str,
        actor_id: str,
        actor_type: str = "USER",
        business_payload: dict[str, Any] | None = None,
    ) -> ProductionReturnWorkflowState:
        handle = self._temporal.get_workflow_handle(production_workflow_id(session_id))
        event = ProductionReturnEvent(
            event_id=event_id,
            event_type=event_type,
            evidence_reference=evidence_reference,
        )
        state: ProductionReturnWorkflowState | None = None
        for attempt in range(3):
            try:
                state = cast(
                    ProductionReturnWorkflowState,
                    await handle.execute_update(
                        ProductionReturnWorkflow.record_production_event,
                        event,
                        id=event_id,
                        rpc_timeout=timedelta(seconds=10),
                    ),
                )
                break
            except RPCError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        if state is None:
            raise RuntimeError("Production workflow update returned no state")
        projection_payload = business_payload or {}
        await self._project_business_event(
            session_id,
            event_type=event_type,
            evidence_reference=evidence_reference,
            business_payload=projection_payload,
            actor_id=actor_id,
        )
        updates: dict[str, object] = {
            "currentStage": state.stage.value,
            "physicalReturnStatus": (
                "COMPLETE" if state.physical_return_complete else "IN_PROGRESS"
            ),
            "customerResolutionStatus": (
                "COMPLETE" if state.customer_resolution_complete else "PENDING"
            ),
            "warehouseStatus": ("COMPLETE" if state.warehouse_processing_complete else "PENDING"),
            "vendorRecoveryStatus": (
                "COMPLETE"
                if state.vendor_recovery_complete
                else ("PENDING" if state.vendor_recovery_required else "NOT_REQUIRED")
            ),
            "caseClosureStatus": "CLOSED" if state.case_fully_closed else "OPEN",
        }
        if state.case_fully_closed:
            updates.update({"status": "COMPLETED", "progressPercentage": 100})
        elif state.cancelled:
            updates["status"] = "CANCELLED"
        else:
            updates["status"] = "RUNNING"
        await self._repository.update_return(session_id, updates)
        await self._repository.append_event(
            session_id,
            event_type=event_type.value,
            actor_type=actor_type,
            actor_id=actor_id,
            payload={
                "evidenceReference": evidence_reference,
                "stage": state.stage.value,
                "businessPayload": projection_payload,
            },
            deduplication_key=f"production-event:{event_id}",
        )
        await self._persist_agent_assessments(
            session_id,
            state,
            event_id=event_id,
            evidence_reference=evidence_reference,
            actor_id=actor_id,
        )
        return state

    async def seed_confirmed_intake(
        self,
        session: ReturnSessionView,
        *,
        discovery_evidence: str,
        details_evidence: str,
        support_evidence: str,
        actor_id: str,
    ) -> ProductionReturnWorkflowState:
        await self.ensure_started(session, actor_id=actor_id)
        await self.record_event(
            session.id,
            event_id=f"discovery:{discovery_evidence}",
            event_type=ProductionReturnEventType.DISCOVERY_CONFIRMED,
            evidence_reference=discovery_evidence,
            actor_id=actor_id,
        )
        await self.record_event(
            session.id,
            event_id=f"details:{details_evidence}",
            event_type=ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
            evidence_reference=details_evidence,
            actor_id=actor_id,
        )
        return await self.record_event(
            session.id,
            event_id=f"support:{support_evidence}",
            event_type=ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
            evidence_reference=support_evidence,
            actor_id=actor_id,
        )
