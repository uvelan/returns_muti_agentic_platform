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


def test_contains_and_prefix_match_case_insensitively(active_schema: ActiveSchema) -> None:
    plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        filters=(
            QueryCondition(
                entity_id="entity_a", field_id="name", operator="CONTAINS", value="maya"
            ),
        ),
        limit=10,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    assert "toLower(n0.`configured_name`) CONTAINS toLower($p0)" in compiled.cypher
    assert compiled.parameters["p0"] == "maya"

    prefix_plan = LogicalQueryPlan(
        operation=QueryOperation.SEARCH,
        start_entity_id="entity_a",
        fields=("id", "name"),
        filters=(
            QueryCondition(entity_id="entity_a", field_id="name", operator="PREFIX", value="Ma"),
        ),
        limit=10,
    )
    prefix_compiled = CypherCompiler().compile_read(active_schema, prefix_plan)
    assert "toLower(n0.`configured_name`) STARTS WITH toLower($p0)" in prefix_compiled.cypher


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


def test_group_by_returns_grouped_counts(active_schema: ActiveSchema) -> None:
    plan = LogicalQueryPlan(
        operation=QueryOperation.GROUP_BY,
        start_entity_id="entity_a",
        group_by_field_ids=("name",),
        aggregation_field_id="count_value",
        limit=10,
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    assert "AS `name`" in compiled.cypher
    assert "count(DISTINCT" in compiled.cypher
    assert "AS count" in compiled.cypher
    assert "ORDER BY count DESC" in compiled.cypher


def test_group_by_without_group_fields_is_rejected() -> None:
    with pytest.raises(ValueError, match="GROUP_BY requires"):
        LogicalQueryPlan(
            operation=QueryOperation.GROUP_BY,
            start_entity_id="entity_a",
        )


def test_exists_returns_boolean_value(active_schema: ActiveSchema) -> None:
    plan = LogicalQueryPlan(
        operation=QueryOperation.EXISTS,
        start_entity_id="entity_a",
        filters=(
            QueryCondition(entity_id="entity_a", field_id="name", operator="EXACT", value="x"),
        ),
    )
    compiled = CypherCompiler().compile_read(active_schema, plan)
    assert "count(n0) > 0 AS value" in compiled.cypher


@pytest.mark.parametrize("operation", [QueryOperation.DATE_RANGE, QueryOperation.SEMANTIC_SEARCH])
def test_unimplemented_operations_fail_loudly_instead_of_silently(
    active_schema: ActiveSchema, operation: QueryOperation
) -> None:
    kwargs = (
        {"semantic_query": "blue faucet"} if operation is QueryOperation.SEMANTIC_SEARCH else {}
    )
    plan = LogicalQueryPlan(operation=operation, start_entity_id="entity_a", **kwargs)
    with pytest.raises(Exception, match="not implemented"):
        CypherCompiler().compile_read(active_schema, plan)
