"""Going from one published release to the next, generation by generation.

The baseline is the real shipped schema, not a fixture: what is under test is
whether a plan reads a release the platform actually runs, and a hand-built one
would let the planner agree with a schema nobody has.

The judgement these pin down is the one thing a plan is for -- when the graph
can absorb a change and when it has to be rebuilt. Getting that wrong in the
permissive direction is the expensive failure: an incremental sync over a
re-keyed label inserts a second copy of every node and the graph starts
answering with both.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_migration import (
    GraphObjectKind,
    MigrationStrategy,
    plan_migration,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


@pytest.fixture(scope="module")
def baseline() -> ActiveSchema:
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _renamed(schema: ActiveSchema, release_id: str) -> ActiveSchema:
    return schema.model_copy(update={"configuration_release_id": release_id})


def _first_node_entity(schema: ActiveSchema) -> str:
    return next(iter(schema.graph.nodes.values())).entity_id


def _without_node(schema: ActiveSchema, entity_id: str) -> ActiveSchema:
    """Drop a label and everything that references it, so the result is a
    release `ActiveSchema` would actually accept."""
    nodes = {key: node for key, node in schema.graph.nodes.items() if node.entity_id != entity_id}
    relationships = {
        key: relationship
        for key, relationship in schema.graph.relationships.items()
        if entity_id not in (relationship.source_entity_id, relationship.target_entity_id)
    }
    entities = {key: value for key, value in schema.entities.items() if key != entity_id}
    policies = {
        key: policy.model_copy(
            update={"allowed_entity_ids": frozenset(policy.allowed_entity_ids) - {entity_id}}
        )
        for key, policy in schema.agent_policies.items()
    }
    return schema.model_copy(
        update={
            "configuration_release_id": "without_node",
            "entities": entities,
            "agent_policies": policies,
            "graph": schema.graph.model_copy(
                update={"nodes": nodes, "relationships": relationships}
            ),
        }
    )


def test_the_first_activation_on_an_installation_builds_rather_than_migrates(
    baseline: ActiveSchema,
) -> None:
    """Nothing is active, so there is no graph to migrate -- only one to build.

    Listing every label as "added" would be true and useless; naming the state
    is what an operator can act on.
    """
    plan = plan_migration(None, baseline)

    assert plan.strategy is MigrationStrategy.FULL_REBUILD
    assert plan.from_release_id is None
    assert plan.node_labels_added
    assert plan.rebuild_reasons


def test_republishing_the_same_shape_under_a_new_id_changes_nothing(
    baseline: ActiveSchema,
) -> None:
    """A release id and a checksum always differ between two releases. A plan
    that called that a change would demand a rebuild for every publish."""
    plan = plan_migration(baseline, _renamed(baseline, "identical_but_renamed"))

    assert plan.strategy is MigrationStrategy.NO_CHANGE
    assert plan.is_empty
    assert plan.rebuild_reasons == ()


def test_a_release_that_stops_projecting_a_label_needs_a_rebuild(
    baseline: ActiveSchema,
) -> None:
    """A merge-only writer cannot reach nodes nobody projects any more, so they
    would sit in the graph being answered with forever."""
    entity_id = _first_node_entity(baseline)
    label = baseline.entity_node(entity_id).label

    plan = plan_migration(baseline, _without_node(baseline, entity_id))

    assert plan.requires_rebuild
    assert label in plan.node_labels_removed
    assert any(label in reason for reason in plan.rebuild_reasons)


def test_the_reverse_direction_is_incremental(baseline: ActiveSchema) -> None:
    """Adding a label back is additive, and additive is what a merge does."""
    entity_id = _first_node_entity(baseline)
    reduced = _without_node(baseline, entity_id)

    plan = plan_migration(reduced, _renamed(baseline, "restored"))

    assert plan.strategy is MigrationStrategy.INCREMENTAL
    assert baseline.entity_node(entity_id).label in plan.node_labels_added
    assert plan.rebuild_reasons == ()


def test_changing_what_identifies_a_label_needs_a_rebuild(baseline: ActiveSchema) -> None:
    """The most expensive silent failure this plan exists to catch: a merge on a
    new key matches nothing and inserts a second node beside every existing one.
    """
    entity_id = _first_node_entity(baseline)
    node = baseline.entity_node(entity_id)
    entity = baseline.entities[entity_id]
    other = next(name for name in entity.fields if name not in node.key_fields)
    rekeyed = baseline.graph.model_copy(
        update={
            "nodes": {
                **baseline.graph.nodes,
                node.projection_id: node.model_copy(
                    update={
                        "key_fields": (other,),
                        "property_fields": tuple(name for name in entity.fields if name != other),
                    }
                ),
            }
        }
    )

    plan = plan_migration(
        baseline,
        baseline.model_copy(update={"configuration_release_id": "rekeyed", "graph": rekeyed}),
    )

    assert plan.requires_rebuild
    assert any("identity changes" in reason for reason in plan.rebuild_reasons)
    # And the constraint following that identity is replaced, not merely added.
    assert plan.objects_to_create and plan.objects_to_drop
    assert all(
        item.kind is GraphObjectKind.NODE_KEY_CONSTRAINT
        for item in (*plan.objects_to_create, *plan.objects_to_drop)
        if item.label == node.label
    )


def test_reading_the_same_label_from_a_different_source_needs_a_rebuild(
    baseline: ActiveSchema,
) -> None:
    """A rebinding reaches the runtime as a compiled release. The nodes already
    in the graph came from the old source and nothing incremental revisits them.
    """
    entity_id = _first_node_entity(baseline)
    entity = baseline.entities[entity_id]
    source = baseline.sources[entity.source_asset_id]
    elsewhere = source.model_copy(update={"source_asset_id": "restored_copy"})

    plan = plan_migration(
        baseline,
        baseline.model_copy(
            update={
                "configuration_release_id": "rebound",
                "sources": {**baseline.sources, "restored_copy": elsewhere},
                "entities": {
                    **baseline.entities,
                    entity_id: entity.model_copy(update={"source_asset_id": "restored_copy"}),
                },
            }
        ),
    )

    assert plan.requires_rebuild
    assert any("restored_copy" in reason for reason in plan.rebuild_reasons)


def test_a_label_that_only_gains_a_property_is_incremental(baseline: ActiveSchema) -> None:
    """The narrow case incremental is actually for. A merge sets properties, so
    a property that was not projected before simply starts being written."""
    entity_id = _first_node_entity(baseline)
    node = baseline.entity_node(entity_id)
    reduced = node.model_copy(update={"property_fields": node.property_fields[:-1]})
    before = baseline.model_copy(
        update={
            "configuration_release_id": "narrower",
            "graph": baseline.graph.model_copy(
                update={"nodes": {**baseline.graph.nodes, node.projection_id: reduced}}
            ),
        }
    )

    plan = plan_migration(before, _renamed(baseline, "wider"))

    assert plan.strategy is MigrationStrategy.INCREMENTAL
    assert [change.element for change in plan.node_labels_changed] == [node.label]
    assert plan.node_labels_changed[0].detail.startswith("adds ")


def test_a_label_that_stops_projecting_a_property_needs_a_rebuild(
    baseline: ActiveSchema,
) -> None:
    """A merge never unsets. Without a rebuild the graph goes on serving a
    property this release says does not exist."""
    entity_id = _first_node_entity(baseline)
    node = baseline.entity_node(entity_id)
    narrower = baseline.model_copy(
        update={
            "configuration_release_id": "narrower",
            "graph": baseline.graph.model_copy(
                update={
                    "nodes": {
                        **baseline.graph.nodes,
                        node.projection_id: node.model_copy(
                            update={"property_fields": node.property_fields[:-1]}
                        ),
                    }
                }
            ),
        }
    )

    plan = plan_migration(baseline, narrower)

    assert plan.requires_rebuild
    assert any("stops projecting" in reason for reason in plan.rebuild_reasons)


def test_moving_the_graph_database_needs_a_rebuild(baseline: ActiveSchema) -> None:
    """Nothing already written is reachable from the new one."""
    plan = plan_migration(
        baseline,
        baseline.model_copy(
            update={
                "configuration_release_id": "elsewhere",
                "graph": baseline.graph.model_copy(update={"database": "other_graph"}),
            }
        ),
    )

    assert plan.requires_rebuild
    assert any("graph database moves" in reason for reason in plan.rebuild_reasons)


def test_dropping_a_relationship_needs_a_rebuild(baseline: ActiveSchema) -> None:
    """Edges already written are not retired by a sync that no longer knows
    about them."""
    if not baseline.graph.relationships:
        pytest.skip("the shipped baseline projects no relationships")
    victim = next(iter(baseline.graph.relationships))
    plan = plan_migration(
        baseline,
        baseline.model_copy(
            update={
                "configuration_release_id": "fewer_edges",
                "graph": baseline.graph.model_copy(
                    update={
                        "relationships": {
                            key: value
                            for key, value in baseline.graph.relationships.items()
                            if key != victim
                        }
                    }
                ),
            }
        ),
    )

    assert plan.requires_rebuild
    assert plan.relationships_removed
    # Named the way the analyzer's own schema diff names an edge, so a plan and
    # a revision read the same way.
    assert "-[" in plan.relationships_removed[0]


def test_a_plan_round_trips_through_its_document_form(baseline: ActiveSchema) -> None:
    """It is stored against the target release, so it has to survive Mongo."""
    from return_platform.dynamic_knowledge.release_migration import MigrationPlan

    plan = plan_migration(baseline, _without_node(baseline, _first_node_entity(baseline)))

    assert MigrationPlan.model_validate(plan.to_document()) == plan
