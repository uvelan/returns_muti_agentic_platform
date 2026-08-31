"""The code-side allowlist of tool input schemas (contracts.md sect. 9).

A released `ToolBindingConfiguration` names a schema by `input_schema_ref`; it
cannot *define* one. This is the same device sect. 8 uses for the formatter
allowlist, applied to tool arguments instead of to rendered fields, and for the
same reason: a schema a release could author would be an argument grammar
shipped by configuration, which is scripting from config under another name.

What a schema declares is deliberately narrow -- entity **names** and **types**,
required and optional. It does not declare where an entity's value comes from.
That is not an omission: sect. 9 says arguments come only from trusted case
facts and graph results, and the way this slice makes that structural is that
`plan_tool_invocation` has no parameter through which raw support text could
reach it (see `tool_router.py`). A schema that named its own sources would be a
second place to get that wrong.

This module imports nothing from the platform. Both the router (which enforces
the schemas) and the resolver configuration (which validates that a released
`input_schema_ref` names one of them) depend on it, and neither depends on the
other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "TOOL_INPUT_SCHEMAS",
    "EntityField",
    "EntityType",
    "ToolInputSchema",
    "UnknownInputSchemaError",
    "known_input_schema_refs",
    "resolve_input_schema",
]


class EntityType(StrEnum):
    """The value shapes a tool argument may take.

    Two, and deliberately not more. Every additional type is another coercion
    rule, and a coercion rule is where a validated argument quietly becomes a
    different value than the fact it came from.
    """

    STRING = "string"
    INTEGER = "integer"


@dataclass(frozen=True, slots=True)
class EntityField:
    """One argument a tool takes: its name, its type, and why it exists."""

    name: str
    entity_type: EntityType
    description: str

    def coerced(self, value: Any) -> Any:
        """`value` as this field's type, or `None` when it is not that type.

        `None` rather than a raised error, because a trusted fact holding the
        wrong shape is a *missing entity* from the router's point of view --
        which refuses -- and not an exception the ladder should propagate as a
        crash. Strings are stripped and an empty one is absent: a blank
        tracking number is not a tracking number.

        `bool` is rejected for `INTEGER` explicitly. `isinstance(True, int)` is
        true in Python, so without this line a boolean fact would validate as a
        quantity and reach a tool as `1`.
        """
        if value is None or isinstance(value, bool):
            return None
        if self.entity_type is EntityType.STRING:
            if not isinstance(value, str):
                return None
            text = value.strip()
            return text or None
        if isinstance(value, int):
            return value
        return None


@dataclass(frozen=True, slots=True)
class ToolInputSchema:
    """The full argument contract for one tool.

    `required` is what the tool cannot run without: a binding whose required
    entities are not all present in the trusted bag is **refused**, never
    invoked with a gap (contracts.md sect. 9, "missing required entities ->
    refuse").
    """

    schema_ref: str
    required: tuple[EntityField, ...]
    optional: tuple[EntityField, ...] = ()

    @property
    def fields(self) -> tuple[EntityField, ...]:
        return self.required + self.optional

    @property
    def required_entity_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.required)


class UnknownInputSchemaError(ValueError):
    """A released binding named a schema this build does not implement.

    Raised at release validation and again at routing. Two checks rather than
    one because they answer different questions: the first refuses to activate
    a release that cannot work, and the second refuses to *route* on a build
    where an older release's schema has since been removed.
    """

    def __init__(self, schema_ref: str) -> None:
        super().__init__(
            f"input_schema_ref {schema_ref!r} is not one of this build's tool input schemas: "
            f"{', '.join(sorted(TOOL_INPUT_SCHEMAS))}"
        )
        self.schema_ref = schema_ref


#: The graph read the resolver's tool rung performs: given a return record the
#: case actually holds, fetch what the knowledge graph knows about it. The
#: reference is the entity because it is the record's identity (T0 investigation
#: 1: `returnReference` *is* the support-issued RMA), and the case id is
#: required beside it so a binding can never be routed at a record that belongs
#: to some other case.
_RETURN_RECORD_LOOKUP = ToolInputSchema(
    schema_ref="graph.return_record_lookup.v1",
    required=(
        EntityField(
            name="caseId",
            entity_type=EntityType.STRING,
            description="The case the question was asked about. Never client-supplied.",
        ),
        EntityField(
            name="returnReference",
            entity_type=EntityType.STRING,
            description="The RMA of a record this case holds.",
        ),
    ),
    optional=(
        EntityField(
            name="orderReference",
            entity_type=EntityType.STRING,
            description="The order the record belongs to, where the case facts carry it.",
        ),
    ),
)

#: The shipment-status read: what the graph knows about a parcel in flight.
#: Separate from the record lookup because its required entity is different --
#: a tracking reference exists only once a carrier has one, and a binding that
#: accepted "either" would run with neither.
_SHIPMENT_STATUS_LOOKUP = ToolInputSchema(
    schema_ref="graph.shipment_status.v1",
    required=(
        EntityField(
            name="caseId",
            entity_type=EntityType.STRING,
            description="The case the question was asked about. Never client-supplied.",
        ),
        EntityField(
            name="trackingReference",
            entity_type=EntityType.STRING,
            description="The carrier tracking number recorded on the case.",
        ),
    ),
    optional=(
        EntityField(
            name="carrier",
            entity_type=EntityType.STRING,
            description="The carrier, where a fact records one.",
        ),
    ),
)

TOOL_INPUT_SCHEMAS: Final[Mapping[str, ToolInputSchema]] = {
    schema.schema_ref: schema for schema in (_RETURN_RECORD_LOOKUP, _SHIPMENT_STATUS_LOOKUP)
}


def known_input_schema_refs() -> frozenset[str]:
    return frozenset(TOOL_INPUT_SCHEMAS)


def resolve_input_schema(schema_ref: str) -> ToolInputSchema:
    try:
        return TOOL_INPUT_SCHEMAS[schema_ref]
    except KeyError as error:
        raise UnknownInputSchemaError(schema_ref) from error
