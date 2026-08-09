"""Who the caller is: `GET /api/session`.

The last thing Wave D owed Wave E. The platform has always resolved a
`Principal` per request — middleware sets `request.state.principal` and every
`require_*_roles` dependency reads it — but nothing ever returned it. The
frontend consequently had no way to know who the user is or what they may do, so
its capability hook **fails open**: it reports `granted` when no principal is
available, because failing closed would blank the console for everyone until
this endpoint existed.

That is the gap this closes. With a real principal the frontend can hide what a
user cannot do, and — more importantly — stop pretending it knows.

**This does not make the frontend an authorization boundary.** Backend
authorization is and remains authoritative: every route enforces its own roles
through `require_read_roles`/`require_write_roles`, and a caller who forges a
capability list client-side gains nothing. What this endpoint provides is
*presentation* input — which is exactly why it returns the caller's own
capabilities and nothing about anyone else.

**Roles are translated to capabilities here, not in the client.** The frontend
previously mirrored `READ_ROLES`/`WRITE_ROLES` in TypeScript
(`shared/rbac.ts`), which is a second copy of an authorization rule that can
drift from the Python one silently. Deriving them server-side from the same
frozensets the dependencies use means there is one definition.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from return_platform.security.principal import Principal
from return_platform.security.roles import READ_ROLES, WRITE_ROLES
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/session", tags=["Session"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def capabilities_for(roles: frozenset[str]) -> list[str]:
    """The caller's capabilities, derived from the same sets the routes enforce.

    Deliberately coarse — `config:read`, `config:write` and so on — because that
    is the granularity the backend actually enforces. Emitting finer-grained
    capabilities than the routes check would let the UI promise precision the
    server does not deliver, which is worse than being coarse and honest.
    """
    granted: list[str] = []
    if roles & READ_ROLES:
        granted.extend(["returns:read", "config:read", "graph-schema:read", "ai:read"])
    if roles & WRITE_ROLES:
        granted.extend(["returns:write", "config:write", "graph-schema:write", "ai:write"])
    return sorted(granted)


@router.get("", response_model=APIResponse[dict[str, Any]])
async def get_session(request: Request) -> APIResponse[Any]:
    """The caller's own identity and capabilities.

    **No role dependency on purpose.** Guarding this with `require_read_roles`
    would make it answer 403 for exactly the caller who most needs an answer —
    someone signed in with no usable role. Such a caller gets an empty
    capability list, which the UI can render as "you have no access" instead of
    a blank screen with a failed request.

    A caller with *no principal at all* is a different case: middleware could not
    identify them, so there is nothing truthful to return and 401 is the honest
    answer.
    """
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No authenticated principal"
        )
    return APIResponse(
        data={
            "subject": principal.subject,
            "roles": sorted(principal.roles),
            "capabilities": capabilities_for(principal.roles),
        },
        meta=_meta(request),
    )
