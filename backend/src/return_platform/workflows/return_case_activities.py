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
from dataclasses import dataclass
from typing import Any, Protocol

from temporalio import activity

from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.return_case_workflow import (
    BayResultNotice,
    DraftSupportRequestInput,
    OpenSupportWorkItemInput,
    RecordCaseStatusInput,
    RecordSupportOutcomeInput,
    RequestBayAssignmentInput,
    SendSupportReminderInput,
    SynchronizeReturnRecordsInput,
)

__all__ = [
    "CaseBayPlacementPort",
    "ReturnCaseActivities",
    "ReturnRecordGraphSyncPort",
    "ReturnRecordSyncOutcome",
]

logger = logging.getLogger("return_platform.workflows.return_case_activities")


class SupportDraftPort:
    """What the workflow needs from the drafting model, and nothing more.

    A protocol rather than `StructuredOutputInvoker` directly so the activity
    can be exercised without a route pool, and so the fallback below is a
    property of this seam rather than of the gateway.
    """

    async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ReturnRecordSyncOutcome:
    """What a record-scoped sync commits, as the activity needs to see it."""

    graph_generation_id: str
    synchronized_record_ids: tuple[str, ...]
    nodes_written: int


class ReturnRecordGraphSyncPort(Protocol):
    """The one method the case workflow needs from the targeted-sync stack.

    Structural, so `workflows` neither imports `dynamic_knowledge` nor learns
    what a generation lease is; the adapter is
    `dynamic_knowledge/integration/return_record_sync.py`. It must raise rather
    than return on failure -- this activity is `blocking`, and a port that
    reported partial success would put the decision in the wrong place.
    """

    async def synchronize_records(
        self, *, case_id: str, return_record_ids: tuple[str, ...]
    ) -> ReturnRecordSyncOutcome: ...


class CaseBayPlacementPort(Protocol):
    """The one method the bay activity needs from placement.

    Structural, so `workflows` does not import the agent registry or the
    warehouse observation stack to ask for a bay. The implementation is
    `operations/warehouse/case_placement.py::CaseBayPlacement`, which is the
    session engine re-keyed -- not a second one.

    It must not raise for "no bay": a state comes back as a recommendation
    carrying its reason. It may raise when placement could not be attempted at
    all, and the workflow's `ActivityError` branch records `REQUEST_FAILED`.
    """

    async def recommend(self, case_id: str) -> Any: ...


