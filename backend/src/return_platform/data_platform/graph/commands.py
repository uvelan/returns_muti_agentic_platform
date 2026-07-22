"""Code-owned parameterized Cypher commands for the Customer graph slice."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Never, Self
from uuid import UUID, uuid5

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    GraphProjectionEvidence,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.mapping.projection import (
    CustomerGraphProjectionMaterialization,
    GraphNodeUpsertParameters,
    GraphParameterMap,
    GraphParameterValue,
    GraphRelationshipUpsertParameters,
)

__all__ = [
    "CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER",
    "CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER",
    "CUSTOMER_CONSTRAINT_CYPHER",
    "CUSTOMER_NODE_UPSERT_CYPHER",
    "HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER",
    "CustomerNeo4jCommandBatch",
    "Neo4jCommandBuildError",
    "Neo4jCommandBuildErrorCode",
    "Neo4jConstraintCommand",
    "Neo4jNodeCommandParameters",
    "Neo4jNodeUpsertCommand",
    "Neo4jRelationshipCommandParameters",
    "Neo4jRelationshipUpsertCommand",
    "build_customer_neo4j_commands",
]

COMMAND_BUILDER_VERSION: Final = "1.0"
_BATCH_DIGEST_DOMAIN: Final = "return-platform:customer-neo4j-command-batch:v1"
_COMMAND_ID_DOMAIN: Final = "return-platform:customer-neo4j-command:v1"

_CUSTOMER_NODE_MAPPING_ID: Final = "graph.customer.v1"
_CUSTOMER_ACCOUNT_NODE_MAPPING_ID: Final = "graph.customer_account.v1"
_HAS_ACCOUNT_MAPPING_ID: Final = "graph.customer.has_account.v1"

CUSTOMER_CONSTRAINT_CYPHER: Final = (
    "CREATE CONSTRAINT customer_customer_key_unique IF NOT EXISTS\n"
    "FOR (n:Customer)\n"
    "REQUIRE n.customer_key IS UNIQUE"
)

CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER: Final = (
    "CREATE CONSTRAINT customer_account_account_key_unique IF NOT EXISTS\n"
    "FOR (n:CustomerAccount)\n"
    "REQUIRE n.account_key IS UNIQUE"
)

CUSTOMER_NODE_UPSERT_CYPHER: Final = (
    "MERGE (n:Customer {customer_key: $key})\nSET n += $properties\nRETURN n.customer_key AS key"
)

CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER: Final = (
    "MERGE (n:CustomerAccount {account_key: $key})\n"
    "SET n += $properties\n"
    "RETURN n.account_key AS key"
)

HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER: Final = (
    "MATCH (source:Customer {customer_key: $source_key})\n"
    "MATCH (target:CustomerAccount {account_key: $target_key})\n"
    "MERGE (source)-[:HAS_ACCOUNT]->(target)\n"
    "RETURN source.customer_key AS source_key, "
    "target.account_key AS target_key"
)


type CustomerConstraintName = Literal[
    "customer_customer_key_unique",
    "customer_account_account_key_unique",
]
type CustomerGraphLabel = Literal["Customer", "CustomerAccount"]
type CustomerNodeMappingId = Literal[
    "graph.customer.v1",
    "graph.customer_account.v1",
]
type CustomerKeyProperty = Literal["customer_key", "account_key"]


class Neo4jCommandBuildErrorCode(StrEnum):
    """Stable command-batch construction failure codes."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    MATERIALIZATION_UNSUPPORTED = "MATERIALIZATION_UNSUPPORTED"
    NODE_PLAN_INVALID = "NODE_PLAN_INVALID"
    RELATIONSHIP_PLAN_INVALID = "RELATIONSHIP_PLAN_INVALID"
    DUPLICATE_NODE_KEY = "DUPLICATE_NODE_KEY"
    DUPLICATE_RELATIONSHIP = "DUPLICATE_RELATIONSHIP"
    RELATIONSHIP_ENDPOINT_MISSING = "RELATIONSHIP_ENDPOINT_MISSING"
    PARAMETER_VALUE_INVALID = "PARAMETER_VALUE_INVALID"


