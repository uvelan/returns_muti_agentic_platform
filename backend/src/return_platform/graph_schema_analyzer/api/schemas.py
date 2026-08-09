"""Wire contracts for `/api/graph-schema`.

Separate from the domain models on purpose. The domain objects carry invariants
and internal fields (`version`, `samples_ref`) that must not become part of a
public contract -- `version` is an optimistic-concurrency token the client does
need, but `samples_ref` is an internal pointer into an encrypted structure and is
deliberately absent from every response model here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from return_platform.graph_schema_analyzer.domain.analysis_session import (
    AnalysisSession,
    SessionStatus,
)
from return_platform.graph_schema_analyzer.domain.clarification import (
    Clarification,
    ClarificationStatus,
)
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    SampleClassification,
    SourceSchemaSnapshot,
)

__all__ = [
    "AnalysisSessionView",
    "AnswerClarificationRequest",
    "ClarificationView",
    "CreateAnalysisRequest",
    "SnapshotView",
]


class CreateAnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_refs: tuple[str, ...] = Field(min_length=1)


class AnalysisSessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str
    status: SessionStatus
    source_refs: tuple[str, ...]
    created_by: str
    created_at: datetime
    updated_at: datetime
    version: int
    snapshot_id: str | None
    draft_id: str | None
    failure_reason: str | None

    @classmethod
    def of(cls, session: AnalysisSession) -> AnalysisSessionView:
        return cls(
            analysis_id=session.analysis_id,
            status=session.status,
            source_refs=session.source_refs,
            created_by=session.created_by,
            created_at=session.created_at,
            updated_at=session.updated_at,
            version=session.version,
            snapshot_id=session.snapshot_id,
            draft_id=session.draft_id,
            failure_reason=session.failure_reason,
        )


class SnapshotView(BaseModel):
    """Metadata and classification only.

    `samples_ref` is intentionally not exposed: the API surfaces *how* samples
    were handled (`sample_classification`) so an operator can audit it, without
    handing out a pointer into the encrypted `source_samples` structure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    analysis_id: str
    content_hash: str
    sample_classification: SampleClassification
    sample_expires_at: datetime | None
    captured_at: datetime
    dataset_count: int

    @classmethod
    def of(cls, snapshot: SourceSchemaSnapshot) -> SnapshotView:
        return cls(
            snapshot_id=snapshot.snapshot_id,
            analysis_id=snapshot.analysis_id,
            content_hash=snapshot.content_hash,
            sample_classification=snapshot.sample_classification,
            sample_expires_at=snapshot.sample_expires_at,
            captured_at=snapshot.captured_at,
            dataset_count=len(snapshot.datasets),
        )


class ClarificationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clarification_id: str
    analysis_id: str
    question: str
    status: ClarificationStatus
    answer: str | None
    asked_at: datetime
    answered_at: datetime | None
    answered_by: str | None

    @classmethod
    def of(cls, clarification: Clarification) -> ClarificationView:
        return cls(
            clarification_id=clarification.clarification_id,
            analysis_id=clarification.analysis_id,
            question=clarification.question,
            status=clarification.status,
            answer=clarification.answer,
            asked_at=clarification.asked_at,
            answered_at=clarification.answered_at,
            answered_by=clarification.answered_by,
        )


class AnswerClarificationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(min_length=1)
