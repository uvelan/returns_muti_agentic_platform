"""Build targeted source plans strictly from active schema and validated anchors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    LogicalAnchorCondition,
    LogicalTargetedReadPlan,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


def build_targeted_read_plan(
    *,
    schema: ActiveSchema,
    entity_id: str,
    normalized_anchors: Mapping[str, tuple[str, Any]],
    dependency_relationship_ids: tuple[str, ...] = (),
    maximum_rows: int = 100,
    maximum_dependency_records: int = 500,
) -> LogicalTargetedReadPlan:
    """One entity's record, reached by its anchors; never a source-wide read.

    `required_field_ids` is what the *caller* wants back -- the anchoring
    entity's own graph-mapped and anchor fields. It is deliberately no longer
    what a document-shaped connector projects: extraction runs every entity
    bound to the source over the document a targeted read returns, so the
    projection is a property of the source asset and is computed by
    `source_connectors.compilation.source_projection_paths`. Narrowing the
    document to one entity's fields is how an anchored read of an order used to
    come back and then throw the order away.
    """

    entity = schema.entities[entity_id]
    node = schema.entity_node(entity_id)
    required_fields = set(node.key_fields + node.property_fields)
    required_fields.update(normalized_anchors)
    for relationship_id in dependency_relationship_ids:
        relationship = schema.graph.relationships[relationship_id]
        if relationship.source_entity_id == entity_id:
            required_fields.update(relationship.source_match_fields)
        if relationship.target_entity_id == entity_id:
            required_fields.update(relationship.target_match_fields)
    conditions = tuple(
        LogicalAnchorCondition(field_id=field_id, operator=operator, value=value)
        for field_id, (operator, value) in sorted(normalized_anchors.items())
    )
    return LogicalTargetedReadPlan(
        source_asset_id=entity.source_asset_id,
        entity_id=entity_id,
        conditions=conditions,
        required_field_ids=tuple(sorted(required_fields)),
        dependency_relationship_ids=dependency_relationship_ids,
        maximum_rows=maximum_rows,
        maximum_dependency_records=maximum_dependency_records,
    )
