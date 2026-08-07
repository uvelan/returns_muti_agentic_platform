from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.graph.write_compiler import (
    WriteCompilationError,
    compile_generation_fence,
    compile_node_writes,
    compile_receipt_lookup,
    compile_receipt_store,
    compile_relationship_cardinality_checks,
    compile_relationship_reconciliation,
    compile_relationship_writes,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphNodeMutation,
    GraphRelationshipMutation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

GEN = "gen-1"


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


def test_node_upsert_merges_on_generation_scoped_key_and_sets_properties(
    active_schema: ActiveSchema,
) -> None:
    mutations = (
        GraphNodeMutation(
            operation="UPSERT",
            projection_id="node_a",
            entity_id="entity_a",
            key_values={"configured_id": "PARENT-1"},
            properties={"configured_name": "n", "configured_count": 1},
        ),
    )
    statements = compile_node_writes(active_schema, mutations, graph_generation_id=GEN)
    assert len(statements) == 1
    statement = statements[0]
    assert "MERGE (n:`ConfiguredAlpha`" in statement.cypher
    assert "graph_generation_id: $generationId" in statement.cypher
    assert "`configured_id`: row.keys.`configured_id`" in statement.cypher
    assert "SET n += row.properties" in statement.cypher
    assert statement.parameters == {
        "rows": [
            {
                "keys": {"configured_id": "PARENT-1"},
                "properties": {"configured_name": "n", "configured_count": 1},
            }
        ],
        "generationId": GEN,
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
    statements = compile_node_writes(active_schema, mutations, graph_generation_id=GEN)
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
    statements = compile_node_writes(active_schema, mutations, graph_generation_id=GEN)
    assert len(statements) == 4
    cyphers = [statement.cypher for statement in statements]
    assert all("graph_generation_id: $generationId" in c for c in cyphers)
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
        compile_node_writes(active_schema, mutations, graph_generation_id=GEN)


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
        compile_node_writes(active_schema, mutations, graph_generation_id=GEN)


def test_relationship_upsert_matches_both_endpoints_generation_scoped_and_merges(
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
    statements = compile_relationship_writes(active_schema, mutations, graph_generation_id=GEN)
    assert len(statements) == 1
    statement = statements[0]
    assert "MATCH (a:`ConfiguredAlpha`" in statement.cypher
    assert "MATCH (b:`ConfiguredBeta`" in statement.cypher
    assert statement.cypher.count("graph_generation_id: $generationId") == 2
    assert "MERGE (a)-[rel:`CONFIGURED_LINK`]->(b)" in statement.cypher
    assert statement.parameters == {
        "rows": [
            {
                "sourceKeys": {"configured_id": "PARENT-1"},
                "targetKeys": {"related_id": "CHILD-1"},
                "properties": {},
            }
        ],
        "generationId": GEN,
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
    statements = compile_relationship_writes(active_schema, mutations, graph_generation_id=GEN)
    assert len(statements) == 1
    assert "DELETE rel" in statements[0].cypher
    assert "MERGE" not in statements[0].cypher
    assert statements[0].cypher.count("graph_generation_id: $generationId") == 2


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
        compile_relationship_writes(active_schema, mutations, graph_generation_id=GEN)


def test_relationship_reconciliation_joins_on_configured_match_fields(
    active_schema: ActiveSchema,
) -> None:
    compiled = compile_relationship_reconciliation(active_schema, "a_to_b", graph_generation_id=GEN)
    assert "MATCH (a:`ConfiguredAlpha` {graph_generation_id: $generationId})" in compiled.cypher
    assert "MATCH (b:`ConfiguredBeta` {graph_generation_id: $generationId})" in compiled.cypher
    assert "a.`configured_id` = b.`configured_parent_id`" in compiled.cypher
    assert "a.`configured_id` IS NOT NULL" in compiled.cypher
    assert "MERGE (a)-[rel:`CONFIGURED_LINK`]->(b)" in compiled.cypher
    assert compiled.parameters == {"generationId": GEN}


def test_relationship_reconciliation_rejects_unknown_relationship(
    active_schema: ActiveSchema,
) -> None:
    with pytest.raises(WriteCompilationError, match="unknown relationship"):
        compile_relationship_reconciliation(
            active_schema, "does_not_exist", graph_generation_id=GEN
        )


def test_cardinality_checks_empty_when_no_bounds_configured(active_schema: ActiveSchema) -> None:
    checks = compile_relationship_cardinality_checks(
        active_schema, "a_to_b", graph_generation_id=GEN
    )
    assert checks == ()


def test_cardinality_checks_compiled_when_bounds_configured(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["graph"]["relationships"]["a_to_b"]["maximum_targets_per_source"] = 3
    raw["graph"]["relationships"]["a_to_b"]["maximum_sources_per_target"] = 1
    schema = ActiveSchema.model_validate(raw)
    checks = compile_relationship_cardinality_checks(schema, "a_to_b", graph_generation_id=GEN)
    assert len(checks) == 2
    assert any(check.parameters.get("maxTargets") == 3 for check in checks)
    assert any(check.parameters.get("maxSources") == 1 for check in checks)
    assert all("violations" in check.cypher for check in checks)


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
