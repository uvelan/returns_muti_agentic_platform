"""Orchestrates mutate / validate / approve over the persistence port.

The pure pieces (`apply_mutations`, `ValidationService`) know nothing about
storage; this service is what turns them into durable state transitions. It owns
one rule the pure layer cannot: **a revision and its draft advance together.**

Mongo has no cross-collection transaction available to us here, so the ordering
is chosen so that a crash mid-way is recoverable rather than corrupting:

    append revision (insert-only, unique on (draft_id, sequence))
      -> then advance the draft

A crash between the two leaves an orphan revision whose sequence exceeds the
draft's `current_revision`. That is detectable, idempotent to retry (the same
sequence insert fails, and the draft advance is then re-applied), and above all
*safe* -- the draft still describes a shape that was really built. The reverse
order would advance a draft to a revision that does not exist, which is
unrecoverable history loss.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
from return_platform.graph_schema_analyzer.application.validation_service import ValidationService
from return_platform.graph_schema_analyzer.domain.approval import Approval
from return_platform.graph_schema_analyzer.domain.errors import (
    AnalyzerError,
    InvalidSessionTransition,
)
from return_platform.graph_schema_analyzer.domain.mutation import MutationCommand
from return_platform.graph_schema_analyzer.domain.schema_draft import (
    DraftStatus,
    GraphSchemaDraft,
)
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaRevision
from return_platform.graph_schema_analyzer.domain.validation_result import ValidationResult
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort

__all__ = ["DraftService", "NoGraphTargetForValidation", "NoSnapshotToValidateAgainst"]

logger = logging.getLogger(__name__)


class NoGraphTargetForValidation(AnalyzerError):
    """Validation was attempted without a graph target. Raised rather than
    skipping the target-owned checks, because a partial validation reporting
    "passed" is worse than no validation."""


class NoSnapshotToValidateAgainst(AnalyzerError):
    """Validation compares a draft against the source shape it was derived from,
    so a draft whose analysis never captured a snapshot cannot be validated at
    all -- rather than being validated against nothing and trivially passing."""


class DraftService:
    def __init__(
        self, persistence: PersistencePort, validation: ValidationService | None = None
    ) -> None:
        """`validation` is optional because only `validate()` needs it.

        Creating a draft and applying mutations touch no graph target, so
        requiring one would force callers to fabricate a stand-in -- and a
        stand-in that silently approves everything is exactly the thing that
        turns a missing target into a passing validation.
        """
        self._persistence = persistence
        self._validation = validation

    async def create_draft(self, *, analysis_id: str, occurred_at: datetime) -> GraphSchemaDraft:
        draft = GraphSchemaDraft(
            draft_id=f"draft-{uuid4()}",
            analysis_id=analysis_id,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        await self._persistence.create_draft(draft)
        return draft

    async def apply(
        self,
        *,
        draft_id: str,
        commands: Sequence[MutationCommand],
        author: str,
        occurred_at: datetime,
        authored_by_model: bool = False,
    ) -> tuple[GraphSchemaDraft, SchemaRevision]:
        """Apply a batch and record it as one revision.

        The batch is applied to the in-memory shape *first*: if any command is
        rejected nothing is written at all, so a bad batch cannot leave a
        half-applied revision in the history.
        """
        draft = await self._persistence.load_draft(draft_id)
        next_shape = apply_mutations(draft.shape, commands)

        revision = SchemaRevision(
            revision_id=f"revision-{uuid4()}",
            draft_id=draft.draft_id,
            sequence=draft.current_revision + 1,
            mutations=tuple(commands),
            author=author,
            created_at=occurred_at,
            authored_by_model=authored_by_model,
        )
        await self._persistence.append_revision(revision)

        mutated = draft.mutated(next_shape, occurred_at=occurred_at)
        await self._persistence.save_draft(mutated, expected_version=draft.version)
        logger.info(
            "analyzer_schema_mutated",
            extra={
                "draft_id": draft.draft_id,
                "revision": revision.sequence,
                "command_count": len(commands),
                "authored_by_model": authored_by_model,
            },
        )
        return mutated, revision

    async def validate(
        self, *, draft_id: str, occurred_at: datetime
    ) -> tuple[GraphSchemaDraft, ValidationResult]:
        if self._validation is None:
            raise NoGraphTargetForValidation(
                "this DraftService was built without a ValidationService, so it cannot "
                "validate; resolve a real graph target first."
            )
        draft = await self._persistence.load_draft(draft_id)
        session = await self._persistence.load_session(draft.analysis_id)
        if session.snapshot_id is None:
            raise NoSnapshotToValidateAgainst(
                f"analysis {draft.analysis_id} has captured no source snapshot; "
                "run discovery before validating."
            )
        snapshot = await self._persistence.load_snapshot(session.snapshot_id)

        revisions = await self._persistence.list_revisions(draft_id)
        if not revisions:
            raise InvalidSessionTransition(f"draft {draft_id} has no revisions to validate.")
        result = await self._validation.validate(
            draft_id=draft_id,
            revision_id=revisions[-1].revision_id,
            shape=draft.shape,
            snapshot=snapshot,
            validated_at=occurred_at,
        )
        await self._persistence.save_validation_result(result)

        # Only a passing result advances the draft. A failing validation is
        # recorded (the analyst needs the findings) but leaves the draft in
        # DRAFT, so nothing downstream can mistake "we checked and it failed"
        # for "it is validated".
        if result.passed:
            draft = draft.validated(result.result_id, occurred_at=occurred_at)
            await self._persistence.save_draft(draft, expected_version=draft.version - 1)
        return draft, result

    async def approve(
        self, *, draft_id: str, approver: str, occurred_at: datetime, note: str | None = None
    ) -> tuple[GraphSchemaDraft, Approval]:
        """Record a human decision and mark the draft APPROVED.

        Reasoning stops before this point; approval is always an explicit human
        act (design doc section 14.4), which is why `approver` is required rather
        than defaulted.
        """
        draft = await self._persistence.load_draft(draft_id)
        if draft.status is not DraftStatus.VALIDATED or draft.validation_result_id is None:
            raise InvalidSessionTransition(
                f"draft {draft_id} is {draft.status}; only a VALIDATED draft with a live "
                "validation result can be approved."
            )
        revisions = await self._persistence.list_revisions(draft_id)
        approval = Approval(
            approval_id=f"approval-{uuid4()}",
            draft_id=draft_id,
            revision_id=revisions[-1].revision_id,
            validation_result_id=draft.validation_result_id,
            approver=approver,
        ).approved(by=approver, occurred_at=occurred_at, note=note)
        await self._persistence.save_approval(approval)

        approved = draft.approved(occurred_at=occurred_at)
        await self._persistence.save_draft(approved, expected_version=draft.version)
        logger.info(
            "analyzer_schema_approved",
            extra={"draft_id": draft_id, "approver": approver},
        )
        return approved, approval
