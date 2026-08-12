"""Re-running discovery on a source that has moved on, without losing the design.

Discovery captured a snapshot and a draft was built from it. Running discovery a
second time -- because a collection gained a field, or a column changed type, or
the whole dataset was restored somewhere else -- had no defined behaviour at all.
The two answers available were both wrong: ignore the new evidence, or rebuild
the draft from it and throw away every decision an analyst made about what an
Order is.

**A re-analysis proposes; it never applies.** What comes back is a batch of the
same typed `MutationCommand`s a human would have written, which the analyst
accepts by sending them to the ordinary mutations endpoint or rejects by not
sending them. There is deliberately no path here that writes a draft: the
apply-and-record-a-revision path already exists and having a second one is how
"the model changed my schema" becomes possible.

**A dataset that moved is not a dataset that changed.** This is the distinction
the whole module turns on. Where `salesInv` lives is a *binding* -- separately
editable since W2.2, because infrastructure moves on a different clock from the
graph's shape. If the same fields turn up under a different source, or under a
different name with an identical shape, the graph does not change at all and the
correct act is a rebinding. Proposing a reshaping for that would rewrite a schema
because a database was restored.

**Silence where a command would be a guess.** A source field that an entity
identifies on has disappeared; a new collection whose name is not a legal label;
a declared type nothing in the authoring vocabulary covers. Each is reported with
what was seen and no command attached, because the command would encode a
decision that is the analyst's -- and a proposal an analyst clicks through
without reading is worse than a question.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
from return_platform.graph_schema_analyzer.application.validation_service import (
    is_compatible_source_type,
    property_type_for_source_type,
)
from return_platform.graph_schema_analyzer.domain.mutation import (
    AddEntity,
    AddProperty,
    ChangeTransformation,
    MutationCommand,
    RemoveProperty,
    TransformationKind,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape
from return_platform.graph_schema_analyzer.domain.schema_revision import SchemaDiff, diff_shapes
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SourceSchemaSnapshot,
)

__all__ = [
    "DriftKind",
    "ProposedChange",
    "ProposedRebinding",
    "ReanalysisProposal",
    "propose_reanalysis",
]

# The same shape the mutation commands enforce, checked here so an illegal name
# becomes a reported observation rather than a pydantic error that aborts the
# whole proposal. A source is allowed to contain a collection this analyzer
# cannot name; that is a thing to say, not a thing to crash on.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SOURCE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,127}$")

# Transformations that legitimately bridge a declared-type mismatch. Mirrors
# what validation already tolerates, so re-analysis does not propose undoing a
# mapping the analyst configured on purpose and validation is happy with.
_COERCING = frozenset({TransformationKind.PARSE_DATE.value, TransformationKind.PARSE_NUMBER.value})


class DriftKind(StrEnum):
    DATASET_ADDED = "DATASET_ADDED"
    DATASET_REMOVED = "DATASET_REMOVED"
    FIELD_ADDED = "FIELD_ADDED"
    FIELD_REMOVED = "FIELD_REMOVED"
    FIELD_TYPE_CHANGED = "FIELD_TYPE_CHANGED"


class ProposedChange(BaseModel):
    """One thing that drifted, and what to do about it.

    `mutations` is empty exactly when no command can express the fix without
    guessing. That is a first-class outcome, not a failure -- see the module
    docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    drift: DriftKind
    dataset: str
    element: str
    detail: str
    mutations: tuple[MutationCommand, ...] = ()

    @property
    def requires_human_decision(self) -> bool:
        return not self.mutations


class ProposedRebinding(BaseModel):
    """A dataset whose *shape* is unchanged and whose *location* is not.

    Carries no mutation on purpose. Nothing in the schema says where a dataset
    lives -- that has been a binding since W2.2 -- so the act this asks for is a
    rebinding through `/api/source-bindings`, and there is no command that could
    express it even if it were wanted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: str
    from_source_id: str
    to_source_id: str
    to_dataset: str
    detail: str


class ReanalysisProposal(BaseModel):
    """The whole answer to "the source moved on; now what".

    `diff` is the draft-side view: the same `SchemaDiff` a revision produces,
    computed by applying the proposed batch to a copy of the draft. So an
    analyst reads a re-analysis in exactly the vocabulary they read history in,
    and the proposal is proven applicable before it is offered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    from_content_hash: str
    to_content_hash: str
    changes: tuple[ProposedChange, ...] = ()
    rebindings: tuple[ProposedRebinding, ...] = ()
    diff: SchemaDiff

    @property
    def mutations(self) -> tuple[MutationCommand, ...]:
        """The batch, flattened in the order it must be applied in."""
        return tuple(command for change in self.changes for command in change.mutations)

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.rebindings


