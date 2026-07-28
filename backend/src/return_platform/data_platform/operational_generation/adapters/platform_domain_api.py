import os
from collections.abc import Sequence

from return_platform.data_platform.operational_generation.capability import (
    CompensationCapability,
    TransactionCapability,
)
from return_platform.data_platform.operational_generation.write_models import (
    Operation,
    OperationType,
)

from .registry import register_adapter


class PlatformDomainApiAdapter:
    @property
    def adapter_key(self) -> str:
        return "domain_api"

    @property
    def target_system(self) -> str:
        return "RETURNS_PLATFORM_API"

    @property
    def supported_operation_types(self) -> frozenset[OperationType]:
        return frozenset([OperationType.DOMAIN_COMMAND])

    @property
    def supported_asset_ids(self) -> frozenset[str]:
        return frozenset(
            [
                "platform.mongodb.support_cases",
            ]
        )

    @property
    def transaction_capability(self) -> TransactionCapability:
        return TransactionCapability.NONE

    @property
    def compensation_capability(self) -> CompensationCapability:
        return CompensationCapability.DOMAIN_COMPENSATE

    async def is_ready(self) -> bool:
        if not os.environ.get("VAULT_DOMAIN_API_CREDENTIALS"):
            return False
        return True

    async def execute(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")

    async def compensate(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")


register_adapter("domain_api", PlatformDomainApiAdapter)
