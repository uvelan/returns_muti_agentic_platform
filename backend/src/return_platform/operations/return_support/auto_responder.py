"""Execute the Support Response Agent's plan against one work item.

The agent decides (`agents/support_response.py`); this module acts. For a
Channel B work item whose opening message carries a `support-handoff-v1`
payload it:

1. acknowledges the work item when it is still NEW, so the queue shows a
   responder took it;
2. posts the agent's message to the thread — which is what the Support UI chat
   renders, beside the human conversation;
3. either records the return outcome through `DurableSupportEventStore` (the
   same seam the console's `submit_return_outcome` uses, so the case workflow
   receives the RMA, tracking, label and instructions through the one door it
   already listens on), or, when the handoff lacks what the method requires,
   asks the clarification on the thread instead.

Idempotent end to end: the outcome's `supportEventId` is fixed per work item,
so a re-run recognises its own recording; the thread message carries that id in
its business payload and is skipped when already present; the acknowledgement
tolerates having lost the race to a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from return_platform.agents.contracts import (
    SupportHandoffItemInput,
    SupportResponseAssessment,
    SupportResponseRequest,
)
from return_platform.agents.support_response import SupportResponseAgent
from return_platform.operations.repository import ConcurrencyConflictError
from return_platform.operations.return_support.service import (
    CreateSupportMessageRequest,
    ReturnSupportService,
    SupportAction,
    SupportActionRequest,
    SupportMessageType,
    SupportWorkItemStatus,
    SupportWorkItemView,
)
from return_platform.operations.support_events import (
    DurableSupportEventStore,
    support_return_record,
)
from return_platform.workflows.return_case_workflow import return_case_workflow_id

#: Who the thread shows as the sender. One spelling: it is also the actor id on
#: the recorded outcome and the acknowledgement.
SUPPORT_RESPONSE_ACTOR = "support-response-agent"

#: Statuses the responder acts from. Anything later means a human or an earlier
#: run has already progressed the item, and a second answer would talk over it.
_ACTIONABLE: frozenset[SupportWorkItemStatus] = frozenset(
    {
        SupportWorkItemStatus.NEW,
        SupportWorkItemStatus.ACKNOWLEDGED,
        SupportWorkItemStatus.IN_PROGRESS,
    }
)


@dataclass(frozen=True, slots=True)
class SupportAgentRunOutcome:
    """What one run did, stated so the caller can render it without guessing."""

    workItemId: str
    caseId: str | None
    #: RESPONDED | CLARIFICATION_REQUESTED | SKIPPED_NO_CASE |
    #: SKIPPED_STATUS | SKIPPED_NO_HANDOFF
    outcome: str
    returnReference: str | None = None
    supportEventId: str | None = None
    missingFields: tuple[str, ...] = ()
    detail: str | None = None


def support_event_id_for(work_item_id: str) -> str:
    return f"{SUPPORT_RESPONSE_ACTOR}:{work_item_id}"


def _handoff_request(
    item: SupportWorkItemView, payload: dict[str, Any]
) -> SupportResponseRequest | None:
    """The agent's request from a `support-handoff-v1` payload, or `None`.

    Reads the structured half of the handoff only — never the message text,
    which is the rule `support_handoff.py` states: nothing may parse the prose
    back into fields.
    """
    order = payload.get("order") or {}
    raw_items = order.get("items") or []
    items = tuple(
        SupportHandoffItemInput(
            lineReference=str(entry.get("lineReference") or ""),
            productName=entry.get("productName"),
            sku=entry.get("sku"),
            quantity=entry.get("quantity"),
            reason=entry.get("reason"),
            condition=entry.get("condition"),
        )
        for entry in raw_items
        if entry.get("lineReference")
    )
    if not items or item.caseId is None:
        return None
    return_details = payload.get("returnDetails") or {}
    bay = payload.get("bayAssignment") or {}
    customer = payload.get("customer") or {}
    return SupportResponseRequest(
        caseId=item.caseId,
        workItemId=item.id,
        orderReference=order.get("reference"),
        customerName=customer.get("name"),
        returnMethod=return_details.get("method"),
        bayReference=bay.get("bayReference"),
        returnLocation=bay.get("returnLocation"),
        handlingInstructions=bay.get("handlingInstructions"),
        items=items,
    )


class SupportAutoResponder:
    def __init__(
        self,
        *,
        service: ReturnSupportService,
        event_store: DurableSupportEventStore,
        agent: SupportResponseAgent,
    ) -> None:
        self._service = service
        self._events = event_store
        self._agent = agent

    async def respond(
        self, work_item_id: str, *, correlation_id: str | None = None
    ) -> SupportAgentRunOutcome:
        item = await self._service.get_work_item(work_item_id)
        if item is None:
            raise KeyError(work_item_id)
        if item.caseId is None:
            return SupportAgentRunOutcome(
                workItemId=work_item_id,
                caseId=None,
                outcome="SKIPPED_NO_CASE",
                detail="This work item belongs to a return session; the agent serves case threads.",
            )
        if SupportWorkItemStatus(item.status) not in _ACTIONABLE:
            return SupportAgentRunOutcome(
                workItemId=work_item_id,
                caseId=item.caseId,
                outcome="SKIPPED_STATUS",
                detail=f"Work item is {item.status}; the agent does not talk over progressed work.",
            )

        messages = await self._service.list_messages(item.threadId)
        request = next(
            (
                parsed
                for message in messages
                if message.businessPayload.get("schemaVersion") == "support-handoff-v1"
                and (parsed := _handoff_request(item, message.businessPayload)) is not None
            ),
            None,
        )
        if request is None:
            return SupportAgentRunOutcome(
                workItemId=work_item_id,
                caseId=item.caseId,
                outcome="SKIPPED_NO_HANDOFF",
                detail="No support-handoff-v1 payload on this thread; nothing safe to plan from.",
            )

        assessment = self._agent.assess(request)
        event_id = support_event_id_for(work_item_id)

        item = await self._acknowledge(item)
        already_posted = any(
            message.businessPayload.get("supportAgentEventId") == event_id for message in messages
        )
        if not already_posted:
            item = await self._post(item, assessment, event_id)

        if not assessment.ready:
            return SupportAgentRunOutcome(
                workItemId=work_item_id,
                caseId=item.caseId,
                outcome="CLARIFICATION_REQUESTED",
                missingFields=assessment.missingFields,
                detail=assessment.clarificationRequest,
            )

        plan = assessment.plan
        assert plan is not None  # ready implies a plan, by the agent's contract
        receipt = await self._events.record_support_response(
            case_id=item.caseId or "",
            work_item_id=work_item_id,
            support_event_id=event_id,
            records=[
                support_return_record(
                    return_reference=plan.returnReference,
                    tracking_reference=plan.trackingReference,
                    label_reference=plan.labelReference,
                    return_location=plan.returnLocation,
                    shipping_instruction_reference=plan.shippingInstructionReference,
                    return_method=plan.returnMethod,
                    carrier=plan.carrier,
                    order_line_references=plan.orderLineReferences,
                )
            ],
            rejected=False,
            reason=None,
            workflow_id=return_case_workflow_id(item.caseId or ""),
            actor_id=SUPPORT_RESPONSE_ACTOR,
            correlation_id=correlation_id,
        )
        return SupportAgentRunOutcome(
            workItemId=work_item_id,
            caseId=item.caseId,
            outcome="RESPONDED",
            returnReference=plan.returnReference,
            supportEventId=receipt.support_event_id,
        )

    async def _acknowledge(self, item: SupportWorkItemView) -> SupportWorkItemView:
        if SupportWorkItemStatus(item.status) is not SupportWorkItemStatus.NEW:
            return item
        try:
            return await self._service.apply_action(
                item.id,
                SupportActionRequest(
                    action=SupportAction.ACKNOWLEDGE,
                    expectedVersion=item.version,
                    reason="Support Response Agent is handling this request.",
                ),
                actor_id=SUPPORT_RESPONSE_ACTOR,
            )
        except ConcurrencyConflictError:
            # A human moved it first; their state stands, and the re-read below
            # is what every later step keys its expectedVersion on.
            refreshed = await self._service.get_work_item(item.id)
            return refreshed if refreshed is not None else item

    async def _post(
        self,
        item: SupportWorkItemView,
        assessment: SupportResponseAssessment,
        event_id: str,
    ) -> SupportWorkItemView:
        message_type = (
            SupportMessageType.RETURN_CREATION
            if assessment.ready
            else SupportMessageType.CLARIFICATION_REQUEST
        )
        payload: dict[str, Any] = {
            "supportAgentEventId": event_id,
            "decision": assessment.decision.model_dump(mode="json"),
        }
        if assessment.plan is not None:
            payload["plan"] = assessment.plan.model_dump(mode="json")
        if assessment.missingFields:
            payload["missingFields"] = list(assessment.missingFields)
        updated, _ = await self._service.add_message(
            item.id,
            CreateSupportMessageRequest(
                messageType=message_type,
                messageText=assessment.messageText,
                businessPayload=payload,
                expectedVersion=item.version,
            ),
            actor_id=SUPPORT_RESPONSE_ACTOR,
            actor_role="AGENT",
        )
        return updated