def propose_reanalysis(
    *,
    draft_id: str,
    shape: GraphSchemaShape,
    before: SourceSchemaSnapshot,
    after: SourceSchemaSnapshot,
    from_sequence: int,
) -> ReanalysisProposal:
    """Diff two captures of the same sources against a draft designed on the first.

    Pure. No persistence, no port, no draft write -- the caller decides what to
    do with the answer, which is what keeps "propose" from quietly becoming
    "apply".
    """
    before_by_name = {dataset.dataset_name: dataset for dataset in before.datasets}
    after_by_name = {dataset.dataset_name: dataset for dataset in after.datasets}
    entities = _entities_by_dataset(shape, before_by_name, after_by_name)

    rebindings, moved_away, moved_in = _rebindings(before_by_name, after_by_name, entities)

    changes: list[ProposedChange] = []
    claimed_labels = set(shape.entities)
    for name in sorted(set(after_by_name) - set(before_by_name) - moved_in):
        changes.append(_dataset_added(after_by_name[name], claimed_labels))
    for name in sorted(set(before_by_name) - set(after_by_name) - moved_away):
        changes.extend(_dataset_removed(name, entities.get(name, ())))

    for name in sorted(set(before_by_name) & set(after_by_name)):
        for label in entities.get(name, ()):
            changes.extend(
                _entity_field_changes(
                    label=label,
                    entity=dict(shape.entities[label]),
                    dataset=after_by_name[name],
                    previous=before_by_name[name],
                )
            )

    projected = apply_mutations(
        shape, tuple(command for change in changes for command in change.mutations)
    )
    return ReanalysisProposal(
        draft_id=draft_id,
        from_content_hash=before.content_hash,
        to_content_hash=after.content_hash,
        changes=tuple(changes),
        rebindings=tuple(rebindings),
        diff=diff_shapes(
            shape.model_dump(mode="json"),
            projected.model_dump(mode="json"),
            from_sequence=from_sequence,
            to_sequence=from_sequence + 1,
        ),
    )


def _entities_by_dataset(
    shape: GraphSchemaShape,
    before: Mapping[str, DatasetMetadata],
    after: Mapping[str, DatasetMetadata],
) -> dict[str, tuple[str, ...]]:
    """Which entities read which dataset.

    An entity may name a dataset bare (`orders`) or qualified
    (`mongo_main.orders`); both are things an analyst writes and validation
    already accepts both, so both resolve here to the same dataset.
    """
    known = {**before, **after}
    mapping: dict[str, list[str]] = {}
    for label, entity in shape.entities.items():
        name = _dataset_of(str(entity.get("source_dataset") or ""), known)
        if name is not None:
            mapping.setdefault(name, []).append(label)
    return {name: tuple(sorted(labels)) for name, labels in mapping.items()}


def _dataset_of(reference: str, known: Mapping[str, DatasetMetadata]) -> str | None:
    if reference in known:
        return reference
    _, _, tail = reference.partition(".")
    return tail if tail in known else None


def _shape_key(dataset: DatasetMetadata) -> tuple[tuple[str, str], ...]:
    """A dataset's field shape, ignoring where it lives and what it is called.

    The whole basis of the moved/changed distinction: two captures with this
    key equal describe the same data, wherever it turned up.
    """
    return tuple(
        sorted((field.field_name, field.declared_type.lower()) for field in dataset.fields)
    )


