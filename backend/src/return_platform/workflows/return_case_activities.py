"""Everything `ReturnCaseWorkflow` is not allowed to do itself.

The split is the whole reason the workflow is replayable: Mongo writes, model
calls and support-thread mutations all live here, behind activity names the
workflow references as strings. Nothing in this module is imported by the
workflow module.

Each activity is idempotent on a key the *workflow* supplies, not one minted
here. A Temporal retry re-runs the activity with identical input, and an
idempotency key generated inside would be new each time -- which for
`send_support_reminder` means a second message to a human.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity

from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.return_case_workflow import (
    DraftSupportRequestInput,
    OpenSupportWorkItemInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    SendSupportReminderInput,
)

__all__ = ["ReturnCaseActivities"]

logger = logging.getLogger("return_platform.workflows.return_case_activities")


class SupportDraftPort:
    """What the workflow needs from the drafting model, and nothing more.

    A protocol rather than `StructuredOutputInvoker` directly so the activity
    can be exercised without a route pool, and so the fallback below is a
    property of this seam rather than of the gateway.
    """

    async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError


class ReturnCaseActivities:
    """Narrow injected surface: one repository, one support service, one drafter."""

    def __init__(
        self,
        *,
        repository: OperationalRepository,
        support_service: Any,
        drafter: SupportDraftPort | None = None,
    ) -> None:
        self._repository = repository
        self._support = support_service
        self._drafter = drafter

    @activity.defn(name="record_case_status")
    async def record_case_status(self, request: RecordCaseStatusInput) -> None:
        case = await self._repository.get_case(request.case_id)
        if case is None:
            raise ValueError(f"case {request.case_id} does not exist")
        await self._repository.update_case(
            request.case_id,
            {"status": request.status},
            expected_version=int(case["version"]),
        )
        if request.fact_name is not None and request.fact_id is not None:
            await self._repository.append_case_fact(
                fact_id=request.fact_id,
                case_id=request.case_id,
                fact_name=request.fact_name,
                value=request.fact_value,
                agent_id="return-workflow-agent",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.DERIVED,
                source_path="RETURN_CASE_WORKFLOW",
            )

    @activity.defn(name="request_bay_assignment")
    async def request_bay_assignment(self, request: RequestBayAssignmentInput) -> None:
        """Ask for a bay. Best-effort by policy, so this records and returns.

        Deliberately does not raise on "no bay available": that is a state, not
        a failure, and the workflow's own timeout covers a request that never
        gets answered. It raises only when the *request* could not be made,
        which is what the workflow's `ActivityError` branch is for.
        """
        await self._repository.append_case_fact(
            fact_id=f"bay-requested-{request.case_id}",
            case_id=request.case_id,
            fact_name="bay_assignment_requested",
            value=True,
            agent_id="bay-assignment-agent",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
            source_path="RETURN_CASE_WORKFLOW",
        )

    @activity.defn(name="draft_support_request")
    async def draft_support_request(self, request: DraftSupportRequestInput) -> str:
        """The message Support will read.

        Falls back to a deterministic template when no drafter is configured or
        the model is unavailable. Support being asked in plainer words is a far
        better outcome than a return that stops because a provider is down.
        """
        facts = await self._repository.latest_case_facts(request.case_id)
        plain = {name: fact.get("value") for name, fact in facts.items()}
        if self._drafter is not None:
            try:
                drafted = await self._drafter.draft(case_id=request.case_id, facts=plain)
                if drafted.strip():
                    return drafted
            except Exception:  # noqa: BLE001 - fall back, never fail the case
                logger.warning(
                    "support_draft_unavailable", extra={"case_id": request.case_id}, exc_info=True
                )
        order = plain.get("confirmed_order_reference") or "an order"
        return (
            f"Hello -- we have a return to raise against {order}. "
            "Could you create the RMA and send the return label or pickup "
            "instructions when you have a moment? Happy to supply anything else "
            "you need. Thank you."
        )

    @activity.defn(name="open_support_work_item")
    async def open_support_work_item(self, request: OpenSupportWorkItemInput) -> str:
        """Open the Channel B thread, once.

        `idempotency_key` comes from the case, so a retry or a replay after
        `continue_as_new` re-reads the existing thread instead of starting a
        second conversation with a person.
        """
        work_item_id = await self._support.open_case_thread(
            case_id=request.case_id,
            tenant_id=request.tenant_id,
            principal_id=request.principal_id,
            support_draft=request.support_draft,
            idempotency_key=request.idempotency_key,
        )
        case = await self._repository.get_case(request.case_id)
        if case is not None and case.get("channelBWorkItemId") != work_item_id:
            # The link that makes a support outcome reachable from the
            # associate's conversation.
            await self._repository.update_case(
                request.case_id,
                {"channelBWorkItemId": work_item_id},
                expected_version=int(case["version"]),
            )
        return str(work_item_id)

    @activity.defn(name="send_support_reminder")
    async def send_support_reminder(self, request: SendSupportReminderInput) -> None:
        """A polite nudge on the existing thread. Never a new one."""
        await self._support.post_reminder(
            work_item_id=request.work_item_id,
            reminder_number=request.reminder_number,
            max_reminders=request.max_reminders,
            idempotency_key=request.idempotency_key,
        )

    @activity.defn(name="record_support_outcome")
    async def record_support_outcome(self, request: RecordSupportOutcomeInput) -> None:
        """One return record per RMA Support issued.

        The ids are supplied by the workflow, so a retry re-uses them and the
        unique index turns the second attempt into a no-op rather than a
        duplicate RMA.
        """
        for record, record_id in zip(request.records, request.return_record_ids, strict=False):
            try:
                created = await self._repository.create_return_record(
                    return_record_id=record_id,
                    case_id=request.case_id,
                    return_reference=record.return_reference,
                    status="ISSUED",
                    source_system="RETURN_SUPPORT",
                )
            except Exception:  # noqa: BLE001 - a replay re-issuing the same RMA
                logger.info(
                    "return_record_already_recorded",
                    extra={"case_id": request.case_id, "rma": record.return_reference},
                )
                continue
            await self._repository.update_return_record(
                created["returnRecordId"],
                {
                    "trackingReference": record.tracking_reference,
                    "labelReference": record.label_reference,
                    "returnLocation": record.return_location,
                    "shippingInstructionReference": record.shipping_instruction_reference,
                },
                expected_version=0,
            )
            # Also a fact, and this is the step that reaches Channel A. The
            # agent's turn context is built from the case's fact projection, so
            # writing the RMA here is what makes it appear in the associate's
            # *original* conversation on their next turn -- no new chat, no
            # client-side join, no poll.
            await self._repository.append_case_fact(
                fact_id=f"rma-{record_id}",
                case_id=request.case_id,
                fact_name="return_reference",
                value=record.return_reference,
                agent_id="return-support",
                channel=FactChannel.CHANNEL_B,
                acquisition_method=FactAcquisition.OBSERVED,
                source_system="RETURN_SUPPORT",
                source_path="SUPPORT_REPLY",
            )
            for name, value in (
                ("tracking_reference", record.tracking_reference),
                ("label_reference", record.label_reference),
                ("return_location", record.return_location),
            ):
                if value is None:
                    continue
                await self._repository.append_case_fact(
                    fact_id=f"{name}-{record_id}",
                    case_id=request.case_id,
                    fact_name=name,
                    value=value,
                    agent_id="return-support",
                    channel=FactChannel.CHANNEL_B,
                    acquisition_method=FactAcquisition.OBSERVED,
                    source_system="RETURN_SUPPORT",
                    source_path="SUPPORT_REPLY",
                )
            for line in record.order_line_references:
                for item in await self._repository.list_case_return_items(request.case_id):
                    if item.get("orderLineId") == line and not item.get("returnRecordId"):
                        await self._repository.assign_return_item_to_record(
                            str(item["returnItemId"]),
                            return_record_id=created["returnRecordId"],
                            expected_version=int(item.get("version", 0)),
                        )
                        break
