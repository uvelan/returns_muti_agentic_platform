"""What changes between two published releases, and whether the graph survives it.

**Releases are immutable, so migration is generational (D8).** The store already
holds every published `ActiveSchema` and a pointer at the one that is live. What
was missing is the step in between: activation was a pointer flip in the dark. An
operator could move the runtime from a release whose `Order` matched on
`order_id` to one that matches on `salesInvId` and learn about it from the first
associate who could not find an order.

This module answers the question the flip should have to answer first: given the
release that is running and the one someone wants to run, what does the graph
have to do about it?

**Three classes of change, and the strategy each one earns.**

    ADDITIVE     nothing existing changes; new labels, edges or properties
                 appear             -> BACKFILL the affected sources
    COMPATIBLE   existing identities stand, but their *mapping* moved -- a
                 property's path, its type, a cardinality bound
                                    -> AFFECTED_SCOPE_RESYNC of those sources
    DESTRUCTIVE  identity changed, or something is withdrawn that a merge can
                 never take back    -> FULL_REBUILD via a generation cutover

The middle class is the one that was missing. Every mapping change used to be
lumped in with the destructive ones, so correcting a mistyped property cost a
complete rebuild of the graph; and the cheap tier was called INCREMENTAL, which
was optimistic in the other direction -- an incremental pass only re-reads
records whose cursor moved, and a property that is new or newly-mapped has to be
written onto records that did not change at all. Both tiers are therefore a
*bounded full scan of the affected sources*, which is what `affected_source_asset_ids`
is for. INCREMENTAL survives in the enum only so plans recorded before this
change still deserialize; nothing produces it any more.

**Why the boundary sits where it does.** The writer merges: it matches a node on
its key and sets properties. Merging can add a label, add a property, overwrite a
property and add an edge. It cannot unset a property the release stopped
projecting, it cannot retire an edge type nobody writes any more, and it cannot
re-key nodes whose identity changed -- it would insert a second copy beside each
one. Overwriting is the capability the COMPATIBLE tier rests on; the other three
are why DESTRUCTIVE still means a cutover, which
`data_platform.graph.sync_service` now actually performs rather than refusing.

Every non-trivial verdict carries its reasons, because "rebuild" without "why"
is not reviewable -- and so does the resync tier, for the same reason.

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
    "SchemaChangeClass",
    "plan_migration",
]


class SchemaChangeClass(StrEnum):
    """How much of the existing graph a change invalidates.

    Ordered: `_RANK` below turns a set of observed changes into the single
    verdict for the release, which is always the most severe one present. A plan
    is only as safe as its worst element.
    """

    NONE = "NONE"
    ADDITIVE = "ADDITIVE"
    COMPATIBLE = "COMPATIBLE"
    DESTRUCTIVE = "DESTRUCTIVE"


_RANK: dict[SchemaChangeClass, int] = {
    SchemaChangeClass.NONE: 0,
    SchemaChangeClass.ADDITIVE: 1,
    SchemaChangeClass.COMPATIBLE: 2,
    SchemaChangeClass.DESTRUCTIVE: 3,
}


class MigrationStrategy(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    #: Retained so plans recorded before the change classes existed still
    #: deserialize. `plan_migration` no longer produces it -- see the module
    #: docstring for why "incremental" was the wrong promise for both cheap tiers.
    INCREMENTAL = "INCREMENTAL"
    BACKFILL = "BACKFILL"
    AFFECTED_SCOPE_RESYNC = "AFFECTED_SCOPE_RESYNC"
    FULL_REBUILD = "FULL_REBUILD"


_STRATEGY_FOR: dict[SchemaChangeClass, MigrationStrategy] = {
    SchemaChangeClass.NONE: MigrationStrategy.NO_CHANGE,
    SchemaChangeClass.ADDITIVE: MigrationStrategy.BACKFILL,
    SchemaChangeClass.COMPATIBLE: MigrationStrategy.AFFECTED_SCOPE_RESYNC,
    SchemaChangeClass.DESTRUCTIVE: MigrationStrategy.FULL_REBUILD,
}


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
    #: Defaulted rather than required: plans recorded before change classes
    #: existed carry no class, and inventing one for them would be a claim about
    #: a judgement nobody made.
    change_class: SchemaChangeClass = SchemaChangeClass.DESTRUCTIVE


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
    #: Defaulted for the same reason as `ElementChange.change_class`: a plan
    #: recorded before this existed is read back, not re-judged.
    change_class: SchemaChangeClass = SchemaChangeClass.DESTRUCTIVE
    node_labels_added: tuple[str, ...] = ()
    node_labels_removed: tuple[str, ...] = ()
    node_labels_changed: tuple[ElementChange, ...] = ()
    relationships_added: tuple[str, ...] = ()
    relationships_removed: tuple[str, ...] = ()
    relationships_changed: tuple[ElementChange, ...] = ()
    objects_to_create: tuple[GraphObject, ...] = ()
    objects_to_drop: tuple[GraphObject, ...] = ()
    rebuild_reasons: tuple[str, ...] = ()
    #: Why the cheaper tiers are owed work, in the same shape as
    #: `rebuild_reasons`. A resync that an operator cannot explain is as
    #: unreviewable as a rebuild they cannot explain.
    resync_reasons: tuple[str, ...] = ()
    #: Which sources a BACKFILL or AFFECTED_SCOPE_RESYNC has to re-scan.
    #:
    #: Empty for NO_CHANGE, and empty for FULL_REBUILD -- a cutover rebuilds
    #: every source by definition, so a partial scope there would be a scope
    #: nothing honours. Non-empty is what makes the cheap tiers cheap: the whole
    #: point is not re-reading sources the change did not touch.
    affected_source_asset_ids: tuple[str, ...] = ()

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
            change_class=SchemaChangeClass.DESTRUCTIVE,
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
    resync_reasons: list[str] = []
    affected: set[str] = set()

    def _record(element: str, change: _LabelChange | None) -> ElementChange | None:
        """File one element's verdict under the tier that has to act on it."""
        if change is None:
            return None
        if change.change_class is SchemaChangeClass.DESTRUCTIVE:
            reasons.append(f"{element}: {change.detail}")
        elif change.change_class is SchemaChangeClass.COMPATIBLE:
            resync_reasons.append(f"{element}: {change.detail}")
        return ElementChange(
            element=element, detail=change.detail, change_class=change.change_class
        )

    changed: list[ElementChange] = []
    for label in sorted(set(from_labels) & set(to_labels)):
        entry = _record(label, _label_change(from_labels[label], to_labels[label]))
        if entry is None:
            continue
        changed.append(entry)
        if entry.change_class is not SchemaChangeClass.DESTRUCTIVE:
            # Only the cheap tiers have a scope worth carrying. The source is
            # the *new* one, because that is what a resync would read.
            affected.add(to_labels[label].source_asset_id)

    # A new label has no nodes yet, so the sync that fills it in has to read
    # every record its source already holds -- an incremental pass would see
    # only what changed since a checkpoint that predates the label entirely.
    for label in added:
        affected.add(to_labels[label].source_asset_id)

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
        entry = _record(key, _relationship_change(before, after))
        if entry is not None:
            relationships_changed.append(entry)
    for key in relationships_removed:
        reasons.append(
            f"{key}: the release stops projecting this relationship, and edges already "
            "written are not retired by a sync that no longer knows about them"
        )
    if relationships_added or relationships_changed:
        # Stage B joins edges from nodes already materialized, and it is driven
        # by a sync run over the endpoints' sources.
        for key in (*relationships_added, *(entry.element for entry in relationships_changed)):
            affected.update(_relationship_sources(target, key))

    if current.graph.database != target.graph.database:
        reasons.append(
            f"the graph database moves from {current.graph.database!r} to "
            f"{target.graph.database!r}, so nothing already written is reachable"
        )

    objects_to_create = tuple(sorted(to_objects - from_objects, key=_object_order))
    objects_to_drop = tuple(sorted(from_objects - to_objects, key=_object_order))

    change_class = _change_class(
        reasons=reasons,
        resync_reasons=resync_reasons,
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
    strategy = _STRATEGY_FOR[change_class]
    return MigrationPlan(
        from_release_id=current.configuration_release_id,
        to_release_id=target.configuration_release_id,
        strategy=strategy,
        change_class=change_class,
        node_labels_added=added,
        node_labels_removed=removed,
        node_labels_changed=tuple(changed),
        relationships_added=relationships_added,
        relationships_removed=relationships_removed,
        relationships_changed=tuple(relationships_changed),
        objects_to_create=objects_to_create,
        objects_to_drop=objects_to_drop,
        rebuild_reasons=tuple(reasons),
        resync_reasons=tuple(resync_reasons),
        # A cutover rebuilds everything, so a partial scope there would be one
        # nothing honours -- see the field's own note.
        affected_source_asset_ids=(
            () if change_class is SchemaChangeClass.DESTRUCTIVE else tuple(sorted(affected))
        ),
    )


def _change_class(
    *, reasons: Sequence[str], resync_reasons: Sequence[str], touched: bool
) -> SchemaChangeClass:
    """The release's verdict is its worst element's verdict."""
    observed = [SchemaChangeClass.NONE]
    if reasons:
        observed.append(SchemaChangeClass.DESTRUCTIVE)
    if resync_reasons:
        observed.append(SchemaChangeClass.COMPATIBLE)
    if touched:
        observed.append(SchemaChangeClass.ADDITIVE)
    return max(observed, key=lambda item: _RANK[item])


def _relationship_sources(schema: ActiveSchema, element: str) -> frozenset[str]:
    """Both endpoint sources of the relationship a plan entry names.

    Entries are keyed by the reader-facing `Source-[TYPE]->Target` string rather
    than by relationship id, so this resolves back through the same rendering
    rather than parsing it -- a parse would break the first time a label
    contained a bracket.
    """
    for relationship in schema.graph.relationships.values():
        source_node = schema.entity_node(relationship.source_entity_id)
        target_node = schema.entity_node(relationship.target_entity_id)
        key = f"{source_node.label}-[{relationship.relationship_type}]->{target_node.label}"
        if key == element:
            return frozenset(
                {
                    schema.entities[relationship.source_entity_id].source_asset_id,
                    schema.entities[relationship.target_entity_id].source_asset_id,
                }
            )
    return frozenset()


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
    #: How each key field is read out of the source document.
    #:
    #: Without this a re-pointed key was invisible: two releases can agree that
    #: `Order` is keyed on `order_id` while disagreeing about where `order_id`
    #: comes from, and the plan would report NO_CHANGE for a change that re-keys
    #: every node in the graph.
    key_mappings: tuple[str, ...] = ()
    #: The same, for projected non-key properties, keyed by graph property.
    property_mappings: tuple[tuple[str, str], ...] = ()
    #: Which source records the entity projects at all -- record_path, explode,
    #: where, distinct, key_resolution. Narrowing this orphans the nodes it
    #: stops producing, and a merge-only writer cannot reach them.
    record_selection: str = ""


class _LabelChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: str
    change_class: SchemaChangeClass


def _mapping_signature(field: Any) -> str:
    """How one field is obtained, as a comparable string.

    A field is either read from a path or derived from other fields; both have
    to participate, because swapping one for the other changes every value the
    field produces while leaving its name and type identical.
    """
    path = ".".join(field.physical_path) if field.physical_path else ""
    derive = "" if field.derive is None else repr(sorted(field.derive.model_dump().items()))
    return f"{field.path_origin.value}|{path}|{derive}"


def _record_selection(entity: Any) -> str:
    """Which records of a source the entity projects, as a comparable string."""
    where = ";".join(
        f"{'.'.join(selector.physical_path)}={selector.equals}"
        for selector in sorted(entity.where, key=lambda item: item.physical_path)
    )
    resolution = (
        ""
        if entity.key_resolution is None
        else repr(sorted(entity.key_resolution.model_dump().items()))
    )
    return (
        f"path={'.'.join(entity.record_path)}|explode={entity.explode}|"
        f"distinct={entity.distinct}|where={where}|key_resolution={resolution}"
    )


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
            key_mappings=tuple(
                _mapping_signature(entity.fields[field_id]) for field_id in node.key_fields
            ),
            property_mappings=tuple(
                sorted(
                    (
                        entity.fields[field_id].graph_property,
                        _mapping_signature(entity.fields[field_id]),
                    )
                    for field_id in node.property_fields
                )
            ),
            record_selection=_record_selection(entity),
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
            change_class=SchemaChangeClass.DESTRUCTIVE,
        )
    if before.key_mappings != after.key_mappings:
        return _LabelChange(
            detail=(
                "the key is read from a different place in the source document, so the "
                "same key fields resolve to different values; a merge would insert a "
                "second node beside every existing one"
            ),
            change_class=SchemaChangeClass.DESTRUCTIVE,
        )
    if before.source_asset_id != after.source_asset_id:
        return _LabelChange(
            detail=(
                f"reads {after.source_asset_id!r} instead of {before.source_asset_id!r}; "
                "the nodes already in the graph came from the old source"
            ),
            change_class=SchemaChangeClass.DESTRUCTIVE,
        )
    if before.record_selection != after.record_selection:
        return _LabelChange(
            detail=(
                "the release projects a different set of the source's records; nodes it "
                "stops producing are unreachable to a writer that only merges what it reads"
            ),
            change_class=SchemaChangeClass.DESTRUCTIVE,
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
    before_mappings = dict(before.property_mappings)
    after_mappings = dict(after.property_mappings)
    remapped = sorted(
        name
        for name in set(before_mappings) & set(after_mappings)
        if before_mappings[name] != after_mappings[name]
    )

    if dropped:
        return _LabelChange(
            detail=(
                f"stops projecting {dropped}; a merge never unsets, so the graph would go "
                "on serving properties this release says do not exist"
            ),
            change_class=SchemaChangeClass.DESTRUCTIVE,
        )
    # Retyping and remapping are the COMPATIBLE tier: identity is untouched, so
    # the nodes are the same nodes, and `SET n += properties` overwrites what is
    # already there. What they cannot survive is an *incremental* pass, which
    # only re-reads records whose cursor moved -- hence a scoped full re-scan.
    if retyped:
        return _LabelChange(
            detail=(
                f"changes the type of {retyped}; the values already stored were written "
                "under the old type and are corrected by re-reading this label's source"
            ),
            change_class=SchemaChangeClass.COMPATIBLE,
        )
    if remapped:
        return _LabelChange(
            detail=(
                f"reads {remapped} from a different place in the source document; the "
                "stored values are stale until this label's source is re-read"
            ),
            change_class=SchemaChangeClass.COMPATIBLE,
        )
    if gained:
        return _LabelChange(detail=f"adds {gained}", change_class=SchemaChangeClass.ADDITIVE)
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


def _relationship_change(before: tuple[str, ...], after: tuple[str, ...]) -> _LabelChange:
    """Match fields are identity; a cardinality bound is only a check.

    Re-matching an edge draws it between different nodes and leaves the old edge
    in place, which nothing incremental retires -- destructive. A cardinality
    bound changes what the *next* reconciliation refuses to write; re-running it
    over the affected sources is the whole remedy, so it belongs in the resync
    tier rather than costing a rebuild.
    """
    if before[1:] != after[1:]:
        return _LabelChange(
            detail=(
                f"matches on {after[1]} -> {after[2]} instead of {before[1]} -> {before[2]}; "
                "the edges already written join different nodes"
            ),
            change_class=SchemaChangeClass.DESTRUCTIVE,
        )
    return _LabelChange(
        detail=(
            f"cardinality changes from {before[0]} to {after[0]}; edges written under the old "
            "declaration are re-checked by re-running reconciliation over these sources"
        ),
        change_class=SchemaChangeClass.COMPATIBLE,
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
