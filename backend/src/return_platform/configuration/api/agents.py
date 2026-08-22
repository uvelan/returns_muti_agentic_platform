"""Per-agent configuration: read, and propose an edit.

**Not mounted under `/api/config`, on purpose.** That surface holds one
invariant worth keeping -- promotion is its only mutation, because
configuration changes by a release moving along its lifecycle and a second
write path there would be a second way to change what the platform runs.

These edits are a different thing, and saying so in the path is more honest
than bending that rule. Agent modules are declared in `manifest.yaml`; until
W4.2 they had never been part of the graph-stored release lifecycle, so this was
the first way to edit that store rather than a second way to edit the other one.

**W4.2 closed the gap this file's own docstring named.** It used to end: "It does
mean an agent edit takes effect without an approval step, which is a real
difference from every other configuration change on this platform and should be
closed by bringing agent modules into the release lifecycle rather than by moving
this endpoint somewhere quieter." That is what happened. `PUT` no longer changes
anything -- it validates the document and submits a `CONFIGURATION` proposal.
Approval and activation happen on `/api/proposals`, the same queue a schema
change and a feedback improvement wait in, and activation publishes a
configuration release rather than rewriting a packaged file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.api.secrets import redact_secret_values
from return_platform.configuration.application.agent_configuration import (
    AgentConfigurationService,
    AgentConfigurationView,
    AgentSummary,
)
from return_platform.platform.governance.errors import GovernanceError
from return_platform.platform.governance.kernel import ProposalKernel
from return_platform.platform.governance.proposal import ProposalStatus, ProposalType
from return_platform.security.authorization import require_capability, require_read_roles
from return_platform.security.capabilities import GOVERNANCE_PROPOSAL_WRITE
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/agents", tags=["Agents"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=redact_secret_values(data), meta=_meta(request))


def _agents(request: Request) -> AgentConfigurationService:
    service = getattr(request.app.state, "agent_configuration", None)
    if isinstance(service, AgentConfigurationService):
        return service
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "AGENT_CONFIGURATION_UNAVAILABLE",
            "message": "Agent configuration is not available in this process.",
            "retryable": True,
        },
    )


def _kernel(request: Request) -> ProposalKernel:
    kernel = getattr(request.app.state, "proposal_kernel", None)
    if isinstance(kernel, ProposalKernel):
        return kernel
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "GOVERNANCE_UNAVAILABLE",
            "message": (
                "The proposal kernel is not available, so an agent configuration change "
                "cannot be recorded for review. The edit is refused rather than applied "
                "ungoverned."
            ),
            "retryable": True,
        },
    )


@router.get("", response_model=APIResponse[list[AgentSummary]])
async def list_agents(
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    return _ok(request, _agents(request).list_agents())


@router.get("/{manifest_id}", response_model=APIResponse[AgentConfigurationView])
async def get_agent_configuration(
    manifest_id: str,
    request: Request,
    _user_id: str = Depends(require_read_roles),
) -> APIResponse[Any]:
    configuration = _agents(request).read(manifest_id)
    if configuration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AGENT_NOT_FOUND",
                "message": f"{manifest_id} is not a configured agent.",
                "retryable": False,
            },
        )
    return _ok(request, configuration)


class AgentConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]


class AgentConfigurationProposalView(BaseModel):
    """What a `PUT` now answers with: a proposal, not a document.

    The response type changed deliberately and visibly. Answering with the
    edited `AgentConfigurationView` would tell an operator their change had been
    applied, which is precisely what it no longer does -- and a screen that
    believes it would never send anyone to the review queue.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposalId: str
    manifestId: str
    status: ProposalStatus
    risk: str
    affectedKeys: tuple[str, ...]
    proposedBy: str
    submittedAt: datetime


@router.put(
    "/{manifest_id}",
    response_model=APIResponse[AgentConfigurationProposalView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_agent_configuration(
    manifest_id: str,
    payload: AgentConfigurationUpdate,
    request: Request,
    # `governance.proposal.write`, not `require_write_roles`. Seven roles pass
    # the role check and two hold the capability, so the server was more
    # permissive than the editor in front of it -- which disables every field
    # without this capability and says so. The capability was declared and
    # enforced nowhere in the backend.
    user_id: str = Depends(require_capability(GOVERNANCE_PROPOSAL_WRITE)),
) -> APIResponse[Any]:
    """Propose a replacement for one agent's document.

    202, not 200: the platform has accepted the change for review and has not
    made it. A rejected document still comes back as 422 carrying the loader's
    own reason -- the editor needs to know *why* it was refused, and "invalid
    configuration" gives an operator nothing to correct.
    """
    service = _agents(request)
    if service.read(manifest_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AGENT_NOT_FOUND",
                "message": f"{manifest_id} is not a configured agent.",
                "retryable": False,
            },
        )
    try:
        proposal = await service.propose(
            manifest_id,
            payload.document,
            kernel=_kernel(request),
            actor=user_id,
            occurred_at=datetime.now(UTC),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AGENT_CONFIGURATION_REJECTED",
                "message": str(error),
                "retryable": False,
            },
        ) from error
    except GovernanceError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AGENT_CONFIGURATION_REFUSED",
                "message": str(error),
                "retryable": False,
            },
        ) from error
    assert proposal.proposal_type is ProposalType.CONFIGURATION
    return _ok(
        request,
        AgentConfigurationProposalView(
            proposalId=proposal.proposal_id,
            manifestId=manifest_id,
            status=proposal.status,
            risk=proposal.risk.value,
            affectedKeys=proposal.affected_keys,
            proposedBy=proposal.proposed_by,
            submittedAt=proposal.created_at,
        ),
    )
