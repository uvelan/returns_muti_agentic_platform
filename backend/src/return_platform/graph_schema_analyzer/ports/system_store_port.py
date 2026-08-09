"""Durable persistence, expressed as the analyzer's own entity-shaped contract.

Not a generic "give me a collection" port on purpose. The analyzer's repositories
speak in `AnalysisSession`/`SourceSchemaSnapshot`/`Clarification`, so the port
does too -- which keeps the classification and optimistic-concurrency rules
inside this module instead of leaking to whoever binds it, and keeps a caller
from reaching past the repositories into raw documents.

`platform.*` is shared infrastructure, not a business module, so importing the
platform system store from the implementing side is allowed; this port exists so
the analyzer's *own* code does not have to.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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

__all__ = ["PersistencePort"]


@runtime_checkable
class PersistencePort(Protocol):
    """One port, three entity families -- each with its own repository behind it."""

    # --- analysis sessions -------------------------------------------------
    async def create_session(self, session: AnalysisSession) -> None:
        """Insert a new session. Distinct from `save_session` because creation has
        no predecessor version to compare against, and must fail on a duplicate id
        rather than adopting whatever document already exists."""

    async def save_session(self, session: AnalysisSession, *, expected_version: int) -> None:
        """Compare-and-set on `expected_version`. Raises ConcurrentModification if
        the stored version moved."""

    async def load_session(self, analysis_id: str) -> AnalysisSession:
        """Raises UnknownAnalysis when absent -- never returns None, so no caller
        can forget to check."""

    async def list_sessions(
        self, *, status: SessionStatus | None = None, limit: int = 50
    ) -> Sequence[AnalysisSession]: ...

    # --- source snapshots --------------------------------------------------
    async def save_snapshot(self, snapshot: SourceSchemaSnapshot) -> None:
        """Snapshots are immutable and content-addressed: re-saving an identical
        snapshot_id is idempotent, changing one is a programming error."""

    async def load_snapshot(self, snapshot_id: str) -> SourceSchemaSnapshot: ...

    # --- clarifications ----------------------------------------------------
    async def save_clarification(self, clarification: Clarification) -> None: ...

    async def load_clarification(self, clarification_id: str) -> Clarification:
        """Raises UnknownAnalysis when absent, matching load_session."""

    async def list_clarifications(self, analysis_id: str) -> Sequence[Clarification]: ...

    # --- drafts, revisions, validation, approval ---------------------------
    async def create_draft(self, draft: GraphSchemaDraft) -> None: ...

    async def save_draft(self, draft: GraphSchemaDraft, *, expected_version: int) -> None:
        """Compare-and-set, like sessions: two analysts can edit one schema."""

    async def load_draft(self, draft_id: str) -> GraphSchemaDraft: ...

    async def load_draft_for_analysis(self, analysis_id: str) -> GraphSchemaDraft | None: ...

    async def append_revision(self, revision: SchemaRevision) -> None:
        """Append-only; `(draft_id, sequence)` is unique."""

    async def list_revisions(self, draft_id: str) -> Sequence[SchemaRevision]: ...

    async def save_validation_result(self, result: ValidationResult) -> None: ...

    async def load_validation_result(self, result_id: str) -> ValidationResult: ...

    async def save_approval(self, approval: Approval) -> None: ...

    async def list_approvals(self, draft_id: str) -> Sequence[Approval]: ...
