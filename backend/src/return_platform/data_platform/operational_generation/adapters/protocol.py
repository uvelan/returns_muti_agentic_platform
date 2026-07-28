from collections.abc import Sequence
from typing import Protocol

from return_platform.data_platform.operational_generation.capability import (
    CompensationCapability,
    TransactionCapability,
)
from return_platform.data_platform.operational_generation.write_models import (
    Operation,
    OperationType,
)


class ExecutionAdapter(Protocol):
    @property
    def adapter_key(self) -> str: ...

    @property
    def target_system(self) -> str: ...

    @property
    def supported_operation_types(self) -> frozenset[OperationType]: ...

    @property
    def supported_asset_ids(self) -> frozenset[str]: ...

    @property
    def transaction_capability(self) -> TransactionCapability: ...

    @property
    def compensation_capability(self) -> CompensationCapability: ...

    async def is_ready(self) -> bool: ...

    async def execute(self, operations: Sequence[Operation]) -> None: ...

    async def compensate(self, operations: Sequence[Operation]) -> None: ...
