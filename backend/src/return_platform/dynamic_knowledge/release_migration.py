"""What changes between two published releases, and whether the graph survives it.

**Releases are immutable, so migration is generational (D8).** The store already
holds every published `ActiveSchema` and a pointer at the one that is live. What
was missing is the step in between: activation was a pointer flip in the dark. An
operator could move the runtime from a release whose `Order` matched on
`order_id` to one that matches on `salesInvId` and learn about it from the first
associate who could not find an order.

This module answers the question the flip should have to answer first: given the
release that is running and the one someone wants to run, what does the graph
have to do about it? Three outcomes only --

    NO_CHANGE      nothing that touches the graph moved
    INCREMENTAL    everything that changed is additive; the next sync fills it in
    FULL_REBUILD   something the graph's existing contents depend on changed

and the rebuild verdict is always accompanied by the reasons, because "rebuild"
without "why" is not reviewable.

**Incremental is the narrow case, not the default.** The writer merges: it
matches a node on its key and sets properties. Merging can add a label, add a
property and add an edge, and it can do nothing else. It cannot unset a property
the release stopped projecting, it cannot retire an edge type nobody writes any
more, and it cannot re-key nodes whose identity changed -- it would insert a
second copy beside each one. So every *subtractive* or *identity-affecting*
change is a rebuild, and the list of them below is the whole of the judgement.

`ActiveSchema` is the one compiled form and this module reads it; the constraint
and index sets come from `graph/constraints.py`, which already derives what a
schema requires. Nothing here is a second graph-schema representation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.graph.constraints import (
    required_node_constraints,
    required_relationship_indexes,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

__all__ = [
    "ElementChange",
    "GraphObject",
    "GraphObjectKind",
    "MigrationPlan",
    "MigrationStrategy",
    "plan_migration",
]


class MigrationStrategy(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    INCREMENTAL = "INCREMENTAL"
    FULL_REBUILD = "FULL_REBUILD"


class GraphObjectKind(StrEnum):
    """Where a constraint or index came from.

    Kept on the object because the two families are provisioned by different
    owners: the derived ones follow identity and are recomputed from the schema,
    while the declared ones are what an analyst asked for in a draft. An operator
    reading a plan needs to know which of those a line is.
    """

    NODE_KEY_CONSTRAINT = "NODE_KEY_CONSTRAINT"
    RELATIONSHIP_MATCH_INDEX = "RELATIONSHIP_MATCH_INDEX"
    DECLARED_CONSTRAINT = "DECLARED_CONSTRAINT"
    DECLARED_INDEX = "DECLARED_INDEX"


class GraphObject(BaseModel):
    """One constraint or index, in the form both families can be compared in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: GraphObjectKind
    label: str
    properties: tuple[str, ...]
    detail: str = ""


class ElementChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    element: str
    detail: str


