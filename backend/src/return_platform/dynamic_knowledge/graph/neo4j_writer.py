"""Generation-fenced, idempotent Neo4j writer for GraphMutationBatch output.

Cypher generation lives in write_compiler.py (pure, unit-testable without a
driver); this module only orchestrates: fencing check first, then receipt
lookup/creation, then dispatch to the compiled statements, all inside one
write transaction so a graph commit without a matching receipt -- or a
receipt without the graph effects it claims -- can never happen from a
partial commit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from return_platform.dynamic_knowledge.graph.generation import (
    GraphGenerationStatus,
    GraphWriteReceipt,
)
from return_platform.dynamic_knowledge.graph.write_compiler import (
    compile_generation_fence,
    compile_node_writes,
    compile_receipt_lookup,
    compile_receipt_store,
    compile_relationship_cardinality_checks,
    compile_relationship_reconciliation,
    compile_relationship_writes,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import GraphMutationBatch
from return_platform.dynamic_knowledge.schema import ActiveSchema


class GenerationFencingError(RuntimeError):
    """The target generation did not match the expected fencing token/status."""


class ReceiptChecksumMismatchError(RuntimeError):
    """The same (sync_run_id, chunk_id) was retried with a different payload."""


class RelationshipCardinalityViolation(RuntimeError):
    """A Stage B reconciliation join produced more edges than a relationship's
    configured cardinality bound allows -- the whole reconciliation transaction
    rolls back rather than leaving a partially-joined graph."""


class GraphWriteTransaction(Protocol):
    """The minimal transaction surface the writer needs.

    Satisfied by neo4j.AsyncManagedTransaction in production and a recording
    fake transaction in tests -- matching this codebase's existing
    `result = await tx.run(...); [dict(record) async for record in result]`
    idiom from neo4j_gateway.py.
    """

    async def run(self, query: str, parameters: dict[str, Any]) -> Any: ...


class GraphSession(Protocol):
    async def execute_write(self, work: Any, **kwargs: Any) -> Any: ...

    async def __aenter__(self) -> "GraphSession": ...

    async def __aexit__(self, *exc_info: Any) -> None: ...


class GraphDriver(Protocol):
    def session(self, *, database: str | None = None) -> GraphSession: ...


def compute_payload_checksum(batch: GraphMutationBatch) -> str:
    canonical = json.dumps(
        {
            "node_mutations": [m.model_dump(mode="json") for m in batch.node_mutations],
            "relationship_mutations": [
                m.model_dump(mode="json") for m in batch.relationship_mutations
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Neo4jDynamicGraphWriter:
    def __init__(self, driver: GraphDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str,
        chunk_id: str,
        batch: GraphMutationBatch,
    ) -> GraphWriteReceipt:
        payload_checksum = compute_payload_checksum(batch)
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                _write_chunk,
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=expected_generation_status,
                sync_run_id=sync_run_id,
                chunk_id=chunk_id,
                payload_checksum=payload_checksum,
                batch=batch,
            )

    async def reconcile_relationships(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        relationship_ids: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        """Stage B: graph-side MATCH/MERGE joins for cross-source relationships.

        Runs after Stage A (write()) has materialized every participating
        source's nodes for this generation. Defaults to every relationship in
        the schema; a caller may narrow to a subset (e.g. only relationships
        touched by an incremental sync's affected keys).
        """

        target_ids = relationship_ids or tuple(schema.graph.relationships)
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(
                _reconcile_relationships,
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=expected_generation_status,
                relationship_ids=target_ids,
            )


async def _reconcile_relationships(
    tx: GraphWriteTransaction,
    *,
    schema: ActiveSchema,
    graph_generation_id: str,
    fencing_token: int,
    expected_generation_status: GraphGenerationStatus,
    relationship_ids: tuple[str, ...],
) -> dict[str, int]:
    fence = compile_generation_fence(
        graph_generation_id=graph_generation_id,
        fencing_token=fencing_token,
        expected_status=expected_generation_status.value,
    )
    fence_rows = await _rows(tx, fence)
    matched = fence_rows[0]["matched"] if fence_rows else 0
    if matched != 1:
        raise GenerationFencingError(
            f"generation {graph_generation_id!r} did not match expected fencing "
            f"token={fencing_token!r} status={expected_generation_status.value!r}"
        )

    counts: dict[str, int] = {}
    for relationship_id in relationship_ids:
        join = compile_relationship_reconciliation(
            schema, relationship_id, graph_generation_id=graph_generation_id
        )
        join_rows = await _rows(tx, join)
        counts[relationship_id] = join_rows[0]["relationshipsWritten"] if join_rows else 0

        for check in compile_relationship_cardinality_checks(
            schema, relationship_id, graph_generation_id=graph_generation_id
        ):
            check_rows = await _rows(tx, check)
            violations = check_rows[0]["violations"] if check_rows else 0
            if violations:
                raise RelationshipCardinalityViolation(
                    f"relationship {relationship_id!r} exceeds its configured cardinality "
                    f"bound ({violations} violating node(s))"
                )
    return counts


async def _write_chunk(
    tx: GraphWriteTransaction,
    *,
    schema: ActiveSchema,
    graph_generation_id: str,
    fencing_token: int,
    expected_generation_status: GraphGenerationStatus,
    sync_run_id: str,
    chunk_id: str,
    payload_checksum: str,
    batch: GraphMutationBatch,
) -> GraphWriteReceipt:
    fence = compile_generation_fence(
        graph_generation_id=graph_generation_id,
        fencing_token=fencing_token,
        expected_status=expected_generation_status.value,
    )
    fence_rows = await _rows(tx, fence)
    matched = fence_rows[0]["matched"] if fence_rows else 0
    if matched != 1:
        raise GenerationFencingError(
            f"generation {graph_generation_id!r} did not match expected fencing "
            f"token={fencing_token!r} status={expected_generation_status.value!r}"
        )

    existing = await _find_existing_receipt(tx, sync_run_id=sync_run_id, chunk_id=chunk_id)
    if existing is not None:
        if existing.payload_checksum != payload_checksum:
            raise ReceiptChecksumMismatchError(
                f"chunk {sync_run_id!r}/{chunk_id!r} retried with a different payload"
            )
        return existing

    for statement in compile_node_writes(
        schema, batch.node_mutations, graph_generation_id=graph_generation_id
    ):
        await tx.run(statement.cypher, statement.parameters)
    for statement in compile_relationship_writes(
        schema, batch.relationship_mutations, graph_generation_id=graph_generation_id
    ):
        await tx.run(statement.cypher, statement.parameters)

    committed_at = datetime.now(UTC)
    store = compile_receipt_store(
        sync_run_id=sync_run_id,
        chunk_id=chunk_id,
        payload_checksum=payload_checksum,
        graph_generation_id=graph_generation_id,
        committed_at=committed_at.isoformat(),
        nodes_written=len(batch.node_mutations),
        relationships_written=len(batch.relationship_mutations),
    )
    await tx.run(store.cypher, store.parameters)

    return GraphWriteReceipt(
        sync_run_id=sync_run_id,
        chunk_id=chunk_id,
        payload_checksum=payload_checksum,
        graph_generation_id=graph_generation_id,
        committed_at=committed_at,
        nodes_written=len(batch.node_mutations),
        relationships_written=len(batch.relationship_mutations),
    )


async def _find_existing_receipt(
    tx: GraphWriteTransaction, *, sync_run_id: str, chunk_id: str
) -> GraphWriteReceipt | None:
    lookup = compile_receipt_lookup(sync_run_id=sync_run_id, chunk_id=chunk_id)
    rows = await _rows(tx, lookup)
    if not rows:
        return None
    row = rows[0]
    return GraphWriteReceipt(
        sync_run_id=row["sync_run_id"],
        chunk_id=row["chunk_id"],
        payload_checksum=row["payload_checksum"],
        graph_generation_id=row["graph_generation_id"],
        committed_at=row["committed_at"],
        nodes_written=row["nodes_written"],
        relationships_written=row["relationships_written"],
    )


async def _rows(tx: GraphWriteTransaction, statement: Any) -> list[dict[str, Any]]:
    result = await tx.run(statement.cypher, statement.parameters)
    return [dict(record) async for record in result]
