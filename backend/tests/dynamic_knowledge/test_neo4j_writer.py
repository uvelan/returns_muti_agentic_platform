from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.graph.neo4j_writer import (
    GenerationFencingError,
    Neo4jDynamicGraphWriter,
    ReceiptChecksumMismatchError,
    RelationshipCardinalityViolation,
    compute_payload_checksum,
)
from return_platform.dynamic_knowledge.graph.write_compiler import canonical_key_hash
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    GraphNodeMutation,
    GraphRelationshipMutation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for row in self._rows:
            yield row


class FakeTransaction:
    def __init__(
        self,
        *,
        fence_matched: int,
        existing_receipt_rows: list[dict[str, Any]] | None = None,
        relationships_written: int = 0,
        cardinality_violations: int = 0,
        existing_ownership_rows: list[dict[str, Any]] | None = None,
        remaining_ownership_count: int = 0,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fence_matched = fence_matched
        self._existing_receipt_rows = existing_receipt_rows or []
        self._relationships_written = relationships_written
        self._cardinality_violations = cardinality_violations
        self._existing_ownership_rows = existing_ownership_rows or []
        self._remaining_ownership_count = remaining_ownership_count

    async def run(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        self.calls.append((query, parameters))
        if "MATCH (g:GraphGeneration" in query:
            return FakeResult([{"matched": self._fence_matched}])
        if query.startswith("MATCH (r:GraphWriteReceipt"):
            return FakeResult(self._existing_receipt_rows)
        if "MERGE (a)-[rel:" in query:
            return FakeResult([{"relationshipsWritten": self._relationships_written}])
        if "AS violations" in query:
            return FakeResult([{"violations": self._cardinality_violations}])
        if "-[:OWNS]->(n:" in query and query.startswith("MATCH (o:ProjectionOwnership"):
            return FakeResult(self._existing_ownership_rows)
        if "AS remaining" in query:
            return FakeResult([{"remaining": self._remaining_ownership_count}])
        return FakeResult([])


class FakeSession:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute_write(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)


class FakeDriver:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def session(self, *, database: str | None = None) -> FakeSession:
        del database
        return FakeSession(self._tx)


def _batch() -> GraphMutationBatch:
    return GraphMutationBatch(
        node_mutations=(
            GraphNodeMutation(
                operation="UPSERT",
                projection_id="node_a",
                entity_id="entity_a",
                key_values={"configured_id": "PARENT-1"},
                properties={"configured_name": "n"},
            ),
        ),
        relationship_mutations=(
            GraphRelationshipMutation(
                operation="UPSERT",
                relationship_id="a_to_b",
                source_key_values={"configured_id": "PARENT-1"},
                target_key_values={"related_id": "CHILD-1"},
                properties={},
            ),
        ),
    )


@pytest.mark.asyncio
async def test_fence_check_runs_before_any_mutation(active_schema: ActiveSchema) -> None:
    tx = FakeTransaction(fence_matched=1)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    await writer.write(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        sync_run_id="run-1",
        chunk_id="chunk-1",
        batch=_batch(),
    )
    assert "MATCH (g:GraphGeneration" in tx.calls[0][0]


@pytest.mark.asyncio
async def test_fencing_mismatch_raises_and_writes_nothing(active_schema: ActiveSchema) -> None:
    tx = FakeTransaction(fence_matched=0)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    with pytest.raises(GenerationFencingError):
        await writer.write(
            schema=active_schema,
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
            sync_run_id="run-1",
            chunk_id="chunk-1",
            batch=_batch(),
        )
    assert len(tx.calls) == 1


@pytest.mark.asyncio
async def test_successful_write_persists_receipt_and_reports_counts(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=1)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    receipt = await writer.write(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        sync_run_id="run-1",
        chunk_id="chunk-1",
        batch=_batch(),
    )
    assert receipt.sync_run_id == "run-1"
    assert receipt.chunk_id == "chunk-1"
    assert receipt.graph_generation_id == "gen-1"
    assert receipt.nodes_written == 1
    assert receipt.relationships_written == 1
    queries = [query for query, _ in tx.calls]
    assert any("MERGE (n:`ConfiguredAlpha`" in q for q in queries)
    assert any("MERGE (a)-[rel:`CONFIGURED_LINK`]->(b)" in q for q in queries)
    assert any(q.startswith("MERGE (r:GraphWriteReceipt") for q in queries)
    assert queries[-1].startswith("MERGE (r:GraphWriteReceipt")


@pytest.mark.asyncio
async def test_replay_with_identical_checksum_returns_cached_receipt_without_rewriting(
    active_schema: ActiveSchema,
) -> None:
    batch = _batch()
    checksum = compute_payload_checksum(batch)
    existing_row = {
        "sync_run_id": "run-1",
        "chunk_id": "chunk-1",
        "payload_checksum": checksum,
        "graph_generation_id": "gen-1",
        "committed_at": "2026-08-04T00:00:00+00:00",
        "nodes_written": 1,
        "relationships_written": 1,
    }
    tx = FakeTransaction(fence_matched=1, existing_receipt_rows=[existing_row])
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    receipt = await writer.write(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        sync_run_id="run-1",
        chunk_id="chunk-1",
        batch=batch,
    )
    assert receipt.payload_checksum == checksum
    queries = [query for query, _ in tx.calls]
    assert not any("MERGE (n:" in q for q in queries)
    assert not any("MERGE (a)-[rel:" in q for q in queries)
    assert not any(q.startswith("MERGE (r:GraphWriteReceipt") for q in queries)


@pytest.mark.asyncio
async def test_replay_with_different_checksum_is_rejected(active_schema: ActiveSchema) -> None:
    existing_row = {
        "sync_run_id": "run-1",
        "chunk_id": "chunk-1",
        "payload_checksum": "different-checksum",
        "graph_generation_id": "gen-1",
        "committed_at": "2026-08-04T00:00:00+00:00",
        "nodes_written": 1,
        "relationships_written": 1,
    }
    tx = FakeTransaction(fence_matched=1, existing_receipt_rows=[existing_row])
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    with pytest.raises(ReceiptChecksumMismatchError):
        await writer.write(
            schema=active_schema,
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
            sync_run_id="run-1",
            chunk_id="chunk-1",
            batch=_batch(),
        )


@pytest.mark.asyncio
async def test_reconciliation_fence_check_runs_before_any_join(active_schema: ActiveSchema) -> None:
    tx = FakeTransaction(fence_matched=1, relationships_written=3)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    await writer.reconcile_relationships(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    assert "MATCH (g:GraphGeneration" in tx.calls[0][0]


@pytest.mark.asyncio
async def test_reconciliation_fencing_mismatch_raises_and_joins_nothing(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=0)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    with pytest.raises(GenerationFencingError):
        await writer.reconcile_relationships(
            schema=active_schema,
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
        )
    assert len(tx.calls) == 1


@pytest.mark.asyncio
async def test_reconciliation_runs_the_configured_join_and_reports_counts(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=1, relationships_written=3)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    counts = await writer.reconcile_relationships(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
    )
    assert counts == {"a_to_b": 3}
    queries = [query for query, _ in tx.calls]
    assert any("MERGE (a)-[rel:`CONFIGURED_LINK`]->(b)" in q for q in queries)


@pytest.mark.asyncio
async def test_reconciliation_narrowed_to_a_subset_of_relationship_ids(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=1, relationships_written=1)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    counts = await writer.reconcile_relationships(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        relationship_ids=("a_to_b",),
    )
    assert set(counts) == {"a_to_b"}


@pytest.mark.asyncio
async def test_reconciliation_raises_on_cardinality_violation(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["graph"]["relationships"]["a_to_b"]["maximum_targets_per_source"] = 1
    schema = ActiveSchema.model_validate(raw)
    tx = FakeTransaction(fence_matched=1, relationships_written=3, cardinality_violations=2)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    with pytest.raises(RelationshipCardinalityViolation):
        await writer.reconcile_relationships(
            schema=schema,
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
        )


@pytest.mark.asyncio
async def test_ownership_reconciliation_fence_check_runs_first(active_schema: ActiveSchema) -> None:
    tx = FakeTransaction(fence_matched=1)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    child_hash = canonical_key_hash({"related_id": "CHILD-1"})
    await writer.reconcile_child_ownership(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        projection_id="node_b",
        source_asset_id="source_b",
        source_identity="parent-doc-1",
        current_children=((child_hash, {"related_id": "CHILD-1"}),),
    )
    assert "MATCH (g:GraphGeneration" in tx.calls[0][0]


@pytest.mark.asyncio
async def test_ownership_reconciliation_fencing_mismatch_raises_and_writes_nothing(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=0)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    with pytest.raises(GenerationFencingError):
        await writer.reconcile_child_ownership(
            schema=active_schema,
            graph_generation_id="gen-1",
            fencing_token=1,
            expected_generation_status=GraphGenerationStatus.ACTIVE,
            projection_id="node_b",
            source_asset_id="source_b",
            source_identity="parent-doc-1",
            current_children=(),
        )
    assert len(tx.calls) == 1


@pytest.mark.asyncio
async def test_ownership_reconciliation_upserts_current_children(
    active_schema: ActiveSchema,
) -> None:
    tx = FakeTransaction(fence_matched=1)
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    child_hash = canonical_key_hash({"related_id": "CHILD-1"})
    await writer.reconcile_child_ownership(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        projection_id="node_b",
        source_asset_id="source_b",
        source_identity="parent-doc-1",
        current_children=((child_hash, {"related_id": "CHILD-1"}),),
    )
    queries = [query for query, _ in tx.calls]
    assert any("MERGE (o:ProjectionOwnership" in q for q in queries)


@pytest.mark.asyncio
async def test_ownership_reconciliation_removes_stale_child_and_deletes_orphaned_node(
    active_schema: ActiveSchema,
) -> None:
    stale_hash = canonical_key_hash({"related_id": "CHILD-OLD"})
    tx = FakeTransaction(
        fence_matched=1,
        existing_ownership_rows=[{"entity_key_hash": stale_hash, "key__related_id": "CHILD-OLD"}],
        remaining_ownership_count=0,
    )
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    result = await writer.reconcile_child_ownership(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        projection_id="node_b",
        source_asset_id="source_b",
        source_identity="parent-doc-1",
        current_children=(),  # CHILD-OLD is no longer in the parent document
    )
    assert result.removed_ownership_count == 1
    assert result.deleted_node_keys == ({"related_id": "CHILD-OLD"},)
    queries = [query for query, _ in tx.calls]
    assert any("DETACH DELETE o" in q for q in queries)
    assert any("DETACH DELETE n" in q for q in queries)


@pytest.mark.asyncio
async def test_ownership_reconciliation_keeps_node_still_owned_by_another_parent(
    active_schema: ActiveSchema,
) -> None:
    stale_hash = canonical_key_hash({"related_id": "CHILD-SHARED"})
    tx = FakeTransaction(
        fence_matched=1,
        existing_ownership_rows=[
            {"entity_key_hash": stale_hash, "key__related_id": "CHILD-SHARED"}
        ],
        remaining_ownership_count=1,  # a different parent document still owns it
    )
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    result = await writer.reconcile_child_ownership(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        projection_id="node_b",
        source_asset_id="source_b",
        source_identity="parent-doc-1",
        current_children=(),
    )
    assert result.removed_ownership_count == 1
    assert result.deleted_node_keys == ()
    queries = [query for query, _ in tx.calls]
    assert not any("DETACH DELETE n" in q for q in queries)


@pytest.mark.asyncio
async def test_ownership_reconciliation_leaves_still_current_children_alone(
    active_schema: ActiveSchema,
) -> None:
    kept_hash = canonical_key_hash({"related_id": "CHILD-KEPT"})
    tx = FakeTransaction(
        fence_matched=1,
        existing_ownership_rows=[{"entity_key_hash": kept_hash, "key__related_id": "CHILD-KEPT"}],
    )
    writer = Neo4jDynamicGraphWriter(FakeDriver(tx))
    result = await writer.reconcile_child_ownership(
        schema=active_schema,
        graph_generation_id="gen-1",
        fencing_token=1,
        expected_generation_status=GraphGenerationStatus.ACTIVE,
        projection_id="node_b",
        source_asset_id="source_b",
        source_identity="parent-doc-1",
        current_children=((kept_hash, {"related_id": "CHILD-KEPT"}),),
    )
    assert result.removed_ownership_count == 0
    assert result.deleted_node_keys == ()
    queries = [query for query, _ in tx.calls]
    assert not any("ProjectionOwnership {" in q and "DETACH DELETE o" in q for q in queries)