_SAFE_MESSAGES: Final = {
    Neo4jCommandBuildErrorCode.INVALID_INPUT_TYPE: (
        "Neo4j command builder inputs have invalid types."
    ),
    Neo4jCommandBuildErrorCode.MATERIALIZATION_UNSUPPORTED: (
        "The command builder supports only the Customer foundation graph slice."
    ),
    Neo4jCommandBuildErrorCode.NODE_PLAN_INVALID: (
        "A Customer graph node parameter contract is invalid."
    ),
    Neo4jCommandBuildErrorCode.RELATIONSHIP_PLAN_INVALID: (
        "The HAS_ACCOUNT relationship parameter contract is invalid."
    ),
    Neo4jCommandBuildErrorCode.DUPLICATE_NODE_KEY: (
        "Graph node upsert keys must be unique within one command batch."
    ),
    Neo4jCommandBuildErrorCode.DUPLICATE_RELATIONSHIP: (
        "Graph relationship endpoints must be unique within one command batch."
    ),
    Neo4jCommandBuildErrorCode.RELATIONSHIP_ENDPOINT_MISSING: (
        "A relationship endpoint has no corresponding node upsert command."
    ),
    Neo4jCommandBuildErrorCode.PARAMETER_VALUE_INVALID: (
        "A Neo4j command parameter value is unsupported."
    ),
}


class Neo4jCommandBuildError(ValueError):
    """Safe failure raised for corrupted graph materialization input."""

    def __init__(self, code: Neo4jCommandBuildErrorCode) -> None:
        """Initialize one safe command-build error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: Neo4jCommandBuildErrorCode) -> Never:
    """Raise one safe command-build error."""
    raise Neo4jCommandBuildError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic model error."""
    raise PydanticCustomError(error_type, message)


class Neo4jConstraintCommand(CanonicalBaseModel):
    """One fixed code-owned idempotent uniqueness-constraint command."""

    command_id: UUID
    constraint_name: CustomerConstraintName
    label: CustomerGraphLabel
    key_property: CustomerKeyProperty
    cypher: str

    @model_validator(mode="after")
    def validate_fixed_template(self) -> Self:
        """Reject any non-code-owned constraint shape or Cypher text."""
        expected = {
            "customer_customer_key_unique": (
                "Customer",
                "customer_key",
                CUSTOMER_CONSTRAINT_CYPHER,
            ),
            "customer_account_account_key_unique": (
                "CustomerAccount",
                "account_key",
                CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
            ),
        }[self.constraint_name]
        if (self.label, self.key_property, self.cypher) != expected:
            _raise_model_error(
                "neo4j_constraint_template_invalid",
                "constraint command must use the fixed Customer schema template",
            )
        return self

    def to_driver_parameters(self) -> dict[str, object]:
        """Return a fresh empty driver-parameter dictionary."""
        return {}


class Neo4jNodeCommandParameters(CanonicalBaseModel):
    """Immutable parameters for one fixed node MERGE command."""

    key: CanonicalIdentifier
    properties: GraphParameterMap

    def to_driver_parameters(self) -> dict[str, object]:
        """Return a fresh mutable dictionary for a future driver call."""
        return {
            "key": self.key,
            "properties": dict(self.properties.as_mapping()),
        }


