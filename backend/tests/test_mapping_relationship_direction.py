"""Regression tests for graph relationship match and edge direction."""

import pytest
from pydantic import ValidationError

from return_platform.data_platform.mapping import (
    GraphRelationshipMapping,
    RelationshipDirection,
)


def _relationship(
    *,
    direction: RelationshipDirection = RelationshipDirection.SOURCE_TO_TARGET,
) -> GraphRelationshipMapping:
    """Create an Account-reference-to-Customer match relationship."""
    return GraphRelationshipMapping(
        relationship_mapping_id="graph.customer.has_account",
        relationship_type="HAS_ACCOUNT",
        source_node_mapping_id="graph.customer_account",
        target_node_mapping_id="graph.customer",
        source_reference_field="customer_key",
        target_key_field="customer_key",
        direction=direction,
        required=True,
    )


def test_relationship_default_preserves_existing_match_direction() -> None:
    """Preserve the prior source-to-target behavior for existing profiles."""
    relationship = _relationship()

    assert relationship.direction is RelationshipDirection.SOURCE_TO_TARGET
    assert relationship.edge_source_node_mapping_id == "graph.customer_account"
    assert relationship.edge_target_node_mapping_id == "graph.customer"


def test_target_to_source_emits_locked_customer_has_account_edge() -> None:
    """Emit Customer -> CustomerAccount while matching Account.customer_key."""
    relationship = _relationship(
        direction=RelationshipDirection.TARGET_TO_SOURCE,
    )

    assert relationship.source_reference_field == "customer_key"
    assert relationship.target_key_field == "customer_key"
    assert relationship.edge_source_node_mapping_id == "graph.customer"
    assert relationship.edge_target_node_mapping_id == "graph.customer_account"


def test_relationship_direction_accepts_yaml_enum_text() -> None:
    """Accept the code-owned direction token produced by parsed YAML."""
    relationship = GraphRelationshipMapping.model_validate(
        {
            "relationship_mapping_id": "graph.customer.has_account",
            "relationship_type": "HAS_ACCOUNT",
            "source_node_mapping_id": "graph.customer_account",
            "target_node_mapping_id": "graph.customer",
            "source_reference_field": "customer_key",
            "target_key_field": "customer_key",
            "direction": "TARGET_TO_SOURCE",
            "required": True,
        },
    )

    assert relationship.direction is RelationshipDirection.TARGET_TO_SOURCE


@pytest.mark.parametrize(
    "direction",
    ["FORWARD", "REVERSE", "TARGET-TO-SOURCE", 1, True],
)
def test_relationship_direction_rejects_unknown_or_coerced_values(
    direction: object,
) -> None:
    """Reject ambiguous booleans, integers, and unofficial direction tokens."""
    with pytest.raises(ValidationError):
        GraphRelationshipMapping.model_validate(
            {
                "relationship_mapping_id": "graph.customer.has_account",
                "relationship_type": "HAS_ACCOUNT",
                "source_node_mapping_id": "graph.customer_account",
                "target_node_mapping_id": "graph.customer",
                "source_reference_field": "customer_key",
                "target_key_field": "customer_key",
                "direction": direction,
                "required": True,
            },
        )