def _rebindings(
    before: Mapping[str, DatasetMetadata],
    after: Mapping[str, DatasetMetadata],
    entities: Mapping[str, tuple[str, ...]],
) -> tuple[list[ProposedRebinding], set[str], set[str]]:
    """Datasets that moved rather than changed, and the names to stop treating
    as added or removed because of it.

    Two ways a dataset moves. It keeps its name and turns up under a different
    source -- a restored database, a migrated cluster. Or it keeps its shape and
    turns up under a different name. The second is only recognised when exactly
    one vanished dataset and one new dataset share a shape: with two candidates
    there is no evidence which went where, and picking one would rebind a live
    entity to real data from the wrong place.
    """
    proposals: list[ProposedRebinding] = []
    moved_away: set[str] = set()
    moved_in: set[str] = set()

    for name in sorted(set(before) & set(after)):
        was, now = before[name], after[name]
        if was.source_id == now.source_id or _shape_key(was) != _shape_key(now):
            continue
        if not entities.get(name):
            continue
        proposals.append(
            ProposedRebinding(
                dataset=name,
                from_source_id=was.source_id,
                to_source_id=now.source_id,
                to_dataset=name,
                detail=(
                    f"the same fields now come from {now.source_id!r} instead of "
                    f"{was.source_id!r}; where a dataset lives is a binding, not part of "
                    "the graph's shape, so nothing in the schema needs to change"
                ),
            )
        )

    vanished = {name: before[name] for name in set(before) - set(after)}
    arrived = {name: after[name] for name in set(after) - set(before)}
    for name in sorted(vanished):
        if not entities.get(name):
            # Nothing reads it, so there is no binding to move. Whatever turned
            # up in its place is judged on its own as a new dataset.
            continue
        key = _shape_key(vanished[name])
        candidates = sorted(
            other for other, dataset in arrived.items() if _shape_key(dataset) == key
        )
        if len(candidates) != 1:
            continue
        target = arrived[candidates[0]]
        proposals.append(
            ProposedRebinding(
                dataset=name,
                from_source_id=vanished[name].source_id,
                to_source_id=target.source_id,
                to_dataset=target.dataset_name,
                detail=(
                    f"{name!r} is gone and {target.dataset_name!r} has exactly its fields; "
                    "this is the dataset in a new place, so rebind it rather than "
                    "reshaping the graph around it"
                ),
            )
        )
        moved_away.add(name)
        moved_in.add(target.dataset_name)
    return proposals, moved_away, moved_in


def _dataset_added(dataset: DatasetMetadata, claimed_labels: set[str]) -> ProposedChange:
    """A collection nothing in the draft reads.

    Proposed as a whole entity -- the label and every field it can type -- but
    with no identifier: which property identifies a thing is a modelling
    decision, and validation will say so loudly if the analyst accepts this and
    stops there.
    """
    label = _label_for(dataset.dataset_name)
    if label is None or label in claimed_labels:
        return ProposedChange(
            drift=DriftKind.DATASET_ADDED,
            dataset=dataset.dataset_name,
            element=dataset.dataset_name,
            detail=(
                f"the source has a new dataset {dataset.dataset_name!r} and no label can be "
                "derived from its name that is both legal and unused; name it yourself if "
                "it belongs in the graph"
            ),
        )
    if not _SOURCE_REF.match(dataset.dataset_name):
        return ProposedChange(
            drift=DriftKind.DATASET_ADDED,
            dataset=dataset.dataset_name,
            element=dataset.dataset_name,
            detail=(
                f"the source has a new dataset {dataset.dataset_name!r}, whose name a "
                "mutation cannot carry; bind it to a name the schema can hold first"
            ),
        )

    claimed_labels.add(label)
    commands: list[MutationCommand] = [
        AddEntity(
            label=label,
            source_dataset=dataset.dataset_name,
            rationale=f"re-analysis found a new dataset {dataset.dataset_name!r}",
        )
    ]
    unmapped: list[str] = []
    used: set[str] = set()
    for field in dataset.fields:
        command = _add_property(label, dataset, field, used)
        if command is None:
            unmapped.append(field.field_name)
            continue
        commands.append(command)
    detail = f"new dataset {dataset.dataset_name!r} with {len(dataset.fields)} field(s)"
    if unmapped:
        detail += f"; {unmapped} could not be typed and are left out"
    return ProposedChange(
        drift=DriftKind.DATASET_ADDED,
        dataset=dataset.dataset_name,
        element=label,
        detail=detail,
        mutations=tuple(commands),
    )


