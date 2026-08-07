from __future__ import annotations

from return_platform.dynamic_knowledge.graph.constraints import (
    required_node_constraints,
    required_relationship_indexes,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


def test_required_node_constraints_use_graph_properties_not_field_ids(
    active_schema: ActiveSchema,
) -> None:
    constraints = {c.label: c.graph_properties for c in required_node_constraints(active_schema)}
    assert constraints["ConfiguredAlpha"] == ("graph_generation_id", "configured_id")
    assert constraints["ConfiguredBeta"] == ("graph_generation_id", "related_id")


def test_required_relationship_indexes_cover_both_endpoints(active_schema: ActiveSchema) -> None:
    indexes = required_relationship_indexes(active_schema)
    labels = {index.label for index in indexes}
    assert labels == {"ConfiguredAlpha", "ConfiguredBeta"}
    by_label = {index.label: index.graph_properties for index in indexes}
    assert by_label["ConfiguredAlpha"] == ("graph_generation_id", "configured_id")
    assert by_label["ConfiguredBeta"] == ("graph_generation_id", "configured_parent_id")