class Neo4jNodeUpsertCommand(CanonicalBaseModel):
    """One fixed parameterized Customer or CustomerAccount node command."""

    command_id: UUID
    node_mapping_id: CustomerNodeMappingId
    label: CustomerGraphLabel
    key_property: CustomerKeyProperty
    cypher: str
    parameters: Neo4jNodeCommandParameters

    @model_validator(mode="after")
    def validate_fixed_template(self) -> Self:
        """Bind node plan identity to one exact code-owned template."""
        expected = {
            _CUSTOMER_NODE_MAPPING_ID: (
                "Customer",
                "customer_key",
                CUSTOMER_NODE_UPSERT_CYPHER,
            ),
            _CUSTOMER_ACCOUNT_NODE_MAPPING_ID: (
                "CustomerAccount",
                "account_key",
                CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
            ),
        }[self.node_mapping_id]
        if (self.label, self.key_property, self.cypher) != expected:
            _raise_model_error(
                "neo4j_node_template_invalid",
                "node command must use a fixed Customer graph template",
            )
        if self.parameters.properties.get(self.key_property) != self.parameters.key:
            _raise_model_error(
                "neo4j_node_key_parameter_mismatch",
                "node command key must match its constrained property",
            )
        return self


class Neo4jRelationshipCommandParameters(CanonicalBaseModel):
    """Immutable endpoint parameters for HAS_ACCOUNT MERGE."""

    source_key: CanonicalIdentifier
    target_key: CanonicalIdentifier

    def to_driver_parameters(self) -> dict[str, object]:
        """Return a fresh mutable dictionary for a future driver call."""
        return {
            "source_key": self.source_key,
            "target_key": self.target_key,
        }


class Neo4jRelationshipUpsertCommand(CanonicalBaseModel):
    """One fixed parameterized Customer-to-CustomerAccount command."""

    command_id: UUID
    relationship_mapping_id: Literal["graph.customer.has_account.v1"]
    relationship_type: Literal["HAS_ACCOUNT"]
    source_node_mapping_id: Literal["graph.customer.v1"]
    source_label: Literal["Customer"]
    source_key_property: Literal["customer_key"]
    target_node_mapping_id: Literal["graph.customer_account.v1"]
    target_label: Literal["CustomerAccount"]
    target_key_property: Literal["account_key"]
    cypher: str
    parameters: Neo4jRelationshipCommandParameters

    @model_validator(mode="after")
    def validate_fixed_template(self) -> Self:
        """Reject reversed endpoints or arbitrary relationship Cypher."""
        if self.cypher != HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER:
            _raise_model_error(
                "neo4j_relationship_template_invalid",
                "relationship command must use the fixed HAS_ACCOUNT template",
            )
        return self


@dataclass(frozen=True, slots=True)
class _CommandComponents:
    """All deterministic command-batch fields except the batch digest."""

    execution_plan_digest: str
    sync_run_id: UUID
    graph_synced_at: datetime
    constraint_commands: tuple[Neo4jConstraintCommand, ...]
    node_commands: tuple[Neo4jNodeUpsertCommand, ...]
    relationship_commands: tuple[Neo4jRelationshipUpsertCommand, ...]
    projection_evidence: tuple[GraphProjectionEvidence, ...]


