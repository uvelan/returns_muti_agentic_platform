"""Adversarial tests for fixed Customer Neo4j command generation."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from return_platform.canonical import (
    GraphProjectionEvidence,
    GraphProjectionStatus,
)
from return_platform.data_platform.graph.commands import (
    CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
    CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
    CUSTOMER_CONSTRAINT_CYPHER,
    CUSTOMER_NODE_UPSERT_CYPHER,
    HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
    CustomerNeo4jCommandBatch,
    Neo4jCommandBuildError,
    Neo4jCommandBuildErrorCode,
    Neo4jConstraintCommand,
    Neo4jNodeUpsertCommand,
    build_customer_neo4j_commands,
)
from return_platform.data_platform.mapping.projection import (
    CustomerGraphProjectionMaterialization,
    GraphNodeUpsertParameters,
    GraphParameterMap,
    GraphRelationshipUpsertParameters,
)

_SYNC_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
_GRAPH_SYNCED_AT = datetime(2026, 7, 22, 5, 30, tzinfo=UTC)
_DIGEST = "a" * 64
_CUSTOMER_KEY = "CUSTOMER_CDM:P100"
_ACCOUNT_KEYS = ("CUSTOMER_CDM:202*C001", "CUSTOMER_CDM:202*C002")


class _MutableBatch(Protocol):
    builder_version: str


def _customer_properties(*, party_id: str = "P100") -> GraphParameterMap:
    return GraphParameterMap.from_mapping(
        {
            "canonical_key": _CUSTOMER_KEY,
            "configuration_digest": _DIGEST,
            "customer_key": _CUSTOMER_KEY,
            "graph_synced_at": _GRAPH_SYNCED_AT,
            "identity_quality": "VERIFIED",
            "mapping_version": "1.0",
            "party_id": party_id,
            "source_asset": "customerOutboundCDM",
            "source_database": "eventMessages",
            "source_record_id": "P100",
            "source_system": "CUSTOMER_CDM",
            "source_updated_at": datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _account_properties(account_key: str) -> GraphParameterMap:
    return GraphParameterMap.from_mapping(
        {
            "account_key": account_key,
            "account_number": account_key.removeprefix("CUSTOMER_CDM:"),
            "canonical_key": account_key,
            "configuration_digest": _DIGEST,
            "customer_key": _CUSTOMER_KEY,
            "graph_synced_at": _GRAPH_SYNCED_AT,
            "identity_quality": "VERIFIED",
            "mapping_version": "1.0",
            "source_asset": "customerOutboundCDM",
            "source_database": "eventMessages",
            "source_record_id": account_key.removeprefix("CUSTOMER_CDM:"),
            "source_system": "CUSTOMER_CDM",
            "source_updated_at": datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _customer_node() -> GraphNodeUpsertParameters:
    return GraphNodeUpsertParameters(
        node_mapping_id="graph.customer.v1",
        label="Customer",
        key_property="customer_key",
        key_value=_CUSTOMER_KEY,
        properties=_customer_properties(),
    )


def _account_node(account_key: str) -> GraphNodeUpsertParameters:
    return GraphNodeUpsertParameters(
        node_mapping_id="graph.customer_account.v1",
        label="CustomerAccount",
        key_property="account_key",
        key_value=account_key,
        properties=_account_properties(account_key),
    )


def _relationship(account_key: str) -> GraphRelationshipUpsertParameters:
    return GraphRelationshipUpsertParameters(
        relationship_mapping_id="graph.customer.has_account.v1",
        relationship_type="HAS_ACCOUNT",
        source_node_mapping_id="graph.customer.v1",
        source_label="Customer",
        source_key_property="customer_key",
        source_key_value=_CUSTOMER_KEY,
        source_match=GraphParameterMap.from_mapping({"customer_key": _CUSTOMER_KEY}),
        target_node_mapping_id="graph.customer_account.v1",
        target_label="CustomerAccount",
        target_key_property="account_key",
        target_key_value=account_key,
        target_match=GraphParameterMap.from_mapping({"account_key": account_key}),
    )


def _evidence() -> tuple[GraphProjectionEvidence, ...]:
    return (
        GraphProjectionEvidence(
            evidence_id=UUID("22222222-2222-4222-8222-222222222222"),
            sync_run_id=_SYNC_RUN_ID,
            source_asset="customerOutboundCDM",
            source_record_id="P100",
            canonical_entity_type="Customer",
            canonical_entity_key=_CUSTOMER_KEY,
            graph_label="Customer",
            graph_key=_CUSTOMER_KEY,
            mapping_version="1.0",
            projection_status=GraphProjectionStatus.PROJECTED,
            projected_at=_GRAPH_SYNCED_AT,
        ),
    )


def _materialization() -> CustomerGraphProjectionMaterialization:
    return CustomerGraphProjectionMaterialization(
        materializer_version="1.0",
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        graph_synced_at=_GRAPH_SYNCED_AT,
        customer_node=_customer_node(),
        customer_account_nodes=tuple(_account_node(key) for key in _ACCOUNT_KEYS),
        has_account_relationships=tuple(_relationship(key) for key in _ACCOUNT_KEYS),
        projection_evidence=_evidence(),
    )


def test_builds_fixed_order_idempotent_customer_command_batch() -> None:
    """Emit schema, node, and edge commands in deterministic execution order."""
    batch = build_customer_neo4j_commands(_materialization())

    assert tuple(command.cypher for command in batch.constraint_commands) == (
        CUSTOMER_CONSTRAINT_CYPHER,
        CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
    )
    assert tuple(command.cypher for command in batch.node_commands) == (
        CUSTOMER_NODE_UPSERT_CYPHER,
        CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
        CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
    )
    assert tuple(command.cypher for command in batch.relationship_commands) == (
        HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
        HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
    )
    assert batch.command_count == 7


def test_constraint_commands_use_current_community_compatible_syntax() -> None:
    """Use IF NOT EXISTS uniqueness constraints rather than Enterprise node keys."""
    batch = build_customer_neo4j_commands(_materialization())

    for command in batch.constraint_commands:
        assert "CREATE CONSTRAINT" in command.cypher
        assert "IF NOT EXISTS" in command.cypher
        assert " IS UNIQUE" in command.cypher
        assert "NODE KEY" not in command.cypher
        assert command.to_driver_parameters() == {}


def test_node_commands_bind_values_only_through_parameters() -> None:
    """Keep canonical keys and properties out of the fixed Cypher text."""
    batch = build_customer_neo4j_commands(_materialization())

    for command in batch.node_commands:
        assert "$key" in command.cypher
        assert "$properties" in command.cypher
        assert command.parameters.key not in command.cypher
        parameters = command.parameters.to_driver_parameters()
        assert parameters["key"] == command.parameters.key
        assert isinstance(parameters["properties"], dict)


def test_relationship_commands_preserve_customer_to_account_direction() -> None:
    """Match constrained endpoints and emit Customer-[:HAS_ACCOUNT]->Account."""
    batch = build_customer_neo4j_commands(_materialization())

    for command, account_key in zip(
        batch.relationship_commands,
        _ACCOUNT_KEYS,
        strict=True,
    ):
        assert command.source_label == "Customer"
        assert command.target_label == "CustomerAccount"
        assert command.relationship_type == "HAS_ACCOUNT"
        assert command.parameters.source_key == _CUSTOMER_KEY
        assert command.parameters.target_key == account_key
        assert "$source_key" in command.cypher
        assert "$target_key" in command.cypher
        assert _CUSTOMER_KEY not in command.cypher
        assert account_key not in command.cypher


def test_driver_parameter_dictionaries_are_fresh_detached_copies() -> None:
    """Prevent a future writer from mutating stored command evidence."""
    command = build_customer_neo4j_commands(_materialization()).node_commands[0]
    first = command.parameters.to_driver_parameters()
    second = command.parameters.to_driver_parameters()

    first["key"] = "changed"
    properties = cast("dict[str, object]", first["properties"])
    properties["party_id"] = "changed"

    assert second["key"] == _CUSTOMER_KEY
    second_properties = cast("dict[str, object]", second["properties"])
    assert second_properties["party_id"] == "P100"
    assert command.parameters.key == _CUSTOMER_KEY


def test_repeated_builds_are_identical_and_digest_bound() -> None:
    """Produce stable commands, UUIDs, and digest for identical inputs."""
    first = build_customer_neo4j_commands(_materialization())
    second = build_customer_neo4j_commands(_materialization())

    assert first == second
    assert first.command_batch_digest == second.command_batch_digest
    assert tuple(item.command_id for item in first.node_commands) == tuple(
        item.command_id for item in second.node_commands
    )


def test_digest_changes_when_parameter_evidence_changes() -> None:
    """Bind the command digest to exact parameter content."""
    original = _materialization()
    assert original.customer_node is not None
    changed_customer = original.customer_node.model_copy(
        update={"properties": _customer_properties(party_id="P101")}
    )
    changed = original.model_copy(update={"customer_node": changed_customer})

    assert (
        build_customer_neo4j_commands(original).command_batch_digest
        != build_customer_neo4j_commands(changed).command_batch_digest
    )


def test_projection_evidence_is_preserved_without_claiming_execution() -> None:
    """Carry materialization evidence forward without creating write evidence."""
    materialization = _materialization()
    batch = build_customer_neo4j_commands(materialization)

    assert batch.projection_evidence == materialization.projection_evidence
    assert batch.projection_evidence[0].projection_status is (GraphProjectionStatus.PROJECTED)


def test_empty_rejected_materialization_still_builds_schema_commands_only() -> None:
    """Allow schema preparation without fabricating data commands."""
    empty = _materialization().model_copy(
        update={
            "customer_node": None,
            "customer_account_nodes": (),
            "has_account_relationships": (),
        }
    )

    batch = build_customer_neo4j_commands(empty)

    assert len(batch.constraint_commands) == 2
    assert batch.node_commands == ()
    assert batch.relationship_commands == ()


def test_rejects_accounts_without_customer_node() -> None:
    """Do not emit orphan CustomerAccount commands."""
    invalid = _materialization().model_copy(update={"customer_node": None})

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is (Neo4jCommandBuildErrorCode.MATERIALIZATION_UNSUPPORTED)


def test_rejects_corrupted_customer_node_tokens() -> None:
    """Reject label and mapping-token drift before Cypher generation."""
    current = _customer_node()
    corrupted = current.model_copy(update={"label": "CustomerAccount"})
    invalid = _materialization().model_copy(update={"customer_node": corrupted})

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is Neo4jCommandBuildErrorCode.NODE_PLAN_INVALID


def test_rejects_reversed_relationship_tokens() -> None:
    """Reject a corrupted Account-to-Customer edge contract."""
    current = _relationship(_ACCOUNT_KEYS[0])
    reversed_edge = current.model_copy(
        update={
            "source_node_mapping_id": "graph.customer_account.v1",
            "source_label": "CustomerAccount",
            "source_key_property": "account_key",
            "source_key_value": _ACCOUNT_KEYS[0],
            "source_match": GraphParameterMap.from_mapping({"account_key": _ACCOUNT_KEYS[0]}),
            "target_node_mapping_id": "graph.customer.v1",
            "target_label": "Customer",
            "target_key_property": "customer_key",
            "target_key_value": _CUSTOMER_KEY,
            "target_match": GraphParameterMap.from_mapping({"customer_key": _CUSTOMER_KEY}),
        }
    )
    invalid = _materialization().model_copy(update={"has_account_relationships": (reversed_edge,)})

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is (Neo4jCommandBuildErrorCode.RELATIONSHIP_PLAN_INVALID)


def test_rejects_duplicate_account_node_keys() -> None:
    """Do not issue multiple node MERGEs for one key in one batch."""
    duplicate = _account_node(_ACCOUNT_KEYS[0])
    invalid = _materialization().model_copy(
        update={"customer_account_nodes": (duplicate, duplicate)}
    )

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is Neo4jCommandBuildErrorCode.DUPLICATE_NODE_KEY


def test_rejects_duplicate_relationship_endpoints() -> None:
    """Prevent duplicate relationship commands in one batch."""
    duplicate = _relationship(_ACCOUNT_KEYS[0])
    invalid = _materialization().model_copy(
        update={
            "customer_account_nodes": (_account_node(_ACCOUNT_KEYS[0]),),
            "has_account_relationships": (duplicate, duplicate),
        }
    )

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is (Neo4jCommandBuildErrorCode.DUPLICATE_RELATIONSHIP)


def test_rejects_relationship_without_materialized_target_node() -> None:
    """Require both endpoint node commands before relationship commands."""
    invalid = _materialization().model_copy(
        update={"customer_account_nodes": (_account_node(_ACCOUNT_KEYS[0]),)}
    )

    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)

    assert exc_info.value.code is (Neo4jCommandBuildErrorCode.RELATIONSHIP_ENDPOINT_MISSING)


def test_command_models_reject_arbitrary_cypher() -> None:
    """Make command contracts impossible to repurpose as a generic query API."""
    valid = build_customer_neo4j_commands(_materialization())
    constraint = valid.constraint_commands[0]
    node = valid.node_commands[0]

    with pytest.raises(ValidationError):
        Neo4jConstraintCommand(
            command_id=constraint.command_id,
            constraint_name=constraint.constraint_name,
            label=constraint.label,
            key_property=constraint.key_property,
            cypher="MATCH (n) DETACH DELETE n",
        )
    with pytest.raises(ValidationError):
        Neo4jNodeUpsertCommand(
            command_id=node.command_id,
            node_mapping_id=node.node_mapping_id,
            label=node.label,
            key_property=node.key_property,
            cypher="CREATE (n)",
            parameters=node.parameters,
        )


def test_batch_is_frozen_and_digest_tampering_is_rejected() -> None:
    """Protect command evidence from mutation and digest replacement."""
    batch = build_customer_neo4j_commands(_materialization())
    mutable = cast(_MutableBatch, batch)

    with pytest.raises(ValidationError):
        mutable.builder_version = "2.0"
    with pytest.raises(ValidationError):
        CustomerNeo4jCommandBatch.model_validate(
            {**batch.model_dump(mode="python"), "command_batch_digest": "0" * 64}
        )


def test_source_has_no_neo4j_driver_or_execution_calls() -> None:
    """Keep this step limited to static command construction."""
    source_path = Path(__file__).parents[1] / (
        "src/return_platform/data_platform/graph/commands.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "neo4j" not in imported_roots
    assert "execute_query" not in source
    assert ".session(" not in source
    assert ".run(" not in source


@pytest.mark.parametrize("invalid", [object(), None, "materialization"])
def test_rejects_invalid_input_types(invalid: object) -> None:
    """Reject caller values that are not projection materializations."""
    with pytest.raises(Neo4jCommandBuildError) as exc_info:
        build_customer_neo4j_commands(invalid)  # type: ignore[arg-type]

    assert exc_info.value.code is Neo4jCommandBuildErrorCode.INVALID_INPUT_TYPE