def _dataset_removed(name: str, labels: Sequence[str]) -> list[ProposedChange]:
    """A dataset an entity reads that discovery no longer finds.

    Never a `RemoveEntity`. A vanished dataset is far more often a move this
    function could not prove -- a rename with a changed shape, a source that was
    not reachable during this run -- than a decision to delete a chunk of the
    graph, and one accepted proposal would be enough to lose it.
    """
    return [
        ProposedChange(
            drift=DriftKind.DATASET_REMOVED,
            dataset=name,
            element=label,
            detail=(
                f"{label} reads {name!r}, which this discovery did not find. If it moved, "
                "rebind it; if it is really gone, remove the entity yourself -- that is "
                "not a change worth proposing on one absent reading"
            ),
        )
        for label in labels
    ]


def _entity_field_changes(
    *,
    label: str,
    entity: Mapping[str, Any],
    dataset: DatasetMetadata,
    previous: DatasetMetadata,
) -> list[ProposedChange]:
    properties: Mapping[str, Mapping[str, Any]] = entity.get("properties") or {}
    identifiers = {str(name) for name in entity.get("identifier_properties") or ()}
    fields_now = {field.field_name: field for field in dataset.fields}
    fields_before = {field.field_name: field for field in previous.fields}

    mapped: dict[str, str] = {}
    for name, spec in properties.items():
        field_name = _field_of(str(spec.get("source_field") or ""), dataset.dataset_name)
        if field_name in fields_now or field_name in fields_before:
            mapped[name] = field_name

    changes: list[ProposedChange] = []
    for name in sorted(mapped):
        field_name = mapped[name]
        spec = properties[name]
        if field_name not in fields_now:
            changes.append(_field_removed(label, name, dataset, identifiers))
            continue
        change = _field_retyped(
            label=label,
            property_name=name,
            spec=spec,
            dataset=dataset,
            before=fields_before.get(field_name),
            now=fields_now[field_name],
            identifiers=identifiers,
        )
        if change is not None:
            changes.append(change)

    used = set(properties)
    for field_name in sorted(set(fields_now) - set(fields_before)):
        if field_name in mapped.values():
            continue
        changes.append(_field_added(label, dataset, fields_now[field_name], used))
    return changes


def _field_removed(
    label: str, property_name: str, dataset: DatasetMetadata, identifiers: set[str]
) -> ProposedChange:
    if property_name in identifiers:
        return ProposedChange(
            drift=DriftKind.FIELD_REMOVED,
            dataset=dataset.dataset_name,
            element=f"{label}.{property_name}",
            detail=(
                f"the source no longer has the field {label} identifies on. Re-identifying "
                "an entity decides which nodes are the same node, and that is not a "
                "decision to infer from one discovery run"
            ),
        )
    return ProposedChange(
        drift=DriftKind.FIELD_REMOVED,
        dataset=dataset.dataset_name,
        element=f"{label}.{property_name}",
        detail=f"the source no longer has the field {label}.{property_name} maps",
        mutations=(
            RemoveProperty(
                label=label,
                property_name=property_name,
                rationale="re-analysis found the source field gone",
            ),
        ),
    )