class CustomerNeo4jCommandBatch(CanonicalBaseModel):
    """Immutable ordered command batch; no command has been executed."""

    builder_version: VersionReference
    execution_plan_digest: Sha256Digest
    command_batch_digest: Sha256Digest
    sync_run_id: UUID
    graph_synced_at: UtcDateTime
    constraint_commands: tuple[Neo4jConstraintCommand, ...]
    node_commands: tuple[Neo4jNodeUpsertCommand, ...]
    relationship_commands: tuple[Neo4jRelationshipUpsertCommand, ...]
    projection_evidence: tuple[GraphProjectionEvidence, ...]

    @property
    def command_count(self) -> int:
        """Return the total command count."""
        return (
            len(self.constraint_commands)
            + len(self.node_commands)
            + len(self.relationship_commands)
        )

    @model_validator(mode="after")
    def validate_fixed_order(self) -> Self:
        """Require schema commands before deterministic data commands."""
        constraint_names = tuple(command.constraint_name for command in self.constraint_commands)
        if constraint_names != (
            "customer_customer_key_unique",
            "customer_account_account_key_unique",
        ):
            _raise_model_error(
                "neo4j_constraint_order_invalid",
                "Customer constraints must appear in fixed execution order",
            )
        if self.node_commands:
            if self.node_commands[0].node_mapping_id != _CUSTOMER_NODE_MAPPING_ID:
                _raise_model_error(
                    "neo4j_node_order_invalid",
                    "Customer node must precede CustomerAccount nodes",
                )
            if any(
                command.node_mapping_id != _CUSTOMER_ACCOUNT_NODE_MAPPING_ID
                for command in self.node_commands[1:]
            ):
                _raise_model_error(
                    "neo4j_node_order_invalid",
                    "Only CustomerAccount nodes may follow the Customer node",
                )
        constraint_command_ids = tuple(
            constraint_command.command_id for constraint_command in self.constraint_commands
        )
        node_command_ids = tuple(node_command.command_id for node_command in self.node_commands)
        relationship_command_ids = tuple(
            relationship_command.command_id for relationship_command in self.relationship_commands
        )
        command_ids = constraint_command_ids + node_command_ids + relationship_command_ids
        if len(set(command_ids)) != len(command_ids):
            _raise_model_error(
                "neo4j_command_id_duplicate",
                "command IDs must be unique within one batch",
            )
        expected_constraint_ids = tuple(
            command.command_id for command in _constraint_commands(self.sync_run_id)
        )
        if tuple(command.command_id for command in self.constraint_commands) != (
            expected_constraint_ids
        ):
            _raise_model_error(
                "neo4j_command_id_invalid",
                "constraint command IDs do not match batch evidence",
            )
        for node_command in self.node_commands:
            expected = _command_id(
                self.sync_run_id,
                "node",
                f"{node_command.node_mapping_id}:{node_command.parameters.key}",
            )
            if node_command.command_id != expected:
                _raise_model_error(
                    "neo4j_command_id_invalid",
                    "node command ID does not match batch evidence",
                )
        for relationship_command in self.relationship_commands:
            identity = (
                f"{relationship_command.parameters.source_key}->"
                f"{relationship_command.parameters.target_key}"
            )
            if relationship_command.command_id != _command_id(
                self.sync_run_id,
                "relationship",
                identity,
            ):
                _raise_model_error(
                    "neo4j_command_id_invalid",
                    "relationship command ID does not match batch evidence",
                )
        try:
            _validate_batch_relationships(
                self.node_commands,
                self.relationship_commands,
            )
        except Neo4jCommandBuildError as exc:
            _raise_model_error(
                "neo4j_command_batch_structure_invalid",
                exc.safe_message,
            )
        if any(
            evidence.sync_run_id != self.sync_run_id
            or evidence.projected_at != self.graph_synced_at
            for evidence in self.projection_evidence
        ):
            _raise_model_error(
                "neo4j_projection_evidence_mismatch",
                "projection evidence must match batch runtime evidence",
            )
        if self.command_batch_digest != _components_digest(_components_from_batch(self)):
            _raise_model_error(
                "neo4j_command_batch_digest_mismatch",
                "command batch digest does not match command contents",
            )
        return self


def _command_id(sync_run_id: UUID, category: str, identity: str) -> UUID:
    """Create one deterministic command UUID within the sync-run namespace."""
    return uuid5(sync_run_id, f"{_COMMAND_ID_DOMAIN}:{category}:{identity}")


