"""MongoDB and in-memory repositories for dependency simulation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
else:
    AsyncMongoClient = Any

try:
    from pymongo import ASCENDING, DESCENDING, ReturnDocument
except ModuleNotFoundError:  # dependency-light focused validation
    ASCENDING = 1
    DESCENDING = -1

    class _FallbackReturnDocument:  # pragma: no cover — only used without pymongo
        AFTER = True

    ReturnDocument = _FallbackReturnDocument  # type: ignore[assignment,misc]

from return_platform.configuration.settings import Settings
from return_platform.dependency_simulation.models import (
    SimulationAISummary,
    SimulationAIUsageMetric,
    SimulationOperationView,
)


class SimulationRepository(Protocol):
    async def ensure_indexes(self) -> None: ...
    async def get_by_idempotency_key(self, key: str) -> SimulationOperationView | None: ...
    async def insert_operation(self, document: dict[str, Any]) -> SimulationOperationView: ...
    async def update_operation(
        self, operation_id: str, changes: dict[str, Any]
    ) -> SimulationOperationView: ...
    async def get_operation(self, operation_id: str) -> SimulationOperationView | None: ...
    async def list_operations(
        self, *, dependency: str | None = None, session_id: str | None = None, limit: int = 200
    ) -> list[SimulationOperationView]: ...
    async def latest_operation(
        self, session_id: str, dependency: str, operations: tuple[str, ...]
    ) -> SimulationOperationView | None: ...
    async def insert_ai_metric(self, document: dict[str, Any]) -> SimulationAIUsageMetric: ...
    async def list_ai_metrics(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[SimulationAIUsageMetric]: ...
    async def ai_summary(self) -> SimulationAISummary: ...
    async def operation_counts(self) -> dict[str, int]: ...
    async def reset(self, session_id: str | None = None) -> None: ...


def _operation_view(document: dict[str, Any]) -> SimulationOperationView:
    payload = {
        key: value for key, value in document.items() if key in SimulationOperationView.model_fields
    }
    payload["id"] = str(document.get("_id") or document.get("id"))
    return SimulationOperationView.model_validate(payload)


def _metric_view(document: dict[str, Any]) -> SimulationAIUsageMetric:
    payload = {
        key: value for key, value in document.items() if key in SimulationAIUsageMetric.model_fields
    }
    payload["id"] = str(document.get("_id") or document.get("id"))
    return SimulationAIUsageMetric.model_validate(payload)


def summarize_metrics(metrics: list[SimulationAIUsageMetric]) -> SimulationAISummary:
    summary = SimulationAISummary()
    summary.requestCount = len(metrics)
    for item in metrics:
        if item.status == "SUCCESS":
            summary.successCount += 1
        elif item.status == "FAILED":
            summary.failureCount += 1
        if item.status in {"FALLBACK", "SKIPPED"}:
            summary.fallbackCount += 1
        summary.totalInputTokens += item.inputTokens
        summary.totalOutputTokens += item.outputTokens
        summary.totalTokens += item.totalTokens
        summary.estimatedCostMicrousd += item.estimatedCostMicrousd
        for bucket, key in (
            (summary.byProvider, item.provider),
            (summary.byModel, item.model),
            (summary.byDependency, item.dependency.value),
            (summary.byOperation, item.operation),
        ):
            current = bucket.setdefault(
                key, {"requests": 0, "tokens": 0, "fallbacks": 0, "costMicrousd": 0}
            )
            current["requests"] += 1
            current["tokens"] += item.totalTokens
            current["fallbacks"] += int(item.fallbackUsed)
            current["costMicrousd"] += item.estimatedCostMicrousd
    return summary


class MongoSimulationRepository:
    def __init__(self, client: AsyncMongoClient[dict[str, object]], settings: Settings) -> None:
        db = client[settings.mongo_database]
        self._operations = db["dependency_simulation_operations"]
        self._metrics = db["dependency_simulation_ai_metrics"]

    async def ensure_indexes(self) -> None:
        await self._operations.create_index("idempotencyKey", unique=True)
        await self._operations.create_index([("dependency", ASCENDING), ("createdAt", DESCENDING)])
        await self._operations.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])
        await self._metrics.create_index([("createdAt", DESCENDING)])
        await self._metrics.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])
        await self._metrics.create_index([("provider", ASCENDING), ("model", ASCENDING)])

    async def get_by_idempotency_key(self, key: str) -> SimulationOperationView | None:
        document = await self._operations.find_one({"idempotencyKey": key})
        return _operation_view(cast(dict[str, Any], document)) if document else None

    async def insert_operation(self, document: dict[str, Any]) -> SimulationOperationView:
        await self._operations.insert_one(document)
        return _operation_view(document)

    async def update_operation(
        self, operation_id: str, changes: dict[str, Any]
    ) -> SimulationOperationView:
        changes = {**changes, "updatedAt": datetime.now(UTC)}
        document = await self._operations.find_one_and_update(
            {"_id": operation_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
        if document is None:
            raise KeyError(operation_id)
        return _operation_view(cast(dict[str, Any], document))

    async def get_operation(self, operation_id: str) -> SimulationOperationView | None:
        document = await self._operations.find_one({"_id": operation_id})
        return _operation_view(cast(dict[str, Any], document)) if document else None

    async def list_operations(
        self, *, dependency: str | None = None, session_id: str | None = None, limit: int = 200
    ) -> list[SimulationOperationView]:
        query: dict[str, Any] = {}
        if dependency:
            query["dependency"] = dependency
        if session_id:
            query["sessionId"] = session_id
        cursor = self._operations.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [_operation_view(cast(dict[str, Any], item)) async for item in cursor]

    async def latest_operation(
        self, session_id: str, dependency: str, operations: tuple[str, ...]
    ) -> SimulationOperationView | None:
        document = await self._operations.find_one(
            {
                "sessionId": session_id,
                "dependency": dependency,
                "operation": {"$in": list(operations)},
                "status": "CONFIRMED",
            },
            sort=[("createdAt", DESCENDING)],
        )
        return _operation_view(cast(dict[str, Any], document)) if document else None

    async def insert_ai_metric(self, document: dict[str, Any]) -> SimulationAIUsageMetric:
        await self._metrics.insert_one(document)
        return _metric_view(document)

    async def list_ai_metrics(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[SimulationAIUsageMetric]:
        query = {"sessionId": session_id} if session_id else {}
        cursor = self._metrics.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [_metric_view(cast(dict[str, Any], item)) async for item in cursor]

    async def ai_summary(self) -> SimulationAISummary:
        return summarize_metrics(await self.list_ai_metrics(limit=10_000))

    async def operation_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        # aggregate() in PyMongo async is a coroutine that must be awaited to obtain
        # the AsyncCommandCursor before async-iterating over it.
        cursor = await self._operations.aggregate(
            [{"$group": {"_id": "$dependency", "count": {"$sum": 1}}}]
        )
        async for item in cursor:
            result[str(item["_id"])] = int(str(item["count"]))
        return result

    async def reset(self, session_id: str | None = None) -> None:
        query = {"sessionId": session_id} if session_id else {}
        await self._operations.delete_many(query)
        await self._metrics.delete_many(query)


class MemorySimulationRepository:
    """Dependency-free repository used by focused tests and source-level E2E validation."""

    def __init__(self) -> None:
        self.operations: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self.metrics: dict[str, dict[str, Any]] = {}

    async def ensure_indexes(self) -> None:
        return None

    async def get_by_idempotency_key(self, key: str) -> SimulationOperationView | None:
        operation_id = self.idempotency.get(key)
        return _operation_view(self.operations[operation_id]) if operation_id else None

    async def insert_operation(self, document: dict[str, Any]) -> SimulationOperationView:
        key = str(document["idempotencyKey"])
        if key in self.idempotency:
            return _operation_view(self.operations[self.idempotency[key]])
        operation_id = str(document["_id"])
        self.operations[operation_id] = dict(document)
        self.idempotency[key] = operation_id
        return _operation_view(document)

    async def update_operation(
        self, operation_id: str, changes: dict[str, Any]
    ) -> SimulationOperationView:
        document = self.operations[operation_id]
        document.update(changes)
        document["updatedAt"] = datetime.now(UTC)
        return _operation_view(document)

    async def get_operation(self, operation_id: str) -> SimulationOperationView | None:
        document = self.operations.get(operation_id)
        return _operation_view(document) if document else None

    async def list_operations(
        self, *, dependency: str | None = None, session_id: str | None = None, limit: int = 200
    ) -> list[SimulationOperationView]:
        values = list(self.operations.values())
        values.sort(key=lambda item: item["createdAt"], reverse=True)
        result = [
            _operation_view(item)
            for item in values
            if (not dependency or item["dependency"] == dependency)
            and (not session_id or item["sessionId"] == session_id)
        ]
        return result[:limit]

    async def latest_operation(
        self, session_id: str, dependency: str, operations: tuple[str, ...]
    ) -> SimulationOperationView | None:
        values = await self.list_operations(
            dependency=dependency, session_id=session_id, limit=10_000
        )
        return next(
            (
                item
                for item in values
                if item.operation in operations and item.status.value == "CONFIRMED"
            ),
            None,
        )

    async def insert_ai_metric(self, document: dict[str, Any]) -> SimulationAIUsageMetric:
        self.metrics[str(document["_id"])] = dict(document)
        return _metric_view(document)

    async def list_ai_metrics(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[SimulationAIUsageMetric]:
        values = list(self.metrics.values())
        values.sort(key=lambda item: item["createdAt"], reverse=True)
        result = [
            _metric_view(item)
            for item in values
            if not session_id or item["sessionId"] == session_id
        ]
        return result[:limit]

    async def ai_summary(self) -> SimulationAISummary:
        return summarize_metrics(await self.list_ai_metrics(limit=10_000))

    async def operation_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for item in self.operations.values():
            counts[str(item["dependency"])] += 1
        return dict(counts)

    async def reset(self, session_id: str | None = None) -> None:
        if session_id is None:
            self.operations.clear()
            self.idempotency.clear()
            self.metrics.clear()
            return
        remove = [key for key, value in self.operations.items() if value["sessionId"] == session_id]
        for key in remove:
            idem = str(self.operations[key]["idempotencyKey"])
            self.operations.pop(key)
            self.idempotency.pop(idem, None)
        for key in [key for key, value in self.metrics.items() if value["sessionId"] == session_id]:
            self.metrics.pop(key)
