from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from return_platform.graph_analyzer import service as service_module
from return_platform.graph_analyzer.models import (
    AnalyzerGraphSchema,
    GraphEntity,
    GraphProperty,
    GraphRelationship,
    SyncRequest,
    SyncRun,
)
from return_platform.graph_analyzer.service import (
    GraphAnalyzerService,
    SyncAlreadyRunningError,
)


class FakeWriteResult:
    def __init__(self, writes: int) -> None:
        self._writes = writes

    async def consume(self) -> None:
        return None

    async def single(self) -> dict[str, int]:
        return {"writes": self._writes}


class FakeSystemGraphSession:
    def __init__(self, queries: list[str]) -> None:
        self._queries = queries

    async def __aenter__(self) -> FakeSystemGraphSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def run(self, query: str, **parameters: object) -> FakeWriteResult:
        self._queries.append(query)
        rows = parameters.get("rows")
        return FakeWriteResult(len(rows) if isinstance(rows, list) else 0)


class FakeSystemGraphDriver:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def session(self, **_kwargs: object) -> FakeSystemGraphSession:
        return FakeSystemGraphSession(self.queries)


class FakeSyncCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.inserted.append(document)

    async def update_one(self, _selector: dict[str, Any], update: dict[str, Any]) -> None:
        self.updated.append(update)

    async def find_one(self, _selector: dict[str, Any]) -> dict[str, Any] | None:
        """No run is ever already in flight in these tests."""
        return None


def finalized_schema() -> AnalyzerGraphSchema:
    customer_id = GraphProperty(
        id="customer:id",
        name="customer_id",
        dataType="string",
        required=True,
        identifier=True,
        indexed=True,
        sourceObjectId="source.customers",
        sourceField="customer_id",
    )
    order_id = GraphProperty(
        id="order:id",
        name="order_id",
        dataType="string",
        required=True,
        identifier=True,
        indexed=True,
        sourceObjectId="source.orders",
        sourceField="order_id",
    )
    return AnalyzerGraphSchema(
        id="schema-1",
        version=1,
        status="FINALIZED",
        updatedAt="2026-08-17T00:00:00Z",
        entities=[
            GraphEntity(
                id="customer",
                name="Customer",
                description="Customer",
                x=20,
                y=20,
                properties=[customer_id],
                constraints=["UNIQUE(customer_id)"],
                change="ADDED",
            ),
            GraphEntity(
                id="order",
                name="Order",
                description="Order",
                x=70,
                y=20,
                properties=[order_id],
                constraints=["UNIQUE(order_id)"],
                change="ADDED",
            ),
        ],
        relationships=[
            GraphRelationship(
                id="placed",
                name="PLACED",
                fromEntityId="customer",
                toEntityId="order",
                direction="OUTBOUND",
                properties=[],
                sourceObjectId="source.orders",
                change="ADDED",
            )
        ],
    )


class SyncHarness(GraphAnalyzerService):
    def __init__(self) -> None:
        self._settings = SimpleNamespace(
            graph_sync_max_records=100,
            neo4j_database="system-graph",
        )
        self._graph_driver = FakeSystemGraphDriver()
        self._sync_runs = FakeSyncCollection()
        self.applied_schema = False

    async def proposed_schema(self) -> AnalyzerGraphSchema:
        return finalized_schema()

    async def _apply_system_graph_schema(self, schema: AnalyzerGraphSchema) -> None:
        assert schema.status == "FINALIZED"
        self.applied_schema = True

    async def _read_for_sync(self, object_id: str) -> list[dict[str, Any]]:
        """The seam sync reads through.

        It used to be `preview()`. That is the *UI* page model, whose `pageSize`
        is capped at 100, so driving a sync through it raised a validation error
        as soon as the sync limit exceeded a screenful. Sync now has its own
        bounded reader and this harness follows it, because a stub on the old
        seam would leave the real read path untested while still passing.
        """
        if object_id == "source.customers":
            return [{"customer_id": "c-1"}]
        return [{"order_id": "o-1", "customer_id": "c-1"}]


async def _drain_background_syncs() -> SyncRun:
    """Await the one synchronization `start_sync` put in flight."""
    pending = set(service_module._BACKGROUND_SYNCS)
    assert len(pending) == 1, f"expected one background sync, found {len(pending)}"
    return await next(iter(pending))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "scope"),
    [
        ("FULL", []),
        ("PARTIAL", ["source.customers", "source.orders"]),
    ],
)
async def test_sync_writes_only_through_system_graph_driver(
    mode: str,
    scope: list[str],
) -> None:
    service = SyncHarness()
    accepted = await service.start_sync(SyncRequest(mode=mode, scope=scope))

    # `start_sync` now accepts and returns immediately; the work runs in a
    # background task so the progress the UI polls for actually exists. The
    # boundary under test is what that task writes, so it is awaited here
    # rather than the assertions being moved onto the accept step.
    assert accepted.status == "PREPARING"
    result = await _drain_background_syncs()

    assert service.applied_schema is True
    assert result.status == "COMPLETED"
    assert result.nodesWritten == 2
    assert result.relationshipsWritten == 1
    assert service._graph_driver.queries
    assert all("MERGE" in query for query in service._graph_driver.queries)


class DeleteOnlyCollection:
    def __init__(self) -> None:
        self.deleted_id: str | None = None

    async def delete_one(self, selector: dict[str, str]) -> SimpleNamespace:
        self.deleted_id = selector["_id"]
        return SimpleNamespace(deleted_count=1)


@pytest.mark.asyncio
async def test_removing_source_configuration_only_deletes_internal_metadata() -> None:
    service = object.__new__(GraphAnalyzerService)
    collection = DeleteOnlyCollection()
    service._sources = collection  # type: ignore[assignment]

    assert await service.delete_source("source-config-1") is True
    assert collection.deleted_id == "source-config-1"


class BusySyncCollection(FakeSyncCollection):
    """A store that already holds a run in flight."""

    async def find_one(self, _selector: dict[str, Any]) -> dict[str, Any] | None:
        return {"_id": "already-running", "status": "RUNNING"}


class BusySyncHarness(SyncHarness):
    """A harness whose run store already reports one in flight."""

    def __init__(self) -> None:
        super().__init__()
        # Replaced through the same attribute the base harness assigns, so this
        # subclass adds no second typing shape of its own.
        self.busy_runs = BusySyncCollection()
        object.__setattr__(self, "_sync_runs", self.busy_runs)


@pytest.mark.asyncio
async def test_a_second_synchronization_is_refused_while_one_is_running() -> None:
    """Two concurrent runs MERGE the same nodes on the same keys.

    The second adds contention and no information, and the ordinary way it
    happens is an impatient second click on Start.
    """
    service = BusySyncHarness()

    with pytest.raises(SyncAlreadyRunningError):
        await service.start_sync(SyncRequest(mode="FULL", scope=[]))

    assert service.busy_runs.inserted == [], "a refused run must not be recorded"