def _constraint_commands(sync_run_id: UUID) -> tuple[Neo4jConstraintCommand, ...]:
    """Build the two Community-compatible uniqueness constraints."""
    return (
        Neo4jConstraintCommand(
            command_id=_command_id(sync_run_id, "constraint", "Customer.customer_key"),
            constraint_name="customer_customer_key_unique",
            label="Customer",
            key_property="customer_key",
            cypher=CUSTOMER_CONSTRAINT_CYPHER,
        ),
        Neo4jConstraintCommand(
            command_id=_command_id(
                sync_run_id,
                "constraint",
                "CustomerAccount.account_key",
            ),
            constraint_name="customer_account_account_key_unique",
            label="CustomerAccount",
            key_property="account_key",
            cypher=CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
        ),
    )


def _validate_customer_node(node: GraphNodeUpsertParameters) -> None:
    """Validate the exact Customer node contract."""
    if (
        node.node_mapping_id != _CUSTOMER_NODE_MAPPING_ID
        or node.label != "Customer"
        or node.key_property != "customer_key"
        or node.properties.get("customer_key") != node.key_value
    ):
        _raise_error(Neo4jCommandBuildErrorCode.NODE_PLAN_INVALID)


def _validate_account_node(node: GraphNodeUpsertParameters) -> None:
    """Validate the exact CustomerAccount node contract."""
    if (
        node.node_mapping_id != _CUSTOMER_ACCOUNT_NODE_MAPPING_ID
        or node.label != "CustomerAccount"
        or node.key_property != "account_key"
        or node.properties.get("account_key") != node.key_value
    ):
        _raise_error(Neo4jCommandBuildErrorCode.NODE_PLAN_INVALID)


def _node_command(
    sync_run_id: UUID,
    node: GraphNodeUpsertParameters,
) -> Neo4jNodeUpsertCommand:
    """Build one fixed parameterized node command."""
    if node.node_mapping_id == _CUSTOMER_NODE_MAPPING_ID:
        _validate_customer_node(node)
        label: CustomerGraphLabel = "Customer"
        key_property: CustomerKeyProperty = "customer_key"
        cypher = CUSTOMER_NODE_UPSERT_CYPHER
        mapping_id: CustomerNodeMappingId = _CUSTOMER_NODE_MAPPING_ID
    elif node.node_mapping_id == _CUSTOMER_ACCOUNT_NODE_MAPPING_ID:
        _validate_account_node(node)
        label = "CustomerAccount"
        key_property = "account_key"
        cypher = CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER
        mapping_id = _CUSTOMER_ACCOUNT_NODE_MAPPING_ID
    else:
        _raise_error(Neo4jCommandBuildErrorCode.NODE_PLAN_INVALID)

    return Neo4jNodeUpsertCommand(
        command_id=_command_id(sync_run_id, "node", f"{mapping_id}:{node.key_value}"),
        node_mapping_id=mapping_id,
        label=label,
        key_property=key_property,
        cypher=cypher,
        parameters=Neo4jNodeCommandParameters(
            key=node.key_value,
            properties=node.properties,
        ),
    )


def _validate_relationship(
    relationship: GraphRelationshipUpsertParameters,
) -> None:
    """Validate exact Customer -> CustomerAccount relationship semantics."""
    valid = (
        relationship.relationship_mapping_id == _HAS_ACCOUNT_MAPPING_ID
        and relationship.relationship_type == "HAS_ACCOUNT"
        and relationship.source_node_mapping_id == _CUSTOMER_NODE_MAPPING_ID
        and relationship.source_label == "Customer"
        and relationship.source_key_property == "customer_key"
        and relationship.source_match.get("customer_key") == relationship.source_key_value
        and relationship.target_node_mapping_id == _CUSTOMER_ACCOUNT_NODE_MAPPING_ID
        and relationship.target_label == "CustomerAccount"
        and relationship.target_key_property == "account_key"
        and relationship.target_match.get("account_key") == relationship.target_key_value
    )
    if not valid:
        _raise_error(Neo4jCommandBuildErrorCode.RELATIONSHIP_PLAN_INVALID)


