"""Neo4j-side GraphGeneration marker lifecycle: create, transition, and look up
status -- the Neo4j half of the blue/green activation protocol. The
MongoDB-authoritative half (ActiveRuntimeSnapshot, RebuildLease, ...) lives in
dynamic_knowledge/lifecycle/. Cypher generation lives in write_compiler.py
(pure, unit-testable without a driver); this module only orchestrates.
"""

from __future__ import annotations

from typing import Any, Protocol

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_generation_create,
    compile_generation_lookup,
    compile_generation_transition,
)


class GenerationTransitionError(RuntimeError):
    """A generation status transition did not apply -- the generation's current
    status did not match what the caller expected, so nothing was changed."""


class GenerationWriteTransaction(Protocol):
    async def run(self, query: str, parameters: dict[str, Any]) -> Any: ...


class GenerationSession(Protocol):
    async def execute_write(self, work: Any, **kwargs: Any) -> Any: ...

    async def execute_read(self, work: Any, **kwargs: Any) -> Any: ...

    async def __aenter__(self) -> GenerationSession: ...

    async def __aexit__(self, *exc_info: Any) -> None: ...


class GenerationDriver(Protocol):
    def session(self, *, database: str | None = None) -> GenerationSession: ...


class Neo4jGenerationWriter:
    def __init__(self, driver: GenerationDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def create_generation(
        self,
        *,
        graph_generation_id: str,
        fencing_token: int,
        status: GraphGenerationStatus = GraphGenerationStatus.PREPARING,
    ) -> None:
        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                _create_generation,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                status=status,
            )

    async def transition(
        self,
        *,
        graph_generation_id: str,
        fencing_token: int,
        expected_status: GraphGenerationStatus,
        new_status: GraphGenerationStatus,
    ) -> None:
        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                _transition,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_status=expected_status,
                new_status=new_status,
            )

    async def get_status(
        self, *, graph_generation_id: str
    ) -> tuple[GraphGenerationStatus, int] | None:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_read(_lookup, graph_generation_id=graph_generation_id)


async def _create_generation(
    tx: GenerationWriteTransaction,
    *,
    graph_generation_id: str,
    fencing_token: int,
    status: GraphGenerationStatus,
) -> None:
    statement = compile_generation_create(
        graph_generation_id=graph_generation_id, fencing_token=fencing_token, status=status.value
    )
    await tx.run(statement.cypher, statement.parameters)


async def _transition(
    tx: GenerationWriteTransaction,
    *,
    graph_generation_id: str,
    fencing_token: int,
    expected_status: GraphGenerationStatus,
    new_status: GraphGenerationStatus,
) -> None:
    statement = compile_generation_transition(
        graph_generation_id=graph_generation_id,
        fencing_token=fencing_token,
        expected_status=expected_status.value,
        new_status=new_status.value,
    )
    result = await tx.run(statement.cypher, statement.parameters)
    rows = [dict(record) async for record in result]
    matched = rows[0]["matched"] if rows else 0
    if matched != 1:
        raise GenerationTransitionError(
            f"generation {graph_generation_id!r} did not transition {expected_status.value!r} "
            f"-> {new_status.value!r}: current status did not match {expected_status.value!r}"
        )


async def _lookup(
    tx: GenerationWriteTransaction, *, graph_generation_id: str
) -> tuple[GraphGenerationStatus, int] | None:
    statement = compile_generation_lookup(graph_generation_id=graph_generation_id)
    result = await tx.run(statement.cypher, statement.parameters)
    rows = [dict(record) async for record in result]
    if not rows:
        return None
    return GraphGenerationStatus(rows[0]["status"]), rows[0]["fencing_token"]
