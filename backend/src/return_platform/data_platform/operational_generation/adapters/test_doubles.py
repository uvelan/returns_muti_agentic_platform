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


class MockAdapter:
    @property
    def adapter_key(self) -> str:
        return "mock_adapter"

    @property
    def target_system(self) -> str:
        return "MOCK"

    @property
    def supported_operation_types(self) -> frozenset[OperationType]:
        return frozenset([OperationType.INSERT, OperationType.DOMAIN_COMMAND])

    @property
    def supported_asset_ids(self) -> frozenset[str]:
        return frozenset(
            [
                "source.mongodb.sales_inv",
                "source.mongodb.customer_outbound_cdm",
                "platform.mongodb.support_cases",
            ]
        )

    @property
    def transaction_capability(self) -> TransactionCapability:
        return TransactionCapability.NONE

    @property
    def compensation_capability(self) -> CompensationCapability:
        return CompensationCapability.NONE

    async def is_ready(self) -> bool:
        return True

    async def execute(self, operations: Sequence[Operation]) -> None:
        pass

    async def compensate(self, operations: Sequence[Operation]) -> None:
        pass


register_adapter("mock_adapter", MockAdapter)
