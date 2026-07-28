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


class DirectMongoDbAdapter:
    @property
    def adapter_key(self) -> str:
        return "direct_operational"

    @property
    def target_system(self) -> str:
        return "RETURNS_PLATFORM_MONGODB"

    @property
    def supported_operation_types(self) -> frozenset[OperationType]:
        return frozenset([OperationType.INSERT])

    @property
    def supported_asset_ids(self) -> frozenset[str]:
        return frozenset(
            [
                "platform.mongodb.operational_returns",
                "platform.mongodb.operational_events",
            ]
        )

    @property
    def transaction_capability(self) -> TransactionCapability:
        return TransactionCapability.MULTI_COLLECTION

    @property
    def compensation_capability(self) -> CompensationCapability:
        return CompensationCapability.DELETE

    async def is_ready(self) -> bool:
        if not os.environ.get("VAULT_DIRECT_OPERATIONAL_CREDENTIALS"):
            return False
        return True

    async def execute(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")

    async def compensate(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")


register_adapter("direct_operational", DirectMongoDbAdapter)
