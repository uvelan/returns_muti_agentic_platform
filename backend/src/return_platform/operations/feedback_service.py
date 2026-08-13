"""Governed feedback-learning service built from persisted workflow evidence.

W4.4: a record whose evidence supports a specific, permitted configuration
change also emits a typed `IMPROVEMENT` proposal into the shared kernel. Before
that, `reviewStatus` was stamped `REVIEW_PENDING` and **nothing could transition
it** -- there was no queue it appeared in, no diff to review, and no path from a
recommendation to a configuration change other than an operator reading the
sentence and retyping the value.

The English recommendations stay. They say things no key can ("run graph
synchronization before associate discovery sessions"), and a reader losing them
because a subset became machine-applicable would be a worse record, not a better
one. `reviewStatus` now reports what the governance kernel says rather than a
constant.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pymongo import AsyncMongoClient

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.operations.feedback_improvement import (
    build_improvement_changes,
    changes_to_documents,
)
from return_platform.operations.models import ReturnSessionView, TimelineEvent
from return_platform.platform.governance.errors import GovernanceError
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.proposal import ProposalType

logger = logging.getLogger("return_platform.operations.feedback")


class FeedbackLearningView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    sessionId: str
    finalOutcome: str
    missingFieldInsights: list[str]
    supportReworkInsights: list[str]
    graphSyncInsights: list[str]
    sourceUsageInsights: list[str]
    bayAssignmentInsights: list[str]
    recommendations: list[str]
    evidenceDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewStatus: str
    #: The governance proposal this record produced, when its evidence supported
    #: a permitted configuration change. `None` is the ordinary case: most
    #: returns support no specific change, and emitting one per return would
    #: make the review queue unreadable.
    improvementProposalId: str | None = None
    createdAt: datetime


class FeedbackLearningService:
    """Create reviewable recommendations; never self-modify production rules."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        *,
        configuration: ReturnPlatformConfiguration | None = None,
        kernel: ProposalKernel | None = None,
    ) -> None:
        """`configuration` and `kernel` are what make an improvement typed.

        Optional together, and only together: a process that has neither records
        recommendations exactly as before, which is what the seed and data-repair
        scripts that construct this service need. A process that has one and not
        the other is a wiring mistake and is refused rather than silently
        degrading to prose -- that degradation is invisible, and "the proposals
        stopped appearing" is not a thing anyone notices.
        """
        if (configuration is None) != (kernel is None):
            raise ValueError(
                "FeedbackLearningService needs both the active configuration and the proposal "
                "kernel to emit improvements, or neither."
            )
        self._db = client[settings.mongo_database]
        self._records = self._db["feedback_learning_records"]
        self._graph_runs = self._db["graph_sync_runs"]
        self._enabled = settings.feedback_learning_enabled
        self._configuration = configuration
        self._kernel = kernel

    async def ensure_indexes(self) -> None:
        await self._records.create_index("sessionId", unique=True)
        await self._records.create_index([("createdAt", -1)])
        await self._records.create_index("reviewStatus")

    @staticmethod
    def _view(document: dict[str, Any]) -> FeedbackLearningView:
        return FeedbackLearningView.model_validate(
            {
                "id": str(document["_id"]),
                **{key: value for key, value in document.items() if key != "_id"},
            }
        )

    async def record(
        self,
        session: ReturnSessionView,
        events: list[TimelineEvent],
        *,
        support_ticket_reference: str | None,
        final_outcome: str | None = None,
    ) -> FeedbackLearningView:
        missing_fields: list[str] = []
        if len(session.itemReferences) != 1:
            missing_fields.append("The workflow did not confirm exactly one order line.")
        if not session.productReferences:
            missing_fields.append("The selected order line was not bound to a product.")
        if not session.processingWarehouseReference:
            missing_fields.append("The processing warehouse was not confirmed.")
        if not session.productType:
            missing_fields.append("The product handling type was not captured.")
        support_rework_types = {
            event.eventType
            for event in events
            if event.eventType
            in {
                "SUPPORT_REVIEW_REQUIRED",
                "AI_INTERCEPTION_REQUIRED",
                "RETURN_SUPPORT_CLARIFICATION_REQUIRED",
            }
        }
        support_rework = [
            f"{event_type} occurred and required human follow-up."
            for event_type in sorted(support_rework_types)
        ]
        latest_graph_run = await self._graph_runs.find_one(
            {"status": "COMPLETED"}, sort=[("completedAt", -1)]
        )
        graph_insights = (
            [f"Graph projection validated by sync run {latest_graph_run['_id']}."]
            if latest_graph_run is not None
            else ["No completed graph synchronization evidence was available."]
        )
        source_usage = [
            (
                "The sealed associate discovery context was validated against source MongoDB."
                if session.channel == "ASSOCIATE"
                else "Source MongoDB order evidence was used."
            ),
            "SQL Server owned return, support-ticket, tracking, and bay-assignment facts.",
        ]
        if support_ticket_reference:
            source_usage.append("Return Support ticket evidence was persisted in SQL Server.")
        bay_insights = (
            [f"Return was staged in bay {session.bayReference}."]
            if session.bayReference
            else ["No physical bay assignment was required."]
        )
        recommendations: list[str] = []
        if self._enabled:
            if missing_fields:
                recommendations.append(
                    "Block support handoff until the order line, product, warehouse, "
                    "and handling type are confirmed."
                )
            if support_rework:
                recommendations.append(
                    "Move recurring support clarification fields into the associate question plan."
                )
            if latest_graph_run is None:
                recommendations.append(
                    "Run graph synchronization before associate discovery sessions."
                )
            if not recommendations:
                recommendations.append(
                    "Retain the current question order; no recurring rework signal was detected."
                )
        evidence = {
            "session": session.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "supportTicketReference": support_ticket_reference,
            "graphSyncRun": str(latest_graph_run["_id"]) if latest_graph_run else None,
            "recommendations": recommendations,
        }
        digest = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        record_id = f"FDB-{session.id}"
        proposal_id = (
            await self._propose_improvement(
                record_id=record_id,
                session=session,
                events=events,
                digest=digest,
                occurred_at=now,
            )
            if self._enabled
            else None
        )
        document: dict[str, Any] = {
            "_id": record_id,
            "sessionId": session.id,
            "finalOutcome": final_outcome or session.status.value,
            "missingFieldInsights": missing_fields,
            "supportReworkInsights": support_rework,
            "graphSyncInsights": graph_insights,
            "sourceUsageInsights": source_usage,
            "bayAssignmentInsights": bay_insights,
            "recommendations": recommendations,
            "evidenceDigest": digest,
            # `REVIEW_PENDING` used to be stamped here and transitioned by
            # nothing. It now means one of two honest things: a proposal is
            # genuinely waiting on a reviewer, or the record is advisory prose
            # with nothing to decide -- which is `ADVISORY`, not a review that
            # will never happen.
            "reviewStatus": self._review_status(proposal_id),
            "improvementProposalId": proposal_id,
            "createdAt": now,
        }
        await self._records.replace_one({"sessionId": session.id}, document, upsert=True)
        return self._view(document)

    def _review_status(self, proposal_id: str | None) -> str:
        if not self._enabled:
            return "ARCHIVED"
        return "REVIEW_PENDING" if proposal_id is not None else "ADVISORY"

    async def _propose_improvement(
        self,
        *,
        record_id: str,
        session: ReturnSessionView,
        events: list[TimelineEvent],
        digest: str,
        occurred_at: datetime,
    ) -> str | None:
        """Emit a typed improvement, or nothing at all.

        Never raises into the caller. This runs at the end of a completed
        return; a governance store that is briefly unavailable must not fail the
        return that produced the evidence, and the record itself still carries
        the recommendation in prose. The failure is logged, not swallowed
        silently.
        """
        if self._kernel is None or self._configuration is None:
            return None
        changes = build_improvement_changes(
            configuration=self._configuration,
            event_types=[event.eventType for event in events],
            confirmed_order_line_count=len(session.itemReferences),
        )
        if not changes:
            return None
        before, after = changes_to_documents(changes)
        try:
            proposal = await self._kernel.submit(
                proposal_type=ProposalType.IMPROVEMENT,
                subject_id=record_id,
                title=f"Improvement from return {session.id}",
                before=before,
                after=after,
                evidence=(
                    f"feedback_record:{record_id}",
                    f"return_session:{session.id}",
                    f"evidence_digest:{digest}",
                    *(f"reason:{change.key}={change.reason}" for change in changes),
                ),
                proposed_by="agent.learning",
                occurred_at=occurred_at,
            )
            # Validated against the permitted-key policy and its numeric bounds
            # by `submit` itself; the receipt names the evidence the change was
            # derived from, which is the only artifact this analysis produces.
            proposal = await self._kernel.validate(
                proposal.proposal_id,
                receipt=f"feedback-evidence:{digest}",
                actor="agent.learning",
                occurred_at=occurred_at,
            )
            proposal = await self._kernel.submit_for_review(
                proposal.proposal_id, actor="agent.learning", occurred_at=occurred_at
            )
        except GovernanceError:
            logger.exception(
                "feedback_improvement_proposal_not_recorded",
                extra={"feedback_record": record_id, "session_id": session.id},
            )
            return None
        return proposal.proposal_id

    async def list(self, limit: int = 200) -> list[FeedbackLearningView]:
        documents = await self._records.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get(self, record_id: str) -> FeedbackLearningView | None:
        document = await self._records.find_one({"_id": record_id})
        return None if document is None else self._view(document)
