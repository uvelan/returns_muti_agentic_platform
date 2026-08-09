"""Per-entity persistence for the analyzer.

One repository per entity family rather than a single god-repository, because
each has a genuinely different write discipline: compare-and-set for sessions
and drafts, immutable content-addressed upsert for snapshots, append-only insert
for revisions, state-machine-guarded upsert for clarifications and approvals.
Collapsing them would force the weakest discipline on all of them.

`SystemStorePersistence` composes them into the single `PersistencePort` the rest
of the module resolves, so callers see one contract while each entity keeps its
own rules. This class lives here rather than in `bootstrap/adapters/` because it
binds the analyzer to `platform.*` -- shared infrastructure, not another business
module -- which the independence rule permits.
"""

from __future__ import annotations

from collections.abc import Sequence

from return_platform.graph_schema_analyzer.domain.analysis_session import (
    AnalysisSession,
    SessionStatus,
)
from return_platform.graph_schema_analyzer.domain.approval import Approval
from return_platform.graph_schema_analyzer.domain.clarification import Clarification
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaDraft
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaRevision
from return_platform.graph_schema_analyzer.domain.source_snapshot import SourceSchemaSnapshot
from return_platform.graph_schema_analyzer.domain.validation_result import ValidationResult
from return_platform.graph_schema_analyzer.persistence.approval_repository import (
    ApprovalRepository,
)
from return_platform.graph_schema_analyzer.persistence.clarification_repository import (
    ClarificationRepository,
)
from return_platform.graph_schema_analyzer.persistence.draft_repository import DraftRepository
from return_platform.graph_schema_analyzer.persistence.session_repository import (
    AnalysisSessionRepository,
)
from return_platform.graph_schema_analyzer.persistence.snapshot_repository import (
    SourceSnapshotRepository,
)
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.platform.system_store.repository import SystemStore

__all__ = [
    "AnalysisSessionRepository",
    "ApprovalRepository",
    "ClarificationRepository",
    "DraftRepository",
    "SourceSnapshotRepository",
    "SystemStorePersistence",
    "build_system_store_persistence",
]


class SystemStorePersistence:
    """The analyzer's `PersistencePort`, backed by the platform system store."""

    def __init__(self, system_store: SystemStore) -> None:
        self.sessions = AnalysisSessionRepository(system_store)
        self.snapshots = SourceSnapshotRepository(system_store)
        self.clarifications = ClarificationRepository(system_store)
        self.drafts = DraftRepository(system_store)
        self.approvals = ApprovalRepository(system_store)

    async def save_session(self, session: AnalysisSession, *, expected_version: int) -> None:
        await self.sessions.save(session, expected_version=expected_version)

    async def create_session(self, session: AnalysisSession) -> None:
        await self.sessions.create(session)

    async def load_session(self, analysis_id: str) -> AnalysisSession:
        return await self.sessions.load(analysis_id)

    async def list_sessions(
        self, *, status: SessionStatus | None = None, limit: int = 50
    ) -> Sequence[AnalysisSession]:
        return await self.sessions.list(status=status, limit=limit)

    async def save_snapshot(self, snapshot: SourceSchemaSnapshot) -> None:
        await self.snapshots.save(snapshot)

    async def load_snapshot(self, snapshot_id: str) -> SourceSchemaSnapshot:
        return await self.snapshots.load(snapshot_id)

    async def save_clarification(self, clarification: Clarification) -> None:
        await self.clarifications.save(clarification)

    async def load_clarification(self, clarification_id: str) -> Clarification:
        return await self.clarifications.load(clarification_id)

    async def list_clarifications(self, analysis_id: str) -> Sequence[Clarification]:
        return await self.clarifications.list_for_analysis(analysis_id)

    async def create_draft(self, draft: GraphSchemaDraft) -> None:
        await self.drafts.create_draft(draft)

    async def save_draft(self, draft: GraphSchemaDraft, *, expected_version: int) -> None:
        await self.drafts.save_draft(draft, expected_version=expected_version)

    async def load_draft(self, draft_id: str) -> GraphSchemaDraft:
        return await self.drafts.load_draft(draft_id)

    async def load_draft_for_analysis(self, analysis_id: str) -> GraphSchemaDraft | None:
        return await self.drafts.load_draft_for_analysis(analysis_id)

    async def append_revision(self, revision: SchemaRevision) -> None:
        await self.drafts.append_revision(revision)

    async def list_revisions(self, draft_id: str) -> Sequence[SchemaRevision]:
        return await self.drafts.list_revisions(draft_id)

    async def save_validation_result(self, result: ValidationResult) -> None:
        await self.drafts.save_validation_result(result)

    async def load_validation_result(self, result_id: str) -> ValidationResult:
        return await self.drafts.load_validation_result(result_id)

    async def save_approval(self, approval: Approval) -> None:
        await self.approvals.save(approval)

    async def list_approvals(self, draft_id: str) -> Sequence[Approval]:
        return await self.approvals.list_for_draft(draft_id)


def build_system_store_persistence(system_store: SystemStore) -> PersistencePort:
    """Typed factory: the return annotation is what makes mypy prove conformance
    to the port, rather than leaving it to a runtime `isinstance` check that only
    verifies method *names* exist (design doc, three-layer conformance table)."""
    return SystemStorePersistence(system_store)
