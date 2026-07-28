from enum import StrEnum

from fastapi import HTTPException, status


class OperationalGenerationPermission(StrEnum):
    GENERATE = "AI_STUDIO_GENERATE"
    VALIDATE = "AI_STUDIO_VALIDATE"
    PLAN = "AI_STUDIO_PLAN"
    APPROVE = "AI_STUDIO_APPROVE"
    APPLY_OPERATIONAL = "AI_STUDIO_APPLY_OPERATIONAL"
    ROLLBACK_OPERATIONAL = "AI_STUDIO_ROLLBACK_OPERATIONAL"
    VIEW_OPERATIONAL = "AI_STUDIO_VIEW_OPERATIONAL"


def require_permission(
    actor_permissions: list[str], required: OperationalGenerationPermission
) -> None:
    if required not in actor_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing required permission: {required}"
        )
