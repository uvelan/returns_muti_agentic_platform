"""Provider-neutral durable state storage and MongoDB bootstrap for V2 services."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from pymongo import ASCENDING, AsyncMongoClient, IndexModel, ReturnDocument

STATE_SCHEMA_VERSION = 1
STATE_NAMESPACES = frozenset({"configuration", "schema_design", "order_sync"})


class StateRevisionConflict(RuntimeError):
    """Raised when durable state loses an optimistic concurrency race."""


class StateSchemaDriftError(RuntimeError):
    """Raised when a state store contains an incompatible schema or checksum."""


def _checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@runtime_checkable
class V2StateStore(Protocol):
    async def prepare(self) -> None: ...

    async def load(self, namespace: str) -> tuple[dict[str, Any], int] | None: ...

    async def save(
        self, namespace: str, payload: dict[str, Any], expected_revision: int
    ) -> int: ...


class InMemoryV2StateStore:
    """Deterministic state store for unit tests and degraded development."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._documents: dict[str, tuple[dict[str, Any], int]] = {}

    async def prepare(self) -> None:
        return None

    async def load(self, namespace: str) -> tuple[dict[str, Any], int] | None:
        document = self._documents.get(namespace)
        return copy.deepcopy(document) if document else None

    async def save(
        self, namespace: str, payload: dict[str, Any], expected_revision: int
    ) -> int:
        if namespace not in STATE_NAMESPACES:
            raise ValueError(f"Unsupported V2 state namespace: {namespace}")
        async with self._lock:
            current = self._documents.get(namespace)
            revision = current[1] if current else 0
            if revision != expected_revision:
                raise StateRevisionConflict(
                    f"State {namespace} expected revision {expected_revision}, current {revision}"
                )
            next_revision = revision + 1
            self._documents[namespace] = (copy.deepcopy(payload), next_revision)
            return next_revision


class MongoV2StateStore:
    """MongoDB state provider with automatic indexes and fail-closed drift validation."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        collection: str = "v2_platform_state",
    ) -> None:
        self._collection = client[database][collection]
        self._migration_collection = client[database][f"{collection}_migrations"]

    async def prepare(self) -> None:
        await self._collection.create_indexes(
            [
                IndexModel([("namespace", ASCENDING)], name="ux_v2_state_namespace", unique=True),
                IndexModel([("updatedAt", ASCENDING)], name="ix_v2_state_updated_at"),
            ]
        )
        await self._migration_collection.create_indexes(
            [
                IndexModel(
                    [("schemaVersion", ASCENDING)],
                    name="ux_v2_state_schema_version",
                    unique=True,
                )
            ]
        )
        receipt = await self._migration_collection.find_one(
            {"schemaVersion": STATE_SCHEMA_VERSION}
        )
        expected = {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "namespaces": sorted(STATE_NAMESPACES),
            "migrationMode": "FORWARD_ONLY",
        }
        expected_checksum = _checksum(expected)
        if receipt is None:
            await self._migration_collection.insert_one(
                {
                    **expected,
                    "checksum": expected_checksum,
                    "appliedAt": datetime.now(UTC),
                    "status": "VERIFIED",
                }
            )
        elif receipt.get("checksum") != expected_checksum:
            raise StateSchemaDriftError(
                "V2 state migration receipt checksum does not match the runtime schema"
            )
        incompatible = await self._collection.find_one(
            {"schemaVersion": {"$ne": STATE_SCHEMA_VERSION}}
        )
        if incompatible is not None:
            raise StateSchemaDriftError("V2 state store contains an incompatible schema version")

    async def load(self, namespace: str) -> tuple[dict[str, Any], int] | None:
        if namespace not in STATE_NAMESPACES:
            raise ValueError(f"Unsupported V2 state namespace: {namespace}")
        document = await self._collection.find_one({"namespace": namespace})
        if document is None:
            return None
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise StateSchemaDriftError(f"V2 state {namespace} payload is not an object")
        normalized = cast(dict[str, Any], payload)
        if document.get("checksum") != _checksum(normalized):
            raise StateSchemaDriftError(f"V2 state {namespace} checksum verification failed")
        revision = document.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise StateSchemaDriftError(f"V2 state {namespace} revision is invalid")
        return copy.deepcopy(normalized), revision

    async def save(
        self, namespace: str, payload: dict[str, Any], expected_revision: int
    ) -> int:
        if namespace not in STATE_NAMESPACES:
            raise ValueError(f"Unsupported V2 state namespace: {namespace}")
        next_revision = expected_revision + 1
        now = datetime.now(UTC)
        if expected_revision == 0:
            try:
                await self._collection.insert_one(
                    {
                        "namespace": namespace,
                        "schemaVersion": STATE_SCHEMA_VERSION,
                        "revision": next_revision,
                        "payload": copy.deepcopy(payload),
                        "checksum": _checksum(payload),
                        "createdAt": now,
                        "updatedAt": now,
                    }
                )
                return next_revision
            except Exception as exc:
                existing = await self._collection.find_one({"namespace": namespace})
                if existing is not None:
                    raise StateRevisionConflict(
                        f"State {namespace} was concurrently initialized"
                    ) from exc
                raise
        updated = await self._collection.find_one_and_update(
            {"namespace": namespace, "revision": expected_revision},
            {
                "$set": {
                    "schemaVersion": STATE_SCHEMA_VERSION,
                    "payload": copy.deepcopy(payload),
                    "checksum": _checksum(payload),
                    "updatedAt": now,
                },
                "$inc": {"revision": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            current = await self._collection.find_one({"namespace": namespace})
            current_revision = current.get("revision") if current else 0
            raise StateRevisionConflict(
                f"State {namespace} expected revision {expected_revision}, current {current_revision}"
            )
        return next_revision
