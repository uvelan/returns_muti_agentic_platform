"""The canonical principal surface: `/api/principal`.

Phase 17. Versionless, matching `/api/returns`, `/api/config`, `/api/ai` and
`/api/graph-schema`.

**Why this endpoint exists.** The four-domain shell has to decide which domains
to show before the user clicks anything, and Phases 18-21 each need to know
whether to enable an action, make a field editable, or offer an approval.
Without this, every screen either guesses or hardcodes role names -- and a
hardcoded role table in the frontend is a second authorization model that
drifts from the enforcing one silently.

**This endpoint grants nothing.** It reports what the caller already has, so
the UI can avoid offering an action that would be refused. Every capability it
names is independently enforced server-side by `require_capability` on the
route that performs the action. Hiding a button is presentation; the refusal is
the backend's.

**Deliberately not included:** any list of *other* principals, roles the caller
does not hold, or the role-to-capability table itself. A UI needs to know what
it may do, not how the mapping is shaped, and publishing the table would make
the role model part of the frontend contract -- exactly what the capability
layer exists to prevent.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from return_platform.security.authorization import resolve_principal
from return_platform.security.capabilities import capabilities_for_roles
from return_platform.shared.contracts import APIResponse, ContractModel, ResponseMeta

router = APIRouter(prefix="/api/principal", tags=["Principal"])


class PrincipalView(ContractModel):
    """Typed so it lands in the OpenAPI snapshot the frontend generates from.

    `roles` is reported alongside `capabilities` for display and support
    ("signed in as X with role Y"), not for the UI to branch on -- the
    capability list is what decides whether an action is offered.
    """

    subject: str
    roles: list[str]
    capabilities: list[str]


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


@router.get(
    "",
    response_model=APIResponse[PrincipalView],
    summary="The calling principal, its roles, and its capabilities",
)
async def get_principal(request: Request) -> APIResponse[PrincipalView]:
    """401 when unauthenticated -- never an anonymous principal with no grants.

    Returning an empty capability set for an unauthenticated caller would make
    "not signed in" and "signed in with nothing granted" indistinguishable, and
    the shell needs to tell those apart to decide between a sign-in prompt and
    an empty state.
    """
    principal = resolve_principal(request)
    capabilities = capabilities_for_roles(principal.roles)

    return APIResponse(
        data=PrincipalView(
            subject=principal.subject,
            roles=sorted(principal.roles),
            capabilities=sorted(capabilities),
        ),
        meta=_meta(request),
    )
