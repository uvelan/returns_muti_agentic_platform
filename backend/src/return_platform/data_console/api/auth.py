from collections.abc import Collection
from typing import Final

from fastapi import Depends, HTTPException, Request

# Standard Data Console Roles
READ_ROLES: Final = frozenset(
    {"console_admin", "console_viewer", "workspace_editor", "workspace_viewer"}
)
WRITE_ROLES: Final = frozenset({"console_admin", "workspace_editor"})
ADMIN_ROLES: Final = frozenset({"console_admin"})


def require_roles(allowed_roles: Collection[str]):
    def _require_roles_dependency(request: Request) -> str:
        principal = getattr(request.state, "principal", None)
        if not principal:
            raise HTTPException(status_code=401, detail="Unauthenticated")

        if not any(role in allowed_roles for role in principal.roles):
            raise HTTPException(status_code=403, detail="Unauthorized")

        return principal.subject

    return _require_roles_dependency


def require_read_roles(request: Request) -> str:
    return require_roles(READ_ROLES)(request)


def require_write_roles(request: Request) -> str:
    return require_roles(WRITE_ROLES)(request)
