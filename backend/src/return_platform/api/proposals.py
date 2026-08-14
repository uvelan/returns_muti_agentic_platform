"""`/api/proposals` — the one inbox (plan section 5B, S9).

Every change a human has to sign off arrives here: a validated graph schema, an
agent configuration edit, a feedback improvement. One surface because they are
one decision queue -- a reviewer asking "what is waiting on me" should not have
to know which subsystem produced it, and a platform with three approval screens
has three places for a change to sit unnoticed.

**This router owns no lifecycle.** Every handler calls `ProposalKernel`, which is
the same object the analyzer's approve route and the agent configuration surface
call. A second implementation of "approve" here would be exactly the failure the
step was written to prevent.

Reading requires the governance read capability; deciding requires the approval
one; activating requires the activation one. `require_write_roles` would have
been enough to mount this, and would have said that anyone who can edit anything
may approve everything.

**The capability parameter comes first in every signature.** FastAPI resolves a
handler's dependencies in the order its parameters are declared, so a `_Kernel`
declared ahead of the grant made `resolve_proposal_kernel` the first gate: an
anonymous caller got 503 GOVERNANCE_UNAVAILABLE and thereby learned whether
governance is composed in this process. The grants are therefore spelled as
`Annotated` aliases -- an `= Depends(...)` default cannot precede a parameter
that has none, so the syntax was quietly enforcing the wrong order.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.api.secrets import redact_secret_values
from return_platform.platform.governance.errors import (
    ActivationRefused,
    ForbiddenProposalKey,
    GovernanceError,
    InvalidProposalTransition,
    ProposalConcurrentModification,
    ProposalIntegrityError,
    UnknownProposal,
)
from return_platform.platform.governance.kernel import NoActivatorRegistered, ProposalKernel
from return_platform.platform.governance.proposal import (
    Proposal,
    ProposalStatus,
    ProposalType,
)
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import (
    GOVERNANCE_PROPOSAL_ACTIVATE,
    GOVERNANCE_PROPOSAL_APPROVE,
    GOVERNANCE_PROPOSAL_READ,
)
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/proposals", tags=["Governance"])


def resolve_proposal_kernel(request: Request) -> ProposalKernel:
    kernel = getattr(request.app.state, "proposal_kernel", None)
    if not isinstance(kernel, ProposalKernel):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GOVERNANCE_UNAVAILABLE",
                "message": "The proposal kernel is not available in this process.",
                "retryable": True,
            },
        )
    return kernel


_Kernel = Annotated[ProposalKernel, Depends(resolve_proposal_kernel)]

#: Declare one of these ahead of `_Kernel`, never after it. See the module
#: docstring: ordering is what makes authorization the first gate.
_Reader = Annotated[str, Depends(require_capability(GOVERNANCE_PROPOSAL_READ))]
_Decider = Annotated[str, Depends(require_capability(GOVERNANCE_PROPOSAL_APPROVE))]
_Activator = Annotated[str, Depends(require_capability(GOVERNANCE_PROPOSAL_ACTIVATE))]


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


class ProposalSummaryView(BaseModel):
    """What a queue row needs. Deliberately without `before`/`after`.

    A proposal's documents are unbounded -- a graph schema is the whole shape --
    and putting them inline would make listing the inbox pay for a payload only
    the detail screen reads.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposalId: str
    proposalType: ProposalType
    subjectId: str
    title: str
    status: ProposalStatus
    risk: str
    affectedKeys: tuple[str, ...]
    proposedBy: str
    decidedBy: str | None
    createdAt: datetime
    updatedAt: datetime


class ProposalDetailView(ProposalSummaryView):
    model_config = ConfigDict(frozen=True, extra="forbid")

    before: dict[str, Any]
    after: dict[str, Any]
    diff: tuple[dict[str, Any], ...]
    evidence: tuple[str, ...]
    evidenceDigest: str
    validationReceipt: str | None
    decisionNote: str | None
    activationReference: str | None
    history: tuple[dict[str, Any], ...]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class ActivationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Passed through to the type's activator. Typed as a free mapping because
    #: each activator owns its own options (the graph target's `activate` flag,
    #: for one) and enumerating them here would put a second copy of each
    #: activator's contract in the router.
    parameters: dict[str, Any] = Field(default_factory=dict)


def _summary(proposal: Proposal) -> ProposalSummaryView:
    return ProposalSummaryView(
        proposalId=proposal.proposal_id,
        proposalType=proposal.proposal_type,
        subjectId=proposal.subject_id,
        title=proposal.title,
        status=proposal.status,
        risk=proposal.risk.value,
        affectedKeys=proposal.affected_keys,
        proposedBy=proposal.proposed_by,
        decidedBy=proposal.decided_by,
        createdAt=proposal.created_at,
        updatedAt=proposal.updated_at,
    )


