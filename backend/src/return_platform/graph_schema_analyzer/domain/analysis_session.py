"""The unit of work: one analyst's attempt to derive a graph schema from sources.

A session is the analyzer's aggregate root. Unlike an Order Discovery turn it is
long-lived and human-paced -- discovery, AI proposal, clarification, editing,
validation, and approval can span days -- so its status transitions are explicit
and checked rather than implied by which service happens to run next.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.domain.errors import InvalidSessionTransition

__all__ = ["AnalysisSession", "SessionStatus"]


class SessionStatus(StrEnum):
    DRAFT = "DRAFT"
    DISCOVERING = "DISCOVERING"
    ANALYZING = "ANALYZING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    ABANDONED = "ABANDONED"
    FAILED = "FAILED"


_TERMINAL: frozenset[SessionStatus] = frozenset(
    {SessionStatus.APPROVED, SessionStatus.ABANDONED, SessionStatus.FAILED}
)

# Only transitions listed here are legal. Written as an explicit table rather than
# inferred, so an illegal jump (e.g. DRAFT straight to APPROVED, skipping discovery
# and validation entirely) is a loud failure instead of an emergent possibility.
_ALLOWED: Mapping[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.DRAFT: frozenset({SessionStatus.DISCOVERING, SessionStatus.ABANDONED}),
    SessionStatus.DISCOVERING: frozenset(
        {SessionStatus.ANALYZING, SessionStatus.FAILED, SessionStatus.ABANDONED}
    ),
    SessionStatus.ANALYZING: frozenset(
        {
            SessionStatus.NEEDS_CLARIFICATION,
            SessionStatus.NEEDS_HUMAN_REVIEW,
            SessionStatus.READY_FOR_APPROVAL,
            SessionStatus.FAILED,
            SessionStatus.ABANDONED,
        }
    ),
    SessionStatus.NEEDS_CLARIFICATION: frozenset(
        {SessionStatus.ANALYZING, SessionStatus.ABANDONED}
    ),
    SessionStatus.NEEDS_HUMAN_REVIEW: frozenset(
        {SessionStatus.ANALYZING, SessionStatus.READY_FOR_APPROVAL, SessionStatus.ABANDONED}
    ),
    # Re-editing a schema that was ready sends it back to ANALYZING: any mutation
    # invalidates the validation result that made it ready in the first place.
    SessionStatus.READY_FOR_APPROVAL: frozenset(
        {SessionStatus.ANALYZING, SessionStatus.APPROVED, SessionStatus.ABANDONED}
    ),
    SessionStatus.APPROVED: frozenset(),
    SessionStatus.ABANDONED: frozenset(),
    SessionStatus.FAILED: frozenset(),
}


class AnalysisSession(BaseModel):
    """One analysis session. `version` is the optimistic-concurrency guard for
    every durable write -- two analysts editing one session must not silently
    clobber each other."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str
    status: SessionStatus
    source_refs: tuple[str, ...]
    created_by: str
    created_at: datetime
    updated_at: datetime
    version: int = 0
    snapshot_id: str | None = None
    draft_id: str | None = None
    failure_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def transitioned_to(
        self,
        status: SessionStatus,
        *,
        occurred_at: datetime,
        failure_reason: str | None = None,
    ) -> AnalysisSession:
        """Return the next session state, or raise if the move is not permitted.

        Returns a new instance and bumps `version`; the caller persists it with a
        compare-and-set on the previous version. A same-status call is rejected
        rather than treated as a no-op, because every legitimate caller knows
        which transition it intends.
        """
        if status not in _ALLOWED[self.status]:
            raise InvalidSessionTransition(
                f"analysis {self.analysis_id} cannot move {self.status} -> {status}; "
                f"permitted: {sorted(_ALLOWED[self.status]) or 'none (terminal)'}"
            )
        return self.model_copy(
            update={
                "status": status,
                "updated_at": occurred_at,
                "version": self.version + 1,
                "failure_reason": failure_reason,
            }
        )

    def with_snapshot(self, snapshot_id: str, *, occurred_at: datetime) -> AnalysisSession:
        return self.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "updated_at": occurred_at,
                "version": self.version + 1,
            }
        )

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
