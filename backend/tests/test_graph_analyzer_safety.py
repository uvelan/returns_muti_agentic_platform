from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.graph_analyzer.models import AgentRequest
from return_platform.graph_analyzer.safety import (
    SourceMutationRejected,
    SourceQueryLanguage,
    assert_mongodb_read_operation,
    assert_read_only_query,
    assert_system_graph_target,
)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO customer VALUES (1)",
        "UPDATE customer SET name = 'x'",
        "DELETE FROM customer",
        (
            "MERGE customer AS target USING incoming AS source ON 1=1 "
            "WHEN MATCHED THEN UPDATE SET target.id=1;"
        ),
    ],
)
def test_relational_source_dml_is_rejected(statement: str) -> None:
    with pytest.raises(SourceMutationRejected, match="read-only"):
        assert_read_only_query(statement, SourceQueryLanguage.SQL)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE customer(id int)",
        "ALTER TABLE customer ADD name varchar(50)",
        "DROP TABLE customer",
        "TRUNCATE TABLE customer",
        "CREATE INDEX ix_customer ON customer(id)",
    ],
)
def test_relational_source_ddl_is_rejected(statement: str) -> None:
    with pytest.raises(SourceMutationRejected, match="read-only"):
        assert_read_only_query(statement, SourceQueryLanguage.SQL)


@pytest.mark.parametrize(
    "operation",
    ["insert_one", "update_many", "delete_one", "replace_one", "create_index", "drop_index"],
)
def test_mongodb_source_mutation_is_rejected(operation: str) -> None:
    with pytest.raises(SourceMutationRejected, match="read-only"):
        assert_mongodb_read_operation(operation)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE (n:Customer)",
        "MATCH (n) SET n.active = true",
        "MATCH (n) DETACH DELETE n",
        "CREATE INDEX customer_id FOR (n:Customer) ON (n.id)",
        "DROP CONSTRAINT customer_id",
    ],
)
def test_external_graph_mutation_is_rejected(statement: str) -> None:
    with pytest.raises(SourceMutationRejected, match="read-only"):
        assert_read_only_query(statement, SourceQueryLanguage.CYPHER)


def test_bounded_read_queries_are_allowed() -> None:
    assert assert_read_only_query("SELECT TOP 25 * FROM customer", SourceQueryLanguage.SQL)
    assert assert_read_only_query("MATCH (n) RETURN n LIMIT 25", SourceQueryLanguage.CYPHER)
    assert assert_mongodb_read_operation("find") == "find"


def test_agent_cannot_return_source_write_action() -> None:
    with pytest.raises(ValidationError, match="cannot propose or execute source mutations"):
        AgentRequest.model_validate(
            {
                "message": "Create index on the PostgreSQL source customer table",
                "context": {"workspace": "SCHEMA"},
            }
        )


def test_graph_write_target_must_be_system_graph() -> None:
    assert_system_graph_target("SYSTEM_GRAPH")
    with pytest.raises(SourceMutationRejected, match="only the system graph"):
        assert_system_graph_target("EXTERNAL_GRAPH_SOURCE")


def test_source_configuration_removal_has_no_database_operation() -> None:
    # The safety boundary exposes no DROP DATABASE/COLLECTION operation at all.
    with pytest.raises(SourceMutationRejected):
        assert_read_only_query("DROP DATABASE source", SourceQueryLanguage.SQL)
