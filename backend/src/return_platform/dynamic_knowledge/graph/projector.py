"""Generic graph projection from logical dynamic records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicSourceRecord,
    GraphWriteBatch,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class GraphProjectionError(ValueError):
    """A configured projection cannot be materialized from source records."""


class GenericGraphProjector:
    """Project records without branching on business entity or field names."""

    async def project(
        self,
        *,
        schema: ActiveSchema,
        records: tuple[DynamicSourceRecord, ...],
    ) -> GraphWriteBatch:
        nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            entity = schema.entities.get(record.entity_id)
            if entity is None or entity.source_asset_id != record.source_asset_id:
                raise GraphProjectionError("record does not match active schema")
            node = schema.entity_node(record.entity_id)
            keys: dict[str, Any] = {}
            properties: dict[str, Any] = {}
            for field_id in node.key_fields:
                if field_id not in record.values:
                    raise GraphProjectionError(
                        f"record is missing configured key field {field_id!r}"
                    )
                keys[field_id] = record.values[field_id]
            for field_id in node.property_fields:
                if field_id in record.values:
                    properties[entity.fields[field_id].graph_property] = record.values[field_id]
            nodes[node.projection_id].append({"keys": keys, "properties": properties})

        relationships: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relationship in schema.graph.relationships.values():
            source_records = [
                item for item in records if item.entity_id == relationship.source_entity_id
            ]
            target_records = [
                item for item in records if item.entity_id == relationship.target_entity_id
            ]
            target_index: dict[tuple[Any, ...], DynamicSourceRecord] = {}
            for target in target_records:
                match_key = tuple(
                    target.values.get(field_id) for field_id in relationship.target_match_fields
                )
                if any(value is None for value in match_key):
                    continue
                target_index[match_key] = target
            for source in source_records:
                match_key = tuple(
                    source.values.get(field_id) for field_id in relationship.source_match_fields
                )
                if any(value is None for value in match_key):
                    continue
                matched_target = target_index.get(match_key)
                if matched_target is None:
                    continue
                relationships[relationship.relationship_id].append(
                    {
                        "sourceKeys": {
                            field_id: source.values[field_id]
                            for field_id in schema.entity_node(source.entity_id).key_fields
                        },
                        "targetKeys": {
                            field_id: matched_target.values[field_id]
                            for field_id in schema.entity_node(matched_target.entity_id).key_fields
                        },
                        "properties": {
                            schema.entities[source.entity_id]
                            .fields[field_id]
                            .graph_property: source.values[field_id]
                            for field_id in relationship.property_fields
                            if field_id in source.values
                        },
                    }
                )
        return GraphWriteBatch(
            node_rows={key: tuple(value) for key, value in nodes.items()},
            relationship_rows={key: tuple(value) for key, value in relationships.items()},
        )
