"""Source ERP MongoDB adapter — writes to the source MongoDB database."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

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
    from pymongo import AsyncMongoClient

    from return_platform.data_platform.schema_registry import SchemaRegistry

logger = logging.getLogger(__name__)

_client: AsyncMongoClient[dict[str, object]] | None = None
_database: str = "source_data"
_collections: dict[str, str] = {}


def configure_source_mongodb(
    client: AsyncMongoClient[dict[str, object]] | None,
    database: str = "source_data",
    registry: SchemaRegistry | None = None,
) -> None:
    """Inject or clear the live source MongoDB connection."""
    global _client, _database, _collections  # noqa: PLW0603
    _client = client
    _database = database
    _collections = (
        {
            asset.asset_id: asset.name
            for asset in registry.assets
            if asset.engine == "MONGODB" and asset.database == database
        }
        if registry is not None
        else {}
    )


def _collection_name(asset_id: str) -> str:
    try:
        return _collections[asset_id]
    except KeyError as error:
        raise RuntimeError(
            f"Source MongoDB asset is not configured in the schema registry: {asset_id}"
        ) from error


def _inflate_document(payload: dict[str, object]) -> dict[str, object]:
    """Convert registry field paths into the nested MongoDB document shape."""
    document: dict[str, object] = {}
    for field_path, value in payload.items():
        target = document
        parts = field_path.split(".")
        for part in parts[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise RuntimeError(f"Conflicting MongoDB field path: {field_path}")
            target = child
        target[parts[-1]] = value
    return document


class SourceMongoDbAdapter:
    @property
    def adapter_key(self) -> str:
        return "source_admin"

    @property
    def target_system(self) -> str:
        return "SOURCE_ERP_MONGODB"

    @property
    def supported_operation_types(self) -> frozenset[OperationType]:
        return frozenset([OperationType.INSERT])

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
        return TransactionCapability.MULTI_COLLECTION

    @property
    def compensation_capability(self) -> CompensationCapability:
        return CompensationCapability.DELETE

    async def is_ready(self) -> bool:
        return _client is not None

    async def execute(self, operations: Sequence[Operation]) -> None:
        if _client is None:
            raise RuntimeError("Source MongoDB adapter is not configured")
        db = _client[_database]
        for op in operations:
            if op.type == OperationType.INSERT:
                collection_name = _collection_name(op.asset_id)
                doc = _inflate_document(dict(op.payload))
                await db[collection_name].insert_one(doc)
                logger.info(
                    "source_mongodb_insert",
                    extra={"asset_id": op.asset_id, "collection": collection_name},
                )

    async def compensate(self, operations: Sequence[Operation]) -> None:
        if _client is None:
            raise RuntimeError("Source MongoDB adapter is not configured")
        db = _client[_database]
        for op in operations:
            if op.type == OperationType.INSERT:
                collection_name = _collection_name(op.asset_id)
                doc_id = op.payload.get("_id")
                if doc_id:
                    await db[collection_name].delete_one({"_id": doc_id})
                    logger.info(
                        "source_mongodb_compensate",
                        extra={"asset_id": op.asset_id, "doc_id": doc_id},
                    )


register_adapter("source_admin", SourceMongoDbAdapter)
