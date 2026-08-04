from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
    TraversalStep,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, validate_graph_identifier


def test_read_query_parameterizes_values(active_schema: ActiveSchema) -> None:
    plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        filters=(
            QueryCondition(
                entity_id="entity_a",
                field_id="name",
                operator="EXACT",
                value="Robert'); MATCH (n) DETACH DELETE n //",
            ),
        ),
        limit=10,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    assert "Robert" not in compiled.cypher
    assert compiled.parameters["p0"].startswith("Robert")
    assert "DETACH DELETE" not in compiled.cypher
    assert compiled.read_only is True


def test_traversal_uses_only_configured_relationship(active_schema: ActiveSchema) -> None:
    plan = LogicalQueryPlan(
        operation=QueryOperation.TRAVERSE,
        start_entity_id="entity_a",
        fields=("id",),
        traversal=(
            TraversalStep(
                relationship_id="a_to_b", direction="OUTBOUND", target_entity_id="entity_b"
            ),
        ),
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    assert "CONFIGURED_LINK" in compiled.cypher
    assert "ConfiguredBeta" in compiled.cypher


def test_unsafe_identifier_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_graph_identifier("Node`) DETACH DELETE n //")


def test_projection_upsert_is_configuration_driven(active_schema: ActiveSchema) -> None:
    compiled = CypherCompiler().compile_node_upsert(active_schema, "entity_a")
    assert "ConfiguredAlpha" in compiled.cypher
    assert "configured_id" in compiled.cypher
    assert compiled.read_only is False
