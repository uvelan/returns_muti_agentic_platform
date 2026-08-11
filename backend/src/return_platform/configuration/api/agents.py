"""Per-agent configuration: read, and edit.

**Not mounted under `/api/config`, on purpose.** That surface holds one
invariant worth keeping -- promotion is its only mutation, because
configuration changes by a release moving along its lifecycle and a second
write path there would be a second way to change what the platform runs.

These edits are a different thing, and saying so in the path is more honest
than bending that rule. Agent modules are *files*, loaded from `manifest.yaml`
at boot; they have never been part of the graph-stored release lifecycle, so
this is the first way to edit that store rather than a second way to edit the
other one.

**It does mean an agent edit takes effect without an approval step**, which is
a real difference from every other configuration change on this platform and
should be closed by bringing agent modules into the release lifecycle rather
than by moving this endpoint somewhere quieter.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.api.secrets import redact_secret_values
from return_platform.configuration.application.agent_configuration import (
    AgentConfigurationService,
    AgentConfigurationView,
    AgentSummary,
)
from return_platform.security.authorization import require_read_roles, require_write_roles
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


@router.put("/{manifest_id}", response_model=APIResponse[AgentConfigurationView])
async def update_agent_configuration(
    manifest_id: str,
    payload: AgentConfigurationUpdate,
    request: Request,
    _user_id: str = Depends(require_write_roles),
) -> APIResponse[Any]:
    """Replace one agent's document.

    A rejected document comes back as 422 carrying the loader's own reason. The
    editor needs to know *why* it was refused -- "invalid configuration" gives
    an operator nothing to correct, and the loader's message names the field.
    """
    try:
        return _ok(request, _agents(request).write(manifest_id, payload.document))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "AGENT_CONFIGURATION_REJECTED",
                "message": str(error),
                "retryable": False,
            },
        ) from error
