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

**Approval is not recorded here.** It used to be, as an `Approval` document that
knew who decided and nothing about what they decided on -- no before, no diff, no
affected elements, and reachable only through the analyzer, so an agent
configuration edit or a feedback recommendation had no equivalent record at all.
That type is gone and this service writes a `GRAPH_SCHEMA` proposal to the shared
kernel instead (`platform.governance`), which every other governed change also
goes through. The kernel is platform infrastructure, not another business module,
which is why importing it here does not breach the analyzer's independence rule
(see `ports/system_store_port.py`'s note).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
from return_platform.graph_schema_analyzer.application.validation_service import ValidationService
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
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.proposal import (
    LIVE_STATUSES,
    Proposal,
    ProposalStatus,
    ProposalType,
)

__all__ = [
    "DraftService",
    "NoGraphTargetForValidation",
    "NoProposalForDraft",
    "NoSnapshotToValidateAgainst",
]

logger = logging.getLogger(__name__)


class NoGraphTargetForValidation(AnalyzerError):
    """Validation was attempted without a graph target. Raised rather than
    skipping the target-owned checks, because a partial validation reporting
    "passed" is worse than no validation."""


class NoSnapshotToValidateAgainst(AnalyzerError):
    """Validation compares a draft against the source shape it was derived from,
    so a draft whose analysis never captured a snapshot cannot be validated at
    all -- rather than being validated against nothing and trivially passing."""


class NoProposalForDraft(AnalyzerError):
    """A draft reached approval with no live governance proposal.

    Only reachable if the draft was validated before this module recorded
    proposals, or if the proposal was superseded underneath the approver. Raised
    rather than minting one on the spot: a proposal created at the moment of
    approval would carry a diff nobody reviewed.
    """


class DraftService:
    def __init__(
        self,
        persistence: PersistencePort,
        kernel: ProposalKernel,
        validation: ValidationService | None = None,
    ) -> None:
        """`validation` is optional because only `validate()` needs it.

        Creating a draft and applying mutations touch no graph target, so
        requiring one would force callers to fabricate a stand-in -- and a
        stand-in that silently approves everything is exactly the thing that
        turns a missing target into a passing validation.

        `kernel` is **not** optional, and that asymmetry is deliberate. A
        governance kernel that a caller may omit is a governance kernel that
        gets omitted, and the failure mode is silent: a draft would be approved
        with no proposal record and nothing would say so.
        """
        self._persistence = persistence
        self._kernel = kernel
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

        # The draft just stopped being the shape any live proposal describes.
        # `GraphSchemaDraft.mutated` already clears the validation result for the
        # same reason; this is the governance half of that rule, without which a
        # reviewer's queue would go on showing a diff that no longer exists.
        for live in await self._kernel.list(
            proposal_type=ProposalType.GRAPH_SCHEMA, subject_id=draft_id, limit=50
        ):
            if live.status in LIVE_STATUSES:
                await self._kernel.supersede(
                    live.proposal_id,
                    actor=author,
                    occurred_at=occurred_at,
                    note=f"draft {draft_id} advanced to revision {revision.sequence}",
                )

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
        self, *, draft_id: str, actor: str, occurred_at: datetime
    ) -> tuple[GraphSchemaDraft, ValidationResult, Proposal | None]:
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
        if not result.passed:
            return draft, result, None

        draft = draft.validated(result.result_id, occurred_at=occurred_at)
        await self._persistence.save_draft(draft, expected_version=draft.version - 1)

        # A validated schema is a change awaiting a decision, so it enters the
        # same queue as every other one. `before` is left to the kernel, which
        # resolves it to whatever this draft last activated -- a diff against the
        # schema that is running, not against an empty document.
        proposal = await self._kernel.submit(
            proposal_type=ProposalType.GRAPH_SCHEMA,
            subject_id=draft_id,
            title=f"Graph schema draft {draft_id}",
            after=draft.shape.model_dump(mode="json"),
            evidence=(
                f"analysis:{draft.analysis_id}",
                f"snapshot:{snapshot.snapshot_id}",
                f"revision:{revisions[-1].revision_id}",
                f"validation_result:{result.result_id}",
            ),
            proposed_by=actor,
            occurred_at=occurred_at,
        )
        proposal = await self._kernel.validate(
            proposal.proposal_id,
            receipt=result.result_id,
            actor=actor,
            occurred_at=occurred_at,
        )
        proposal = await self._kernel.submit_for_review(
            proposal.proposal_id, actor=actor, occurred_at=occurred_at
        )
        return draft, result, proposal

    async def approve(
        self, *, draft_id: str, approver: str, occurred_at: datetime, note: str | None = None
    ) -> tuple[GraphSchemaDraft, Proposal]:
        """Record a human decision and mark the draft APPROVED.

        Reasoning stops before this point; approval is always an explicit human
        act (design doc section 14.4), which is why `approver` is required rather
        than defaulted -- and why the route that calls this now carries a
        capability dependency instead of accepting whoever the middleware let in.
        """
        draft = await self._persistence.load_draft(draft_id)
        if draft.status is not DraftStatus.VALIDATED or draft.validation_result_id is None:
            raise InvalidSessionTransition(
                f"draft {draft_id} is {draft.status}; only a VALIDATED draft with a live "
                "validation result can be approved."
            )
        pending = await self._pending_proposal(draft_id)
        proposal = await self._kernel.approve(
            pending.proposal_id, actor=approver, occurred_at=occurred_at, note=note
        )

        approved = draft.approved(occurred_at=occurred_at)
        await self._persistence.save_draft(approved, expected_version=draft.version)
        logger.info(
            "analyzer_schema_approved",
            extra={"draft_id": draft_id, "approver": approver, "proposal_id": proposal.proposal_id},
        )
        return approved, proposal

    async def reject(
        self, *, draft_id: str, approver: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        """Refuse a validated draft.

        Nothing could express this before: `Approval` had a `REJECTED` status and
        no code path that ever set it, so a reviewer who did not want a schema
        simply closed the tab and the draft sat VALIDATED forever.
        """
        pending = await self._pending_proposal(draft_id)
        return await self._kernel.reject(
            pending.proposal_id, actor=approver, occurred_at=occurred_at, note=note
        )

    async def _pending_proposal(self, draft_id: str) -> Proposal:
        proposals = await self._kernel.list(
            proposal_type=ProposalType.GRAPH_SCHEMA, subject_id=draft_id, limit=50
        )
        for proposal in proposals:
            if proposal.status is ProposalStatus.REVIEW_PENDING:
                return proposal
        raise NoProposalForDraft(
            f"draft {draft_id} has no proposal awaiting review; re-validate it so the change "
            "enters the review queue with a diff attached."
        )
