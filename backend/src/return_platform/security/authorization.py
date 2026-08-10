"""Capability enforcement for API routes.

Phase 17. `require_capability` is the canonical dependency for new surfaces;
`require_roles` is kept because 34 modules already depend on it through
`data_console/api/auth.py` and rewriting them all is Wave F's import sweep, not
this phase's.

Both answer 401 for an absent or unauthenticated principal and 403 for an
authenticated one that lacks the grant, matching the existing behaviour
exactly -- a status-code change here would silently alter how every current
router reports refusal.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from fastapi import HTTPException, Request

from return_platform.security.capabilities import capabilities_for_roles
from return_platform.security.principal import Principal


def resolve_principal(request: Request) -> Principal:
    """Return the request's principal or raise 401.

    Middleware sets `request.state.principal`; a route reached without it is
    unauthenticated rather than unauthorized.
    """
    principal = getattr(request.state, "principal", None)
    if not principal or not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal  # type: ignore[no-any-return]


def actor_roles(request: Request) -> frozenset[str]:
    """The caller's roles, for a check the route dependency cannot express.

    A `Depends(require_*_roles)` answers "may you call this endpoint" and returns
    only the subject. Where the *effect* of an endpoint depends on the payload --
    a support action that may or may not tender a BOL, say -- the handler needs
    the roles themselves to authorize the effect rather than the entrypoint.
    Raises 401 for the same reason `resolve_principal` does.
    """
    return frozenset(resolve_principal(request).roles)


def require_roles(allowed_roles: Collection[str]) -> Callable[[Request], str]:
    def dependency(request: Request) -> str:
        principal = resolve_principal(request)
        if not any(role in allowed_roles for role in principal.roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return str(principal.subject)

    return dependency


def require_capability(capability: str) -> Callable[[Request], str]:
    """Refuse the request unless the principal's roles grant `capability`.

    Returns the subject so a handler can attribute the action, matching what
    the role dependencies already return.
    """

    def dependency(request: Request) -> str:
        principal = resolve_principal(request)
        if capability not in capabilities_for_roles(principal.roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return str(principal.subject)

    return dependency
