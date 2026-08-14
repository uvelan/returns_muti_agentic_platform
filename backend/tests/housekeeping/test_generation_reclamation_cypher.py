"""The adapter's statements, asserted against the two property names.

The `GraphGeneration` marker keys itself on `generation_id`
(`compile_generation_create`). Every projected node, `ProjectionOwnership` record
and `GraphWriteReceipt` carries `graph_generation_id`
(`_generation_scoped_pattern`). Using the wrong one silently matches nothing,
which reads as a cleanup that ran fine and reclaimed zero bytes -- the failure
mode that would make the whole housekeeping worker look like it worked while the
graph kept growing.

Driven through a recording transaction rather than a live Neo4j: the assertion is
about which property each statement addresses, which does not need a database, and
a rule that needs one is a rule nobody re-checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.housekeeping.graph_adapter import Neo4jGenerationReclamationAdapter


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> Any:
        async def iterator() -> Any:
            for row in self._rows:
                yield row

        return iterator()


class _Transaction:
    def __init__(self, recorder: list[tuple[str, dict[str, Any]]], rows: list[dict[str, Any]]):
        self._recorder = recorder
        self._rows = rows

    async def run(self, query: str, parameters: dict[str, Any]) -> _Result:
        self._recorder.append((query, parameters))
        return _Result(self._rows)


class _Session:
    def __init__(self, recorder: list[tuple[str, dict[str, Any]]], rows: list[dict[str, Any]]):
        self._recorder = recorder
        self._rows = rows

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute_write(self, work: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await work(_Transaction(self._recorder, self._rows), *args, **kwargs)

    async def execute_read(self, work: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await work(_Transaction(self._recorder, self._rows), *args, **kwargs)


class _Driver:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self._rows = rows or []

    def session(self, *, database: str | None = None) -> _Session:
        return _Session(self.statements, self._rows)


@pytest.mark.asyncio
async def test_the_node_sweep_addresses_graph_generation_id_not_generation_id() -> None:
    driver = _Driver([{"deleted": 750}])
    adapter = Neo4jGenerationReclamationAdapter(driver)

    deleted = await adapter.delete_generation_nodes(graph_generation_id="g-1", batch_size=1_000)

    cypher, parameters = driver.statements[0]
    assert "n.graph_generation_id = $generationId" in cypher
    # Not the marker's key. A sweep on `generation_id` would match the marker
    # alone and delete none of the projection.
    assert "n.generation_id" not in cypher
    assert "DETACH DELETE n" in cypher
    # Bounded, or one statement takes the whole generation in one transaction.
    assert "LIMIT $batchSize" in cypher
    assert parameters == {"generationId": "g-1", "batchSize": 1_000}
    assert deleted == 750


@pytest.mark.asyncio
async def test_the_marker_delete_addresses_generation_id_and_is_status_guarded() -> None:
    driver = _Driver([{"deleted": 1}])
    adapter = Neo4jGenerationReclamationAdapter(driver)

    removed = await adapter.delete_generation_marker(graph_generation_id="g-1", status="RETIRED")

    cypher, parameters = driver.statements[0]
    assert "GraphGeneration {generation_id: $generationId, status: $status}" in cypher
    assert parameters == {"generationId": "g-1", "status": "RETIRED"}
    assert removed is True


@pytest.mark.asyncio
async def test_the_quarantine_stamp_is_written_once_and_only_while_retired() -> None:
    """Re-stamping on every pass would restart the window each interval.

    A generation would then never age out of quarantine: housekeeping would run
    forever and reclaim nothing, which is indistinguishable from working.
    """
    driver = _Driver()
    adapter = Neo4jGenerationReclamationAdapter(driver)
    observed = datetime.now(UTC)

    await adapter.mark_reclaim_eligible(graph_generation_id="g-1", observed_at=observed)

    cypher, parameters = driver.statements[0]
    assert "g.reclaim_eligible_since IS NULL" in cypher
    assert "status: $status" in cypher
    assert parameters["status"] == "RETIRED"
    assert parameters["observedAt"] == observed


@pytest.mark.asyncio
async def test_listing_only_asks_for_the_requested_status() -> None:
    driver = _Driver(
        [{"graph_generation_id": "g-1", "status": "RETIRED", "reclaim_eligible_since": None}]
    )
    adapter = Neo4jGenerationReclamationAdapter(driver)

    rows = await adapter.list_generations_by_status(status="RETIRED", limit=20)

    cypher, parameters = driver.statements[0]
    assert "GraphGeneration {status: $status}" in cypher
    assert parameters == {"status": "RETIRED", "limit": 20}
    assert rows[0]["graph_generation_id"] == "g-1"


@pytest.mark.asyncio
async def test_a_driver_native_temporal_is_reduced_to_an_aware_datetime() -> None:
    """The driver returns its own `DateTime` for a temporal property.

    Comparing one to `now` in the reclaimer raises rather than answering, so a
    stamped generation would crash the pass instead of ageing out.
    """

    class _Neo4jDateTime:
        def __init__(self, value: datetime) -> None:
            self._value = value

        def to_native(self) -> datetime:
            return self._value

    naive = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 - the point is it is naive
    driver = _Driver(
        [
            {
                "graph_generation_id": "g-1",
                "status": "RETIRED",
                "reclaim_eligible_since": _Neo4jDateTime(naive),
            }
        ]
    )
    rows = await Neo4jGenerationReclamationAdapter(driver).list_generations_by_status(
        status="RETIRED", limit=1
    )

    stamped = rows[0]["reclaim_eligible_since"]
    assert isinstance(stamped, datetime)
    assert stamped.tzinfo is not None
