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
    SchemaChangeClass,
    plan_migration,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, FieldType, WhereSelector


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


def test_the_reverse_direction_only_needs_a_backfill(baseline: ActiveSchema) -> None:
    """Adding a label back is additive, and additive is what a merge does.

    BACKFILL rather than INCREMENTAL: the label has no nodes at all, so the sync
    that fills it in has to read every record the source already holds. An
    incremental pass resumes from a checkpoint that predates the label entirely
    and would project only whatever happened to change since.
    """
    entity_id = _first_node_entity(baseline)
    reduced = _without_node(baseline, entity_id)

    plan = plan_migration(reduced, _renamed(baseline, "restored"))

    assert plan.change_class is SchemaChangeClass.ADDITIVE
    assert plan.strategy is MigrationStrategy.BACKFILL
    assert baseline.entity_node(entity_id).label in plan.node_labels_added
    assert plan.rebuild_reasons == ()
    assert plan.resync_reasons == ()
    assert baseline.entities[entity_id].source_asset_id in plan.affected_source_asset_ids


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


def test_a_label_that_only_gains_a_property_needs_a_backfill(baseline: ActiveSchema) -> None:
    """A merge sets properties, so a property that was not projected before
    simply starts being written -- but only onto records the sync actually
    re-reads. Every node already in the graph is missing it until its source is
    scanned again, which is why the additive tier is a backfill and not a
    checkpointed incremental pass."""
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

    assert plan.change_class is SchemaChangeClass.ADDITIVE
    assert plan.strategy is MigrationStrategy.BACKFILL
    assert [change.element for change in plan.node_labels_changed] == [node.label]
    assert plan.node_labels_changed[0].detail.startswith("adds ")
    assert plan.node_labels_changed[0].change_class is SchemaChangeClass.ADDITIVE
    assert plan.affected_source_asset_ids == (baseline.entities[entity_id].source_asset_id,)


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


# --- GRAPH-02: the COMPATIBLE tier, and the mapping changes nothing could see --
#
# Before this there were two answers and every mapping change got the expensive
# one: correcting a mistyped property cost a complete rebuild of the graph. The
# middle tier exists because identity is what a merge cannot repair, and a
# mapping is not identity -- `SET n += properties` overwrites in place, so
# re-reading the affected source converges. What it cannot survive is an
# *incremental* pass, which only revisits records whose cursor moved.


def _first_property_field(schema: ActiveSchema, entity_id: str) -> str:
    return schema.entity_node(entity_id).property_fields[0]


def _with_field(
    schema: ActiveSchema, entity_id: str, field_id: str, **updates: object
) -> ActiveSchema:
    entity = schema.entities[entity_id]
    field = entity.fields[field_id].model_copy(update=updates)
    return schema.model_copy(
        update={
            "configuration_release_id": "candidate",
            "entities": {
                **schema.entities,
                entity_id: entity.model_copy(update={"fields": {**entity.fields, field_id: field}}),
            },
        }
    )


def test_retyping_a_projected_property_is_compatible_not_destructive(
    baseline: ActiveSchema,
) -> None:
    """The change that used to cost a full rebuild for no reason.

    The nodes are the same nodes -- identity is untouched -- so re-reading the
    label's source overwrites the values in place. Charging a generation cutover
    for it would make correcting a mistyped field the most expensive operation
    on the platform.
    """
    entity_id = _first_node_entity(baseline)
    field_id = _first_property_field(baseline, entity_id)
    before_type = baseline.entities[entity_id].fields[field_id].data_type
    retyped = FieldType.STRING if before_type is not FieldType.STRING else FieldType.INTEGER

    plan = plan_migration(baseline, _with_field(baseline, entity_id, field_id, data_type=retyped))

    assert plan.change_class is SchemaChangeClass.COMPATIBLE
    assert plan.strategy is MigrationStrategy.AFFECTED_SCOPE_RESYNC
    assert not plan.requires_rebuild
    assert plan.rebuild_reasons == ()
    assert plan.resync_reasons, "a resync with no stated reason is not reviewable"
    assert plan.affected_source_asset_ids == (baseline.entities[entity_id].source_asset_id,)


