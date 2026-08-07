"""Generic graph projection from logical dynamic record mutations.

Relationship removal for a deleted entity is intentionally out of scope here:
this projector only sees the mutations in one batch, so it has no visibility
into which relationships a now-deleted node previously had. That is the
two-stage sync's Stage B reconciliation responsibility (a later stage of the
source-to-graph alignment plan), not something a single-pass projector can
correctly infer on its own.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    GraphMutationBatch,
    GraphNodeMutation,
    GraphRelationshipMutation,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDeletionPolicy,
    RelationshipCardinality,
)

_DELETE_OPERATION_BY_POLICY: dict[EntityDeletionPolicy, str] = {
    EntityDeletionPolicy.HARD_DELETE: "HARD_DELETE",
    EntityDeletionPolicy.TOMBSTONE: "TOMBSTONE",
    EntityDeletionPolicy.DETACH_ONLY: "DETACH_ONLY",
}


class GraphProjectionError(ValueError):
    """A configured projection cannot be materialized from source mutations."""


class GenericGraphProjector:
    """Project mutations without branching on business entity or field names."""

    async def project(
        self,
        *,
        schema: ActiveSchema,
        mutations: tuple[DynamicRecordMutation, ...],
    ) -> GraphMutationBatch:
        node_mutations: list[GraphNodeMutation] = []
        upserted_by_entity: dict[str, list[DynamicRecordMutation]] = defaultdict(list)

        for mutation in mutations:
            entity = schema.entities.get(mutation.entity_id)
            if entity is None or entity.source_asset_id != mutation.source_asset_id:
                raise GraphProjectionError("mutation does not match active schema")
            node = schema.entity_node(mutation.entity_id)

            if mutation.operation == "DELETE":
                node_mutations.append(
                    GraphNodeMutation(
                        operation=_DELETE_OPERATION_BY_POLICY[entity.deletion_policy],
                        projection_id=node.projection_id,
                        entity_id=mutation.entity_id,
                        key_values=dict(mutation.resolved_key),
                    )
                )
                continue

            record = mutation.record
            if record is None:
                raise GraphProjectionError("UPSERT mutation is missing its record")
            keys: dict[str, Any] = {}
            for field_id in node.key_fields:
                if field_id not in record.values:
                    raise GraphProjectionError(
                        f"record is missing configured key field {field_id!r}"
                    )
                keys[field_id] = record.values[field_id]
            properties: dict[str, Any] = {
                entity.fields[field_id].graph_property: record.values[field_id]
                for field_id in node.property_fields
                if field_id in record.values
            }
            node_mutations.append(
                GraphNodeMutation(
                    operation="UPSERT",
                    projection_id=node.projection_id,
                    entity_id=mutation.entity_id,
                    key_values=keys,
                    properties=properties,
                )
            )
            upserted_by_entity[mutation.entity_id].append(mutation)

        relationship_mutations = _project_relationships(schema, upserted_by_entity)
        return GraphMutationBatch(
            node_mutations=tuple(node_mutations),
            relationship_mutations=relationship_mutations,
        )


def _project_relationships(
    schema: ActiveSchema,
    upserted_by_entity: dict[str, list[DynamicRecordMutation]],
) -> tuple[GraphRelationshipMutation, ...]:
    relationship_mutations: list[GraphRelationshipMutation] = []
    for relationship in schema.graph.relationships.values():
        source_mutations = upserted_by_entity.get(relationship.source_entity_id, [])
        target_mutations = upserted_by_entity.get(relationship.target_entity_id, [])

        # One target match key can legitimately map to MANY target mutations
        # (e.g. several OrderLine records sharing one SalesOrder's key) --
        # a plain dict overwrite here is exactly the bug this fix corrects.
        target_index: dict[tuple[Any, ...], list[DynamicRecordMutation]] = defaultdict(list)
        for target in target_mutations:
            assert target.record is not None
            match_key = tuple(
                target.record.values.get(field_id) for field_id in relationship.target_match_fields
            )
            if any(value is None for value in match_key):
                continue
            target_index[match_key].append(target)

        seen: set[tuple[str, tuple[Any, ...], tuple[Any, ...]]] = set()
        targets_per_source: dict[tuple[Any, ...], int] = defaultdict(int)
        sources_per_target: dict[tuple[Any, ...], int] = defaultdict(int)

        for source in source_mutations:
            assert source.record is not None
            source_match_key = tuple(
                source.record.values.get(field_id) for field_id in relationship.source_match_fields
            )
            if any(value is None for value in source_match_key):
                continue
            matched_targets = target_index.get(source_match_key, [])
            if not matched_targets:
                continue
            _check_cardinality(
                relationship,
                targets_for_this_source=len(matched_targets),
                existing_targets_per_source=targets_per_source[source_match_key],
            )
            for target in matched_targets:
                assert target.record is not None
                source_keys = _key_values(schema, source.record)
                target_keys = _key_values(schema, target.record)
                dedupe_key = (
                    relationship.relationship_id,
                    tuple(sorted(source_keys.items())),
                    tuple(sorted(target_keys.items())),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                targets_per_source[source_match_key] += 1
                target_match_key = tuple(
                    target.record.values.get(field_id)
                    for field_id in relationship.target_match_fields
                )
                sources_per_target[target_match_key] += 1
                if (
                    relationship.maximum_sources_per_target is not None
                    and sources_per_target[target_match_key] > relationship.maximum_sources_per_target
                ):
                    raise GraphProjectionError(
                        f"relationship {relationship.relationship_id!r} exceeds "
                        f"maximum_sources_per_target={relationship.maximum_sources_per_target}"
                    )
                relationship_mutations.append(
                    GraphRelationshipMutation(
                        operation="UPSERT",
                        relationship_id=relationship.relationship_id,
                        source_key_values=source_keys,
                        target_key_values=target_keys,
                        properties={
                            schema.entities[source.entity_id].fields[field_id].graph_property: (
                                source.record.values[field_id]
                            )
                            for field_id in relationship.property_fields
                            if field_id in source.record.values
                        },
                    )
                )
    return tuple(relationship_mutations)


def _check_cardinality(
    relationship: Any,
    *,
    targets_for_this_source: int,
    existing_targets_per_source: int,
) -> None:
    limit = relationship.maximum_targets_per_source
    if limit is not None and existing_targets_per_source + targets_for_this_source > limit:
        raise GraphProjectionError(
            f"relationship {relationship.relationship_id!r} exceeds "
            f"maximum_targets_per_source={limit}"
        )


def _key_values(schema: ActiveSchema, record: Any) -> dict[str, Any]:
    node = schema.entity_node(record.entity_id)
    return {field_id: record.values[field_id] for field_id in node.key_fields}