def _relationship_command(
    sync_run_id: UUID,
    relationship: GraphRelationshipUpsertParameters,
) -> Neo4jRelationshipUpsertCommand:
    """Build one fixed parameterized HAS_ACCOUNT command."""
    _validate_relationship(relationship)
    identity = f"{relationship.source_key_value}->{relationship.target_key_value}"
    return Neo4jRelationshipUpsertCommand(
        command_id=_command_id(sync_run_id, "relationship", identity),
        relationship_mapping_id="graph.customer.has_account.v1",
        relationship_type="HAS_ACCOUNT",
        source_node_mapping_id="graph.customer.v1",
        source_label="Customer",
        source_key_property="customer_key",
        target_node_mapping_id="graph.customer_account.v1",
        target_label="CustomerAccount",
        target_key_property="account_key",
        cypher=HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
        parameters=Neo4jRelationshipCommandParameters(
            source_key=relationship.source_key_value,
            target_key=relationship.target_key_value,
        ),
    )


def _encode_parameter_value(value: GraphParameterValue) -> object:
    """Encode one parameter value deterministically for digest evidence."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _raise_error(Neo4jCommandBuildErrorCode.PARAMETER_VALUE_INVALID)
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace(
                "+00:00",
                "Z",
            )
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            _raise_error(Neo4jCommandBuildErrorCode.PARAMETER_VALUE_INVALID)
        return value
    if isinstance(value, (str, bool, int)):
        return value
    _raise_error(Neo4jCommandBuildErrorCode.PARAMETER_VALUE_INVALID)


def _parameter_map_payload(values: GraphParameterMap) -> list[dict[str, object]]:
    """Encode one ordered immutable graph property map."""
    return [
        {"name": entry.name, "value": _encode_parameter_value(entry.value)}
        for entry in values.entries
    ]


def _components_from_batch(
    batch: CustomerNeo4jCommandBatch,
) -> _CommandComponents:
    """Copy one validated batch into digest components."""
    return _CommandComponents(
        execution_plan_digest=batch.execution_plan_digest,
        sync_run_id=batch.sync_run_id,
        graph_synced_at=batch.graph_synced_at,
        constraint_commands=batch.constraint_commands,
        node_commands=batch.node_commands,
        relationship_commands=batch.relationship_commands,
        projection_evidence=batch.projection_evidence,
    )


def _components_payload(components: _CommandComponents) -> dict[str, object]:
    """Return deterministic command evidence excluding its own digest."""
    return {
        "builder_version": COMMAND_BUILDER_VERSION,
        "execution_plan_digest": components.execution_plan_digest,
        "sync_run_id": str(components.sync_run_id),
        "graph_synced_at": components.graph_synced_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "constraints": [
            {
                "command_id": str(command.command_id),
                "constraint_name": command.constraint_name,
                "label": command.label,
                "key_property": command.key_property,
                "cypher": command.cypher,
            }
            for command in components.constraint_commands
        ],
        "nodes": [
            {
                "command_id": str(command.command_id),
                "node_mapping_id": command.node_mapping_id,
                "label": command.label,
                "key_property": command.key_property,
                "cypher": command.cypher,
                "key": command.parameters.key,
                "properties": _parameter_map_payload(command.parameters.properties),
            }
            for command in components.node_commands
        ],
        "relationships": [
            {
                "command_id": str(command.command_id),
                "relationship_mapping_id": command.relationship_mapping_id,
                "relationship_type": command.relationship_type,
                "source_node_mapping_id": command.source_node_mapping_id,
                "target_node_mapping_id": command.target_node_mapping_id,
                "cypher": command.cypher,
                "source_key": command.parameters.source_key,
                "target_key": command.parameters.target_key,
            }
            for command in components.relationship_commands
        ],
        "projection_evidence": [
            {
                "evidence_id": str(item.evidence_id),
                "sync_run_id": str(item.sync_run_id),
                "source_asset": item.source_asset,
                "source_record_id": item.source_record_id,
                "canonical_entity_type": item.canonical_entity_type,
                "canonical_entity_key": item.canonical_entity_key,
                "graph_label": item.graph_label,
                "graph_key": item.graph_key,
                "mapping_version": item.mapping_version,
                "status": item.projection_status.value,
                "rejection_reason": item.rejection_reason,
                "projected_at": item.projected_at.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
            for item in components.projection_evidence
        ],
    }


def _components_digest(components: _CommandComponents) -> str:
    """Hash deterministic command evidence with one domain tag."""
    payload = json.dumps(
        _components_payload(components),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(_BATCH_DIGEST_DOMAIN.encode("ascii"))
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    return digest.hexdigest()


def _validate_batch_relationships(
    node_commands: tuple[Neo4jNodeUpsertCommand, ...],
    relationship_commands: tuple[Neo4jRelationshipUpsertCommand, ...],
) -> None:
    """Require unique nodes, unique edges, and present endpoints."""
    node_keys = tuple(
        (command.node_mapping_id, command.parameters.key) for command in node_commands
    )
    if len(set(node_keys)) != len(node_keys):
        _raise_error(Neo4jCommandBuildErrorCode.DUPLICATE_NODE_KEY)

    available = set(node_keys)
    relationships = tuple(
        (command.parameters.source_key, command.parameters.target_key)
        for command in relationship_commands
    )
    if len(set(relationships)) != len(relationships):
        _raise_error(Neo4jCommandBuildErrorCode.DUPLICATE_RELATIONSHIP)

    for source_key, target_key in relationships:
        if (_CUSTOMER_NODE_MAPPING_ID, source_key) not in available or (
            _CUSTOMER_ACCOUNT_NODE_MAPPING_ID,
            target_key,
        ) not in available:
            _raise_error(Neo4jCommandBuildErrorCode.RELATIONSHIP_ENDPOINT_MISSING)


def build_customer_neo4j_commands(
    materialization: CustomerGraphProjectionMaterialization,
) -> CustomerNeo4jCommandBatch:
    """Build one immutable fixed-template command batch without Neo4j I/O."""
    if not isinstance(materialization, CustomerGraphProjectionMaterialization):
        _raise_error(Neo4jCommandBuildErrorCode.INVALID_INPUT_TYPE)

    node_inputs: list[GraphNodeUpsertParameters] = []
    if materialization.customer_node is not None:
        node_inputs.append(materialization.customer_node)
    elif materialization.customer_account_nodes or materialization.has_account_relationships:
        _raise_error(Neo4jCommandBuildErrorCode.MATERIALIZATION_UNSUPPORTED)
    node_inputs.extend(materialization.customer_account_nodes)

    node_commands = tuple(_node_command(materialization.sync_run_id, node) for node in node_inputs)
    relationship_commands = tuple(
        _relationship_command(materialization.sync_run_id, relationship)
        for relationship in materialization.has_account_relationships
    )
    _validate_batch_relationships(node_commands, relationship_commands)

    constraint_commands = _constraint_commands(materialization.sync_run_id)
    components = _CommandComponents(
        execution_plan_digest=materialization.execution_plan_digest,
        sync_run_id=materialization.sync_run_id,
        graph_synced_at=materialization.graph_synced_at,
        constraint_commands=constraint_commands,
        node_commands=node_commands,
        relationship_commands=relationship_commands,
        projection_evidence=materialization.projection_evidence,
    )
    digest = _components_digest(components)
    return CustomerNeo4jCommandBatch(
        builder_version=COMMAND_BUILDER_VERSION,
        execution_plan_digest=materialization.execution_plan_digest,
        command_batch_digest=digest,
        sync_run_id=materialization.sync_run_id,
        graph_synced_at=materialization.graph_synced_at,
        constraint_commands=constraint_commands,
        node_commands=node_commands,
        relationship_commands=relationship_commands,
        projection_evidence=materialization.projection_evidence,
    )
