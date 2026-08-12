"""Apply typed mutations to a draft, producing a new revision.

Pure: takes a shape and commands, returns the next shape. No I/O, so the whole
command set is exercisable without a database, and applying the same revision
twice is trivially reproducible.

Every command is applied through an explicit handler. There is deliberately no
generic "set attribute from command field" reflection: reflection over a
model-authored payload is exactly how an unintended field becomes writable, and
the handler table is the thing a reviewer can read to see the complete set of
effects a model can have on a schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from return_platform.graph_schema_analyzer.domain.errors import AnalyzerError
from return_platform.graph_schema_analyzer.domain.mutation import (
    AddEntity,
    AddGraphConstraint,
    AddGraphIndex,
    AddProperty,
    AddRelationship,
    ChangeCardinality,
    ChangeIdentifier,
    ChangeOwnershipPolicy,
    ChangeSourceMapping,
    ChangeSyncRule,
    ChangeTransformation,
    MutationCommand,
    RemoveEntity,
    RemoveGraphConstraint,
    RemoveGraphIndex,
    RemoveProperty,
    RemoveRelationship,
    RenameEntity,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape

__all__ = ["MutationRejected", "apply_mutations"]


class MutationRejected(AnalyzerError):
    """A command that cannot be applied to this shape -- e.g. adding a property
    to an entity that does not exist. Rejected as a batch, never partially
    applied: half a revision is a shape nobody designed."""


def apply_mutations(
    shape: GraphSchemaShape, commands: Sequence[MutationCommand]
) -> GraphSchemaShape:
    """Apply an ordered batch atomically.

    Order matters within a batch (AddEntity then AddProperty on it is valid), so
    commands are applied sequentially against the accumulating shape rather than
    all against the original. If any command fails the whole batch raises and the
    caller keeps the original shape untouched.
    """
    # deepcopy, not dict(): an entity's `properties` is itself a dict, so a
    # shallow copy would leave the working set aliasing the caller's shape and
    # AddProperty would mutate the "immutable" input in place. Caught by a diff
    # test that saw no change between before and after -- because both had been
    # changed.
    entities: dict[str, dict[str, Any]] = deepcopy(
        {label: dict(definition) for label, definition in shape.entities.items()}
    )
    relationships: list[dict[str, Any]] = deepcopy([dict(item) for item in shape.relationships])
    indexes: list[dict[str, Any]] = deepcopy([dict(item) for item in shape.graph_indexes])
    constraints: list[dict[str, Any]] = deepcopy([dict(item) for item in shape.graph_constraints])

    for command in commands:
        _apply_one(command, entities, relationships, indexes, constraints)

    return GraphSchemaShape(
        entities={label: dict(definition) for label, definition in entities.items()},
        relationships=tuple(dict(item) for item in relationships),
        graph_indexes=tuple(dict(item) for item in indexes),
        graph_constraints=tuple(dict(item) for item in constraints),
    )


def _require_entity(entities: Mapping[str, Any], label: str) -> dict[str, Any]:
    if label not in entities:
        raise MutationRejected(f"entity {label!r} does not exist in this draft.")
    entity: dict[str, Any] = entities[label]
    return entity


def _apply_one(  # noqa: C901 - one branch per command is the readable shape here
    command: MutationCommand,
    entities: dict[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> None:
    match command:
        case AddEntity():
            if command.label in entities:
                raise MutationRejected(f"entity {command.label!r} already exists.")
            entities[command.label] = {
                "label": command.label,
                "source_dataset": command.source_dataset,
                "properties": {},
                "identifier_properties": [],
                "ownership": "SOURCE_OWNED",
                "sync_mode": "ON_DEMAND",
            }

        case RemoveEntity():
            _require_entity(entities, command.label)
            # Removing an entity must take its relationships with it, or the
            # shape is left referencing a label that no longer exists -- which
            # RELATIONSHIPS_RESOLVABLE would then flag as a validation error the
            # analyst never caused.
            del entities[command.label]
            relationships[:] = [
                item
                for item in relationships
                if command.label not in (item.get("from_label"), item.get("to_label"))
            ]
            indexes[:] = [item for item in indexes if item.get("label") != command.label]
            constraints[:] = [item for item in constraints if item.get("label") != command.label]

        case RenameEntity():
            entity = _require_entity(entities, command.label)
            if command.new_label in entities:
                raise MutationRejected(f"entity {command.new_label!r} already exists.")
            entity["label"] = command.new_label
            entities[command.new_label] = entity
            del entities[command.label]
            for item in relationships:
                if item.get("from_label") == command.label:
                    item["from_label"] = command.new_label
                if item.get("to_label") == command.label:
                    item["to_label"] = command.new_label
            for item in (*indexes, *constraints):
                if item.get("label") == command.label:
                    item["label"] = command.new_label

        case AddProperty():
            entity = _require_entity(entities, command.label)
            properties: dict[str, Any] = entity["properties"]
            if command.property_name in properties:
                raise MutationRejected(
                    f"property {command.property_name!r} already exists on {command.label!r}."
                )
            properties[command.property_name] = {
                "type": command.property_type.value,
                "source_field": command.source_field,
                "transformation": "NONE",
            }

        case RemoveProperty():
            entity = _require_entity(entities, command.label)
            if command.property_name not in entity["properties"]:
                raise MutationRejected(
                    f"property {command.property_name!r} does not exist on {command.label!r}."
                )
            if command.property_name in entity.get("identifier_properties", []):
                raise MutationRejected(
                    f"property {command.property_name!r} is an identifier of {command.label!r}; "
                    "change the identifier before removing it."
                )
            del entity["properties"][command.property_name]
            constraints[:] = [
                item
                for item in constraints
                if not (
                    item.get("label") == command.label
                    and item.get("property_name") == command.property_name
                )
            ]

        case ChangeIdentifier():
            entity = _require_entity(entities, command.label)
            missing = [
                name for name in command.identifier_properties if name not in entity["properties"]
            ]
            if missing:
                raise MutationRejected(
                    f"cannot identify {command.label!r} by unknown propert(ies): "
                    f"{', '.join(missing)}."
                )
            entity["identifier_properties"] = list(command.identifier_properties)

        case AddRelationship():
            _require_entity(entities, command.from_label)
            _require_entity(entities, command.to_label)
            if _find_relationship(relationships, command) is not None:
                raise MutationRejected(
                    f"relationship {command.relationship_type!r} between "
                    f"{command.from_label!r} and {command.to_label!r} already exists."
                )
            relationships.append(
                {
                    "relationship_type": command.relationship_type,
                    "from_label": command.from_label,
                    "to_label": command.to_label,
                    "cardinality": command.cardinality.value,
                    # Omitted when unstated rather than written as an empty
                    # tuple: absent means "use the identifier", and an empty
                    # join is a mistake the compiler must be able to reject.
                    **(
                        {"from_properties": list(command.from_properties)}
                        if command.from_properties
                        else {}
                    ),
                    **(
                        {"to_properties": list(command.to_properties)}
                        if command.to_properties
                        else {}
                    ),
                }
            )

        case RemoveRelationship():
            existing = _find_relationship(relationships, command)
            if existing is None:
                raise MutationRejected(
                    f"relationship {command.relationship_type!r} between "
                    f"{command.from_label!r} and {command.to_label!r} does not exist."
                )
            relationships.remove(existing)

        case ChangeCardinality():
            existing = _find_relationship(relationships, command)
            if existing is None:
                raise MutationRejected(
                    f"relationship {command.relationship_type!r} between "
                    f"{command.from_label!r} and {command.to_label!r} does not exist."
                )
            existing["cardinality"] = command.cardinality.value

        case ChangeSourceMapping():
            entity = _require_entity(entities, command.label)
            if command.property_name not in entity["properties"]:
                raise MutationRejected(
                    f"property {command.property_name!r} does not exist on {command.label!r}."
                )
            entity["properties"][command.property_name]["source_field"] = command.source_field

        case ChangeTransformation():
            entity = _require_entity(entities, command.label)
            if command.property_name not in entity["properties"]:
                raise MutationRejected(
                    f"property {command.property_name!r} does not exist on {command.label!r}."
                )
            entity["properties"][command.property_name]["transformation"] = (
                command.transformation.value
            )

        case AddGraphIndex():
            entity = _require_entity(entities, command.label)
            _require_properties(entity, command.label, command.properties)
            candidate = {"label": command.label, "properties": list(command.properties)}
            if candidate in indexes:
                raise MutationRejected(f"that graph index on {command.label!r} already exists.")
            indexes.append(candidate)

        case RemoveGraphIndex():
            candidate = {"label": command.label, "properties": list(command.properties)}
            if candidate not in indexes:
                raise MutationRejected(f"that graph index on {command.label!r} does not exist.")
            indexes.remove(candidate)

        case AddGraphConstraint():
            entity = _require_entity(entities, command.label)
            _require_properties(entity, command.label, (command.property_name,))
            constraints.append(
                {
                    "label": command.label,
                    "property_name": command.property_name,
                    "unique": command.unique,
                    "required": command.required,
                }
            )

        case RemoveGraphConstraint():
            matching = [
                item
                for item in constraints
                if item.get("label") == command.label
                and item.get("property_name") == command.property_name
            ]
            if not matching:
                raise MutationRejected(
                    f"no graph constraint on {command.label}.{command.property_name}."
                )
            for item in matching:
                constraints.remove(item)

        case ChangeOwnershipPolicy():
            entity = _require_entity(entities, command.label)
            entity["ownership"] = command.ownership.value

        case ChangeSyncRule():
            entity = _require_entity(entities, command.label)
            entity["sync_mode"] = command.sync_mode.value


def _require_properties(entity: Mapping[str, Any], label: str, names: Sequence[str]) -> None:
    missing = [name for name in names if name not in entity["properties"]]
    if missing:
        raise MutationRejected(f"{label!r} has no propert(ies) {', '.join(missing)} to target.")


def _find_relationship(
    relationships: Sequence[dict[str, Any]], command: Any
) -> dict[str, Any] | None:
    for item in relationships:
        if (
            item.get("relationship_type") == command.relationship_type
            and item.get("from_label") == command.from_label
            and item.get("to_label") == command.to_label
        ):
            return item
    return None
