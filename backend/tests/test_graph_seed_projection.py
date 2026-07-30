"""Active seed selection safeguards for rebuildable graph projection."""

from typing import Any, cast

import pytest

from return_platform.data_platform.graph.sync_service import GraphSyncService


class FakeCursor:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.limit_called = False

    def limit(self, _value: int) -> "FakeCursor":
        self.limit_called = True
        return self

    async def to_list(self) -> list[dict[str, Any]]:
        return self.records


class FakeCollection:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.last_cursor: FakeCursor | None = None

    async def count_documents(
        self,
        query: dict[str, Any],
        **_kwargs: Any,
    ) -> int:
        version = query["seedVersion"]
        expected = query["seedDigest"]["$ne"]
        return sum(
            item.get("seedVersion") == version and item.get("seedDigest") != expected
            for item in self.records
        )

    def find(self, query: dict[str, Any]) -> FakeCursor:
        selected = [
            item
            for item in self.records
            if all(item.get(key) == value for key, value in query.items())
        ]
        self.last_cursor = FakeCursor(selected)
        return self.last_cursor


def service_with(records: list[dict[str, Any]]) -> tuple[Any, FakeCollection]:
    collection = FakeCollection(records)
    service = cast(Any, object.__new__(GraphSyncService))
    service._source_db = {"salesInv": collection}
    return service, collection


@pytest.mark.asyncio
async def test_active_seed_selection_reads_all_matching_orders_without_limit() -> None:
    records = [
        {"_id": str(index), "seedVersion": "v2", "seedDigest": "digest-v2"}
        for index in range(100_000)
    ]
    records.append({"_id": "stale", "seedVersion": "v1", "seedDigest": "digest-v1"})
    service, collection = service_with(records)

    selected = await service._source_seed_documents(
        "salesInv",
        limit=10,
        seed_version="v2",
        seed_digest="digest-v2",
    )

    assert len(selected) == 100_000
    assert all(item["seedVersion"] == "v2" for item in selected)
    assert collection.last_cursor is not None
    assert collection.last_cursor.limit_called is False


@pytest.mark.asyncio
async def test_active_seed_digest_mismatch_fails_closed() -> None:
    service, _ = service_with([{"_id": "bad", "seedVersion": "v2", "seedDigest": "unexpected"}])

    with pytest.raises(ValueError, match="Seed digest mismatch"):
        await service._source_seed_documents(
            "salesInv",
            limit=100_000,
            seed_version="v2",
            seed_digest="digest-v2",
        )
