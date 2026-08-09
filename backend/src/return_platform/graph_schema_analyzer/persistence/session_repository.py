"""`analysis_sessions` persistence with real optimistic concurrency.

Sessions are edited by humans over long stretches, often by more than one analyst,
so a lost update here is a realistic failure rather than a theoretical one. Every
write is a conditional replace on `(analysis_id, version)`; a non-match means
someone else wrote first and the caller is told, never silently overwritten.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.graph_schema_analyzer.domain.analysis_session import (
    AnalysisSession,
    SessionStatus,
)
from return_platform.graph_schema_analyzer.domain.errors import (
    ConcurrentModification,
    UnknownAnalysis,
)
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["ANALYSIS_SESSIONS", "AnalysisSessionRepository"]

ANALYSIS_SESSIONS = "analysis_sessions"


class AnalysisSessionRepository:
    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store

    async def save(self, session: AnalysisSession, *, expected_version: int) -> None:
        """Compare-and-set. `expected_version` is the version the caller read, and
        `session.version` is that value already incremented by the domain object's
        own transition method -- so the filter matches exactly one predecessor.

        `upsert` is deliberately False even for a brand-new session: creation goes
        through `create()`, which fails loudly on a duplicate id instead of quietly
        adopting whatever document happened to already be there.
        """
        result = await self._store.replace_one(
            ANALYSIS_SESSIONS,
            {"analysis_id": session.analysis_id, "version": expected_version},
            dict(session.to_document()),
            upsert=False,
        )
        if result.matched_count != 1:
            raise ConcurrentModification(
                f"analysis {session.analysis_id} was not at version {expected_version}; "
                "re-read the session and re-apply the change."
            )

    async def create(self, session: AnalysisSession) -> None:
        """Insert a new session. The manifest's unique index on `analysis_id` is
        what actually enforces uniqueness -- a duplicate raises from the driver
        rather than being pre-checked, because a pre-check would be a race."""
        await self._store.insert_one(ANALYSIS_SESSIONS, dict(session.to_document()))

    async def load(self, analysis_id: str) -> AnalysisSession:
        document = await self._store.read_only(ANALYSIS_SESSIONS).find_one(
            {"analysis_id": analysis_id}, {"_id": 0}
        )
        if document is None:
            raise UnknownAnalysis(f"no analysis session with id {analysis_id!r}")
        return AnalysisSession.model_validate(document)

    async def list(
        self, *, status: SessionStatus | None = None, limit: int = 50
    ) -> Sequence[AnalysisSession]:
        query: dict[str, object] = {} if status is None else {"status": status.value}
        cursor = (
            self._store.read_only(ANALYSIS_SESSIONS)
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return [AnalysisSession.model_validate(document) async for document in cursor]
