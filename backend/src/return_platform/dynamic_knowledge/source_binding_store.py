"""Stored rebindings: the overrides layered over the configured baseline.

Only the differences live here. A binding that agrees with the shipped schema is
not written, so the store answers "what has someone deliberately changed" rather
than duplicating configuration -- and a source removed from the file does not
linger in Mongo pretending to still be reachable.

Bindings are mutable by design, which is the whole point of separating them from
the graph's shape: infrastructure moves and the schema should not have to. A
*release* is still immutable, because it captures the binding it compiled
against at the moment it was compiled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, AsyncMongoClient

from return_platform.dynamic_knowledge.schema import SourceAssetDefinition
from return_platform.dynamic_knowledge.source_bindings import SourceBinding

__all__ = ["SOURCE_BINDINGS_COLLECTION", "SourceBindingStore"]

SOURCE_BINDINGS_COLLECTION = "graph_source_bindings"


class SourceBindingStore:
    def __init__(self, client: AsyncMongoClient[dict[str, Any]], database: str) -> None:
        self._bindings = client[database][SOURCE_BINDINGS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._bindings.create_index([("dataset", ASCENDING)], unique=True, name="uq_dataset")

    async def list(self) -> list[SourceBinding]:
        cursor = self._bindings.find({}, {"_id": 0}).sort("dataset", ASCENDING)
        return [
            SourceBinding(
                dataset=str(document["dataset"]),
                asset=SourceAssetDefinition.model_validate(document["asset"]),
            )
            async for document in cursor
        ]

    async def rebind(self, binding: SourceBinding, *, changed_by: str) -> None:
        """Point a dataset at a different source asset.

        An upsert rather than an insert: rebinding twice is an ordinary act,
        and the second is not a mistake to reject. Who and when are recorded
        because "why is this reading from staging" is the question this
        collection exists to answer six months later.
        """
        await self._bindings.update_one(
            {"dataset": binding.dataset},
            {
                "$set": {
                    "asset": binding.asset.model_dump(mode="json"),
                    "changedBy": changed_by,
                    "changedAt": datetime.now(UTC),
                },
                "$setOnInsert": {"dataset": binding.dataset},
            },
            upsert=True,
        )

    async def clear(self, dataset: str) -> bool:
        """Drop an override so the dataset resolves through configuration again.

        Returns whether one was there. Not an error when it was not: "make this
        follow the file" is the same intention whether or not an override
        existed, and a 404 would make the console's reset button conditional on
        state the operator cannot see.
        """
        result = await self._bindings.delete_one({"dataset": dataset})
        return result.deleted_count > 0