class MigrationPlan(BaseModel):
    """What activating `to_release_id` does to the graph.

    `from_release_id` is None when nothing is active yet, which is the state
    every installation starts in and is always a FULL_REBUILD -- there is no
    graph to migrate, only one to build.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_release_id: str | None
    to_release_id: str
    strategy: MigrationStrategy
    node_labels_added: tuple[str, ...] = ()
    node_labels_removed: tuple[str, ...] = ()
    node_labels_changed: tuple[ElementChange, ...] = ()
    relationships_added: tuple[str, ...] = ()
    relationships_removed: tuple[str, ...] = ()
    relationships_changed: tuple[ElementChange, ...] = ()
    objects_to_create: tuple[GraphObject, ...] = ()
    objects_to_drop: tuple[GraphObject, ...] = ()
    rebuild_reasons: tuple[str, ...] = ()

    @property
    def requires_rebuild(self) -> bool:
        return self.strategy is MigrationStrategy.FULL_REBUILD

    @property
    def is_empty(self) -> bool:
        return self.strategy is MigrationStrategy.NO_CHANGE

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")


def plan_migration(current: ActiveSchema | None, target: ActiveSchema) -> MigrationPlan:
    """Compare the running release with a candidate and decide what has to happen.

    Pure, and constructed from two schemas rather than from a store, so the same
    comparison runs in a test, in a console preview and at the moment of
    activation -- and a preview cannot disagree with what activation then does.
    """
    to_labels = _labels(target)
    to_relationships = _relationships(target)
    to_objects = _objects(target)

    if current is None:
        # No graph exists to migrate. Listing every label as "added" would be
        # true and useless; naming the state is the useful answer.
        return MigrationPlan(
            from_release_id=None,
            to_release_id=target.configuration_release_id,
            strategy=MigrationStrategy.FULL_REBUILD,
            node_labels_added=tuple(sorted(to_labels)),
            relationships_added=tuple(sorted(to_relationships)),
            objects_to_create=tuple(sorted(to_objects, key=_object_order)),
            rebuild_reasons=(
                "no release is active, so there is no graph to migrate -- this release "
                "must build the one it describes",
            ),
        )

    from_labels = _labels(current)
    from_relationships = _relationships(current)
    from_objects = _objects(current)

    added = tuple(sorted(set(to_labels) - set(from_labels)))
    removed = tuple(sorted(set(from_labels) - set(to_labels)))
    reasons: list[str] = []

    changed: list[ElementChange] = []
    for label in sorted(set(from_labels) & set(to_labels)):
        change = _label_change(from_labels[label], to_labels[label])
        if change is None:
            continue
        changed.append(ElementChange(element=label, detail=change.detail))
        if change.forces_rebuild:
            reasons.append(f"{label}: {change.detail}")

    for label in removed:
        # A merge-only writer has no way to reach nodes nobody projects any
        # more, so they would sit in the graph being answered with forever.
        reasons.append(
            f"{label}: the release stops projecting this label, and nothing incremental "
            "removes nodes it no longer writes"
        )

    relationships_added = tuple(sorted(set(to_relationships) - set(from_relationships)))
    relationships_removed = tuple(sorted(set(from_relationships) - set(to_relationships)))
    relationships_changed: list[ElementChange] = []
    for key in sorted(set(from_relationships) & set(to_relationships)):
        before, after = from_relationships[key], to_relationships[key]
        if before == after:
            continue
        detail = _relationship_detail(before, after)
        relationships_changed.append(ElementChange(element=key, detail=detail))
        # Every relationship field that can differ changes which nodes an edge
        # joins. Re-running sync would draw the new edges beside the old ones.
        reasons.append(f"{key}: {detail}")
    for key in relationships_removed:
        reasons.append(
            f"{key}: the release stops projecting this relationship, and edges already "
            "written are not retired by a sync that no longer knows about them"
        )

    if current.graph.database != target.graph.database:
        reasons.append(
            f"the graph database moves from {current.graph.database!r} to "
            f"{target.graph.database!r}, so nothing already written is reachable"
        )

    objects_to_create = tuple(sorted(to_objects - from_objects, key=_object_order))
    objects_to_drop = tuple(sorted(from_objects - to_objects, key=_object_order))

    strategy = _strategy(
        reasons=reasons,
        touched=bool(
            added
            or removed
            or changed
            or relationships_added
            or relationships_removed
            or relationships_changed
            or objects_to_create
            or objects_to_drop
        ),
    )
    return MigrationPlan(
        from_release_id=current.configuration_release_id,
        to_release_id=target.configuration_release_id,
        strategy=strategy,
        node_labels_added=added,
        node_labels_removed=removed,
        node_labels_changed=tuple(changed),
        relationships_added=relationships_added,
        relationships_removed=relationships_removed,
        relationships_changed=tuple(relationships_changed),
        objects_to_create=objects_to_create,
        objects_to_drop=objects_to_drop,
        rebuild_reasons=tuple(reasons),
    )


def _strategy(*, reasons: Sequence[str], touched: bool) -> MigrationStrategy:
    if reasons:
        return MigrationStrategy.FULL_REBUILD
    return MigrationStrategy.INCREMENTAL if touched else MigrationStrategy.NO_CHANGE


class _Label(BaseModel):
    """One node label as the graph will actually see it.

    Graph *properties*, not field ids: two releases can rename a field id
    without changing anything a node carries, and a plan that called that a
    change would demand a rebuild for a refactor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_asset_id: str
    key_properties: tuple[str, ...]
    properties: tuple[tuple[str, str], ...]


class _LabelChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: str
    forces_rebuild: bool


def _labels(schema: ActiveSchema) -> dict[str, _Label]:
    labels: dict[str, _Label] = {}
    for node in schema.graph.nodes.values():
        entity = schema.entities[node.entity_id]
        labels[node.label] = _Label(
            source_asset_id=entity.source_asset_id,
            key_properties=tuple(
                entity.fields[field_id].graph_property for field_id in node.key_fields
            ),
            properties=tuple(
                sorted(
                    (
                        entity.fields[field_id].graph_property,
                        entity.fields[field_id].data_type.value,
                    )
                    for field_id in node.property_fields
                )
            ),
        )
    return labels


