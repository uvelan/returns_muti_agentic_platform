"""`clarifications` persistence."""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.graph_schema_analyzer.domain.clarification import Clarification
from return_platform.graph_schema_analyzer.domain.errors import UnknownAnalysis
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["CLARIFICATIONS", "ClarificationRepository"]

CLARIFICATIONS = "clarifications"


class ClarificationRepository:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    async def save(self, clarification: Clarification) -> None:
        """Upsert on `clarification_id`. Safe without a version guard because the
        domain object already refuses every illegal state change (answering a
        withdrawn question, re-answering an answered one), so the only writes that
        reach here are ones the state machine permitted."""
        await self._store.replace_one(
            CLARIFICATIONS,
            {"clarification_id": clarification.clarification_id},
            dict(clarification.to_document()),
            upsert=True,
        )

    async def load(self, clarification_id: str) -> Clarification:
        document = await self._store.read_only(CLARIFICATIONS).find_one(
            {"clarification_id": clarification_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no clarification with id {clarification_id!r}")
        return Clarification.model_validate(document)

    async def list_for_analysis(self, analysis_id: str) -> Sequence[Clarification]:
        cursor = (
            self._store.read_only(CLARIFICATIONS)
            .find({"analysis_id": analysis_id}, {"_id": 0})
            .sort("asked_at", 1)
        )
        return [Clarification.model_validate(document) async for document in cursor]
