"""Pure Cypher generation for graph writes -- no I/O, no Neo4j driver here.

Separating compilation from execution is what makes this unit-testable
without a live Neo4j instance: every function here takes typed mutations and
returns (cypher, parameters), nothing more. Values are always parameters,
never interpolated; every identifier (label, relationship type, property
name) is taken only from the active schema and passed through
validate_graph_identifier before being embedded in generated Cypher text.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphNodeMutation,
    GraphRelationshipMutation,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, validate_graph_identifier


@dataclass(frozen=True, slots=True)
class CompiledWrite:
    cypher: str
    parameters: dict[str, Any]


class WriteCompilationError(ValueError):
    """A mutation could not be compiled into valid, safe Cypher."""


def compile_generation_fence(
    *, graph_generation_id: str, fencing_token: int, expected_status: str
) -> CompiledWrite:
    """A single-row check the caller runs first and must see matched == 1 before writing."""

    return CompiledWrite(
        cypher=(
            "MATCH (g:GraphGeneration {"
            "generation_id: $generationId, fencing_token: $fencingToken, status: $expectedStatus"
            "}) RETURN count(g) AS matched"
        ),
        parameters={
            "generationId": graph_generation_id,
            "fencingToken": fencing_token,
            "expectedStatus": expected_status,
        },
    )


def compile_receipt_lookup(*, sync_run_id: str, chunk_id: str) -> CompiledWrite:
    return CompiledWrite(
        cypher=(
            "MATCH (r:GraphWriteReceipt {sync_run_id: $syncRunId, chunk_id: $chunkId}) "
            "RETURN r.sync_run_id AS sync_run_id, r.chunk_id AS chunk_id, "
            "r.payload_checksum AS payload_checksum, r.graph_generation_id AS graph_generation_id, "
            "r.committed_at AS committed_at, r.nodes_written AS nodes_written, "
            "r.relationships_written AS relationships_written"
        ),
        parameters={"syncRunId": sync_run_id, "chunkId": chunk_id},
    )


def compile_receipt_store(
    *,
    sync_run_id: str,
    chunk_id: str,
    payload_checksum: str,
    graph_generation_id: str,
    committed_at: str,
    nodes_written: int,
    relationships_written: int,
) -> CompiledWrite:
    return CompiledWrite(
        cypher=(
            "MERGE (r:GraphWriteReceipt {sync_run_id: $syncRunId, chunk_id: $chunkId}) "
            "SET r.payload_checksum = $payloadChecksum, "
            "r.graph_generation_id = $graphGenerationId, "
            "r.committed_at = $committedAt, "
            "r.nodes_written = $nodesWritten, "
            "r.relationships_written = $relationshipsWritten"
        ),
        parameters={
            "syncRunId": sync_run_id,
            "chunkId": chunk_id,
            "payloadChecksum": payload_checksum,
            "graphGenerationId": graph_generation_id,
            "committedAt": committed_at,
            "nodesWritten": nodes_written,
            "relationshipsWritten": relationships_written,
        },
    )


def compile_node_writes(
    schema: ActiveSchema,
    mutations: tuple[GraphNodeMutation, ...],
    *,
    graph_generation_id: str,
) -> tuple[CompiledWrite, ...]:
    """Every MATCH/MERGE below is scoped by graph_generation_id in addition to the
    entity's configured (logical) key fields -- the physical Neo4j uniqueness is
    always (graph_generation_id, logical key), so two generations sharing the
    same business key never collide. graph_generation_id is never part of the
    configured logical key itself (see the plan's "logical vs. physical keys")."""

    grouped: dict[tuple[str, str], list[GraphNodeMutation]] = defaultdict(list)
    for mutation in mutations:
        node = schema.graph.nodes.get(mutation.projection_id)
        if node is None:
            raise WriteCompilationError(
                f"mutation references unknown projection {mutation.projection_id!r}"
            )
        grouped[(node.label, mutation.operation)].append(mutation)

    statements: list[CompiledWrite] = []
    for (label, operation), group in grouped.items():
        validate_graph_identifier(label)
        _validate_key_shape_consistent(group)
        if operation == "UPSERT":
            statements.append(_compile_node_upsert(label, group, graph_generation_id))
        elif operation == "HARD_DELETE":
            statements.append(_compile_node_hard_delete(label, group, graph_generation_id))
        elif operation == "TOMBSTONE":
            statements.append(_compile_node_tombstone(label, group, graph_generation_id))
        elif operation == "DETACH_ONLY":
            statements.append(_compile_node_detach_only(label, group, graph_generation_id))
        else:
            raise WriteCompilationError(f"unsupported node mutation operation: {operation!r}")
    return tuple(statements)


def compile_relationship_writes(
    schema: ActiveSchema,
    mutations: tuple[GraphRelationshipMutation, ...],
    *,
    graph_generation_id: str,
) -> tuple[CompiledWrite, ...]:
    """Both endpoints are matched within the same graph_generation_id -- a relationship
    can never link nodes from two different generations."""

    grouped: dict[tuple[str, str], list[GraphRelationshipMutation]] = defaultdict(list)
    for mutation in mutations:
        relationship = schema.graph.relationships.get(mutation.relationship_id)
        if relationship is None:
            raise WriteCompilationError(
                f"mutation references unknown relationship {mutation.relationship_id!r}"
            )
        grouped[(mutation.relationship_id, mutation.operation)].append(mutation)

    statements: list[CompiledWrite] = []
    for (relationship_id, operation), group in grouped.items():
        relationship = schema.graph.relationships[relationship_id]
        source_label = schema.entity_node(relationship.source_entity_id).label
        target_label = schema.entity_node(relationship.target_entity_id).label
        for label in (source_label, target_label, relationship.relationship_type):
            validate_graph_identifier(label)
        _validate_relationship_key_shape_consistent(group)
        if operation == "UPSERT":
            statements.append(
                _compile_relationship_upsert(
                    relationship.relationship_type,
                    source_label,
                    target_label,
                    group,
                    graph_generation_id,
                )
            )
        elif operation == "DELETE":
            statements.append(
                _compile_relationship_delete(
                    relationship.relationship_type,
                    source_label,
                    target_label,
                    group,
                    graph_generation_id,
                )
            )
        else:
            raise WriteCompilationError(
                f"unsupported relationship mutation operation: {operation!r}"
            )
    return tuple(statements)


def _validate_key_shape_consistent(group: list[GraphNodeMutation]) -> None:
    expected = frozenset(group[0].key_values)
    for mutation in group:
        if frozenset(mutation.key_values) != expected:
            raise WriteCompilationError(
                "node mutations for the same label/operation must share identical key fields"
            )
        for name in mutation.key_values:
            validate_graph_identifier(name)


def _validate_relationship_key_shape_consistent(group: list[GraphRelationshipMutation]) -> None:
    expected_source = frozenset(group[0].source_key_values)
    expected_target = frozenset(group[0].target_key_values)
    for mutation in group:
        if frozenset(mutation.source_key_values) != expected_source or frozenset(
            mutation.target_key_values
        ) != expected_target:
            raise WriteCompilationError(
                "relationship mutations for the same relationship/operation must share "
                "identical key fields"
            )
        for name in (*mutation.source_key_values, *mutation.target_key_values):
            validate_graph_identifier(name)


def _compile_node_upsert(
    label: str, group: list[GraphNodeMutation], graph_generation_id: str
) -> CompiledWrite:
    key_names = frozenset(group[0].key_values)
    rows = [{"keys": m.key_values, "properties": m.properties} for m in group]
    key_inner = _generation_scoped_pattern("row.keys", key_names)
    cypher = (
        "UNWIND $rows AS row "
        f"MERGE (n:`{label}` {{{key_inner}}}) "
        "SET n += row.properties"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _compile_node_hard_delete(
    label: str, group: list[GraphNodeMutation], graph_generation_id: str
) -> CompiledWrite:
    key_names = frozenset(group[0].key_values)
    rows = [{"keys": m.key_values} for m in group]
    key_inner = _generation_scoped_pattern("row.keys", key_names)
    cypher = f"UNWIND $rows AS row MATCH (n:`{label}` {{{key_inner}}}) DETACH DELETE n"
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _compile_node_tombstone(
    label: str, group: list[GraphNodeMutation], graph_generation_id: str
) -> CompiledWrite:
    key_names = frozenset(group[0].key_values)
    rows = [{"keys": m.key_values} for m in group]
    key_inner = _generation_scoped_pattern("row.keys", key_names)
    cypher = (
        "UNWIND $rows AS row "
        f"MATCH (n:`{label}` {{{key_inner}}}) "
        "SET n.tombstoned = true, n.tombstonedAt = datetime()"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _compile_node_detach_only(
    label: str, group: list[GraphNodeMutation], graph_generation_id: str
) -> CompiledWrite:
    key_names = frozenset(group[0].key_values)
    rows = [{"keys": m.key_values} for m in group]
    key_inner = _generation_scoped_pattern("row.keys", key_names)
    cypher = (
        "UNWIND $rows AS row "
        f"MATCH (n:`{label}` {{{key_inner}}}) "
        "OPTIONAL MATCH (n)-[r]-() DELETE r"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _compile_relationship_upsert(
    relationship_type: str,
    source_label: str,
    target_label: str,
    group: list[GraphRelationshipMutation],
    graph_generation_id: str,
) -> CompiledWrite:
    source_keys = frozenset(group[0].source_key_values)
    target_keys = frozenset(group[0].target_key_values)
    rows = [
        {
            "sourceKeys": m.source_key_values,
            "targetKeys": m.target_key_values,
            "properties": m.properties,
        }
        for m in group
    ]
    source_inner = _generation_scoped_pattern("row.sourceKeys", source_keys)
    target_inner = _generation_scoped_pattern("row.targetKeys", target_keys)
    cypher = (
        "UNWIND $rows AS row "
        f"MATCH (a:`{source_label}` {{{source_inner}}}) "
        f"MATCH (b:`{target_label}` {{{target_inner}}}) "
        f"MERGE (a)-[rel:`{relationship_type}`]->(b) "
        "SET rel += row.properties"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _compile_relationship_delete(
    relationship_type: str,
    source_label: str,
    target_label: str,
    group: list[GraphRelationshipMutation],
    graph_generation_id: str,
) -> CompiledWrite:
    source_keys = frozenset(group[0].source_key_values)
    target_keys = frozenset(group[0].target_key_values)
    rows = [{"sourceKeys": m.source_key_values, "targetKeys": m.target_key_values} for m in group]
    source_inner = _generation_scoped_pattern("row.sourceKeys", source_keys)
    target_inner = _generation_scoped_pattern("row.targetKeys", target_keys)
    cypher = (
        "UNWIND $rows AS row "
        f"MATCH (a:`{source_label}` {{{source_inner}}})"
        f"-[rel:`{relationship_type}`]->"
        f"(b:`{target_label}` {{{target_inner}}}) "
        "DELETE rel"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"rows": rows, "generationId": graph_generation_id}
    )


def _generation_scoped_pattern(row_field: str, key_names: frozenset[str]) -> str:
    key_inner = ", ".join(f"`{name}`: {row_field}.`{name}`" for name in sorted(key_names))
    return f"graph_generation_id: $generationId, {key_inner}" if key_inner else (
        "graph_generation_id: $generationId"
    )