def _detail(proposal: Proposal) -> ProposalDetailView:
    """Secret *references* survive; resolved values never appear in a proposal
    to begin with, and `redact_secret_values` is the same belt the configuration
    surface wears -- a proposal over a configuration document is exactly the
    payload that would otherwise carry one."""
    document = proposal.model_dump(mode="json")
    return ProposalDetailView(
        **_summary(proposal).model_dump(),
        before=redact_secret_values(document["before"]),
        after=redact_secret_values(document["after"]),
        diff=tuple(redact_secret_values(entry) for entry in document["diff"]),
        evidence=proposal.evidence,
        evidenceDigest=proposal.evidence_digest,
        validationReceipt=proposal.validation_receipt,
        decisionNote=proposal.decision_note,
        activationReference=proposal.activation_reference,
        history=tuple(document["history"]),
    )


def _governance_error(exc: GovernanceError) -> HTTPException:
    """One mapping, so a new handler cannot invent a different status for the
    same refusal."""
    if isinstance(exc, UnknownProposal):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UNKNOWN_PROPOSAL", "message": str(exc)},
        )
    if isinstance(exc, ForbiddenProposalKey | ProposalIntegrityError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "PROPOSAL_REFUSED", "message": str(exc)},
        )
    if isinstance(exc, NoActivatorRegistered):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ACTIVATOR_UNAVAILABLE", "message": str(exc)},
        )
    if isinstance(exc, ActivationRefused):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "ACTIVATION_REFUSED", "message": str(exc)},
        )
    if isinstance(exc, InvalidProposalTransition | ProposalConcurrentModification):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_TRANSITION", "message": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "PROPOSAL_REFUSED", "message": str(exc)},
    )


@router.get("", response_model=APIResponse[list[ProposalSummaryView]])
async def list_proposals(
    request: Request,
    _user_id: _Reader,
    kernel: _Kernel,
    proposal_type: Annotated[ProposalType | None, Query(alias="type")] = None,
    proposal_status: Annotated[ProposalStatus | None, Query(alias="status")] = None,
    subject_id: Annotated[str | None, Query(alias="subjectId")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> APIResponse[Sequence[ProposalSummaryView]]:
    proposals = await kernel.list(
        proposal_type=proposal_type,
        status=proposal_status,
        subject_id=subject_id,
        limit=limit,
    )
    return APIResponse(data=[_summary(item) for item in proposals], meta=_meta(request))


@router.get("/{proposal_id}", response_model=APIResponse[ProposalDetailView])
async def get_proposal(
    proposal_id: str,
    request: Request,
    _user_id: _Reader,
    kernel: _Kernel,
) -> APIResponse[ProposalDetailView]:
    try:
        proposal = await kernel.get(proposal_id)
    except GovernanceError as exc:
        raise _governance_error(exc) from exc
    return APIResponse(data=_detail(proposal), meta=_meta(request))


@router.post("/{proposal_id}/approve", response_model=APIResponse[ProposalDetailView])
async def approve_proposal(
    proposal_id: str,
    payload: DecisionRequest,
    request: Request,
    actor: _Decider,
    kernel: _Kernel,
) -> APIResponse[ProposalDetailView]:
    try:
        proposal = await kernel.approve(
            proposal_id, actor=actor, occurred_at=datetime.now(UTC), note=payload.note
        )
    except GovernanceError as exc:
        raise _governance_error(exc) from exc
    return APIResponse(data=_detail(proposal), meta=_meta(request))


@router.post("/{proposal_id}/reject", response_model=APIResponse[ProposalDetailView])
async def reject_proposal(
    proposal_id: str,
    payload: DecisionRequest,
    request: Request,
    actor: _Decider,
    kernel: _Kernel,
) -> APIResponse[ProposalDetailView]:
    try:
        proposal = await kernel.reject(
            proposal_id, actor=actor, occurred_at=datetime.now(UTC), note=payload.note
        )
    except GovernanceError as exc:
        raise _governance_error(exc) from exc
    return APIResponse(data=_detail(proposal), meta=_meta(request))


@router.post("/{proposal_id}/activate", response_model=APIResponse[ProposalDetailView])
async def activate_proposal(
    proposal_id: str,
    payload: ActivationRequest,
    request: Request,
    actor: _Activator,
    kernel: _Kernel,
) -> APIResponse[ProposalDetailView]:
    """Carry an approved change into the runtime.

    Separate from approval, and separately granted. Approving says the change is
    right; activating says now is the moment -- and on this platform that second
    act publishes a configuration release, which is not a decision the reviewer
    of a wording change should be making by side effect.
    """
    try:
        proposal, _ = await kernel.activate(
            proposal_id,
            actor=actor,
            occurred_at=datetime.now(UTC),
            parameters=payload.parameters,
        )
    except GovernanceError as exc:
        raise _governance_error(exc) from exc
    return APIResponse(data=_detail(proposal), meta=_meta(request))
