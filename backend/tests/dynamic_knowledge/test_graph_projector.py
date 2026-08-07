from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.dynamic_knowledge.graph.projector import (
    GenericGraphProjector,
    GraphProjectionError,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    ProjectionReadScope,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


def _one_to_many_schema(
    active_schema: ActiveSchema,
    *,
    maximum_targets_per_source: int | None = None,
) -> ActiveSchema:
    """entity_a (parent, one) -[:a_to_b]-> entity_b (child, many), matching a
    SalesOrder -> OrderLine shape without using Ferguson-specific names."""

    raw = active_schema.model_dump(mode="json")
    raw["graph"]["relationships"]["a_to_b"]["cardinality"] = "ONE_TO_MANY"
    raw["graph"]["relationships"]["a_to_b"]["maximum_targets_per_source"] = (
        maximum_targets_per_source
    )
    return ActiveSchema.model_validate(raw)


def _upsert(entity_id: str, source_asset_id: str, values: dict[str, object]) -> DynamicRecordMutation:
    node_projection_id = {"entity_a": "node_a", "entity_b": "node_b"}[entity_id]
    natural_key = {"id": values["id"]}
    return DynamicRecordMutation(
        operation="UPSERT",
        record=DynamicSourceRecord(
            source_asset_id=source_asset_id,
            entity_id=entity_id,
            natural_key=natural_key,
            values=values,
        ),
        entity_id=entity_id,
        projection_id=node_projection_id,
        source_asset_id=source_asset_id,
        source_identity=str(values["id"]),
        resolved_key=natural_key,
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )


def _delete(entity_id: str, source_asset_id: str, resolved_key: dict[str, object]) -> DynamicRecordMutation:
    return DynamicRecordMutation(
        operation="DELETE",
        record=None,
        entity_id=entity_id,
        projection_id={"entity_a": "node_a", "entity_b": "node_b"}[entity_id],
        source_asset_id=source_asset_id,
        source_identity=str(resolved_key.get("id")),
        resolved_key=resolved_key,
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )


@pytest.mark.asyncio
async def test_one_parent_with_three_children_produces_exactly_three_relationships(
    active_schema: ActiveSchema,
) -> None:
    """Flagship regression test: a plain dict-overwrite target_index would collapse
    three OrderLine-shaped children sharing one SalesOrder-shaped parent key down
    to a single relationship. This must produce exactly three."""
    schema = _one_to_many_schema(active_schema)
    mutations = (
        _upsert("entity_a", "source_a", {"id": "PARENT-1", "name": "n", "count_value": 1}),
        _upsert("entity_b", "source_b", {"id": "CHILD-1", "parent_id": "PARENT-1"}),
        _upsert("entity_b", "source_b", {"id": "CHILD-2", "parent_id": "PARENT-1"}),
        _upsert("entity_b", "source_b", {"id": "CHILD-3", "parent_id": "PARENT-1"}),
    )
    batch = await GenericGraphProjector().project(schema=schema, mutations=mutations)
    assert len(batch.relationship_mutations) == 3
    # entity_b.id's graph_property is "related_id"; entity_a.id's is "configured_id" --
    # relationship key_values are always keyed by graph_property, never the logical field_id.
    target_ids = {mutation.target_key_values["related_id"] for mutation in batch.relationship_mutations}
    assert target_ids == {"CHILD-1", "CHILD-2", "CHILD-3"}
    assert all(
        mutation.source_key_values["configured_id"] == "PARENT-1"
        for mutation in batch.relationship_mutations
    )


@pytest.mark.asyncio
async def test_relationship_mutations_are_deduplicated(active_schema: ActiveSchema) -> None:
    schema = _one_to_many_schema(active_schema)
    mutations = (
        _upsert("entity_a", "source_a", {"id": "PARENT-1", "name": "n", "count_value": 1}),
        _upsert("entity_b", "source_b", {"id": "CHILD-1", "parent_id": "PARENT-1"}),
        _upsert("entity_b", "source_b", {"id": "CHILD-1", "parent_id": "PARENT-1"}),
    )
    batch = await GenericGraphProjector().project(schema=schema, mutations=mutations)
    assert len(batch.relationship_mutations) == 1


@pytest.mark.asyncio
async def test_maximum_targets_per_source_violation_fails_loudly(
    active_schema: ActiveSchema,
) -> None:
    schema = _one_to_many_schema(active_schema, maximum_targets_per_source=2)
    mutations = (
        _upsert("entity_a", "source_a", {"id": "PARENT-1", "name": "n", "count_value": 1}),
        _upsert("entity_b", "source_b", {"id": "CHILD-1", "parent_id": "PARENT-1"}),
        _upsert("entity_b", "source_b", {"id": "CHILD-2", "parent_id": "PARENT-1"}),
        _upsert("entity_b", "source_b", {"id": "CHILD-3", "parent_id": "PARENT-1"}),
    )
    with pytest.raises(GraphProjectionError, match="maximum_targets_per_source"):
        await GenericGraphProjector().project(schema=schema, mutations=mutations)


@pytest.mark.asyncio
async def test_delete_mutation_maps_to_entitys_deletion_policy(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["deletion_policy"] = "TOMBSTONE"
    schema = ActiveSchema.model_validate(raw)
    mutations = (_delete("entity_a", "source_a", {"id": "PARENT-1"}),)
    batch = await GenericGraphProjector().project(schema=schema, mutations=mutations)
    assert len(batch.node_mutations) == 1
    assert batch.node_mutations[0].operation == "TOMBSTONE"
    assert batch.node_mutations[0].key_values == {"configured_id": "PARENT-1"}


@pytest.mark.asyncio
async def test_hard_delete_is_the_default_deletion_policy(active_schema: ActiveSchema) -> None:
    mutations = (_delete("entity_a", "source_a", {"id": "PARENT-1"}),)
    batch = await GenericGraphProjector().project(schema=active_schema, mutations=mutations)
    assert batch.node_mutations[0].operation == "HARD_DELETE"


@pytest.mark.asyncio
async def test_upsert_mutation_without_record_is_rejected(active_schema: ActiveSchema) -> None:
    bad = DynamicRecordMutation(
        operation="UPSERT",
        record=None,
        entity_id="entity_a",
        projection_id="node_a",
        source_asset_id="source_a",
        source_identity="x",
        resolved_key={"id": "PARENT-1"},
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    with pytest.raises(GraphProjectionError, match="missing its record"):
        await GenericGraphProjector().project(schema=active_schema, mutations=(bad,))
