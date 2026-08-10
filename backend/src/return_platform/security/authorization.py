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
from return_platform.security.roles import (
    ADMIN_ROLES,
    ASSOCIATE_ROLES,
    AUDIT_ROLES,
    LOGISTICS_ROLES,
    READ_ROLES,
    RETURN_COLLABORATION_ROLES,
    SUPPORT_ROLES,
    WAREHOUSE_ROLES,
    WRITE_ROLES,
)


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


# --- the named role dependencies --------------------------------------------
#
# These lived in `data_console/api/auth.py`, a shim Phase 17 left behind when it
# moved the role model here. Its own docstring said the sweep that deletes it is
# Wave F; this is that sweep. Thirty-two modules imported them through the shim
# and now import them from the module that already owned `require_roles`.
#
# `request: Request` is load-bearing, not decoration: FastAPI resolves
# `Depends(require_read_roles)` by inspecting the annotation, so widening it
# breaks injection at runtime in every one of those call sites.


def require_read_roles(request: Request) -> str:
    return str(require_roles(READ_ROLES)(request))


def require_write_roles(request: Request) -> str:
    return str(require_roles(WRITE_ROLES)(request))


def require_admin_roles(request: Request) -> str:
    return str(require_roles(ADMIN_ROLES)(request))


def require_associate_roles(request: Request) -> str:
    return str(require_roles(ASSOCIATE_ROLES)(request))


def require_support_roles(request: Request) -> str:
    return str(require_roles(SUPPORT_ROLES)(request))


def require_return_collaboration_roles(request: Request) -> str:
    return str(require_roles(RETURN_COLLABORATION_ROLES)(request))


def require_logistics_roles(request: Request) -> str:
    return str(require_roles(LOGISTICS_ROLES)(request))


def require_warehouse_roles(request: Request) -> str:
    return str(require_roles(WAREHOUSE_ROLES)(request))


def require_audit_roles(request: Request) -> str:
    return str(require_roles(AUDIT_ROLES)(request))