def _field_retyped(
    *,
    label: str,
    property_name: str,
    spec: Mapping[str, Any],
    dataset: DatasetMetadata,
    before: FieldMetadata | None,
    now: FieldMetadata,
    identifiers: set[str],
) -> ProposedChange | None:
    """A declared type that moved, but only where it actually breaks the mapping.

    `varchar` becoming `text` is a source-side detail with no consequence for a
    STRING property, and proposing a change for it would bury the one type
    change that matters under a page of ones that do not.
    """
    if before is not None and before.declared_type.lower() == now.declared_type.lower():
        return None
    declared = str(spec.get("type") or "")
    if is_compatible_source_type(declared, now.declared_type):
        return None
    if str(spec.get("transformation") or "") in _COERCING:
        return None

    element = f"{label}.{property_name}"
    was = "unknown" if before is None else before.declared_type
    if property_name in identifiers:
        return ProposedChange(
            drift=DriftKind.FIELD_TYPE_CHANGED,
            dataset=dataset.dataset_name,
            element=element,
            detail=(
                f"the source type changed from {was!r} to {now.declared_type!r} on a property "
                f"{label} identifies on; retyping an identifier changes what counts as the "
                "same node, so it is left to you"
            ),
        )
    replacement = property_type_for_source_type(now.declared_type)
    if replacement is None:
        return ProposedChange(
            drift=DriftKind.FIELD_TYPE_CHANGED,
            dataset=dataset.dataset_name,
            element=element,
            detail=(
                f"the source type changed from {was!r} to {now.declared_type!r}, which no "
                "property type covers; map it by hand or declare a transformation"
            ),
        )

    source_field = str(spec.get("source_field") or "")
    # Remove-then-add, in one batch, because the command set has no retype: a
    # property's type is set when it is added. Two commands in the order the
    # mutation service applies them is the vocabulary already saying this; a
    # `ChangePropertyType` would be a second way to say it.
    commands: list[MutationCommand] = [
        RemoveProperty(
            label=label,
            property_name=property_name,
            rationale=f"re-analysis: source type is now {now.declared_type!r}",
        ),
        AddProperty(
            label=label,
            property_name=property_name,
            property_type=replacement,
            source_field=source_field,
            rationale=f"re-analysis: retyped from {declared} to {replacement.value}",
        ),
    ]
    transformation = str(spec.get("transformation") or TransformationKind.NONE.value)
    if transformation != TransformationKind.NONE.value:
        # AddProperty resets the transformation, and silently dropping one the
        # analyst configured would change what the sync writes without saying so.
        commands.append(
            ChangeTransformation(
                label=label,
                property_name=property_name,
                transformation=TransformationKind(transformation),
                rationale="re-analysis: preserved across the retype",
            )
        )
    return ProposedChange(
        drift=DriftKind.FIELD_TYPE_CHANGED,
        dataset=dataset.dataset_name,
        element=element,
        detail=(
            f"the source type changed from {was!r} to {now.declared_type!r}, which is not "
            f"compatible with {declared or 'the declared property type'}"
        ),
        mutations=tuple(commands),
    )


def _field_added(
    label: str, dataset: DatasetMetadata, field: FieldMetadata, used: set[str]
) -> ProposedChange:
    command = _add_property(label, dataset, field, used)
    if command is None:
        return ProposedChange(
            drift=DriftKind.FIELD_ADDED,
            dataset=dataset.dataset_name,
            element=f"{label}.{field.field_name}",
            detail=(
                f"the source gained {field.field_name!r} (declared {field.declared_type!r}), "
                "which no legal property name and type can be derived from"
            ),
        )
    return ProposedChange(
        drift=DriftKind.FIELD_ADDED,
        dataset=dataset.dataset_name,
        element=f"{label}.{field.field_name}",
        detail=f"the source gained {field.field_name!r} (declared {field.declared_type!r})",
        mutations=(command,),
    )


def _add_property(
    label: str, dataset: DatasetMetadata, field: FieldMetadata, used: set[str]
) -> AddProperty | None:
    if not _IDENTIFIER.match(field.field_name) or field.field_name in used:
        return None
    source_field = f"{dataset.dataset_name}.{field.field_name}"
    if not _SOURCE_REF.match(source_field):
        return None
    property_type = property_type_for_source_type(field.declared_type)
    if property_type is None:
        return None
    used.add(field.field_name)
    return AddProperty(
        label=label,
        property_name=field.field_name,
        property_type=property_type,
        source_field=source_field,
        rationale=f"re-analysis found {source_field} in the source",
    )


def _field_of(source_field: str, dataset_name: str) -> str:
    """The field name a property maps, with the dataset qualifier taken off.

    Only the dataset's own prefix is stripped. A dotted `address.city` is a path
    into a record, not a qualified name, and treating it as one would compare a
    property against a field that does not exist.
    """
    prefix = f"{dataset_name}."
    return source_field[len(prefix) :] if source_field.startswith(prefix) else source_field


def _label_for(dataset_name: str) -> str | None:
    """A graph label from a collection name, or nothing if none can be made.

    Pascal-cased across the separators source systems use. Returns None rather
    than a mangled fallback: a label is what everything downstream calls this
    thing, and one derived from an unrecognisable name is worse than asking.
    """
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", dataset_name) if part]
    if not parts:
        return None
    candidate = "".join(part[:1].upper() + part[1:] for part in parts)
    return candidate if _IDENTIFIER.match(candidate) else None
