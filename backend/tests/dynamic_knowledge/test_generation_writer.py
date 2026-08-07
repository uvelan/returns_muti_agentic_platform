from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.generation_writer import (
    GenerationTransitionError,
    Neo4jGenerationWriter,
)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for row in self._rows:
            yield row


class FakeTransaction:
    def __init__(self, *, transition_matched: int = 1, lookup_rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._transition_matched = transition_matched
        self._lookup_rows = lookup_rows or []

    async def run(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((query, parameters))
        if query.startswith("CREATE (g:GraphGeneration"):
            return FakeResult([])
        if "SET g.status = $newStatus" in query:
            return FakeResult([{"matched": self._transition_matched}])
        if "g.status AS status" in query:
            return FakeResult(self._lookup_rows)
        return FakeResult([])


class FakeSession:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute_write(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)

    async def execute_read(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)


class FakeDriver:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def session(self, *, database: str | None = None) -> FakeSession:
        del database
        return FakeSession(self._tx)


@pytest.mark.asyncio
async def test_create_generation_issues_a_create_not_a_merge() -> None:
    tx = FakeTransaction()
    writer = Neo4jGenerationWriter(FakeDriver(tx))
    await writer.create_generation(graph_generation_id="gen-1", fencing_token=1)
    assert len(tx.calls) == 1
    query, parameters = tx.calls[0]
    assert query.startswith("CREATE (g:GraphGeneration")
    assert parameters == {"generationId": "gen-1", "fencingToken": 1, "status": "PREPARING"}


@pytest.mark.asyncio
async def test_transition_succeeds_when_current_status_matches() -> None:
    tx = FakeTransaction(transition_matched=1)
    writer = Neo4jGenerationWriter(FakeDriver(tx))
    await writer.transition(
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_status=GraphGenerationStatus.PREPARING,
        new_status=GraphGenerationStatus.BUILDING,
    )
    assert tx.calls[0][1]["expectedStatus"] == "PREPARING"
    assert tx.calls[0][1]["newStatus"] == "BUILDING"


@pytest.mark.asyncio
async def test_transition_raises_when_current_status_does_not_match() -> None:
    tx = FakeTransaction(transition_matched=0)
    writer = Neo4jGenerationWriter(FakeDriver(tx))
    with pytest.raises(GenerationTransitionError, match="did not transition"):
        await writer.transition(
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_status=GraphGenerationStatus.PREPARING,
            new_status=GraphGenerationStatus.BUILDING,
        )


@pytest.mark.asyncio
async def test_get_status_returns_status_and_fencing_token() -> None:
    tx = FakeTransaction(lookup_rows=[{"status": "ACTIVE", "fencing_token": 3}])
    writer = Neo4jGenerationWriter(FakeDriver(tx))
    result = await writer.get_status(graph_generation_id="gen-1")
    assert result == (GraphGenerationStatus.ACTIVE, 3)


@pytest.mark.asyncio
async def test_get_status_returns_none_when_generation_does_not_exist() -> None:
    tx = FakeTransaction(lookup_rows=[])
    writer = Neo4jGenerationWriter(FakeDriver(tx))
    result = await writer.get_status(graph_generation_id="gen-missing")
    assert result is None
