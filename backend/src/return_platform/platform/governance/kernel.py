"""The one place a proposed change is validated, reviewed, decided and activated.

Every transition in the lifecycle passes through here, for all three proposal
types. That is the whole point: the platform previously had an approval that knew
a decision but not what changed (the analyzer's `Approval`) and a review status
nothing could transition (feedback records), and the obvious fix -- give feedback
its own approval flow -- would have produced a third half-path rather than closing
either.

**Activation re-checks everything.** The permitted-key policy is re-derived from
the proposal *type* and re-applied, and the diff and affected keys are re-derived
from the before/after documents and compared to what was stored. A proposal is
data; anything that can write the collection can write a proposal claiming
whatever it likes about itself, and the UI is never the authority on what a
change is allowed to touch.

**Every transition writes an audit record**, and it writes it after the state
change has been persisted. An audit line for a transition that then failed to
save would be worse than none.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from return_platform.platform.governance.errors import (
    ForbiddenProposalKey,
    GovernanceError,
    InvalidProposalTransition,
)
from return_platform.platform.governance.key_policy import (
    KeyPolicy,
    KeyPolicyDecision,
    evaluate_keys,
    policy_for,
)
from return_platform.platform.governance.ports import (
    ActivationReceipt,
    GovernanceAuditPort,
    ProposalActivationPort,
    ProposalStorePort,
)
from return_platform.platform.governance.proposal import (
    Proposal,
    ProposalStatus,
    ProposalType,
    assess_risk,
    diff_documents,
    evidence_digest,
)

__all__ = ["NoActivatorRegistered", "ProposalKernel"]

logger = logging.getLogger("return_platform.governance")


class NoActivatorRegistered(GovernanceError):
    """An approved proposal has no way to reach the runtime.

    Raised rather than marking it ACTIVATED anyway. "Approved and activated" with
    nothing behind it is the single most expensive lie this record can tell.
    """


class ProposalKernel:
    def __init__(
        self,
        store: ProposalStorePort,
        *,
        activators: Mapping[ProposalType, ProposalActivationPort],
        audit: GovernanceAuditPort,
    ) -> None:
        self._store = store
        self._activators = dict(activators)
        self._audit = audit

    # --- reads --------------------------------------------------------------

    async def get(self, proposal_id: str) -> Proposal:
        return await self._store.load(proposal_id)

    async def list(
        self,
        *,
        proposal_type: ProposalType | None = None,
        status: ProposalStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Proposal]:
        return await self._store.list(
            proposal_type=proposal_type, status=status, subject_id=subject_id, limit=limit
        )

    async def last_activated(self, proposal_type: ProposalType, subject_id: str) -> Proposal | None:
        return await self._store.last_activated(proposal_type, subject_id)

    # --- submission ---------------------------------------------------------

    async def submit(
        self,
        *,
        proposal_type: ProposalType,
        subject_id: str,
        title: str,
        after: Mapping[str, Any],
        before: Mapping[str, Any] | None = None,
        evidence: Sequence[str] = (),
        proposed_by: str,
        occurred_at: datetime,
        supersede_live: bool = True,
    ) -> Proposal:
        """Record a change as a DRAFT proposal.

        `before` defaults to whatever this subject last activated, which is the
        only `before` a reviewer can act on -- a diff against a document
        assembled for the occasion tells them what the author was looking at, not
        what is running.

        `supersede_live` closes any earlier proposal for the same subject that
        can still change state. Two live proposals for one subject are two
        reviewers approving different futures of the same thing, and whichever
        activates second silently reverts the first.
        """
        resolved_before: Mapping[str, Any]
        if before is not None:
            resolved_before = before
        else:
            previous = await self._store.last_activated(proposal_type, subject_id)
            resolved_before = previous.after if previous is not None else {}

        if supersede_live:
            for live in await self._store.live_for_subject(proposal_type, subject_id):
                await self._transition(
                    live.superseded(
                        actor=proposed_by,
                        occurred_at=occurred_at,
                        note="replaced by a newer proposal for the same subject",
                    ),
                    expected_version=live.version,
                    action="PROPOSAL_SUPERSEDED",
                )

        diff = diff_documents(resolved_before, after)
        proposal = Proposal(
            proposal_id=f"proposal-{uuid4()}",
            proposal_type=proposal_type,
            subject_id=subject_id,
            title=title,
            before=resolved_before,
            after=after,
            diff=diff,
            affected_keys=tuple(entry.key for entry in diff),
            risk=assess_risk(diff),
            evidence=tuple(evidence),
            evidence_digest=evidence_digest(
                before=resolved_before, after=after, evidence=tuple(evidence)
            ),
            proposed_by=proposed_by,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        # Refused at submission as well as at activation. The person who can fix
        # a forbidden key is the one writing the proposal, and telling them now
        # is the difference between a corrected draft and a rejected release.
        self._enforce_keys(proposal)
        await self._store.create(proposal)
        await self._record(
            "PROPOSAL_SUBMITTED",
            proposal,
            actor=proposed_by,
            extra={"risk": proposal.risk.value, "affected_keys": list(proposal.affected_keys)},
        )
        return proposal

    # --- lifecycle ----------------------------------------------------------

    async def validate(
        self, proposal_id: str, *, receipt: str, actor: str, occurred_at: datetime
    ) -> Proposal:
        proposal = await self._store.load(proposal_id)
        self._enforce_keys(proposal)
        return await self._transition(
            proposal.validated(receipt, actor=actor, occurred_at=occurred_at),
            expected_version=proposal.version,
            action="PROPOSAL_VALIDATED",
        )

    async def invalidate(
        self, proposal_id: str, *, actor: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        proposal = await self._store.load(proposal_id)
        return await self._transition(
            proposal.invalidated(actor=actor, occurred_at=occurred_at, note=note),
            expected_version=proposal.version,
            action="PROPOSAL_INVALIDATED",
        )

    async def submit_for_review(
        self, proposal_id: str, *, actor: str, occurred_at: datetime
    ) -> Proposal:
        proposal = await self._store.load(proposal_id)
        return await self._transition(
            proposal.submitted_for_review(actor=actor, occurred_at=occurred_at),
            expected_version=proposal.version,
            action="PROPOSAL_REVIEW_REQUESTED",
        )

    async def approve(
        self, proposal_id: str, *, actor: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        """Record a human decision.

        The capability check that gates *who* may call this is a route
        dependency, not a check here: the kernel is reached from three surfaces
        and an actor string it was handed is not evidence of anything. What the
        kernel enforces is that the decision is made once, on a proposal that is
        actually awaiting one.
        """
        proposal = await self._store.load(proposal_id)
        return await self._transition(
            proposal.approved(actor=actor, occurred_at=occurred_at, note=note),
            expected_version=proposal.version,
            action="PROPOSAL_APPROVED",
        )

    async def reject(
        self, proposal_id: str, *, actor: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        proposal = await self._store.load(proposal_id)
        return await self._transition(
            proposal.rejected(actor=actor, occurred_at=occurred_at, note=note),
            expected_version=proposal.version,
            action="PROPOSAL_REJECTED",
        )

    async def supersede(
        self, proposal_id: str, *, actor: str, occurred_at: datetime, note: str | None = None
    ) -> Proposal:
        proposal = await self._store.load(proposal_id)
        return await self._transition(
            proposal.superseded(actor=actor, occurred_at=occurred_at, note=note),
            expected_version=proposal.version,
            action="PROPOSAL_SUPERSEDED",
        )

    async def activate(
        self,
        proposal_id: str,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any] | None = None,
    ) -> tuple[Proposal, ActivationReceipt]:
        """Carry an APPROVED proposal into the runtime.

        Order matters and is chosen so a failure is recoverable rather than a
        lie: re-check, then run the activator, then record ACTIVATED. A failed
        activation leaves the proposal APPROVED -- which is true, and retryable
        -- rather than ACTIVATED against a release that was never published.
        """
        proposal = await self._store.load(proposal_id)
        if proposal.status is not ProposalStatus.APPROVED:
            raise InvalidProposalTransition(
                f"proposal {proposal_id} is {proposal.status}; only an APPROVED proposal can be "
                "activated."
            )
        # Never trust the record about itself.
        proposal.verify_derivations()
        self._enforce_keys(proposal)

        activator = self._activators.get(proposal.proposal_type)
        if activator is None:
            raise NoActivatorRegistered(
                f"no activator is registered for {proposal.proposal_type} proposals in this "
                "process, so this change cannot be carried into the runtime here."
            )
        # Read before the activation, not after: once this proposal is ACTIVATED
        # it is itself the most recent one, and asking afterwards would return
        # the proposal we just activated instead of the one it replaces.
        previous = await self._store.last_activated(proposal.proposal_type, proposal.subject_id)

        receipt = await activator.activate(
            proposal, actor=actor, occurred_at=occurred_at, parameters=dict(parameters or {})
        )
        activated = await self._transition(
            proposal.activated(receipt.reference, actor=actor, occurred_at=occurred_at),
            expected_version=proposal.version,
            action="PROPOSAL_ACTIVATED",
            extra={"reference": receipt.reference, "detail": receipt.detail},
        )
        # Only now is the predecessor genuinely history.
        if previous is not None and previous.proposal_id != activated.proposal_id:
            await self._transition(
                previous.superseded(
                    actor=actor,
                    occurred_at=occurred_at,
                    note=f"superseded by {activated.proposal_id}",
                ),
                expected_version=previous.version,
                action="PROPOSAL_SUPERSEDED",
            )
        return activated, receipt

    # --- internals ----------------------------------------------------------

    def _key_decision(self, proposal: Proposal) -> KeyPolicyDecision:
        policy = policy_for(proposal.proposal_type)
        if policy is KeyPolicy.NOT_KEY_SCOPED:
            return evaluate_keys(policy, ())
        values = {entry.key: entry.after for entry in proposal.diff if entry.after is not None}
        return evaluate_keys(policy, proposal.affected_keys, values)

    def _enforce_keys(self, proposal: Proposal) -> None:
        decision = self._key_decision(proposal)
        if not decision.accepted:
            raise ForbiddenProposalKey(
                f"proposal {proposal.proposal_id} ({proposal.proposal_type}): {decision.reason()}"
            )

    async def _transition(
        self,
        proposal: Proposal,
        *,
        expected_version: int,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> Proposal:
        await self._store.save(proposal, expected_version=expected_version)
        await self._record(
            action,
            proposal,
            actor=proposal.history[-1].actor if proposal.history else proposal.proposed_by,
            extra=extra,
        )
        return proposal

    async def _record(
        self,
        action: str,
        proposal: Proposal,
        *,
        actor: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "proposal_type": proposal.proposal_type.value,
            "subject_id": proposal.subject_id,
            "status": proposal.status.value,
            "risk": proposal.risk.value,
            "evidence_digest": proposal.evidence_digest,
        }
        if extra:
            details.update({key: value for key, value in extra.items() if value is not None})
        await self._audit.append_audit(
            action=action, actor=actor, target=proposal.proposal_id, details=details
        )
        logger.info(
            "governance_proposal_transition",
            extra={
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type.value,
                "proposal_status": proposal.status.value,
                "actor": actor,
            },
        )