class ReturnCaseActivities:
    """Narrow injected surface: one repository, one support service, one drafter."""

    def __init__(
        self,
        *,
        repository: OperationalRepository,
        support_service: Any,
        drafter: SupportDraftPort | None = None,
        graph_sync: ReturnRecordGraphSyncPort | None = None,
        bay_placement: CaseBayPlacementPort | None = None,
    ) -> None:
        self._repository = repository
        self._support = support_service
        self._drafter = drafter
        self._graph_sync = graph_sync
        self._bay_placement = bay_placement

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
    async def request_bay_assignment(
        self, request: RequestBayAssignmentInput
    ) -> BayResultNotice:
        """Recommend a bay for this case, and answer with the whole result.

        This was the BAY-01 defect in one method: it appended a single
        `bay_assignment_requested` fact and returned nothing. It queried no
        graph, consulted no bay, computed no confidence and resolved no
        location -- and the workflow then waited the full bay window for a
        signal nothing sent. "Bay is best-effort" was true; there was simply
        nothing to be best-effort about.

        Deliberately does not raise on "no bay available": that is a state, not
        a failure, and it comes back as a notice whose `reason` says which
        state. It raises only when the *request* could not be made, which is
        what the workflow's `ActivityError` branch is for.
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
        if self._bay_placement is None:
            # Named, not silent. A worker registered without placement gives
            # every case the same empty bay, and a reason an operator can grep
            # for is the difference between a misconfiguration and a mystery.
            return BayResultNotice(
                warehouse_reference=None,
                bay_reference=None,
                reason="BAY_PLACEMENT_NOT_CONFIGURED",
            )
        recommendation = await self._bay_placement.recommend(request.case_id)
        notice = BayResultNotice(
            warehouse_reference=recommendation.warehouse_reference,
            bay_reference=recommendation.bay_reference,
            reason=recommendation.reason,
            return_location=recommendation.return_location,
            confidence_millionths=recommendation.confidence_millionths,
            explanation=recommendation.explanation,
            evidence_reference=recommendation.evidence_reference,
            graph_generation_id=recommendation.graph_generation_id,
            capacity_evidence=recommendation.capacity_evidence,
        )
        await self._record_bay_facts(request.case_id, notice)
        return notice

    async def _record_bay_facts(self, case_id: str, notice: BayResultNotice) -> None:
        """The recommendation, on the case, where the associate's next turn reads it.

        Facts rather than a case column for the reason every other placement
        output is a fact: the agent's turn context is built from the fact
        projection, so writing them here is what lets Order Discovery tell the
        associate where the goods are going without a second query.

        `fact_id` is derived from the case and the fact name, so a bay result
        that arrives twice -- a retry, a replay, a late signal followed by a
        recommendation -- rewrites one fact instead of appending a second
        opinion. Best-effort like everything else on this path: a fact that
        could not be written never invalidates the recommendation itself.
        """
        values: tuple[tuple[str, Any], ...] = (
            ("bay_warehouse_reference", notice.warehouse_reference),
            ("bay_reference", notice.bay_reference),
            ("bay_return_location", notice.return_location),
            ("bay_confidence_millionths", notice.confidence_millionths),
            ("bay_reason", notice.reason),
            ("bay_evidence_reference", notice.evidence_reference),
            ("bay_capacity_evidence", notice.capacity_evidence),
        )
        for name, value in values:
            if value is None:
                continue
            try:
                await self._repository.append_case_fact(
                    fact_id=f"{name}-{case_id}",
                    case_id=case_id,
                    fact_name=name,
                    value=value,
                    agent_id="bay-assignment-agent",
                    channel=FactChannel.SYSTEM,
                    acquisition_method=FactAcquisition.DERIVED,
                    source_path="RETURN_CASE_WORKFLOW",
                )
            except Exception:  # noqa: BLE001 - advisory, like the bay itself
                logger.warning(
                    "bay_fact_not_recorded",
                    extra={"case_id": case_id, "fact_name": name},
                    exc_info=True,
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

    @activity.defn(name="synchronize_return_records")
    async def synchronize_return_records(self, request: SynchronizeReturnRecordsInput) -> str:
        """Project the committed records into the graph, then say which generation.

        Returns *after* the write commits, never before: the port's contract is
        that a successful return is a post-commit fact, because generation-fenced
        writes mean an agent turn landing mid-write reads a case whose RMAs are
        half there. The workflow records the returned generation, so "the sync
        finished" and "which graph the associate will be answered from" are the
        same statement rather than two hopeful ones.

        Raises when no port is configured. A worker that registers this activity
        without one would silently skip the step for every case, which is the
        shape of the bug W2.5 exists to close -- and unlike the drafter, there is
        no honest fallback: nothing else puts a return into the graph before the
        next turn.
        """
        if self._graph_sync is None:
            raise RuntimeError(
                "synchronize_return_records was registered without a graph sync port; "
                "the return records for case "
                f"{request.case_id} would exist in the store and not in the graph"
            )
        outcome = await self._graph_sync.synchronize_records(
            case_id=request.case_id,
            return_record_ids=request.return_record_ids,
        )
        # A fact rather than a case column: which generation answered a case is
        # provenance, and `case_facts` is where provenance that must survive a
        # later correction lives. `fact_id` is derived from the case and the
        # generation, so an activity retry after a partial failure rewrites the
        # same fact instead of appending a second one.
        await self._repository.append_case_fact(
            fact_id=f"return-graph-generation-{request.case_id}-{outcome.graph_generation_id}",
            case_id=request.case_id,
            fact_name="return_graph_generation_id",
            value=outcome.graph_generation_id,
            agent_id="return-workflow-agent",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
            source_path="RETURN_RECORD_ON_DEMAND_SYNC",
        )
        logger.info(
            "return_records_synchronized",
            extra={
                "case_id": request.case_id,
                "graph_generation_id": outcome.graph_generation_id,
                "record_count": len(outcome.synchronized_record_ids),
                "nodes_written": outcome.nodes_written,
            },
        )
        return outcome.graph_generation_id
