"""Neo4j-side GraphGeneration marker lifecycle: create, transition, and look up
status -- the Neo4j half of the blue/green activation protocol. The
MongoDB-authoritative half (ActiveRuntimeSnapshot, RebuildLease, ...) lives in
dynamic_knowledge/lifecycle/. Cypher generation lives in write_compiler.py
(pure, unit-testable without a driver); this module only orchestrates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_generation_create,
    compile_generation_fence_claim,
    compile_generation_lookup,
    compile_generation_transition,
)


class GenerationTransitionError(RuntimeError):
    """A generation status transition did not apply -- the generation's current
    status did not match what the caller expected, so nothing was changed."""


class GenerationMarkerMissing(RuntimeError):
    """No GraphGeneration marker exists for the id a caller wanted to own.

    Distinguished from losing a claim: there is nothing to fence against at all,
    which means the caller resolved a generation id that was never created or
    has been removed, not that someone else got there first.
    """


class StaleFencingToken(RuntimeError):
    """A write-ownership claim lost to a higher fencing token already on the marker.

    The holder of that higher token owns the generation now. Raised rather than
    returned so a caller cannot proceed to write under a token the marker will
    reject -- which is the whole failure this fence exists to prevent.
    """

    def __init__(self, graph_generation_id: str, *, requested: int, observed: int) -> None:
        self.graph_generation_id = graph_generation_id
        self.requested = requested
        self.observed = observed
        super().__init__(
            f"generation {graph_generation_id!r} is held at fencing token {observed}; "
            f"this claim presented {requested} and has been fenced off"
        )


class GenerationWriteTransaction(Protocol):
    async def run(self, query: str, parameters: dict[str, Any]) -> Any: ...


_ReadResult = TypeVar("_ReadResult")


_WriteResult = TypeVar("_WriteResult")


class GenerationSession(Protocol):
    async def execute_write(
        self,
        work: Callable[..., Awaitable[_WriteResult]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _WriteResult: ...

    # Positional-only and generic, for the same two reasons as
    # `GraphSession.execute_write` next door: neo4j names the parameter
    # `transaction_function`, and an `Any` return made every declared return
    # type on the callers below an unchecked assertion.
    async def execute_read(
        self,
        work: Callable[..., Awaitable[_ReadResult]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _ReadResult: ...

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

    async def claim_write_ownership(
        self, *, graph_generation_id: str, fencing_token: int
    ) -> tuple[GraphGenerationStatus, int]:
        """Become the generation's writer by raising its fencing token.

        Returns the marker's status and the token now stored on it. The token is
        always the one presented -- a claim that lost raises `StaleFencingToken`
        instead of returning the winner's token, because a caller that carried
        on writing with a token the marker no longer holds would have every
        write rejected one at a time rather than stopping here.

        `fencing_token` must come from a durable monotonic allocator
        (`lifecycle.mongo_store.MongoFencingTokenAllocator`); a locally chosen
        value would not survive a restart and could re-issue a token an earlier
        owner already used.
        """
        async with self._driver.session(database=self._database) as session:
            row = await session.execute_write(
                _claim,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
            )
        if row is None:
            raise GenerationMarkerMissing(
                f"no GraphGeneration marker exists for {graph_generation_id!r}; "
                "there is nothing to claim write ownership of"
            )
        status, observed = row
        if observed != fencing_token:
            raise StaleFencingToken(
                graph_generation_id, requested=fencing_token, observed=observed
            )
        return status, observed

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


async def _claim(
    tx: GenerationWriteTransaction, *, graph_generation_id: str, fencing_token: int
) -> tuple[GraphGenerationStatus, int] | None:
    statement = compile_generation_fence_claim(
        graph_generation_id=graph_generation_id, fencing_token=fencing_token
    )
    result = await tx.run(statement.cypher, statement.parameters)
    rows = [dict(record) async for record in result]
    if not rows:
        return None
    return GraphGenerationStatus(rows[0]["status"]), int(rows[0]["fencing_token"])


async def _lookup(
    tx: GenerationWriteTransaction, *, graph_generation_id: str
) -> tuple[GraphGenerationStatus, int] | None:
    statement = compile_generation_lookup(graph_generation_id=graph_generation_id)
    result = await tx.run(statement.cypher, statement.parameters)
    rows = [dict(record) async for record in result]
    if not rows:
        return None
    return GraphGenerationStatus(rows[0]["status"]), rows[0]["fencing_token"]
