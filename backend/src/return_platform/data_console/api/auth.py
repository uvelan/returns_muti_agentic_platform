"""Deprecated re-export shim over `return_platform.security`.

Phase 17 moved the role model to `security/roles.py` and the enforcement
dependencies to `security/authorization.py`. Data Console is retired in Wave F,
so the model could not keep living here.

34 modules import this path. This shim keeps every one of them working
unchanged; the import sweep that deletes it is Wave F, the same treatment D1
gave `ai_gateway/`. Nothing new should import from here.

The `request: Request` annotations below are load-bearing, not decoration:
FastAPI resolves `Depends(require_read_roles)` by inspecting the annotation, so
widening it breaks injection at runtime in every one of those 34 modules.
"""

from __future__ import annotations

from fastapi import Request

from return_platform.security.authorization import require_roles
from return_platform.security.roles import (
    ADMIN_ROLES,
    ASSOCIATE_ROLES,
    AUDIT_ROLES,
    BUSINESS_READ_ROLES,
    CONSOLE_READ_ROLES,
    LOGISTICS_ROLES,
    READ_ROLES,
    RETURN_COLLABORATION_ROLES,
    SUPPORT_ROLES,
    WAREHOUSE_ROLES,
    WRITE_ROLES,
)

__all__ = [
    "ADMIN_ROLES",
    "ASSOCIATE_ROLES",
    "AUDIT_ROLES",
    "BUSINESS_READ_ROLES",
    "CONSOLE_READ_ROLES",
    "LOGISTICS_ROLES",
    "READ_ROLES",
    "RETURN_COLLABORATION_ROLES",
    "SUPPORT_ROLES",
    "WAREHOUSE_ROLES",
    "WRITE_ROLES",
    "require_admin_roles",
    "require_associate_roles",
    "require_audit_roles",
    "require_logistics_roles",
    "require_read_roles",
    "require_return_collaboration_roles",
    "require_roles",
    "require_support_roles",
    "require_warehouse_roles",
    "require_write_roles",
]


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
