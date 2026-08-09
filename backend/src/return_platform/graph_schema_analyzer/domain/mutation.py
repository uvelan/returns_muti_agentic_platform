"""The complete typed mutation command set (design doc section 10.4).

**The model emits these structures; the compiler turns them into graph
operations.** No model-authored executable statement ever reaches a database.

That guarantee is structural, not procedural. Every command below is a closed
pydantic model with `extra="forbid"` and only enumerated, identifier-shaped
fields -- there is deliberately **no** `statement`, `sql`, `cypher`, `script`, or
free-form `expression` field anywhere in this module, because a command that
*could* carry an executable string would make "we don't execute model output" a
policy someone has to remember rather than a thing the type system prevents.
`tests/graph_schema_analyzer/test_mutations.py` asserts that absence.

Identifier fields are pattern-constrained for the same reason: a label of
`Order) DETACH DELETE n //` is not a label, and rejecting it at parse time is
cheaper and more reliable than escaping it at compile time.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MUTATION_KINDS",
    "AddEntity",
    "AddGraphConstraint",
    "AddGraphIndex",
    "AddProperty",
    "AddRelationship",
    "Cardinality",
    "ChangeCardinality",
    "ChangeIdentifier",
    "ChangeOwnershipPolicy",
    "ChangeSourceMapping",
    "ChangeSyncRule",
    "ChangeTransformation",
    "MutationCommand",
    "MutationKind",
    "PropertyType",
    "RemoveEntity",
    "RemoveGraphConstraint",
    "RemoveGraphIndex",
    "RemoveProperty",
    "RemoveRelationship",
    "RenameEntity",
    "TransformationKind",
]

# A graph label / relationship type / property name. Letters, digits and
# underscores only, starting with a letter -- everything a legitimate identifier
# needs and nothing an injection needs.
_IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
Identifier = Annotated[str, Field(pattern=_IDENTIFIER)]

# Source-side names may carry dots (schema.table) and hyphens, so they are
# looser than graph identifiers -- but still no quotes, semicolons, or
# whitespace, which is what a statement would need.
_SOURCE_REF = r"^[A-Za-z][A-Za-z0-9_.\-]{0,127}$"
SourceRef = Annotated[str, Field(pattern=_SOURCE_REF)]


class MutationKind(StrEnum):
    ADD_ENTITY = "AddEntity"
    REMOVE_ENTITY = "RemoveEntity"
    RENAME_ENTITY = "RenameEntity"
    ADD_PROPERTY = "AddProperty"
    REMOVE_PROPERTY = "RemoveProperty"
    CHANGE_IDENTIFIER = "ChangeIdentifier"
    ADD_RELATIONSHIP = "AddRelationship"
    REMOVE_RELATIONSHIP = "RemoveRelationship"
    CHANGE_CARDINALITY = "ChangeCardinality"
    CHANGE_SOURCE_MAPPING = "ChangeSourceMapping"
    CHANGE_TRANSFORMATION = "ChangeTransformation"
    ADD_GRAPH_INDEX = "AddGraphIndex"
    REMOVE_GRAPH_INDEX = "RemoveGraphIndex"
    ADD_GRAPH_CONSTRAINT = "AddGraphConstraint"
    REMOVE_GRAPH_CONSTRAINT = "RemoveGraphConstraint"
    CHANGE_OWNERSHIP_POLICY = "ChangeOwnershipPolicy"
    CHANGE_SYNC_RULE = "ChangeSyncRule"


class PropertyType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    DATETIME = "DATETIME"


class Cardinality(StrEnum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"


class TransformationKind(StrEnum):
    """A closed set of supported transformations.

    Enumerated rather than expressed as a function body precisely because a
    transformation is the most tempting place to accept arbitrary code. A
    transformation the platform does not already implement is a platform change,
    not something a model can introduce at analysis time.
    """

    NONE = "NONE"
    TRIM = "TRIM"
    UPPERCASE = "UPPERCASE"
    LOWERCASE = "LOWERCASE"
    PARSE_DATE = "PARSE_DATE"
    PARSE_NUMBER = "PARSE_NUMBER"


class OwnershipPolicy(StrEnum):
    SOURCE_OWNED = "SOURCE_OWNED"
    GRAPH_OWNED = "GRAPH_OWNED"
    SHARED = "SHARED"


class SyncMode(StrEnum):
    FULL = "FULL"
    INCREMENTAL = "INCREMENTAL"
    ON_DEMAND = "ON_DEMAND"


class _Command(BaseModel):
    """Every command is frozen and closed. `rationale` is the only free text and
    is documentation, never interpreted -- nothing downstream parses it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rationale: str = Field(default="", max_length=500)


class AddEntity(_Command):
    kind: Literal[MutationKind.ADD_ENTITY] = MutationKind.ADD_ENTITY
    label: Identifier
    source_dataset: SourceRef


