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


class GraphSyncAdapter:
    @property
    def adapter_key(self) -> str:
        return "GRAPH_SYNC_ADAPTER"

    @property
    def target_system(self) -> str:
        return "GRAPH_DATABASE"

    @property
    def supported_operation_types(self) -> frozenset[OperationType]:
        return frozenset([OperationType.GRAPH_SYNC_REQUEST])

    @property
    def supported_asset_ids(self) -> frozenset[str]:
        return frozenset(
            [
                "source.mongodb.sales_inv",
                "source.mongodb.customer_outbound_cdm",
                "source.mongodb.shipment_info",
                "source.mongodb.product_search",
                "source.mongodb.customers",
                "source.mongodb.products",
                "source.mongodb.orders",
            ]
        )

    @property
    def transaction_capability(self) -> TransactionCapability:
        return TransactionCapability.NONE

    @property
    def compensation_capability(self) -> CompensationCapability:
        return CompensationCapability.NONE

    async def is_ready(self) -> bool:
        if not os.environ.get("VAULT_GRAPH_SYNC_CREDENTIALS"):
            return False
        return True

    async def execute(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")

    async def compensate(self, operations: Sequence[Operation]) -> None:
        raise NotImplementedError("Execution remains disabled before AIG6")


register_adapter("GRAPH_SYNC_ADAPTER", GraphSyncAdapter)
