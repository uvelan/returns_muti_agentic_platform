"""`GET /api/runtime-config` -- the shell's bootstrap payload.

Design doc section 9.5 places this on the versionless canonical surface, and
section 12's migration table maps `api/runtime_config.py` here. It served
`/api/v1/runtime-config` until then, which left the four-domain shell unable to
boot without a `/api/v1` route -- the last such dependency, and the reason the
README carried a "known leftover" note.

The two vocabulary lists are derived, not retyped. Both existed as literals in
this module *and* at the place that actually enforces them, which is a drift the
type checker cannot see: a client is validated against the enforcing definition,
never against the advertisement.
"""

from __future__ import annotations

from typing import Literal, get_args

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from return_platform.configuration.bootstrap_runtime_integrations import HOSTED_AI_PROVIDERS
from return_platform.configuration.runtime_validation import DataSourceValidateAndStageRequest
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api", tags=["runtime-config"])

#: The source types a client may actually submit, read off the request model
#: that rejects everything else.
_AVAILABLE_SOURCE_TYPES: tuple[str, ...] = tuple(
    get_args(DataSourceValidateAndStageRequest.model_fields["sourceType"].annotation)
)


class RuntimeConfigFeatures(BaseModel):
    orderDiscoveryCopilot: bool


class RuntimeConfigCapabilities(BaseModel):
    availableSourceTypes: tuple[str, ...]
    availableModelProviders: tuple[str, ...]


class RuntimeConfig(BaseModel):
    releaseId: str
    environment: str
    apiBasePath: Literal["/api"]
    features: RuntimeConfigFeatures
    capabilities: RuntimeConfigCapabilities


@router.get(
    "/runtime-config",
    response_model=APIResponse[RuntimeConfig],
    status_code=status.HTTP_200_OK,
)
async def get_runtime_config(request: Request) -> APIResponse[RuntimeConfig]:
    """Safe, non-secret configuration the shell can read before authenticating."""
    settings = request.app.state.settings
    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)

    return APIResponse[RuntimeConfig](
        data=RuntimeConfig(
            releaseId=getattr(snapshot, "release_id", "unknown") if snapshot else "unknown",
            environment=settings.environment,
            # Was "/api/v1", which stopped being true when Wave F made the
            # versionless surface canonical. A literal type, so it cannot drift
            # back without the contract changing.
            apiBasePath="/api",
            features=RuntimeConfigFeatures(
                # Was hardcoded `True` next to an `aiStudioOperationalGeneration`
                # flag for a feature Wave F deleted. This one is a real setting.
                orderDiscoveryCopilot=settings.dynamic_order_agent_enabled,
            ),
            capabilities=RuntimeConfigCapabilities(
                availableSourceTypes=_AVAILABLE_SOURCE_TYPES,
                availableModelProviders=HOSTED_AI_PROVIDERS,
            ),
        ),
        meta=ResponseMeta(request_id=getattr(request.state, "correlation_id", "unknown")),
    )
