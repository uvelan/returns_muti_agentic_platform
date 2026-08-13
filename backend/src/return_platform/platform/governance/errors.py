"""Typed failures for the proposal kernel.

Platform-owned rather than borrowed from a domain module: three domains submit
proposals through this kernel, and an exception type is as much a coupling as a
service type -- `graph_schema_analyzer` may not import `configuration`'s errors
and vice versa, so neither may own these.
"""

from __future__ import annotations

__all__ = [
    "ActivationRefused",
    "ForbiddenProposalKey",
    "GovernanceError",
    "InvalidProposalTransition",
    "ProposalConcurrentModification",
    "ProposalIntegrityError",
    "UnknownProposal",
]


class GovernanceError(Exception):
    """Base for every proposal-kernel failure."""


class ActivationRefused(GovernanceError):
    """The activator declined the change and said why.

    Distinct from a failure: nothing broke, the target simply will not take this
    document. The proposal stays APPROVED so a corrected one can be published
    without re-running the review, and the caller gets the target's own words --
    which entity was ambiguous, which field is out of range -- because that is
    the only part an operator can act on.
    """

    def __init__(self, detail: str, *, reference: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.reference = reference


class UnknownProposal(GovernanceError):
    """No proposal exists for the requested id."""


class InvalidProposalTransition(GovernanceError):
    """A status change the lifecycle does not permit."""


class ProposalConcurrentModification(GovernanceError):
    """An optimistic-concurrency write lost: the stored `version` moved.

    Never retried silently. Two reviewers deciding the same proposal is exactly
    the case this exists for, and a blind retry would let the second overwrite
    the first's decision without either of them knowing.
    """


class ForbiddenProposalKey(GovernanceError):
    """A proposal names a configuration key its type may not change (plan section 7).

    Raised at validation *and again* at activation. The second check is the one
    that matters: the first can be bypassed by anything that writes a proposal
    record directly, and the UI is never the authority on what a proposal is
    allowed to touch.
    """


class ProposalIntegrityError(GovernanceError):
    """The stored `diff`/`affected_keys` do not follow from the stored
    `before`/`after`.

    A proposal is reviewed on its diff and enforced on its keys. If those two
    can disagree with the documents they claim to describe, a reviewer approves
    one change and the activator applies another.
    """
