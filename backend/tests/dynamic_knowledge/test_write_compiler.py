from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.graph.write_compiler import (
    WriteCompilationError,
    compile_generation_fence,
    compile_node_writes,
    compile_receipt_lookup,
    compile_receipt_store,
    compile_relationship_writes,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphNodeMutation,
    GraphRelationshipMutation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


def test_generation_fence_is_parameterized() -> None:
    compiled = compile_generation_fence(
        graph_generation_id="gen-1", fencing_token=7, expected_status="ACTIVE"
    )
    assert "MATCH (g:GraphGeneration" in compiled.cypher
    assert "$generationId" in compiled.cypher
    assert compiled.parameters == {
        "generationId": "gen-1",
        "fencingToken": 7,
        "expectedStatus": "ACTIVE",
    }


def test_node_upsert_merges_on_key_and_sets_properties(active_schema: ActiveSchema) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={"configured_name": "n", "configured_count": 1},
        ),
    )
    statements = compile_node_writes(active_schema, mutations)
    assert len(statements) == 1
    statement = statements[0]
    assert "MERGE (n:`ConfiguredAlpha`" in statement.cypher
    assert "`configured_id`: row.keys.`configured_id`" in statement.cypher
    assert "SET n += row.properties" in statement.cypher
    assert statement.parameters == {
        "rows": [
            {
                "keys": {"configured_id": "PARENT-1"},
                "properties": {"configured_name": "n", "configured_count": 1},
            }
        ]
    }


def test_node_upsert_groups_multiple_mutations_into_one_statement(
    active_schema: ActiveSchema,
) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={},
        ),
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-2"},
            properties={},
        ),
    )
    statements = compile_node_writes(active_schema, mutations)
    assert len(statements) == 1
    assert len(statements[0].parameters["rows"]) == 2


def test_node_operations_produce_distinct_statements(active_schema: ActiveSchema) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={},
        ),
        GraphNodeMutation(
            operation="HARD_DELETE",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-2"},
            properties={},
        ),
        GraphNodeMutation(
            operation="TOMBSTONE",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-3"},
            properties={},
        ),
        GraphNodeMutation(
            operation="DETACH_ONLY",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-4"},
            properties={},
        ),
    )
    statements = compile_node_writes(active_schema, mutations)
    assert len(statements) == 4
    cyphers = [statement.cypher for statement in statements]
    assert any("DETACH DELETE n" in c and "SET" not in c for c in cyphers)
    assert any("n.tombstoned = true" in c for c in cyphers)
    assert any("OPTIONAL MATCH (n)-[r]-() DELETE r" in c for c in cyphers)


def test_node_writes_reject_inconsistent_key_shape_within_a_group(
    active_schema: ActiveSchema,
) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={},
        ),
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-2", "extra": "x"},
            properties={},
        ),
    )
    with pytest.raises(WriteCompilationError, match="identical key fields"):
        compile_node_writes(active_schema, mutations)


def test_node_writes_reject_unknown_projection(active_schema: ActiveSchema) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="does_not_exist",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={},
        ),
    )
    with pytest.raises(WriteCompilationError, match="unknown projection"):
        compile_node_writes(active_schema, mutations)


def test_relationship_upsert_matches_both_endpoints_and_merges(
    active_schema: ActiveSchema,
) -> None:
    mutations = (
        GraphRelationshipMutation(
            operation="UPSERT",
            relationship_id="a_to_b",
            source_key_values={"configured_id": "PARENT-1"},
            target_key_values={"related_id": "CHILD-1"},
            properties={},
        ),
    )
    statements = compile_relationship_writes(active_schema, mutations)
    assert len(statements) == 1
    statement = statements[0]
    assert "MATCH (a:`ConfiguredAlpha`" in statement.cypher
    assert "MATCH (b:`ConfiguredBeta`" in statement.cypher
    assert "MERGE (a)-[rel:`CONFIGURED_LINK`]->(b)" in statement.cypher
    assert statement.parameters == {
        "rows": [
            {
                "sourceKeys": {"configured_id": "PARENT-1"},
                "targetKeys": {"related_id": "CHILD-1"},
                "properties": {},
            }
        ]
    }


def test_relationship_delete_matches_and_deletes(active_schema: ActiveSchema) -> None:
    mutations = (
        GraphRelationshipMutation(
            operation="DELETE",
            relationship_id="a_to_b",
            source_key_values={"configured_id": "PARENT-1"},
            target_key_values={"related_id": "CHILD-1"},
            properties={},
        ),
    )
    statements = compile_relationship_writes(active_schema, mutations)
    assert len(statements) == 1
    assert "DELETE rel" in statements[0].cypher
    assert "MERGE" not in statements[0].cypher


def test_relationship_writes_reject_unknown_relationship(active_schema: ActiveSchema) -> None:
    mutations = (
        GraphRelationshipMutation(
            operation="UPSERT",
            relationship_id="does_not_exist",
            source_key_values={"configured_id": "PARENT-1"},
            target_key_values={"related_id": "CHILD-1"},
            properties={},
        ),
    )
    with pytest.raises(WriteCompilationError, match="unknown relationship"):
        compile_relationship_writes(active_schema, mutations)


def test_receipt_lookup_and_store_are_parameterized() -> None:
    lookup = compile_receipt_lookup(sync_run_id="run-1", chunk_id="chunk-1")
    assert lookup.parameters == {"syncRunId": "run-1", "chunkId": "chunk-1"}

    store = compile_receipt_store(
        sync_run_id="run-1",
        chunk_id="chunk-1",
        payload_checksum="deadbeef",
        graph_generation_id="gen-1",
        committed_at="2026-08-04T00:00:00+00:00",
        nodes_written=2,
        relationships_written=1,
    )
    assert "MERGE (r:GraphWriteReceipt" in store.cypher
    assert store.parameters["payloadChecksum"] == "deadbeef"
    assert store.parameters["nodesWritten"] == 2
