"""Pure Cypher generation for graph writes -- no I/O, no Neo4j driver here.

Separating compilation from execution is what makes this unit-testable
without a live Neo4j instance: every function here takes typed mutations and
returns (cypher, parameters), nothing more. Values are always parameters,
never interpolated; every identifier (label, relationship type, property
name) is taken only from the active schema and passed through
validate_graph_identifier before being embedded in generated Cypher text.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
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


def compile_relationship_reconciliation(
    schema: ActiveSchema, relationship_id: str, *, graph_generation_id: str
) -> CompiledWrite:
    """Stage B: a graph-side MATCH/MERGE join on one relationship's configured match
    fields, producing the cross-source relationships Stage A alone cannot (its two
    endpoints may come from mutations extracted from entirely different source
    pages/sources, long since out of memory by the time the other side is written).

    This only ever creates relationships implied by the current node state; it does
    not remove relationships whose match no longer holds after a source update --
    that is a replace-child-set / stale-relationship reconciliation concern layered
    on top of this, not yet implemented here.
    """

    relationship = schema.graph.relationships.get(relationship_id)
    if relationship is None:
        raise WriteCompilationError(f"unknown relationship {relationship_id!r}")
    source_entity = schema.entities[relationship.source_entity_id]
    target_entity = schema.entities[relationship.target_entity_id]
    source_label = schema.entity_node(relationship.source_entity_id).label
    target_label = schema.entity_node(relationship.target_entity_id).label
    for label in (source_label, target_label, relationship.relationship_type):
        validate_graph_identifier(label)

    source_properties = [
        source_entity.fields[field_id].graph_property
        for field_id in relationship.source_match_fields
    ]
    target_properties = [
        target_entity.fields[field_id].graph_property
        for field_id in relationship.target_match_fields
    ]
    for prop in (*source_properties, *target_properties):
        validate_graph_identifier(prop)
    if len(source_properties) != len(target_properties):
        raise WriteCompilationError(
            f"relationship {relationship_id!r} has mismatched source/target match field counts"
        )

    join_predicate = " AND ".join(
        f"a.`{source_prop}` = b.`{target_prop}`"
        for source_prop, target_prop in zip(source_properties, target_properties, strict=True)
    )
    not_null_predicate = " AND ".join(f"a.`{prop}` IS NOT NULL" for prop in source_properties)
    cypher = (
        f"MATCH (a:`{source_label}` {{graph_generation_id: $generationId}}) "
        f"MATCH (b:`{target_label}` {{graph_generation_id: $generationId}}) "
        f"WHERE {join_predicate} AND {not_null_predicate} "
        f"MERGE (a)-[rel:`{relationship.relationship_type}`]->(b) "
        "RETURN count(rel) AS relationshipsWritten"
    )
    return CompiledWrite(cypher=cypher, parameters={"generationId": graph_generation_id})


def compile_relationship_cardinality_checks(
    schema: ActiveSchema, relationship_id: str, *, graph_generation_id: str
) -> tuple[CompiledWrite, ...]:
    """One check per configured cardinality bound, each returning a violation count.

    Run these after compile_relationship_reconciliation's MERGE in the same
    transaction; the caller raises and lets the transaction roll back if any
    check reports violations > 0, matching this codebase's fail-loudly stance
    on cardinality (never silently truncate or explode edges).
    """

    relationship = schema.graph.relationships.get(relationship_id)
    if relationship is None:
        raise WriteCompilationError(f"unknown relationship {relationship_id!r}")
    source_label = schema.entity_node(relationship.source_entity_id).label
    target_label = schema.entity_node(relationship.target_entity_id).label
    for label in (source_label, target_label, relationship.relationship_type):
        validate_graph_identifier(label)

    checks: list[CompiledWrite] = []
    if relationship.maximum_targets_per_source is not None:
        cypher = (
            f"MATCH (a:`{source_label}` {{graph_generation_id: $generationId}})"
            f"-[rel:`{relationship.relationship_type}`]->"
            f"(b:`{target_label}` {{graph_generation_id: $generationId}}) "
            "WITH a, count(rel) AS targetCount "
            "WHERE targetCount > $maxTargets "
            "RETURN count(a) AS violations"
        )
        checks.append(
            CompiledWrite(
                cypher=cypher,
                parameters={
                    "generationId": graph_generation_id,
                    "maxTargets": relationship.maximum_targets_per_source,
                },
            )
        )
    if relationship.maximum_sources_per_target is not None:
        cypher = (
            f"MATCH (a:`{source_label}` {{graph_generation_id: $generationId}})"
            f"-[rel:`{relationship.relationship_type}`]->"
            f"(b:`{target_label}` {{graph_generation_id: $generationId}}) "
            "WITH b, count(rel) AS sourceCount "
            "WHERE sourceCount > $maxSources "
            "RETURN count(b) AS violations"
        )
        checks.append(
            CompiledWrite(
                cypher=cypher,
                parameters={
                    "generationId": graph_generation_id,
                    "maxSources": relationship.maximum_sources_per_target,
                },
            )
        )
    return tuple(checks)


def _generation_scoped_pattern(row_field: str, key_names: frozenset[str]) -> str:
    key_inner = ", ".join(f"`{name}`: {row_field}.`{name}`" for name in sorted(key_names))
    return f"graph_generation_id: $generationId, {key_inner}" if key_inner else (
        "graph_generation_id: $generationId"
    )


def canonical_key_hash(key_values: Mapping[str, Any]) -> str:
    """A normalized, comparable identity for one node's key_values -- not a
    security hash, just canonicalization so ProjectionOwnership records work
    uniformly regardless of a key's field shape (single field or composite)."""

    canonical = json.dumps(dict(sorted(key_values.items())), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_ownership_upsert(
    *,
    graph_generation_id: str,
    projection_id: str,
    source_asset_id: str,
    source_identity: str,
    label: str,
    owned_children: tuple[tuple[str, Mapping[str, Any]], ...],
    source_version: str | None = None,
) -> CompiledWrite:
    """One ProjectionOwnership node per (entity_key_hash, owning parent document),
    MERGEd, plus an :OWNS relationship to the already-upserted business node.
    owned_children is (entity_key_hash, key_values) for every child currently
    present in the parent document being reconciled -- the caller (extraction
    layer) computes entity_key_hash via canonical_key_hash on each child's
    resolved key_values so both sides always agree on the same hash.
    """

    validate_graph_identifier(label)
    key_names = frozenset(owned_children[0][1]) if owned_children else frozenset()
    for _, key_values in owned_children:
        for name in key_values:
            validate_graph_identifier(name)
    rows = [{"entityKeyHash": key_hash, "keys": dict(key_values)} for key_hash, key_values in owned_children]
    key_inner = _generation_scoped_pattern("row.keys", key_names)
    cypher = (
        "UNWIND $rows AS row "
        "MERGE (o:ProjectionOwnership {"
        "graph_generation_id: $generationId, projection_id: $projectionId, "
        "source_asset_id: $sourceAssetId, source_identity: $sourceIdentity, "
        "entity_key_hash: row.entityKeyHash"
        "}) "
        "SET o.source_version = $sourceVersion "
        f"WITH o, row MATCH (n:`{label}` {{{key_inner}}}) "
        "MERGE (o)-[:OWNS]->(n)"
    )
    return CompiledWrite(
        cypher=cypher,
        parameters={
            "rows": rows,
            "generationId": graph_generation_id,
            "projectionId": projection_id,
            "sourceAssetId": source_asset_id,
            "sourceIdentity": source_identity,
            "sourceVersion": source_version,
        },
    )


def compile_ownership_query_existing(
    schema: ActiveSchema,
    projection_id: str,
    *,
    graph_generation_id: str,
    source_asset_id: str,
    source_identity: str,
) -> CompiledWrite:
    """Every entity_key_hash this parent document currently owns, before this
    reconciliation pass, plus the owned node's own key properties (aliased
    `key__<property>`) -- diffed by the caller against the freshly-extracted
    current child set to find which ownership records are now stale, and
    needed to re-check the owned node's remaining ownership count afterward.
    """

    node = schema.graph.nodes[projection_id]
    entity = schema.entities[node.entity_id]
    label = validate_graph_identifier(node.label)
    key_properties = [validate_graph_identifier(entity.fields[field_id].graph_property) for field_id in node.key_fields]
    key_returns = ", ".join(f"n.`{prop}` AS `key__{prop}`" for prop in key_properties)
    returns = "o.entity_key_hash AS entity_key_hash" + (f", {key_returns}" if key_returns else "")
    cypher = (
        "MATCH (o:ProjectionOwnership {"
        "graph_generation_id: $generationId, projection_id: $projectionId, "
        "source_asset_id: $sourceAssetId, source_identity: $sourceIdentity"
        f"}})-[:OWNS]->(n:`{label}`) "
        f"RETURN {returns}"
    )
    return CompiledWrite(
        cypher=cypher,
        parameters={
            "generationId": graph_generation_id,
            "projectionId": projection_id,
            "sourceAssetId": source_asset_id,
            "sourceIdentity": source_identity,
        },
    )


def compile_ownership_removal(
    *,
    graph_generation_id: str,
    projection_id: str,
    source_asset_id: str,
    source_identity: str,
    entity_key_hashes: tuple[str, ...],
) -> CompiledWrite:
    """Removes only the ownership records themselves (not the business node) --
    the caller checks compile_remaining_ownership_count per affected node
    afterward and applies the entity's EntityDeletionPolicy only when zero
    ownership records remain from any source document."""

    cypher = (
        "UNWIND $hashes AS hash "
        "MATCH (o:ProjectionOwnership {"
        "graph_generation_id: $generationId, projection_id: $projectionId, "
        "source_asset_id: $sourceAssetId, source_identity: $sourceIdentity, "
        "entity_key_hash: hash"
        "}) "
        "DETACH DELETE o"
    )
    return CompiledWrite(
        cypher=cypher,
        parameters={
            "hashes": list(entity_key_hashes),
            "generationId": graph_generation_id,
            "projectionId": projection_id,
            "sourceAssetId": source_asset_id,
            "sourceIdentity": source_identity,
        },
    )


def compile_remaining_ownership_count(
    *, graph_generation_id: str, label: str, key_values: Mapping[str, Any]
) -> CompiledWrite:
    validate_graph_identifier(label)
    for name in key_values:
        validate_graph_identifier(name)
    key_inner = _generation_scoped_pattern("$keys", frozenset(key_values))
    cypher = (
        f"MATCH (n:`{label}` {{{key_inner}}}) "
        "OPTIONAL MATCH (o:ProjectionOwnership)-[:OWNS]->(n) "
        "RETURN count(o) AS remaining"
    )
    return CompiledWrite(
        cypher=cypher, parameters={"generationId": graph_generation_id, "keys": dict(key_values)}
    )
