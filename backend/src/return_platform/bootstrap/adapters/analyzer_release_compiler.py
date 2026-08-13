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

**What the draft does not carry comes from two places, not one.** Where each
dataset lives comes from the *source binding catalogue*, which is separately
editable precisely because infrastructure moves on a different clock from the
graph's shape. Everything else that is configuration rather than shape -- agent
policies, the graph database name, prompt, policy and compiler versions --
comes from a baseline release. Neither is guessed: a dataset the catalogue
cannot resolve is an error here, because a release that picked a plausible
neighbouring source would sync real data from the wrong place.

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

from return_platform.bootstrap.adapters.analyzer_source_observation import (
    SourceObservation,
    observed_capabilities,
    observed_permissions,
    observed_selectivity,
    observed_strong_anchors,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntityDefinition,
    EntitySourceAccess,
    FieldDefinition,
    FieldType,
    GraphDefinition,
    NodeProjection,
    RelationshipCardinality,
    RelationshipProjection,
    SourceAssetDefinition,
    SourceContractStatus,
    maximum_relationship_access,
)
from return_platform.dynamic_knowledge.source_bindings import (
    SourceBindingCatalogue,
    catalogue_from,
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
    bindings: SourceBindingCatalogue | None = None,
    observations: Mapping[str, SourceObservation] | None = None,
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

    `bindings` resolves dataset names to source assets. Omitted, it falls back
    to what the baseline itself reaches -- the state of an installation that
    has never rebound anything -- so the argument is a widening, not a new
    requirement.

    `observations` -- keyed by the dataset name the draft's entities cite -- is
    what the analyzer's inspection port saw in each source. Supplying one is what
    lets a compiled entity be *readable*: nullability, per-field capabilities,
    permissions and strong anchors all follow from the source's declared types
    and indexes (`analyzer_source_observation`). Without one the compiler has
    only the authoring form, which says nothing about any of that, so the entity
    is marked `UNVERIFIED` -- an honest statement that nothing checked its paths
    against a source, and previously the one thing the compiler asserted without
    evidence.
    """
    catalogue = catalogue_from(baseline) if bindings is None else bindings
    observed = dict(observations or {})
    shape_entities: Mapping[str, Mapping[str, Any]] = shape.get("entities") or {}
    shape_relationships: Sequence[Mapping[str, Any]] = shape.get("relationships") or ()
    if not shape_entities:
        raise ReleaseCompilationError("an empty shape has nothing to run")

    entities: dict[str, EntityDefinition] = {}
    nodes: dict[str, NodeProjection] = {}
    used_sources: dict[str, SourceAssetDefinition] = {}

    for label, entity in sorted(shape_entities.items()):
        definition, source = _entity(
            label, entity, bindings=catalogue, observation=observed.get(_dataset_of(entity))
        )
        entities[label] = definition
        used_sources[definition.source_asset_id] = source
        identifiers = tuple(str(name) for name in entity.get("identifier_properties") or ())
        nodes[label] = NodeProjection(
            projection_id=label,
            entity_id=label,
            # The graph label, which is not the entity id. The runtime keeps them
            # apart -- `sales_order` is projected as `SalesOrder` -- and the
            # authoring form has one name for both, so the label is derived by a
            # stated rule rather than left as whatever the draft happened to type.
            # Pascal-casing is identity for a label already in that form, so
            # every existing draft compiles to exactly what it did before.
            label=_graph_label(label),
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


def _dataset_of(entity: Mapping[str, Any]) -> str:
    return str(entity.get("source_dataset") or "")


def _record_path(source_field: str, *, dataset: str) -> tuple[str, ...]:
    """The path into a record, with the dataset's own qualifier removed.

    Only that prefix. A dotted `customer.address` is a path inside a document and
    stripping anything else would address the wrong thing.
    """
    prefix = f"{dataset}."
    trimmed = (
        source_field[len(prefix) :] if dataset and source_field.startswith(prefix) else source_field
    )
    return tuple(trimmed.split("."))


def _graph_label(label: str) -> str:
    """`warehouse` -> `Warehouse`, `OrderLine` -> `OrderLine`.

    Underscore-separated parts are joined and each part's first letter is
    upper-cased; everything else is left alone, so a label already written in the
    graph's convention passes through untouched.
    """
    return "".join(part[:1].upper() + part[1:] for part in label.split("_") if part)


def _entity(
    label: str,
    entity: Mapping[str, Any],
    *,
    bindings: SourceBindingCatalogue,
    observation: SourceObservation | None,
) -> tuple[EntityDefinition, SourceAssetDefinition]:
    dataset = _dataset_of(entity)
    source = _source(dataset, bindings=bindings)

    properties: Mapping[str, Mapping[str, Any]] = entity.get("properties") or {}
    if not properties:
        raise ReleaseCompilationError(f"entity {label!r} has no properties to project")

    fields = {
        name: _field(label, name, spec, dataset=dataset, observation=observation)
        for name, spec in sorted(properties.items())
    }

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
            # Provenance, not prose. The authoring form carries no description --
            # `rationale` on a command is documentation the design says nothing
            # interprets -- so writing anything *about* the entity here would be
            # this compiler inventing meaning. What it can state is what it saw:
            # which object, how many rows the catalogue reported, what the key is.
            # `compact_schema` puts this in front of the model, and an entity with
            # an empty description arrives there unusable.
            description=_provenance(label, source, identifiers, observation),
            source_asset_id=source.source_asset_id,
            fields=fields,
            natural_key=identifiers,
            strong_anchors=(
                {}
                if observation is None
                else observed_strong_anchors(
                    observation,
                    columns_by_field=_columns_of(fields, observation),
                    natural_key=identifiers,
                    capabilities={field_id: item.capabilities for field_id, item in fields.items()},
                )
            ),
            # The one claim the compiler used to make with no evidence at all:
            # `SourceContractStatus` defaults to VERIFIED on the model, so an
            # entity compiled from an authoring form nobody had checked against a
            # source arrived declaring its paths confirmed -- and, since
            # `EntityDefinition` refuses `CONNECTED_SYNC` on an unverified
            # contract, arrived permitted to sync from a source nothing had read.
            # The two move together, which is the coupling the model already
            # enforces in the other direction.
            source_contract_status=(
                SourceContractStatus.UNVERIFIED
                if observation is None
                else SourceContractStatus.VERIFIED
            ),
            source_access=(
                EntitySourceAccess.SEED_ONLY
                if observation is None
                else EntitySourceAccess.CONNECTED_SYNC
            ),
        ),
        source,
    )


def _provenance(
    label: str,
    source: SourceAssetDefinition,
    identifiers: tuple[str, ...],
    observation: SourceObservation | None,
) -> str:
    object_name = source.object_ref.get("name") or source.source_asset_id
    key = ", ".join(identifiers)
    if observation is None:
        return (
            f"{label}, from {object_name} in {source.source_asset_id}, keyed on {key}. "
            "Compiled from an approved draft with no source observation, so its field "
            "paths are UNVERIFIED: nothing has checked them against the source."
        )
    rows = observation.description.approximate_row_count
    counted = "an unreported number of" if rows is None else f"approximately {rows}"
    return (
        f"{label}, from {object_name} in {source.source_asset_id}, keyed on {key}. "
        f"Compiled from the analyzer's inspection of that object: {counted} rows, "
        f"{len(observation.description.fields)} declared column(s), "
        f"{len(observation.indexes)} declared index(es). Every field below reads a column "
        "the source's own catalogue declares, and the capabilities follow from the indexes "
        "over it."
    )


def _columns_of(
    fields: Mapping[str, FieldDefinition], observation: SourceObservation
) -> dict[str, str]:
    """Field id -> the source column it reads, for the flat fields only.

    A nested `physical_path` addresses a position inside a document, and an
    object description reports the keys at the top of one. Matching the joined
    path against a field name would compare `customer.address` to nothing and
    silently answer "no such column", so only single-segment paths are offered
    here -- which is every column of a relational source and the top level of a
    document one.
    """
    columns: dict[str, str] = {}
    for field_id, definition in fields.items():
        path = definition.physical_path
        if path is not None and len(path) == 1 and observation.declared_type(path[0]) is not None:
            columns[field_id] = path[0]
    return columns


def _field(
    label: str,
    name: str,
    spec: Mapping[str, Any],
    *,
    dataset: str,
    observation: SourceObservation | None,
) -> FieldDefinition:
    declared = str(spec.get("type") or "")
    data_type = _PROPERTY_TYPES.get(declared)
    if data_type is None:
        raise ReleaseCompilationError(f"property {label}.{name} has unsupported type {declared!r}")
    source_field = str(spec.get("source_field") or "")
    if not source_field:
        raise ReleaseCompilationError(f"property {label}.{name} is not mapped to a source field")
    # `a.b.c` in the authoring form is a path into the record, which is what the
    # runtime's physical_path means. A dotted name and a nested path are the same
    # statement said two ways -- *except* for a leading dataset qualifier, which
    # is not part of any record.
    #
    # `ReanalysisService` writes `source_field` as `<dataset>.<column>`, and
    # `_field_of` there strips that prefix before comparing. This did not, so
    # every property a re-analysis proposed compiled to a path beginning with the
    # collection or table name -- `salesInv.trkNum`, `platform.bay_configuration.
    # bay_id` -- which resolves on no document and projects null forever. The two
    # halves of the same convention now agree.
    physical_path = _record_path(source_field, dataset=dataset)
    column = physical_path[0] if len(physical_path) == 1 else None
    if observation is None or column is None or observation.declared_type(column) is None:
        return FieldDefinition(
            field_id=name,
            physical_path=physical_path,
            graph_property=name,
            data_type=data_type,
        )
    capabilities = observed_capabilities(observation, column=column, data_type=data_type)
    return FieldDefinition(
        field_id=name,
        physical_path=physical_path,
        graph_property=name,
        data_type=data_type,
        # `nullable` defaults to True on the model, which is the safe reading
        # when nothing is known. The catalogue does know, and a NOT NULL column
        # declared nullable makes every consumer treat a guaranteed value as
        # optional.
        nullable=bool(observation.nullable(column)),
        capabilities=capabilities,
        permissions=observed_permissions(capabilities),
        # W4.8. `capabilities` says what the source's access paths *permit* being
        # asked of this column; selectivity says what asking would be worth. They
        # come from different evidence -- indexes and declared types for one,
        # counted rows for the other -- and a field can easily be searchable and
        # useless at narrowing. Carrying only the first is what left ranked
        # elicitation to a model guessing over a catalogue with no statistics.
        selectivity=observed_selectivity(observation, column=column),
    )


def _source(dataset: str, *, bindings: SourceBindingCatalogue) -> SourceAssetDefinition:
    """The source asset behind a dataset name.

    One lookup, through the catalogue, which already layers deliberate
    rebindings over what configuration reaches. The compiler used to do this
    matching itself against the baseline, which meant a draft could only ever
    name a source the shipped file already had -- and rebinding one was a
    schema edit.
    """
    if not dataset:
        raise ReleaseCompilationError("an entity has no source dataset")
    resolved = bindings.resolve(dataset)
    if resolved is None:
        raise ReleaseCompilationError(
            f"no source binding for dataset {dataset!r}; the draft can name it but only "
            f"a binding says how to reach it (known: {', '.join(bindings.datasets()) or 'none'})"
        )
    return resolved


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
        # Capped by its endpoints rather than left at the model's
        # `CONNECTED_SYNC` default. An edge between two seed-only entities that
        # declared a connected sync fails the schema's own cross-validation, so
        # this is what lets an unobserved addition compile at all -- and, more to
        # the point, an edge cannot claim to be formed by a sync that neither of
        # the things it joins is permitted to run.
        access=maximum_relationship_access(
            entities[from_label].source_access, entities[to_label].source_access
        ),
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
