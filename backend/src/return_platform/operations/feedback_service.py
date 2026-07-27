"""Governed feedback-learning service built from persisted workflow evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.models import ReturnSessionView, TimelineEvent


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
    createdAt: datetime


class FeedbackLearningService:
    """Create reviewable recommendations; never self-modify production rules."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
    ) -> None:
        self._db = client[settings.mongo_database]
        self._records = self._db["feedback_learning_records"]
        self._graph_runs = self._db["graph_sync_runs"]
        self._enabled = settings.feedback_learning_enabled

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
            "reviewStatus": "REVIEW_PENDING" if self._enabled else "ARCHIVED",
            "createdAt": now,
        }
        await self._records.replace_one({"sessionId": session.id}, document, upsert=True)
        return self._view(document)

    async def list(self, limit: int = 200) -> list[FeedbackLearningView]:
        documents = await self._records.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get(self, record_id: str) -> FeedbackLearningView | None:
        document = await self._records.find_one({"_id": record_id})
        return None if document is None else self._view(document)
