from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import ActiveRuntimeSnapshot
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    MongoActiveRuntimeSnapshotStore,
    MongoRebuildLeaseStore,
)


class FakeReplaceResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeStoreCollection:
    """Emulates just enough of pymongo's AsyncCollection semantics -- unique
    _id, filtered replace, and upsert-with-conflict -- to prove the stores'
    atomicity logic without a live Mongo instance."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        doc = self.documents.get(query["_id"])
        return dict(doc) if doc is not None else None

    async def insert_one(self, payload: dict[str, Any]) -> None:
        doc_id = payload["_id"]
        if doc_id in self.documents:
            raise DuplicateKeyError("duplicate _id")
        self.documents[doc_id] = dict(payload)

    async def replace_one(self, filter_: dict[str, Any], payload: dict[str, Any]) -> FakeReplaceResult:
        doc_id = filter_["_id"]
        existing = self.documents.get(doc_id)
        if existing is None or any(
            existing.get(key) != value for key, value in filter_.items() if key != "_id"
        ):
            return FakeReplaceResult(matched_count=0)
        self.documents[doc_id] = dict(payload)
        return FakeReplaceResult(matched_count=1)

    async def find_one_and_update(
        self, filter_: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> dict[str, Any] | None:
        doc_id = filter_["_id"]
        existing = self.documents.get(doc_id)
        if existing is not None:
            matches = True
            for key, value in filter_.items():
                if key == "_id":
                    continue
                current = existing.get(key)
                if isinstance(value, dict) and "$lt" in value:
                    if not (current is not None and current < value["$lt"]):
                        matches = False
                elif current != value:
                    matches = False
            if matches:
                self.documents[doc_id] = dict(update["$set"])
                return dict(self.documents[doc_id])
            if upsert:
                raise DuplicateKeyError("duplicate _id")
            return None
        if upsert:
            self.documents[doc_id] = dict(update["$set"])
            return dict(self.documents[doc_id])
        return None

    async def delete_one(self, filter_: dict[str, Any]) -> None:
        doc_id = filter_["_id"]
        existing = self.documents.get(doc_id)
        if existing is not None and all(
            existing.get(key) == value for key, value in filter_.items() if key != "_id"
        ):
            del self.documents[doc_id]


def _snapshot(version: int) -> ActiveRuntimeSnapshot:
    return ActiveRuntimeSnapshot(
        snapshot_name="ORDER_DISCOVERY",
        configuration_release_id="release-1",
        schema_fingerprint="a" * 64,
        graph_generation_id=f"gen-{version}",
        search_index_release_id="search-1",
        activation_id=f"activation-{version}",
        activation_version=version,
        activated_at=datetime(2026, 8, 7, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_read_returns_none_when_no_snapshot_exists() -> None:
    store = MongoActiveRuntimeSnapshotStore(FakeStoreCollection())
    assert await store.read(snapshot_name="ORDER_DISCOVERY") is None


@pytest.mark.asyncio
async def test_compare_and_swap_creates_the_first_snapshot() -> None:
    collection = FakeStoreCollection()
    store = MongoActiveRuntimeSnapshotStore(collection)
    ok = await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=None, new_snapshot=_snapshot(1)
    )
    assert ok is True
    read_back = await store.read(snapshot_name="ORDER_DISCOVERY")
    assert read_back is not None
    assert read_back.activation_version == 1


@pytest.mark.asyncio
async def test_compare_and_swap_fails_when_a_snapshot_already_exists_and_none_expected() -> None:
    collection = FakeStoreCollection()
    store = MongoActiveRuntimeSnapshotStore(collection)
    await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=None, new_snapshot=_snapshot(1)
    )
    ok = await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=None, new_snapshot=_snapshot(2)
    )
    assert ok is False
    read_back = await store.read(snapshot_name="ORDER_DISCOVERY")
    assert read_back is not None
    assert read_back.activation_version == 1  # unchanged


@pytest.mark.asyncio
async def test_compare_and_swap_succeeds_when_version_matches() -> None:
    collection = FakeStoreCollection()
    store = MongoActiveRuntimeSnapshotStore(collection)
    await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=None, new_snapshot=_snapshot(1)
    )
    ok = await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=1, new_snapshot=_snapshot(2)
    )
    assert ok is True
    read_back = await store.read(snapshot_name="ORDER_DISCOVERY")
    assert read_back is not None
    assert read_back.activation_version == 2


@pytest.mark.asyncio
async def test_compare_and_swap_fails_when_version_is_stale() -> None:
    """The core CAS guarantee: a concurrent activation that already moved the
    snapshot forward must make a stale-version swap fail, never overwrite."""

    collection = FakeStoreCollection()
    store = MongoActiveRuntimeSnapshotStore(collection)
    await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=None, new_snapshot=_snapshot(1)
    )
    await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=1, new_snapshot=_snapshot(2)
    )
    # A second activator still thinks version 1 is current -- must fail now.
    ok = await store.compare_and_swap(
        snapshot_name="ORDER_DISCOVERY", expected_activation_version=1, new_snapshot=_snapshot(3)
    )
    assert ok is False
    read_back = await store.read(snapshot_name="ORDER_DISCOVERY")
    assert read_back is not None
    assert read_back.activation_version == 2


@pytest.mark.asyncio
async def test_compare_and_swap_rejects_a_snapshot_name_mismatch() -> None:
    store = MongoActiveRuntimeSnapshotStore(FakeStoreCollection())
    with pytest.raises(ValueError, match="snapshot_name must match"):
        await store.compare_and_swap(
            snapshot_name="OTHER_NAME", expected_activation_version=None, new_snapshot=_snapshot(1)
        )


@pytest.mark.asyncio
async def test_rebuild_lease_acquire_succeeds_when_no_lease_exists() -> None:
    store = MongoRebuildLeaseStore(FakeStoreCollection())
    lease = await store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="gen-1",
        owner_instance_id="worker-1",
        ttl_seconds=300,
    )
    assert lease is not None
    assert lease.graph_generation_id == "gen-1"


@pytest.mark.asyncio
async def test_rebuild_lease_acquire_fails_while_an_active_lease_is_held() -> None:
    collection = FakeStoreCollection()
    store = MongoRebuildLeaseStore(collection)
    first = await store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="gen-1",
        owner_instance_id="worker-1",
        ttl_seconds=300,
    )
    assert first is not None
    second = await store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="gen-2",
        owner_instance_id="worker-2",
        ttl_seconds=300,
    )
    assert second is None


@pytest.mark.asyncio
async def test_rebuild_lease_acquire_succeeds_after_the_prior_lease_expired() -> None:
    collection = FakeStoreCollection()
    store = MongoRebuildLeaseStore(collection)
    # Seed an already-expired lease directly, bypassing the TTL wait.
    expired_payload = {
        "_id": "ORDER_DISCOVERY",
        "lease_id": "old-lease",
        "snapshot_name": "ORDER_DISCOVERY",
        "graph_generation_id": "gen-1",
        "owner_instance_id": "worker-1",
        "acquired_at": datetime.now(UTC) - timedelta(seconds=600),
        "expires_at": datetime.now(UTC) - timedelta(seconds=1),
    }
    collection.documents["ORDER_DISCOVERY"] = expired_payload

    second = await store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="gen-2",
        owner_instance_id="worker-2",
        ttl_seconds=300,
    )
    assert second is not None
    assert second.graph_generation_id == "gen-2"


@pytest.mark.asyncio
async def test_rebuild_lease_release_removes_only_the_matching_lease() -> None:
    collection = FakeStoreCollection()
    store = MongoRebuildLeaseStore(collection)
    lease = await store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="gen-1",
        owner_instance_id="worker-1",
        ttl_seconds=300,
    )
    assert lease is not None
    await store.release(snapshot_name="ORDER_DISCOVERY", lease_id="wrong-lease-id")
    assert "ORDER_DISCOVERY" in collection.documents  # not removed -- lease_id didn't match

    await store.release(snapshot_name="ORDER_DISCOVERY", lease_id=lease.lease_id)
    assert "ORDER_DISCOVERY" not in collection.documents
