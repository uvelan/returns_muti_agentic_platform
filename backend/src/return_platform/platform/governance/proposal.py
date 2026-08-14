"""One proposal record and one lifecycle, for every kind of change a human signs off.

    DRAFT --validate--> VALIDATED --submit--> REVIEW_PENDING --+--> APPROVED --activate--> ACTIVATED
      |                    |                       |           |
      |                    +-- back to DRAFT       +--> REJECTED (terminal)
      |                        when the subject changed
      +------------------------------------------------------------> SUPERSEDED (terminal)

Three types share it -- `GRAPH_SCHEMA`, `IMPROVEMENT`, `CONFIGURATION` -- because
the platform needs *one* inbox. Before this existed there were two half-paths: the
analyzer's `Approval`, which knew a decision but nothing about what changed, and
feedback records stamped `REVIEW_PENDING` that nothing could transition at all.
A second approval path for the third type would have made three.

**The record carries the whole basis for the decision**, not a pointer to it:
before, after, diff, evidence, actor, validation receipt, affected keys and risk.
A reviewer approving a row in a queue is approving *this document*, and an
activator that read the change from somewhere else could apply something the
reviewer never saw.

`diff` and `affected_keys` are derived from `before`/`after` by `diff_documents`
and re-derived at activation -- they are a rendering of the change, never an
independent statement about it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.platform.governance.errors import (
    InvalidProposalTransition,
    ProposalIntegrityError,
)

__all__ = [
    "LIVE_STATUSES",
    "PROPOSAL_TRANSITIONS",
    "ChangeKind",
    "Proposal",
    "ProposalDiffEntry",
    "ProposalStatus",
    "ProposalTransition",
    "ProposalType",
    "RiskLevel",
    "assess_risk",
    "diff_documents",
    "evidence_digest",
    "transition_allowed",
]


class ProposalType(StrEnum):
    GRAPH_SCHEMA = "GRAPH_SCHEMA"
    IMPROVEMENT = "IMPROVEMENT"
    CONFIGURATION = "CONFIGURATION"


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    REVIEW_PENDING = "REVIEW_PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACTIVATED = "ACTIVATED"
    SUPERSEDED = "SUPERSEDED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


#: The lifecycle, as one table -- the same shape as
#: `configuration.graph_repository.RELEASE_TRANSITIONS`, and for the same reason:
#: a state machine written out at each call site is a state machine that is legal
#: in one place and refused in another.
#:
#: `VALIDATED -> DRAFT` is not a rollback an operator performs. It is what happens
#: when the subject changes underneath a validated proposal, mirroring
#: `GraphSchemaDraft.mutated` clearing its validation result: a validation is a
#: statement about one exact shape, and the statement is stale the moment the
#: shape moves.
#:
#: `REJECTED` and `SUPERSEDED` are terminal. A rejected proposal is not re-opened
#: -- the answer to "what did this person decide" has to stay answerable -- and a
#: superseded one describes a document that no longer exists.
PROPOSAL_TRANSITIONS: Mapping[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset({ProposalStatus.VALIDATED, ProposalStatus.SUPERSEDED}),
    ProposalStatus.VALIDATED: frozenset(
        {ProposalStatus.REVIEW_PENDING, ProposalStatus.DRAFT, ProposalStatus.SUPERSEDED}
    ),
    ProposalStatus.REVIEW_PENDING: frozenset(
        {ProposalStatus.APPROVED, ProposalStatus.REJECTED, ProposalStatus.SUPERSEDED}
    ),
    ProposalStatus.APPROVED: frozenset({ProposalStatus.ACTIVATED, ProposalStatus.SUPERSEDED}),
    ProposalStatus.ACTIVATED: frozenset({ProposalStatus.SUPERSEDED}),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
}

#: Statuses a proposal can still leave. Anything else is history.
LIVE_STATUSES: frozenset[ProposalStatus] = frozenset(
    status for status, targets in PROPOSAL_TRANSITIONS.items() if targets
)


def transition_allowed(current: ProposalStatus, target: ProposalStatus) -> bool:
    return target in PROPOSAL_TRANSITIONS.get(current, frozenset())


class ChangeKind(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    CHANGED = "CHANGED"


class ProposalDiffEntry(BaseModel):
    """One leaf that differs, addressed by its dotted path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    change: ChangeKind
    before: Any = None
    after: Any = None


