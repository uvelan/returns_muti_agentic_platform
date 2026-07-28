from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncScope,
)
from return_platform.data_platform.operational_generation.capability import (
    CompensationCapability,
    TransactionCapability,
)
from return_platform.data_platform.operational_generation.write_models import (
    Operation,
    OperationType,
)

from .registry import register_adapter

if TYPE_CHECKING:
    from return_platform.data_platform.graph.sync_service import GraphSyncService

_service: GraphSyncService | None = None


def configure_graph_sync(service: GraphSyncService | None) -> None:
    """Inject or clear the canonical graph projection service."""
    global _service  # noqa: PLW0603
    _service = service


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
        return _service is not None

    async def execute(self, operations: Sequence[Operation]) -> None:
        if _service is None:
            raise RuntimeError("Graph synchronization adapter is not configured")
        if operations:
            result = await _service.sync(
                GraphSyncRequest(
                    mode=GraphSyncScope.SOURCE_MONGODB,
                    applySchema=False,
                ),
                actor_id="operational-generation",
            )
            if result.status != "COMPLETED":
                raise RuntimeError("Graph synchronization did not complete")

    async def compensate(self, operations: Sequence[Operation]) -> None:
        await self.execute(operations)
        if _service is None:
            raise RuntimeError("Graph synchronization adapter is not configured")
        records: list[tuple[str, Mapping[str, object]]] = []
        for operation in operations:
            raw_records = operation.payload.get("records", [])
            if isinstance(raw_records, list):
                records.extend(
                    (operation.asset_id, record)
                    for record in raw_records
                    if isinstance(record, Mapping)
                )
        await _service.remove_source_mongodb_records(records)


register_adapter("GRAPH_SYNC_ADAPTER", GraphSyncAdapter)
