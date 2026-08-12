"""Turns an approved analyzer draft into the runtime's `ActiveSchema`.

**The analyzer has never reached the runtime.** An analyst could discover a
source, edit a schema, validate it and approve it, and the agent would go on
reading a YAML file checked into the repository -- `request_build` raises
`NotImplementedError` and nothing else consumed the approved shape. Two schemas
existed: the one people edited and the one the platform ran.

**In `bootstrap/adapters/` because the analyzer may not import the runtime.**
The analyzer reaches every other module through a port bound here (design doc
2.7), and this translation is exactly such a binding: it takes the draft shape
as plain data -- the currency `GraphTargetPort` already speaks -- and hands back
the runtime's own model.

This is the single translation between them, and it produces `ActiveSchema`
itself rather than a third representation. The draft's `GraphSchemaShape` is the
*authoring* form -- labels, properties, source fields, cardinalities -- and
`ActiveSchema` is the *compiled* form the sync service and every agent turn
read. One is edited, one is executed, and this function is the only thing that
knows both.

**What the draft does not carry comes from a baseline release.** Connector type,
connection reference and object reference per source; agent policies; the graph
database name; prompt, policy and compiler versions. None of those are graph
shape, and inventing analyzer surfaces for them would put configuration in two
places (see the split in W2.2). Anything the baseline cannot answer is an error
here, never a default: a compiled release that guessed at a connection would
fail at sync time, a long way from the person who could have said.

Refusal is the design. Every uncertainty -- an entity with no identifier, a
relationship whose join cannot be resolved, a source the baseline has never
heard of -- raises `ReleaseCompilationError` naming the element. An approved
schema that compiles into something subtly different from what was approved is
the one outcome worse than not compiling.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDefinition,
    FieldDefinition,
    FieldType,
    GraphDefinition,
    NodeProjection,
    RelationshipCardinality,
    RelationshipProjection,
    SourceAssetDefinition,
)

__all__ = ["ReleaseCompilationError", "compile_active_schema", "release_checksum"]


class ReleaseCompilationError(RuntimeError):
    """An approved shape that cannot become a runnable release."""


# The analyzer's authoring vocabulary is deliberately smaller than the runtime's
# -- it has no OBJECT or ARRAY, because a property the model can map to a source
# field is a scalar. FLOAT widens to NUMBER, which is the runtime's name for the
# same thing.
_PROPERTY_TYPES: Mapping[str, FieldType] = {
    "STRING": FieldType.STRING,
    "INTEGER": FieldType.INTEGER,
    "FLOAT": FieldType.NUMBER,
    "BOOLEAN": FieldType.BOOLEAN,
    "DATE": FieldType.DATE,
    "DATETIME": FieldType.DATETIME,
}

_CARDINALITIES: Mapping[str, RelationshipCardinality] = {
    "ONE_TO_ONE": RelationshipCardinality.ONE_TO_ONE,
    "ONE_TO_MANY": RelationshipCardinality.ONE_TO_MANY,
    "MANY_TO_ONE": RelationshipCardinality.MANY_TO_ONE,
    "MANY_TO_MANY": RelationshipCardinality.MANY_TO_MANY,
}


def compile_active_schema(
    shape: Mapping[str, Any],
    *,
    baseline: ActiveSchema,
    configuration_release_id: str,
    schema_version: str,
    approved_by: str,
    approved_at: datetime,
) -> ActiveSchema:
    """Compile an approved shape into a release the runtime can load.

    `baseline` supplies everything that is configuration rather than graph
    shape. It is read, never merged: an entity the draft dropped is gone from
    the release, because a compiler that kept the baseline's version of it would
    make removal impossible and the approved shape a suggestion.
    """
    shape_entities: Mapping[str, Mapping[str, Any]] = shape.get("entities") or {}
    shape_relationships: Sequence[Mapping[str, Any]] = shape.get("relationships") or ()
    if not shape_entities:
        raise ReleaseCompilationError("an empty shape has nothing to run")

    entities: dict[str, EntityDefinition] = {}
    nodes: dict[str, NodeProjection] = {}
    used_sources: dict[str, SourceAssetDefinition] = {}

    for label, entity in sorted(shape_entities.items()):
        definition, source = _entity(label, entity, baseline=baseline)
        entities[label] = definition
        used_sources[definition.source_asset_id] = source
        identifiers = tuple(str(name) for name in entity.get("identifier_properties") or ())
        nodes[label] = NodeProjection(
            projection_id=label,
            entity_id=label,
            label=label,
            key_fields=identifiers,
            # Everything that is not a key. Listing keys twice would project the
            # same property under two headings, and the writer treats key and
            # property fields differently on merge.
            property_fields=tuple(
                name for name in sorted(definition.fields) if name not in identifiers
            ),
        )

    relationships: dict[str, RelationshipProjection] = {}
    for item in shape_relationships:
        projection = _relationship(item, entities=entities)
        if projection.relationship_id in relationships:
            raise ReleaseCompilationError(
                f"two relationships compile to the same id {projection.relationship_id!r}"
            )
        relationships[projection.relationship_id] = projection

    draft_release = ActiveSchema(
        configuration_release_id=configuration_release_id,
        # Replaced below. The model requires a well-formed digest, and the
        # digest is over the release, so it cannot be known until the rest is.
        configuration_checksum="0" * 64,
        release_status="ACTIVE",
        approved_by=approved_by,
        approved_at=approved_at,
        schema_version=schema_version,
        policy_version=baseline.policy_version,
        prompt_version=baseline.prompt_version,
        compiler_version=baseline.compiler_version,
        runtime_mode=baseline.runtime_mode,
        sources=used_sources,
        entities=entities,
        graph=GraphDefinition(
            database=baseline.graph.database,
            nodes=nodes,
            relationships=relationships,
            constraints=tuple(dict(item) for item in shape.get("graph_constraints") or ()),
            indexes=tuple(dict(item) for item in shape.get("graph_indexes") or ()),
        ),
        agent_policies=_agent_policies(baseline, entities),
    )
    return draft_release.model_copy(
        update={"configuration_checksum": release_checksum(draft_release)}
    )


def release_checksum(schema: ActiveSchema) -> str:
    """A digest over everything in the release except the digest.

    Canonical JSON with sorted keys, so two compilations of the same approved
    shape produce the same checksum on different machines -- which is the only
    thing that makes the checksum useful for recognising "this release is the
    one that was approved".
    """
    payload = schema.model_dump(mode="json")
    payload.pop("configuration_checksum", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _entity(
    label: str, entity: Mapping[str, Any], *, baseline: ActiveSchema
) -> tuple[EntityDefinition, SourceAssetDefinition]:
    dataset = str(entity.get("source_dataset") or "")
    source = _source(dataset, baseline=baseline)

    properties: Mapping[str, Mapping[str, Any]] = entity.get("properties") or {}
    if not properties:
        raise ReleaseCompilationError(f"entity {label!r} has no properties to project")

    fields = {name: _field(label, name, spec) for name, spec in sorted(properties.items())}

    identifiers = tuple(str(name) for name in entity.get("identifier_properties") or ())
    if not identifiers:
        # Without one, every sync run matches nothing and merges nothing, so it
        # inserts the whole dataset again. Better to refuse than to duplicate
        # the graph on the second run.
        raise ReleaseCompilationError(
            f"entity {label!r} has no identifier properties, so sync could not match a node"
        )
    unknown = [name for name in identifiers if name not in fields]
    if unknown:
        raise ReleaseCompilationError(
            f"entity {label!r} identifies on properties it does not have: {sorted(unknown)}"
        )

    cursor = source.incremental_cursor_field
    if cursor is not None and cursor not in fields:
        # Dropping the cursor instead would compile, and would quietly turn an
        # incremental source into a full re-read on every run. The analyst who
        # approved the shape is the one who should decide to map the field.
        raise ReleaseCompilationError(
            f"entity {label!r} reads source {source.source_asset_id!r}, which syncs "
            f"incrementally on {cursor!r}, and the draft does not map that property"
        )

    return (
        EntityDefinition(
            entity_id=label,
            source_asset_id=source.source_asset_id,
            fields=fields,
            natural_key=identifiers,
        ),
        source,
    )


def _field(label: str, name: str, spec: Mapping[str, Any]) -> FieldDefinition:
    declared = str(spec.get("type") or "")
    data_type = _PROPERTY_TYPES.get(declared)
    if data_type is None:
        raise ReleaseCompilationError(f"property {label}.{name} has unsupported type {declared!r}")
    source_field = str(spec.get("source_field") or "")
    if not source_field:
        raise ReleaseCompilationError(f"property {label}.{name} is not mapped to a source field")
    return FieldDefinition(
        field_id=name,
        # `a.b.c` in the authoring form is a path into the record, which is what
        # the runtime's physical_path means. A dotted name and a nested path are
        # the same statement said two ways.
        physical_path=tuple(source_field.split(".")),
        graph_property=name,
        data_type=data_type,
    )


def _source(dataset: str, *, baseline: ActiveSchema) -> SourceAssetDefinition:
    """The connection behind a dataset name, from the baseline release.

    Matched on the source asset id first and on the object reference second, so
    a draft naming `orders` finds the asset whose object_ref points at
    `orders` even when the asset is called something else.
    """
    if not dataset:
        raise ReleaseCompilationError("an entity has no source dataset")
    identifier = _as_identifier(dataset)
    direct = baseline.sources.get(identifier)
    if direct is not None:
        return direct
    for source in baseline.sources.values():
        if dataset in source.object_ref.values():
            return source
    raise ReleaseCompilationError(
        f"no configured source connection for dataset {dataset!r}; "
        "the draft can name it but only configuration can say how to reach it"
    )


def _as_identifier(dataset: str) -> str:
    """`schema.table` and `order-lines` are legal source names, not identifiers."""
    return dataset.replace(".", "_").replace("-", "_")


def _relationship(
    item: Mapping[str, Any], *, entities: Mapping[str, EntityDefinition]
) -> RelationshipProjection:
    relationship_type = str(item.get("relationship_type") or "")
    from_label = str(item.get("from_label") or "")
    to_label = str(item.get("to_label") or "")
    for label in (from_label, to_label):
        if label not in entities:
            raise ReleaseCompilationError(
                f"relationship {relationship_type!r} references unknown entity {label!r}"
            )

    source_fields, target_fields = _join(
        item,
        source=entities[from_label],
        target=entities[to_label],
        described_as=f"relationship {relationship_type!r} between {from_label} and {to_label}",
    )

    cardinality = _CARDINALITIES.get(str(item.get("cardinality") or ""))
    if cardinality is None:
        raise ReleaseCompilationError(
            f"relationship {relationship_type!r} has unsupported cardinality "
            f"{item.get('cardinality')!r}"
        )
    return RelationshipProjection(
        relationship_id=f"{from_label}_{relationship_type}_{to_label}",
        relationship_type=relationship_type,
        source_entity_id=from_label,
        target_entity_id=to_label,
        source_match_fields=source_fields,
        target_match_fields=target_fields,
        cardinality=cardinality,
        maximum_targets_per_source=(
            1
            if cardinality
            in {RelationshipCardinality.ONE_TO_ONE, RelationshipCardinality.MANY_TO_ONE}
            else None
        ),
        maximum_sources_per_target=(
            1
            if cardinality
            in {RelationshipCardinality.ONE_TO_ONE, RelationshipCardinality.ONE_TO_MANY}
            else None
        ),
    )


def _join(
    item: Mapping[str, Any],
    *,
    source: EntityDefinition,
    target: EntityDefinition,
    described_as: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which fields of each side the edge matches on.

    Stated wins, and a stated join is checked on both sides. Where the draft
    said nothing, the fallback is the ordinary foreign-key shape: whichever
    entity's natural key the *other* one also carries. Tried in both directions
    because either can hold the key -- a line carries its order's id, an order
    carries its customer's -- and refused outright when neither does.

    Refusing is the point. A projection matching on a field one side lacks
    draws no edges at all, and at runtime that is indistinguishable from a
    source that had no data.
    """
    stated_source = _stated(item.get("from_properties"))
    stated_target = _stated(item.get("to_properties"))
    if stated_source is not None or stated_target is not None:
        if stated_source is None or stated_target is None:
            raise ReleaseCompilationError(
                f"{described_as} states the join on one side only; both or neither"
            )
        if len(stated_source) != len(stated_target):
            raise ReleaseCompilationError(
                f"{described_as} joins {len(stated_source)} field(s) to {len(stated_target)}"
            )
        _require_fields(stated_source, holder=source, described_as=described_as)
        _require_fields(stated_target, holder=target, described_as=described_as)
        return stated_source, stated_target

    for candidate in (source.natural_key, target.natural_key):
        if all(name in source.fields and name in target.fields for name in candidate):
            return candidate, candidate
    raise ReleaseCompilationError(
        f"{described_as} has no stated join and neither entity carries the other's "
        f"identifier, so there is nothing to match on"
    )


def _stated(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        names = tuple(str(name) for name in value)
        return names if names else ()
    return None


def _require_fields(names: tuple[str, ...], *, holder: EntityDefinition, described_as: str) -> None:
    if not names:
        raise ReleaseCompilationError(f"{described_as} declares an empty join")
    missing = [name for name in names if name not in holder.fields]
    if missing:
        raise ReleaseCompilationError(
            f"{described_as} joins on {sorted(missing)}, which {holder.entity_id} does not have"
        )


def _agent_policies(
    baseline: ActiveSchema, entities: Mapping[str, EntityDefinition]
) -> dict[str, Any]:
    """Baseline policies, narrowed to entities this release actually has.

    A policy is an allow-list, and `ActiveSchema` rejects one naming an entity
    that does not exist -- so a draft that dropped an entity would otherwise
    make every compilation fail on a policy nobody edited. Narrowing is the
    conservative direction: an agent loses reach, never gains it.
    """
    narrowed: dict[str, Any] = {}
    for agent_id, policy in baseline.agent_policies.items():
        allowed = frozenset(policy.allowed_entity_ids) & frozenset(entities)
        narrowed[agent_id] = policy.model_copy(update={"allowed_entity_ids": allowed})
    return narrowed
