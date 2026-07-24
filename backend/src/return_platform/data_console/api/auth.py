from collections.abc import Collection, Callable
from typing import Final

from fastapi import HTTPException, Request

# Standard Data Console Roles
READ_ROLES: Final = frozenset(
    {"console_admin", "console_viewer", "workspace_editor", "workspace_viewer"}
)
WRITE_ROLES: Final = frozenset({"console_admin", "workspace_editor"})
ADMIN_ROLES: Final = frozenset({"console_admin"})


def require_roles(allowed_roles: Collection[str]) -> Callable[[Request], str]:
    def dependency(request: Request) -> str:
        principal = getattr(request.state, "principal", None)
        if not principal or not principal.is_authenticated:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not any(role in allowed_roles for role in principal.roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return str(principal.subject)

    return dependency


def require_read_roles(request: Request) -> str:
    return str(require_roles(READ_ROLES)(request))


def require_write_roles(request: Request) -> str:
    return str(require_roles(WRITE_ROLES)(request))
