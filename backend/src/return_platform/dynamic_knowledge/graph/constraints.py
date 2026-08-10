"""Derive the composite Neo4j constraints/indexes an active schema requires.

Purely derived from NodeProjection.key_fields and RelationshipProjection's
match-field tuples -- nothing here is hand-configured, so a constraint can
never silently drift from the schema that defines identity. Provisioning
these against a real Neo4j instance is the concrete writer's job (a later
stage of the source-to-graph alignment plan); this module only computes what
is required.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.schema import ActiveSchema


class RequiredNodeConstraint(BaseModel):
    """A composite uniqueness constraint on one node label's full physical key.

    Single-field indexes are not sufficient for composite-equality lookups,
    so every node gets one constraint over graph_generation_id plus the
    ordered tuple of graph properties backing its key_fields -- never one
    constraint per field. graph_generation_id leads the tuple because it is
    the physical key's outermost scope: two generations may legitimately
    share the same logical key (see the plan's "logical vs. physical keys"),
    so the constraint must cover both or it would wrongly unique-constrain
    across generations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    graph_properties: tuple[str, ...]


class RequiredRelationshipIndex(BaseModel):
    """A composite index over graph_generation_id plus one relationship's match
    fields, on one endpoint label -- matching how Stage B reconciliation and
    the writer both always query (generation-scoped first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str
    label: str
    graph_properties: tuple[str, ...]


def required_node_constraints(schema: ActiveSchema) -> tuple[RequiredNodeConstraint, ...]:
    constraints: list[RequiredNodeConstraint] = []
    for node in schema.graph.nodes.values():
        entity = schema.entities[node.entity_id]
        graph_properties = (
            "graph_generation_id",
            *(entity.fields[field_id].graph_property for field_id in node.key_fields),
        )
        constraints.append(
            RequiredNodeConstraint(label=node.label, graph_properties=graph_properties)
        )
    return tuple(constraints)


def required_relationship_indexes(schema: ActiveSchema) -> tuple[RequiredRelationshipIndex, ...]:
    indexes: list[RequiredRelationshipIndex] = []
    for relationship in schema.graph.relationships.values():
        source_entity = schema.entities[relationship.source_entity_id]
        source_node = schema.entity_node(relationship.source_entity_id)
        target_entity = schema.entities[relationship.target_entity_id]
        target_node = schema.entity_node(relationship.target_entity_id)
        indexes.append(
            RequiredRelationshipIndex(
                relationship_id=relationship.relationship_id,
                label=source_node.label,
                graph_properties=(
                    "graph_generation_id",
                    *(
                        source_entity.fields[field_id].graph_property
                        for field_id in relationship.source_match_fields
                    ),
                ),
            )
        )
        indexes.append(
            RequiredRelationshipIndex(
                relationship_id=relationship.relationship_id,
                label=target_node.label,
                graph_properties=(
                    "graph_generation_id",
                    *(
                        target_entity.fields[field_id].graph_property
                        for field_id in relationship.target_match_fields
                    ),
                ),
            )
        )
    return tuple(indexes)
