"""The Neo4j half of graph-generation reclamation.

Separate from `graph_generations.py` on purpose: that module holds the rules
about *whether* a generation may be removed, which is the part that must not be
got wrong and is worth testing without a database. This one holds the four
statements that do it.

**Two different property names, and the bug that comes from confusing them.**
The `GraphGeneration` marker keys itself on `generation_id`
(`compile_generation_create`), while every projected node, every
`ProjectionOwnership` record and every `GraphWriteReceipt` carries
`graph_generation_id` (`_generation_scoped_pattern`). Deleting by the wrong one
silently matches nothing, which reads as a successful cleanup that reclaimed
zero bytes -- the failure mode that would leave this whole module looking like
it worked.

**Why the node sweep is unlabelled.** `MATCH (n) WHERE n.graph_generation_id =
$id` reaches every generation-scoped node whatever its label -- and the labels
are schema-driven, so an entity added by a configuration release would be missed
by any hardcoded list. That is a scan, which is why it runs in bounded batches
behind a `LIMIT` rather than as one statement.

Every statement is parameterized. There is no identifier interpolation anywhere
in this module -- no label, property name or database name is composed from
input -- so there is nothing here to allowlist.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

__all__ = ["Neo4jGenerationReclamationAdapter"]

_Result = TypeVar("_Result")


class _Session(Protocol):
    async def execute_write(
        self,
        work: Callable[..., Awaitable[_Result]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _Result: ...

    async def execute_read(
        self,
        work: Callable[..., Awaitable[_Result]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _Result: ...

    async def __aenter__(self) -> _Session: ...

    async def __aexit__(self, *exc_info: Any) -> None: ...


class _Driver(Protocol):
    def session(self, *, database: str | None = None) -> _Session: ...


class _Transaction(Protocol):
    async def run(self, query: str, parameters: dict[str, Any]) -> Any: ...


class Neo4jGenerationReclamationAdapter:
    """`GenerationGraphPort` against a real driver."""

    def __init__(self, driver: _Driver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def list_generations_by_status(
        self, *, status: str, limit: int
    ) -> tuple[dict[str, Any], ...]:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_read(_list_by_status, status=status, limit=limit)

    async def mark_reclaim_eligible(
        self, *, graph_generation_id: str, status: str, observed_at: datetime
    ) -> datetime:
        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                _mark_eligible,
                graph_generation_id=graph_generation_id,
                status=status,
                observed_at=observed_at,
            )
        return observed_at

    async def transition_generation_status(
        self, *, graph_generation_id: str, expected_status: str, next_status: str
    ) -> bool:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                _transition_status,
                graph_generation_id=graph_generation_id,
                expected_status=expected_status,
                next_status=next_status,
            )

    async def delete_generation_nodes(self, *, graph_generation_id: str, batch_size: int) -> int:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                _delete_nodes,
                graph_generation_id=graph_generation_id,
                batch_size=batch_size,
            )

    async def delete_generation_marker(self, *, graph_generation_id: str, status: str) -> bool:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                _delete_marker, graph_generation_id=graph_generation_id, status=status
            )


async def _list_by_status(
    tx: _Transaction, *, status: str, limit: int
) -> tuple[dict[str, Any], ...]:
    result = await tx.run(
        "MATCH (g:GraphGeneration {status: $status}) "
        "RETURN g.generation_id AS graph_generation_id, g.status AS status, "
        "g.reclaim_eligible_since AS reclaim_eligible_since "
        "ORDER BY g.created_at ASC "
        "LIMIT $limit",
        {"status": status, "limit": limit},
    )
    rows: list[dict[str, Any]] = []
    async for record in result:
        row = dict(record)
        row["reclaim_eligible_since"] = _as_datetime(row.get("reclaim_eligible_since"))
        rows.append(row)
    return tuple(rows)


async def _mark_eligible(
    tx: _Transaction, *, graph_generation_id: str, status: str, observed_at: datetime
) -> None:
    # Only when absent, and only while still in the status it was assessed
    # under. Overwriting an existing stamp on every pass would restart the
    # quarantine each interval and the generation would never age out of it -- a
    # cleanup that runs forever and reclaims nothing.
    #
    # The status is a parameter, not the literal "RETIRED" it used to be. With
    # the literal, stamping a FAILED or PREPARING candidate matched zero rows:
    # the stamp never landed, every later pass re-read "quarantine starts now",
    # and the generation could never become eligible. The guard would have
    # silently cancelled the widening it is guarding.
    await tx.run(
        "MATCH (g:GraphGeneration {generation_id: $generationId, status: $status}) "
        "WHERE g.reclaim_eligible_since IS NULL "
        "SET g.reclaim_eligible_since = $observedAt",
        {
            "generationId": graph_generation_id,
            "status": status,
            "observedAt": observed_at,
        },
    )


async def _transition_status(
    tx: _Transaction, *, graph_generation_id: str, expected_status: str, next_status: str
) -> bool:
    # Compare-and-set on the marker, so a generation that moved under us is left
    # alone rather than dragged backwards. `generation_id` addresses the marker;
    # `graph_generation_id` addresses its projected nodes, and confusing the two
    # reads as a transition that ran fine and moved nothing.
    result = await tx.run(
        "MATCH (g:GraphGeneration {generation_id: $generationId, status: $expected}) "
        "SET g.status = $next "
        "RETURN count(g) AS moved",
        {
            "generationId": graph_generation_id,
            "expected": expected_status,
            "next": next_status,
        },
    )
    rows = [dict(record) async for record in result]
    return bool(rows) and int(rows[0]["moved"]) == 1


async def _delete_nodes(tx: _Transaction, *, graph_generation_id: str, batch_size: int) -> int:
    # `graph_generation_id`, not `generation_id`: this matches the projected
    # nodes, the ownership records and the write receipts, and deliberately not
    # the marker -- which `_delete_marker` removes last, after these are gone.
    result = await tx.run(
        "MATCH (n) WHERE n.graph_generation_id = $generationId "
        "WITH n LIMIT $batchSize "
        "DETACH DELETE n "
        "RETURN count(n) AS deleted",
        {"generationId": graph_generation_id, "batchSize": batch_size},
    )
    rows = [dict(record) async for record in result]
    return int(rows[0]["deleted"]) if rows else 0


async def _delete_marker(tx: _Transaction, *, graph_generation_id: str, status: str) -> bool:
    # Still guarded on status. RETIRED is terminal so nothing should move it,
    # and the guard costs one property comparison to make that an assertion
    # rather than an assumption.
    result = await tx.run(
        "MATCH (g:GraphGeneration {generation_id: $generationId, status: $status}) "
        "DETACH DELETE g "
        "RETURN count(g) AS deleted",
        {"generationId": graph_generation_id, "status": status},
    )
    rows = [dict(record) async for record in result]
    return bool(rows) and int(rows[0]["deleted"]) == 1


def _as_datetime(value: Any) -> datetime | None:
    """Neo4j temporals and driver-native datetimes, reduced to one aware type.

    The driver returns its own `DateTime` for a temporal property, which is not
    a `datetime.datetime`; comparing one to `now` in the reclaimer raises rather
    than answering. `to_native` is the driver's own conversion and is preferred
    when present so a stored temporal keeps its value rather than being
    reconstructed here.
    """

    if value is None:
        return None
    native = getattr(value, "to_native", None)
    if callable(native):
        value = native()
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
