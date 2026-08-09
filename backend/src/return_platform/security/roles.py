"""The canonical role model.

Phase 17. This is a *move*, not a new invention.

The plan states "the role model does not exist" and instructs this phase to
build it. That premise is inaccurate and was corrected rather than followed:
nine roles and ten role-group dependencies already existed in
`data_console/api/auth.py`, wired into 34 importing modules. Reinventing them
would have produced a second, competing role vocabulary and left the enforcing
one in place -- the exact failure Phase 15 documents for configuration release
lifecycles.

What genuinely did not exist is (a) a canonical home for the model, since Data
Console is retired in Wave F, and (b) a capability layer -- see
`capabilities.py` for why a UI must not branch on role names.

The frozensets below are byte-identical in membership to the ones they replace.
`data_console/api/auth.py` is now a re-export shim over this module, the same
pattern D1 used for `ai_gateway/`, so all 34 importers keep working and the
shim stays deletable in Wave F.
"""

from __future__ import annotations

from typing import Final

CONSOLE_ADMIN: Final = "console_admin"
CONSOLE_VIEWER: Final = "console_viewer"
WORKSPACE_EDITOR: Final = "workspace_editor"
WORKSPACE_VIEWER: Final = "workspace_viewer"
RETURN_ASSOCIATE: Final = "return_associate"
RETURN_SUPPORT: Final = "return_support"
LOGISTICS_COORDINATOR: Final = "logistics_coordinator"
WAREHOUSE_ASSOCIATE: Final = "warehouse_associate"
RETURN_AUDITOR: Final = "return_auditor"
RETURN_PLATFORM_SERVICE: Final = "return_platform_service"

ALL_ROLES: Final = frozenset(
    {
        CONSOLE_ADMIN,
        CONSOLE_VIEWER,
        WORKSPACE_EDITOR,
        WORKSPACE_VIEWER,
        RETURN_ASSOCIATE,
        RETURN_SUPPORT,
        LOGISTICS_COORDINATOR,
        WAREHOUSE_ASSOCIATE,
        RETURN_AUDITOR,
        RETURN_PLATFORM_SERVICE,
    }
)

CONSOLE_READ_ROLES: Final = frozenset(
    {CONSOLE_ADMIN, CONSOLE_VIEWER, WORKSPACE_EDITOR, WORKSPACE_VIEWER}
)
BUSINESS_READ_ROLES: Final = frozenset(
    {
        RETURN_ASSOCIATE,
        RETURN_SUPPORT,
        LOGISTICS_COORDINATOR,
        WAREHOUSE_ASSOCIATE,
        RETURN_AUDITOR,
        RETURN_PLATFORM_SERVICE,
    }
)
READ_ROLES: Final = CONSOLE_READ_ROLES | BUSINESS_READ_ROLES
WRITE_ROLES: Final = frozenset(
    {
        CONSOLE_ADMIN,
        WORKSPACE_EDITOR,
        RETURN_ASSOCIATE,
        RETURN_SUPPORT,
        LOGISTICS_COORDINATOR,
        WAREHOUSE_ASSOCIATE,
        RETURN_PLATFORM_SERVICE,
    }
)
ADMIN_ROLES: Final = frozenset({CONSOLE_ADMIN})
ASSOCIATE_ROLES: Final = frozenset({CONSOLE_ADMIN, RETURN_ASSOCIATE, RETURN_PLATFORM_SERVICE})
SUPPORT_ROLES: Final = frozenset({CONSOLE_ADMIN, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE})
LOGISTICS_ROLES: Final = frozenset(
    {CONSOLE_ADMIN, LOGISTICS_COORDINATOR, RETURN_PLATFORM_SERVICE}
)
WAREHOUSE_ROLES: Final = frozenset({CONSOLE_ADMIN, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE})
AUDIT_ROLES: Final = frozenset({CONSOLE_ADMIN, RETURN_AUDITOR, RETURN_PLATFORM_SERVICE})
RETURN_COLLABORATION_ROLES: Final = ASSOCIATE_ROLES | SUPPORT_ROLES