def test_repointing_a_projected_property_is_seen_at_all(baseline: ActiveSchema) -> None:
    """The silent-wrong-answer case.

    A release can keep a property's name and type while reading it from a
    different place in the source document. The plan compared names and types
    only, so this was reported as NO_CHANGE -- the pointer moved, no sync was
    owed, and the graph went on serving values from the old path indefinitely.
    """
    entity_id = _first_node_entity(baseline)
    field_id = _first_property_field(baseline, entity_id)
    original = baseline.entities[entity_id].fields[field_id]
    assert original.physical_path, "this test needs a path-mapped field"
    moved = (*original.physical_path[:-1], f"{original.physical_path[-1]}_v2")

    plan = plan_migration(baseline, _with_field(baseline, entity_id, field_id, physical_path=moved))

    assert plan.change_class is SchemaChangeClass.COMPATIBLE
    assert plan.strategy is MigrationStrategy.AFFECTED_SCOPE_RESYNC
    assert plan.affected_source_asset_ids == (baseline.entities[entity_id].source_asset_id,)


def test_repointing_a_key_field_is_destructive(baseline: ActiveSchema) -> None:
    """The same edit on a key field is the opposite verdict, and the reason the
    two are distinguished at all: the key fields' *names* are unchanged, so a
    name-only comparison would call this compatible and let an in-place resync
    insert a second node beside every existing one."""
    entity_id = _first_node_entity(baseline)
    key_field_id = baseline.entity_node(entity_id).key_fields[0]
    original = baseline.entities[entity_id].fields[key_field_id]
    assert original.physical_path, "this test needs a path-mapped key"
    moved = (*original.physical_path[:-1], f"{original.physical_path[-1]}_v2")

    plan = plan_migration(
        baseline, _with_field(baseline, entity_id, key_field_id, physical_path=moved)
    )

    assert plan.change_class is SchemaChangeClass.DESTRUCTIVE
    assert plan.strategy is MigrationStrategy.FULL_REBUILD
    assert plan.requires_rebuild
    assert plan.affected_source_asset_ids == (), "a cutover rebuilds everything; scope is a lie"


def test_narrowing_which_records_an_entity_projects_is_destructive(
    baseline: ActiveSchema,
) -> None:
    """Adding a `where` selector stops producing nodes the graph already holds,
    and a writer that only merges what it reads can never reach them."""
    entity_id = _first_node_entity(baseline)
    entity = baseline.entities[entity_id]
    narrowed = entity.model_copy(
        update={"where": (*entity.where, WhereSelector(physical_path=("status",), equals="OPEN"))}
    )
    candidate = baseline.model_copy(
        update={
            "configuration_release_id": "narrowed",
            "entities": {**baseline.entities, entity_id: narrowed},
        }
    )

    plan = plan_migration(baseline, candidate)

    assert plan.change_class is SchemaChangeClass.DESTRUCTIVE
    assert plan.requires_rebuild


def test_an_unchanged_release_still_reports_no_change(baseline: ActiveSchema) -> None:
    """The regression guard for the new comparisons: `key_mappings`,
    `property_mappings` and `record_selection` are derived from the schema, so a
    schema compared with itself must produce no work. A signature that was not
    stable would make every activation a rebuild."""
    plan = plan_migration(baseline, _renamed(baseline, "identical"))

    assert plan.change_class is SchemaChangeClass.NONE
    assert plan.strategy is MigrationStrategy.NO_CHANGE
    assert plan.is_empty
    assert plan.affected_source_asset_ids == ()


def test_a_plan_recorded_before_change_classes_still_deserializes() -> None:
    """Plans are stored in Mongo against the pair they describe. Documents written
    before this change carry no `change_class`, no `resync_reasons` and no scope,
    and reading one must not fail -- an operator looking at the history of a
    migration that already happened is not re-judging it."""
    from return_platform.dynamic_knowledge.release_migration import MigrationPlan

    plan = MigrationPlan.model_validate(
        {
            "from_release_id": "old",
            "to_release_id": "new",
            "strategy": "INCREMENTAL",
            "node_labels_added": ["Order"],
            "rebuild_reasons": [],
        }
    )

    assert plan.strategy is MigrationStrategy.INCREMENTAL
    assert plan.change_class is SchemaChangeClass.DESTRUCTIVE
    assert plan.affected_source_asset_ids == ()