class RemoveEntity(_Command):
    kind: Literal[MutationKind.REMOVE_ENTITY] = MutationKind.REMOVE_ENTITY
    label: Identifier


class RenameEntity(_Command):
    kind: Literal[MutationKind.RENAME_ENTITY] = MutationKind.RENAME_ENTITY
    label: Identifier
    new_label: Identifier


class AddProperty(_Command):
    kind: Literal[MutationKind.ADD_PROPERTY] = MutationKind.ADD_PROPERTY
    label: Identifier
    property_name: Identifier
    property_type: PropertyType
    source_field: SourceRef


class RemoveProperty(_Command):
    kind: Literal[MutationKind.REMOVE_PROPERTY] = MutationKind.REMOVE_PROPERTY
    label: Identifier
    property_name: Identifier


class ChangeIdentifier(_Command):
    kind: Literal[MutationKind.CHANGE_IDENTIFIER] = MutationKind.CHANGE_IDENTIFIER
    label: Identifier
    identifier_properties: tuple[Identifier, ...] = Field(min_length=1)


class AddRelationship(_Command):
    kind: Literal[MutationKind.ADD_RELATIONSHIP] = MutationKind.ADD_RELATIONSHIP
    relationship_type: Identifier
    from_label: Identifier
    to_label: Identifier
    cardinality: Cardinality


class RemoveRelationship(_Command):
    kind: Literal[MutationKind.REMOVE_RELATIONSHIP] = MutationKind.REMOVE_RELATIONSHIP
    relationship_type: Identifier
    from_label: Identifier
    to_label: Identifier


class ChangeCardinality(_Command):
    kind: Literal[MutationKind.CHANGE_CARDINALITY] = MutationKind.CHANGE_CARDINALITY
    relationship_type: Identifier
    from_label: Identifier
    to_label: Identifier
    cardinality: Cardinality


class ChangeSourceMapping(_Command):
    kind: Literal[MutationKind.CHANGE_SOURCE_MAPPING] = MutationKind.CHANGE_SOURCE_MAPPING
    label: Identifier
    property_name: Identifier
    source_field: SourceRef


class ChangeTransformation(_Command):
    kind: Literal[MutationKind.CHANGE_TRANSFORMATION] = MutationKind.CHANGE_TRANSFORMATION
    label: Identifier
    property_name: Identifier
    transformation: TransformationKind


class AddGraphIndex(_Command):
    """Targets the **graph**, never a source system (design doc C3.3)."""

    kind: Literal[MutationKind.ADD_GRAPH_INDEX] = MutationKind.ADD_GRAPH_INDEX
    label: Identifier
    properties: tuple[Identifier, ...] = Field(min_length=1)


class RemoveGraphIndex(_Command):
    kind: Literal[MutationKind.REMOVE_GRAPH_INDEX] = MutationKind.REMOVE_GRAPH_INDEX
    label: Identifier
    properties: tuple[Identifier, ...] = Field(min_length=1)


class AddGraphConstraint(_Command):
    """Graph-target constraint. Uniqueness/existence only -- a general predicate
    would be an expression, and an expression is executable."""

    kind: Literal[MutationKind.ADD_GRAPH_CONSTRAINT] = MutationKind.ADD_GRAPH_CONSTRAINT
    label: Identifier
    property_name: Identifier
    unique: bool = True
    required: bool = False


class RemoveGraphConstraint(_Command):
    kind: Literal[MutationKind.REMOVE_GRAPH_CONSTRAINT] = MutationKind.REMOVE_GRAPH_CONSTRAINT
    label: Identifier
    property_name: Identifier


class ChangeOwnershipPolicy(_Command):
    kind: Literal[MutationKind.CHANGE_OWNERSHIP_POLICY] = MutationKind.CHANGE_OWNERSHIP_POLICY
    label: Identifier
    ownership: OwnershipPolicy


class ChangeSyncRule(_Command):
    kind: Literal[MutationKind.CHANGE_SYNC_RULE] = MutationKind.CHANGE_SYNC_RULE
    label: Identifier
    sync_mode: SyncMode


MutationCommand = Annotated[
    AddEntity
    | RemoveEntity
    | RenameEntity
    | AddProperty
    | RemoveProperty
    | ChangeIdentifier
    | AddRelationship
    | RemoveRelationship
    | ChangeCardinality
    | ChangeSourceMapping
    | ChangeTransformation
    | AddGraphIndex
    | RemoveGraphIndex
    | AddGraphConstraint
    | RemoveGraphConstraint
    | ChangeOwnershipPolicy
    | ChangeSyncRule,
    Field(discriminator="kind"),
]

# The closed set, for tests and for the API's own completeness checks. Kept
# beside the union so adding a command without registering it is visible.
MUTATION_KINDS: frozenset[MutationKind] = frozenset(MutationKind)