def _label_change(before: _Label, after: _Label) -> _LabelChange | None:
    """What differs about one label, and whether the graph can absorb it.

    Order matters: identity first, because a key change makes every other
    difference irrelevant -- the nodes the new release writes are not the nodes
    the old one wrote, whatever else is true of them.
    """
    if before.key_properties != after.key_properties:
        return _LabelChange(
            detail=(
                f"identity changes from {list(before.key_properties)} to "
                f"{list(after.key_properties)}; a merge would insert a second node "
                "beside every existing one instead of updating it"
            ),
            forces_rebuild=True,
        )
    if before.source_asset_id != after.source_asset_id:
        return _LabelChange(
            detail=(
                f"reads {after.source_asset_id!r} instead of {before.source_asset_id!r}; "
                "the nodes already in the graph came from the old source"
            ),
            forces_rebuild=True,
        )

    before_properties = dict(before.properties)
    after_properties = dict(after.properties)
    dropped = sorted(set(before_properties) - set(after_properties))
    gained = sorted(set(after_properties) - set(before_properties))
    retyped = sorted(
        name
        for name in set(before_properties) & set(after_properties)
        if before_properties[name] != after_properties[name]
    )

    if dropped:
        return _LabelChange(
            detail=(
                f"stops projecting {dropped}; a merge never unsets, so the graph would go "
                "on serving properties this release says do not exist"
            ),
            forces_rebuild=True,
        )
    if retyped:
        return _LabelChange(
            detail=(
                f"changes the type of {retyped}; the values already stored were written "
                "under the old type and are not re-read by an additive sync"
            ),
            forces_rebuild=True,
        )
    if gained:
        return _LabelChange(detail=f"adds {gained}", forces_rebuild=False)
    return None


def _relationships(schema: ActiveSchema) -> dict[str, tuple[str, ...]]:
    """Each relationship, keyed by how a reader names it.

    `Order-[HAS_LINE]->OrderLine` rather than the compiled id, matching the
    analyzer's own diff vocabulary so a schema diff and a migration plan read
    the same way.
    """
    result: dict[str, tuple[str, ...]] = {}
    for relationship in schema.graph.relationships.values():
        source_node = schema.entity_node(relationship.source_entity_id)
        target_node = schema.entity_node(relationship.target_entity_id)
        key = f"{source_node.label}-[{relationship.relationship_type}]->{target_node.label}"
        source_entity = schema.entities[relationship.source_entity_id]
        target_entity = schema.entities[relationship.target_entity_id]
        result[key] = (
            relationship.cardinality.value,
            ",".join(
                source_entity.fields[field_id].graph_property
                for field_id in relationship.source_match_fields
            ),
            ",".join(
                target_entity.fields[field_id].graph_property
                for field_id in relationship.target_match_fields
            ),
        )
    return result


def _relationship_detail(before: tuple[str, ...], after: tuple[str, ...]) -> str:
    if before[1:] != after[1:]:
        return (
            f"matches on {after[1]} -> {after[2]} instead of {before[1]} -> {before[2]}; "
            "the edges already written join different nodes"
        )
    return (
        f"cardinality changes from {before[0]} to {after[0]}; edges written under the old "
        "declaration were never checked against the new one"
    )


def _objects(schema: ActiveSchema) -> frozenset[GraphObject]:
    """Every constraint and index this release requires, derived and declared.

    Derived from `graph/constraints.py` rather than recomputed here: what a
    schema requires is already answered there, and answering it twice is how the
    provisioner and the plan end up disagreeing.
    """
    objects: set[GraphObject] = set()
    for constraint in required_node_constraints(schema):
        objects.add(
            GraphObject(
                kind=GraphObjectKind.NODE_KEY_CONSTRAINT,
                label=constraint.label,
                properties=constraint.graph_properties,
                detail="unique",
            )
        )
    for index in required_relationship_indexes(schema):
        objects.add(
            GraphObject(
                kind=GraphObjectKind.RELATIONSHIP_MATCH_INDEX,
                label=index.label,
                properties=index.graph_properties,
                detail=index.relationship_id,
            )
        )
    for declared in schema.graph.constraints:
        objects.add(
            GraphObject(
                kind=GraphObjectKind.DECLARED_CONSTRAINT,
                label=str(declared.get("label", "")),
                properties=(str(declared.get("property_name", "")),),
                detail=",".join(
                    name
                    for name, present in (
                        ("unique", bool(declared.get("unique"))),
                        ("required", bool(declared.get("required"))),
                    )
                    if present
                ),
            )
        )
    for declared in schema.graph.indexes:
        objects.add(
            GraphObject(
                kind=GraphObjectKind.DECLARED_INDEX,
                label=str(declared.get("label", "")),
                properties=tuple(str(name) for name in declared.get("properties", ())),
            )
        )
    return frozenset(objects)


def _object_order(item: GraphObject) -> tuple[str, str, tuple[str, ...], str]:
    return (item.kind.value, item.label, item.properties, item.detail)
