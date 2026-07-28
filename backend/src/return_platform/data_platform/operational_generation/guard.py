from collections.abc import Callable
from typing import Any

from return_platform.data_platform.schema_registry import SchemaRegistry

from .models import OperationProposal, ValidationResult
from .validator import ExistenceResolver, validate_proposal


class HallucinationGuard:
    def __init__(self, registry: SchemaRegistry):
        self.registry = registry

    def validate(
        self,
        proposal: OperationProposal,
        resolver: ExistenceResolver | None = None,
        tenant_id: str | None = None,
        pii_validator: Callable[[str, Any], bool] | None = None
    ) -> ValidationResult:
        return validate_proposal(
            self.registry,
            proposal,
            resolver=resolver,
            tenant_id=tenant_id,
            pii_validator=pii_validator
        )