class ProposalTransition(BaseModel):
    """One entry in the proposal's own history.

    Kept on the record rather than only in the audit trail: the audit collection
    answers "what happened on this platform", and this answers "how did *this*
    proposal reach the state it is in", which is the question a reviewer looking
    at the inbox actually has.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ProposalStatus
    actor: str
    occurred_at: datetime
    note: str | None = None


def _leaves(document: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten to dotted leaf paths.

    Sequences are leaves, not recursion points. A list whose third element moved
    is one change to that list, and addressing it as `fields.2.priority` would
    make the key -- the thing the permitted-key policy is enforced on -- depend on
    an ordering nobody promised to keep stable.
    """
    flat: dict[str, Any] = {}
    for key, value in document.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            nested = _leaves(value, f"{path}.")
            if nested:
                flat.update(nested)
            else:
                # An empty mapping is itself a value; dropping it would make
                # "the block was cleared" invisible in the diff.
                flat[path] = {}
        else:
            flat[path] = value
    return flat


def diff_documents(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[ProposalDiffEntry, ...]:
    """The change, as sorted dotted-path entries.

    One derivation, used to build the proposal *and* to re-derive it at
    activation. Two implementations would let a proposal be reviewed on one
    reading of the documents and enforced on another.
    """
    before_leaves = _leaves(before)
    after_leaves = _leaves(after)
    entries: list[ProposalDiffEntry] = []
    for key in sorted(set(before_leaves) | set(after_leaves)):
        in_before = key in before_leaves
        in_after = key in after_leaves
        if in_before and in_after:
            if before_leaves[key] != after_leaves[key]:
                entries.append(
                    ProposalDiffEntry(
                        key=key,
                        change=ChangeKind.CHANGED,
                        before=before_leaves[key],
                        after=after_leaves[key],
                    )
                )
        elif in_after:
            entries.append(
                ProposalDiffEntry(key=key, change=ChangeKind.ADDED, after=after_leaves[key])
            )
        else:
            entries.append(
                ProposalDiffEntry(key=key, change=ChangeKind.REMOVED, before=before_leaves[key])
            )
    return tuple(entries)


def assess_risk(diff: Sequence[ProposalDiffEntry]) -> RiskLevel:
    """Risk from the shape of the change, not from who asked for it.

    Removal is HIGH because it is the only class that destroys information a
    running system may still be reading -- a dropped entity, a deleted template,
    a cleared weight. Modification is MEDIUM: something already in use behaves
    differently. Pure addition is LOW: nothing that works today stops working.

    Deliberately not a model's opinion. A reviewer needs "why is this HIGH" to
    have an answer they can check.
    """
    if any(entry.change is ChangeKind.REMOVED for entry in diff):
        return RiskLevel.HIGH
    if any(entry.change is ChangeKind.CHANGED for entry in diff):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def evidence_digest(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    evidence: Sequence[str],
) -> str:
    """Content address for what the proposal was built from.

    So that "this is the proposal I reviewed" is checkable after the fact rather
    than assumed. Matches the digest discipline `FeedbackLearningService` already
    applies to its own evidence.
    """
    encoded = json.dumps(
        {"before": before, "after": after, "evidence": list(evidence)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Proposal(BaseModel):
    """One proposed change, and everything needed to decide on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    proposal_type: ProposalType
    #: What the change is about: a draft id, an agent manifest id, a feedback
    #: record id. Scoped by type, so two types may use the same string safely.
    subject_id: str
    title: str
    status: ProposalStatus = ProposalStatus.DRAFT
    before: Mapping[str, Any] = {}
    after: Mapping[str, Any] = {}
    diff: tuple[ProposalDiffEntry, ...] = ()
    affected_keys: tuple[str, ...] = ()
    risk: RiskLevel = RiskLevel.LOW
    #: References, never payloads. Evidence is a session id, a validation result
    #: id, a sync run id -- naming the artifact keeps the proposal small and
    #: keeps source rows out of a document the whole console can read.
    evidence: tuple[str, ...] = ()
    evidence_digest: str
    proposed_by: str
    #: The id of the check that says this change is sound: a
    #: `ValidationResult.result_id` for a schema draft, a configuration
    #: reload receipt for an agent module. Set on entering VALIDATED and
    #: cleared when the proposal falls back to DRAFT.
    validation_receipt: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    #: What the activator produced -- the release id a change was carried into.
    activation_reference: str | None = None
    history: tuple[ProposalTransition, ...] = ()
    created_at: datetime
    updated_at: datetime
    version: int = 0

    # --- integrity ---------------------------------------------------------

    def verify_derivations(self) -> None:
        """Re-derive `diff` and `affected_keys` and refuse a record that disagrees.

        Called at activation. Everything up to that point can be re-read by a
        human; activation is the step where the document stops being a proposal
        and starts being a change, so it is the last place a forged or drifted
        record can be caught.
        """
        recomputed = diff_documents(self.before, self.after)
        if recomputed != self.diff:
            raise ProposalIntegrityError(
                f"proposal {self.proposal_id}: the recorded diff does not follow from its "
                "before/after documents."
            )
        keys = tuple(entry.key for entry in recomputed)
        if keys != self.affected_keys:
            raise ProposalIntegrityError(
                f"proposal {self.proposal_id}: the recorded affected keys do not follow from "
                "its diff."
            )

    # --- transitions -------------------------------------------------------

    def _moved(
        self,
        status: ProposalStatus,
        *,
        actor: str,
        occurred_at: datetime,
        note: str | None = None,
        **updates: Any,
    ) -> Proposal:
        if not transition_allowed(self.status, status):
            raise InvalidProposalTransition(
                f"proposal {self.proposal_id} is {self.status}; {status} is not reachable from "
                "there."
            )
        return self.model_copy(
            update={
                "status": status,
                "history": (
                    *self.history,
                    ProposalTransition(
                        status=status, actor=actor, occurred_at=occurred_at, note=note
                    ),
                ),
                "updated_at": occurred_at,
                "version": self.version + 1,
                **updates,
            }
        )

    def validated(self, receipt: str, *, actor: str, occurred_at: datetime) -> Proposal:
        """A named check passed. The receipt is required, not optional: a
        VALIDATED proposal with nothing to point at is indistinguishable from one
        nobody checked."""
        return self._moved(
            ProposalStatus.VALIDATED,
            actor=actor,
            occurred_at=occurred_at,
            validation_receipt=receipt,
        )

    def invalidated(
        self, *, actor: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        """The subject moved, so the validation no longer describes it."""
        return self._moved(
            ProposalStatus.DRAFT,
            actor=actor,
            occurred_at=occurred_at,
            note=note,
            validation_receipt=None,
        )

    def submitted_for_review(self, *, actor: str, occurred_at: datetime) -> Proposal:
        return self._moved(ProposalStatus.REVIEW_PENDING, actor=actor, occurred_at=occurred_at)

    def approved(self, *, actor: str, occurred_at: datetime, note: str | None = None) -> Proposal:
        return self._moved(
            ProposalStatus.APPROVED,
            actor=actor,
            occurred_at=occurred_at,
            note=note,
            decided_by=actor,
            decided_at=occurred_at,
            decision_note=note,
        )

    def rejected(self, *, actor: str, occurred_at: datetime, note: str | None = None) -> Proposal:
        return self._moved(
            ProposalStatus.REJECTED,
            actor=actor,
            occurred_at=occurred_at,
            note=note,
            decided_by=actor,
            decided_at=occurred_at,
            decision_note=note,
        )

    def activated(self, reference: str, *, actor: str, occurred_at: datetime) -> Proposal:
        return self._moved(
            ProposalStatus.ACTIVATED,
            actor=actor,
            occurred_at=occurred_at,
            activation_reference=reference,
        )

    def superseded(self, *, actor: str, occurred_at: datetime, note: str | None = None) -> Proposal:
        return self._moved(
            ProposalStatus.SUPERSEDED, actor=actor, occurred_at=occurred_at, note=note
        )

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
