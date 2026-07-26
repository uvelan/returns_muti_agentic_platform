from collections.abc import Callable, Collection
from typing import Final

from fastapi import HTTPException, Request

CONSOLE_READ_ROLES: Final = frozenset(
    {"console_admin", "console_viewer", "workspace_editor", "workspace_viewer"}
)
BUSINESS_READ_ROLES: Final = frozenset(
    {
        "return_associate",
        "return_support",
        "logistics_coordinator",
        "warehouse_associate",
        "return_auditor",
        "return_platform_service",
    }
)
READ_ROLES: Final = CONSOLE_READ_ROLES | BUSINESS_READ_ROLES
WRITE_ROLES: Final = frozenset(
    {
        "console_admin",
        "workspace_editor",
        "return_associate",
        "return_support",
        "logistics_coordinator",
        "warehouse_associate",
        "return_platform_service",
    }
)
ADMIN_ROLES: Final = frozenset({"console_admin"})
ASSOCIATE_ROLES: Final = frozenset({"console_admin", "return_associate", "return_platform_service"})
SUPPORT_ROLES: Final = frozenset({"console_admin", "return_support", "return_platform_service"})
LOGISTICS_ROLES: Final = frozenset(
    {"console_admin", "logistics_coordinator", "return_platform_service"}
)
WAREHOUSE_ROLES: Final = frozenset(
    {"console_admin", "warehouse_associate", "return_platform_service"}
)
AUDIT_ROLES: Final = frozenset({"console_admin", "return_auditor", "return_platform_service"})
RETURN_COLLABORATION_ROLES: Final = ASSOCIATE_ROLES | SUPPORT_ROLES


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
