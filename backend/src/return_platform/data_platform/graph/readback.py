"""Fixed-query Customer graph read-back and idempotency validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, LiteralString, Never, Self
from uuid import UUID

from neo4j import (
    READ_ACCESS,
    AsyncDriver,
    AsyncSession,
    AsyncTransaction,
    Bookmarks,
)
from neo4j.exceptions import (
    AuthError,
    DriverError,
    IncompleteCommit,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
)
from pydantic import Field, StringConstraints, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.graph.commands import (
    CustomerNeo4jCommandBatch,
    Neo4jNodeUpsertCommand,
)
from return_platform.data_platform.graph.writer import (
    CustomerNeo4jDataWriteEvidence,
)

__all__ = [
    "CUSTOMER_ACCOUNT_READBACK_CYPHER",
    "CUSTOMER_READBACK_CYPHER",
    "HAS_ACCOUNT_READBACK_CYPHER",
    "CustomerGraphIdempotencyEvidence",
    "CustomerGraphReadbackError",
    "CustomerGraphReadbackErrorCode",
    "CustomerGraphReadbackEvidence",
    "CustomerGraphReadbackValidator",
    "CustomerNodeReadback",
    "CustomerRelationshipReadback",
    "assert_customer_graph_idempotency",
    "validate_customer_graph_snapshot_records",
]

READBACK_VERSION: Final = "1.0"
_READBACK_DIGEST_DOMAIN: Final = "return-platform:customer-graph-readback:v1"
_IDEMPOTENCY_DIGEST_DOMAIN: Final = "return-platform:customer-graph-idempotency:v1"
_DATABASE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_MIN_TIMEOUT_SECONDS: Final = 0.05
_MAX_TRANSACTION_TIMEOUT_SECONDS: Final = 300.0
_MAX_OPERATION_TIMEOUT_SECONDS: Final = 600.0
_MAX_EXPECTED_ACCOUNTS: Final = 10_000
_MANDATORY_PROVENANCE: Final = (
    "canonical_key",
    "configuration_digest",
    "graph_synced_at",
    "identity_quality",
    "mapping_version",
    "source_asset",
    "source_database",
    "source_record_id",
    "source_system",
    "source_updated_at",
    "sync_run_id",
)

Neo4jDatabaseName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,62}$",
        strict=True,
    ),
]
TransactionTimeoutSeconds = Annotated[
    float,
    Field(
        strict=True,
        ge=_MIN_TIMEOUT_SECONDS,
        le=_MAX_TRANSACTION_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    ),
]
OperationTimeoutSeconds = Annotated[
    float,
    Field(
        strict=True,
        ge=_MIN_TIMEOUT_SECONDS,
        le=_MAX_OPERATION_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    ),
]
Count = Annotated[int, Field(strict=True, ge=0, le=_MAX_EXPECTED_ACCOUNTS)]

CUSTOMER_READBACK_CYPHER: Final[LiteralString] = (
    "MATCH (n:Customer {customer_key: $customer_key})\n"
    "WITH collect(n) AS nodes\n"
    "RETURN size(nodes) AS match_count,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].customer_key END "
    "AS customer_key,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].canonical_key END "
    "AS canonical_key,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].configuration_digest END "
    "AS configuration_digest,\n"
    "       CASE WHEN size(nodes) = 1 THEN toString(nodes[0].graph_synced_at) "
    "END AS graph_synced_at,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].identity_quality END "
    "AS identity_quality,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].mapping_version END "
    "AS mapping_version,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_asset END "
    "AS source_asset,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_database END "
    "AS source_database,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_record_id END "
    "AS source_record_id,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_system END "
    "AS source_system,\n"
    "       CASE WHEN size(nodes) = 1 THEN "
    "toString(nodes[0].source_updated_at) END AS source_updated_at,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].sync_run_id END "
    "AS sync_run_id"
)

CUSTOMER_ACCOUNT_READBACK_CYPHER: Final[LiteralString] = (
    "UNWIND $account_keys AS account_key\n"
    "OPTIONAL MATCH (n:CustomerAccount {account_key: account_key})\n"
    "WITH account_key, collect(n) AS nodes\n"
    "RETURN account_key,\n"
    "       size(nodes) AS match_count,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].canonical_key END "
    "AS canonical_key,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].configuration_digest END "
    "AS configuration_digest,\n"
    "       CASE WHEN size(nodes) = 1 THEN toString(nodes[0].graph_synced_at) "
    "END AS graph_synced_at,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].identity_quality END "
    "AS identity_quality,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].mapping_version END "
    "AS mapping_version,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_asset END "
    "AS source_asset,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_database END "
    "AS source_database,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_record_id END "
    "AS source_record_id,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].source_system END "
    "AS source_system,\n"
    "       CASE WHEN size(nodes) = 1 THEN "
    "toString(nodes[0].source_updated_at) END AS source_updated_at,\n"
    "       CASE WHEN size(nodes) = 1 THEN nodes[0].sync_run_id END "
    "AS sync_run_id\n"
    "ORDER BY account_key"
)

HAS_ACCOUNT_READBACK_CYPHER: Final[LiteralString] = (
    "UNWIND $account_keys AS account_key\n"
    "OPTIONAL MATCH "
    "(source:Customer {customer_key: $customer_key})"
    "-[relationship:HAS_ACCOUNT]->"
    "(target:CustomerAccount {account_key: account_key})\n"
    "WITH account_key, collect(relationship) AS relationships,\n"
    "     collect(source.customer_key) AS source_keys,\n"
    "     collect(target.account_key) AS target_keys\n"
    "RETURN account_key,\n"
    "       size(relationships) AS match_count,\n"
    "       CASE WHEN size(relationships) = 1 THEN source_keys[0] END "
    "AS source_key,\n"
    "       CASE WHEN size(relationships) = 1 THEN target_keys[0] END "
    "AS target_key\n"
    "ORDER BY account_key"
)


class CustomerGraphReadbackErrorCode(StrEnum):
    """Stable safe read-back and idempotency failure codes."""

    INVALID_INPUT = "INVALID_INPUT"
    DATABASE_INVALID = "DATABASE_INVALID"
    TIMEOUT_INVALID = "TIMEOUT_INVALID"
    COMMAND_BATCH_INVALID = "COMMAND_BATCH_INVALID"
    WRITE_EVIDENCE_MISMATCH = "WRITE_EVIDENCE_MISMATCH"
    AUTH_FAILED = "AUTH_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    TIMEOUT = "TIMEOUT"
    RESULT_CARDINALITY_INVALID = "RESULT_CARDINALITY_INVALID"
    RESULT_VALUE_INVALID = "RESULT_VALUE_INVALID"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    READ_EXECUTION_FAILED = "READ_EXECUTION_FAILED"
    COMMIT_OUTCOME_UNKNOWN = "COMMIT_OUTCOME_UNKNOWN"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"


_SAFE_MESSAGES: Final = {
    CustomerGraphReadbackErrorCode.INVALID_INPUT: ("Customer graph read-back inputs are invalid."),
    CustomerGraphReadbackErrorCode.DATABASE_INVALID: (
        "The configured Neo4j database name is invalid."
    ),
    CustomerGraphReadbackErrorCode.TIMEOUT_INVALID: (
        "Customer graph read-back timeout values are invalid."
    ),
    CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID: (
        "The Customer Neo4j command batch is invalid."
    ),
    CustomerGraphReadbackErrorCode.WRITE_EVIDENCE_MISMATCH: (
        "Committed Customer write evidence does not match the read-back input."
    ),
    CustomerGraphReadbackErrorCode.AUTH_FAILED: ("Neo4j authentication or authorization failed."),
    CustomerGraphReadbackErrorCode.CONNECTION_FAILED: "Neo4j is unavailable.",
    CustomerGraphReadbackErrorCode.TIMEOUT: (
        "Customer graph read-back exceeded its bounded timeout."
    ),
    CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID: (
        "Customer graph read-back returned an invalid cardinality."
    ),
    CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID: (
        "Customer graph read-back returned invalid identity evidence."
    ),
    CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH: (
        "Customer graph mandatory provenance does not match the write input."
    ),
    CustomerGraphReadbackErrorCode.READ_EXECUTION_FAILED: ("Customer graph read-back failed."),
    CustomerGraphReadbackErrorCode.COMMIT_OUTCOME_UNKNOWN: (
        "The Neo4j read transaction commit outcome is unknown."
    ),
    CustomerGraphReadbackErrorCode.ROLLBACK_FAILED: (
        "The Neo4j read transaction could not be rolled back cleanly."
    ),
    CustomerGraphReadbackErrorCode.IDEMPOTENCY_MISMATCH: (
        "The second Customer graph execution changed graph cardinality or state."
    ),
}


class CustomerGraphReadbackError(RuntimeError):
    """Safe read-back failure with a stable public code."""

    def __init__(self, code: CustomerGraphReadbackErrorCode) -> None:
        """Initialize one safe read-back error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: CustomerGraphReadbackErrorCode) -> Never:
    """Raise one safe read-back error."""
    raise CustomerGraphReadbackError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic contract error."""
    raise PydanticCustomError(error_type, message)


class CustomerNodeReadback(CanonicalBaseModel):
    """Validated read-back state for one Customer graph node."""

    label: Annotated[
        str,
        StringConstraints(pattern=r"^(Customer|CustomerAccount)$", strict=True),
    ]
    key: CanonicalIdentifier
    match_count: Count
    canonical_key: CanonicalIdentifier
    configuration_digest: Sha256Digest
    graph_synced_at: UtcDateTime
    identity_quality: CanonicalIdentifier
    mapping_version: VersionReference
    source_asset: CanonicalIdentifier
    source_database: CanonicalIdentifier
    source_record_id: CanonicalIdentifier
    source_system: CanonicalIdentifier
    source_updated_at: UtcDateTime
    sync_run_id: UUID

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require one exact node whose canonical key equals its graph key."""
        if self.match_count != 1:
            _raise_model_error(
                "customer_readback_node_cardinality_invalid",
                "read-back node cardinality must be exactly one",
            )
        if self.canonical_key != self.key:
            _raise_model_error(
                "customer_readback_node_key_mismatch",
                "read-back canonical key must equal the graph key",
            )
        return self


class CustomerRelationshipReadback(CanonicalBaseModel):
    """Validated read-back state for one required HAS_ACCOUNT edge."""

    relationship_type: Annotated[
        str,
        StringConstraints(pattern=r"^HAS_ACCOUNT$", strict=True),
    ]
    source_key: CanonicalIdentifier
    target_key: CanonicalIdentifier
    match_count: Count

    @model_validator(mode="after")
    def validate_cardinality(self) -> Self:
        """Require exactly one directed relationship for each account."""
        if self.match_count != 1:
            _raise_model_error(
                "customer_readback_relationship_cardinality_invalid",
                "read-back relationship cardinality must be exactly one",
            )
        if self.source_key == self.target_key:
            _raise_model_error(
                "customer_readback_relationship_self_loop",
                "HAS_ACCOUNT cannot be a self-loop",
            )
        return self


class CustomerGraphReadbackEvidence(CanonicalBaseModel):
    """Digest-bound exact graph snapshot from one committed write."""

    readback_version: VersionReference
    command_batch_digest: Sha256Digest
    write_evidence_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    snapshot_digest: Sha256Digest
    evidence_digest: Sha256Digest
    sync_run_id: UUID
    database: Neo4jDatabaseName
    transaction_timeout_seconds: TransactionTimeoutSeconds
    operation_timeout_seconds: OperationTimeoutSeconds
    started_at: UtcDateTime
    completed_at: UtcDateTime
    committed: bool
    customer: CustomerNodeReadback
    customer_accounts: tuple[CustomerNodeReadback, ...]
    has_account_relationships: tuple[CustomerRelationshipReadback, ...]

    @property
    def customer_count(self) -> int:
        """Return exact Customer-node cardinality."""
        return self.customer.match_count

    @property
    def customer_account_count(self) -> int:
        """Return exact CustomerAccount-node cardinality."""
        return sum(item.match_count for item in self.customer_accounts)

    @property
    def relationship_count(self) -> int:
        """Return exact HAS_ACCOUNT cardinality."""
        return sum(item.match_count for item in self.has_account_relationships)

    @classmethod
    def create(
        cls,
        *,
        command_batch_digest: str,
        write_evidence_digest: str,
        execution_plan_digest: str,
        sync_run_id: UUID,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
        started_at: datetime,
        completed_at: datetime,
        customer: CustomerNodeReadback,
        customer_accounts: tuple[CustomerNodeReadback, ...],
        has_account_relationships: tuple[CustomerRelationshipReadback, ...],
    ) -> Self:
        """Create one validated digest-bound read-back evidence object."""
        snapshot_digest = _snapshot_digest(
            customer,
            customer_accounts,
            has_account_relationships,
        )
        digest_payload = _readback_creation_payload(
            readback_version=READBACK_VERSION,
            command_batch_digest=command_batch_digest,
            write_evidence_digest=write_evidence_digest,
            execution_plan_digest=execution_plan_digest,
            snapshot_digest=snapshot_digest,
            sync_run_id=sync_run_id,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
            started_at=started_at,
            completed_at=completed_at,
            committed=True,
            customer=customer,
            customer_accounts=customer_accounts,
            has_account_relationships=has_account_relationships,
        )
        model_payload: dict[str, object] = {
            "readback_version": READBACK_VERSION,
            "command_batch_digest": command_batch_digest,
            "write_evidence_digest": write_evidence_digest,
            "execution_plan_digest": execution_plan_digest,
            "snapshot_digest": snapshot_digest,
            "sync_run_id": sync_run_id,
            "database": database,
            "transaction_timeout_seconds": transaction_timeout_seconds,
            "operation_timeout_seconds": operation_timeout_seconds,
            "started_at": started_at,
            "completed_at": completed_at,
            "committed": True,
            "customer": customer,
            "customer_accounts": customer_accounts,
            "has_account_relationships": has_account_relationships,
        }
        return cls.model_validate(
            {
                **model_payload,
                "evidence_digest": _sha256(
                    _READBACK_DIGEST_DOMAIN,
                    digest_payload,
                ),
            }
        )

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Reject incomplete, unordered, or digest-tampered evidence."""
        if not self.committed:
            _raise_model_error(
                "customer_readback_not_committed",
                "read-back evidence must represent one committed transaction",
            )
        if self.completed_at < self.started_at:
            _raise_model_error(
                "customer_readback_time_invalid",
                "read-back completion cannot precede start",
            )
        if self.operation_timeout_seconds <= self.transaction_timeout_seconds:
            _raise_model_error(
                "customer_readback_timeout_order_invalid",
                "operation timeout must exceed transaction timeout",
            )
        if self.customer.label != "Customer":
            _raise_model_error(
                "customer_readback_customer_label_invalid",
                "root read-back node must use the Customer label",
            )
        if not self.customer_accounts:
            _raise_model_error(
                "customer_readback_accounts_missing",
                "Customer read-back requires at least one CustomerAccount",
            )
        if any(item.label != "CustomerAccount" for item in self.customer_accounts):
            _raise_model_error(
                "customer_readback_account_label_invalid",
                "account read-back nodes must use the CustomerAccount label",
            )
        if self.customer.sync_run_id != self.sync_run_id or any(
            item.sync_run_id != self.sync_run_id for item in self.customer_accounts
        ):
            _raise_model_error(
                "customer_readback_sync_run_mismatch",
                "all read-back nodes must match the evidence sync run",
            )
        account_keys = tuple(item.key for item in self.customer_accounts)
        relationship_keys = tuple(item.target_key for item in self.has_account_relationships)
        if account_keys != tuple(sorted(account_keys)):
            _raise_model_error(
                "customer_readback_account_order_invalid",
                "CustomerAccount evidence must be sorted by canonical key",
            )
        if len(set(account_keys)) != len(account_keys):
            _raise_model_error(
                "customer_readback_account_duplicate",
                "CustomerAccount evidence keys must be unique",
            )
        if relationship_keys != account_keys:
            _raise_model_error(
                "customer_readback_relationship_set_invalid",
                "HAS_ACCOUNT targets must exactly match CustomerAccount keys",
            )
        if any(item.source_key != self.customer.key for item in self.has_account_relationships):
            _raise_model_error(
                "customer_readback_relationship_source_invalid",
                "HAS_ACCOUNT sources must equal the Customer key",
            )
        if self.snapshot_digest != _snapshot_digest(
            self.customer,
            self.customer_accounts,
            self.has_account_relationships,
        ):
            _raise_model_error(
                "customer_readback_snapshot_digest_mismatch",
                "read-back snapshot digest does not match graph state",
            )
        if self.evidence_digest != _readback_evidence_digest(self):
            _raise_model_error(
                "customer_readback_evidence_digest_mismatch",
                "read-back evidence digest does not match its contents",
            )
        return self


class CustomerGraphIdempotencyEvidence(CanonicalBaseModel):
    """Proof that a second identical execution preserved exact graph state."""

    validator_version: VersionReference
    execution_plan_digest: Sha256Digest
    command_batch_digest: Sha256Digest
    first_write_evidence_digest: Sha256Digest
    second_write_evidence_digest: Sha256Digest
    first_readback_evidence_digest: Sha256Digest
    second_readback_evidence_digest: Sha256Digest
    snapshot_digest: Sha256Digest
    evidence_digest: Sha256Digest
    expected_customer_count: Count
    expected_customer_account_count: Count
    expected_relationship_count: Count
    first_customer_count: Count
    first_customer_account_count: Count
    first_relationship_count: Count
    second_customer_count: Count
    second_customer_account_count: Count
    second_relationship_count: Count
    idempotent: bool

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Require exact expected counts and equal first/second snapshots."""
        expected = (
            self.expected_customer_count,
            self.expected_customer_account_count,
            self.expected_relationship_count,
        )
        first = (
            self.first_customer_count,
            self.first_customer_account_count,
            self.first_relationship_count,
        )
        second = (
            self.second_customer_count,
            self.second_customer_account_count,
            self.second_relationship_count,
        )
        if not self.idempotent or first != expected or second != expected:
            _raise_model_error(
                "customer_idempotency_counts_invalid",
                "idempotency evidence must preserve all expected counts",
            )
        if self.evidence_digest != _idempotency_evidence_digest(self):
            _raise_model_error(
                "customer_idempotency_digest_mismatch",
                "idempotency evidence digest does not match its contents",
            )
        return self


def _canonical_json(payload: object) -> bytes:
    """Serialize one evidence payload deterministically."""
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _utc_json(value: datetime) -> str:
    """Serialize one UTC datetime exactly as Pydantic JSON mode does."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(domain: str, payload: object) -> str:
    """Hash one domain-separated deterministic payload."""
    digest = hashlib.sha256()
    domain_bytes = domain.encode("ascii")
    digest.update(len(domain_bytes).to_bytes(4, "big"))
    digest.update(domain_bytes)
    encoded = _canonical_json(payload)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.hexdigest()


def _snapshot_digest(
    customer: CustomerNodeReadback,
    accounts: tuple[CustomerNodeReadback, ...],
    relationships: tuple[CustomerRelationshipReadback, ...],
) -> str:
    """Hash only exact graph state, excluding run timestamps and evidence IDs."""
    return _sha256(
        _READBACK_DIGEST_DOMAIN,
        {
            "customer": customer.model_dump(mode="json"),
            "customer_accounts": [item.model_dump(mode="json") for item in accounts],
            "has_account_relationships": [item.model_dump(mode="json") for item in relationships],
        },
    )


def _readback_unsigned_payload(
    evidence: CustomerGraphReadbackEvidence,
) -> dict[str, object]:
    """Build the deterministic read-back payload excluding its own digest."""
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    return {str(key): value for key, value in payload.items()}


def _readback_creation_payload(
    *,
    readback_version: str,
    command_batch_digest: str,
    write_evidence_digest: str,
    execution_plan_digest: str,
    snapshot_digest: str,
    sync_run_id: UUID,
    database: str,
    transaction_timeout_seconds: float,
    operation_timeout_seconds: float,
    started_at: datetime,
    completed_at: datetime,
    committed: bool,
    customer: CustomerNodeReadback,
    customer_accounts: tuple[CustomerNodeReadback, ...],
    has_account_relationships: tuple[CustomerRelationshipReadback, ...],
) -> dict[str, object]:
    """Build a JSON-compatible unsigned read-back creation payload."""
    return {
        "readback_version": readback_version,
        "command_batch_digest": command_batch_digest,
        "write_evidence_digest": write_evidence_digest,
        "execution_plan_digest": execution_plan_digest,
        "snapshot_digest": snapshot_digest,
        "sync_run_id": str(sync_run_id),
        "database": database,
        "transaction_timeout_seconds": transaction_timeout_seconds,
        "operation_timeout_seconds": operation_timeout_seconds,
        "started_at": _utc_json(started_at),
        "completed_at": _utc_json(completed_at),
        "committed": committed,
        "customer": customer.model_dump(mode="json"),
        "customer_accounts": [item.model_dump(mode="json") for item in customer_accounts],
        "has_account_relationships": [
            item.model_dump(mode="json") for item in has_account_relationships
        ],
    }


def _readback_evidence_digest(evidence: CustomerGraphReadbackEvidence) -> str:
    """Calculate the read-back evidence integrity digest."""
    return _sha256(_READBACK_DIGEST_DOMAIN, _readback_unsigned_payload(evidence))


def _idempotency_unsigned_payload(
    evidence: CustomerGraphIdempotencyEvidence,
) -> dict[str, object]:
    """Build the deterministic idempotency payload excluding its digest."""
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    return {str(key): value for key, value in payload.items()}


def _idempotency_evidence_digest(
    evidence: CustomerGraphIdempotencyEvidence,
) -> str:
    """Calculate the idempotency evidence integrity digest."""
    return _sha256(
        _IDEMPOTENCY_DIGEST_DOMAIN,
        _idempotency_unsigned_payload(evidence),
    )


def _validate_database(value: object) -> str:
    """Validate one strict code-selected Neo4j database name."""
    if not isinstance(value, str) or _DATABASE_PATTERN.fullmatch(value) is None:
        _raise_error(CustomerGraphReadbackErrorCode.DATABASE_INVALID)
    return value


def _validate_timeouts(
    transaction_timeout_seconds: object,
    operation_timeout_seconds: object,
) -> tuple[float, float]:
    """Validate finite bounded timeout values and their ordering."""
    if (
        isinstance(transaction_timeout_seconds, bool)
        or not isinstance(transaction_timeout_seconds, (int, float))
        or isinstance(operation_timeout_seconds, bool)
        or not isinstance(operation_timeout_seconds, (int, float))
    ):
        _raise_error(CustomerGraphReadbackErrorCode.TIMEOUT_INVALID)
    transaction_timeout = float(transaction_timeout_seconds)
    operation_timeout = float(operation_timeout_seconds)
    if (
        not math.isfinite(transaction_timeout)
        or not math.isfinite(operation_timeout)
        or not _MIN_TIMEOUT_SECONDS <= transaction_timeout <= _MAX_TRANSACTION_TIMEOUT_SECONDS
        or not _MIN_TIMEOUT_SECONDS <= operation_timeout <= _MAX_OPERATION_TIMEOUT_SECONDS
        or operation_timeout <= transaction_timeout
    ):
        _raise_error(CustomerGraphReadbackErrorCode.TIMEOUT_INVALID)
    return transaction_timeout, operation_timeout


def _validate_batch(value: object) -> CustomerNeo4jCommandBatch:
    """Revalidate one immutable command batch before graph access."""
    if not isinstance(value, CustomerNeo4jCommandBatch):
        _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
    try:
        return CustomerNeo4jCommandBatch.model_validate(value.model_dump(mode="python"))
    except ValidationError as error:
        raise CustomerGraphReadbackError(
            CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID
        ) from error


def _validate_write_evidence(
    value: object,
    batch: CustomerNeo4jCommandBatch,
    database: str,
) -> CustomerNeo4jDataWriteEvidence:
    """Require committed write evidence matching the exact command batch."""
    if not isinstance(value, CustomerNeo4jDataWriteEvidence):
        _raise_error(CustomerGraphReadbackErrorCode.WRITE_EVIDENCE_MISMATCH)
    try:
        evidence = CustomerNeo4jDataWriteEvidence.model_validate(value.model_dump(mode="python"))
    except ValidationError as error:
        raise CustomerGraphReadbackError(
            CustomerGraphReadbackErrorCode.WRITE_EVIDENCE_MISMATCH
        ) from error
    expected_node_writes = tuple(
        (command.node_mapping_id, command.parameters.key) for command in batch.node_commands
    )
    observed_node_writes = tuple((item.node_mapping_id, item.key) for item in evidence.node_writes)
    expected_relationship_writes = tuple(
        (
            command.relationship_mapping_id,
            command.parameters.source_key,
            command.parameters.target_key,
        )
        for command in batch.relationship_commands
    )
    observed_relationship_writes = tuple(
        (
            item.relationship_mapping_id,
            item.source_key,
            item.target_key,
        )
        for item in evidence.relationship_writes
    )
    if (
        evidence.command_batch_digest != batch.command_batch_digest
        or evidence.execution_plan_digest != batch.execution_plan_digest
        or evidence.sync_run_id != batch.sync_run_id
        or evidence.database != database
        or not evidence.committed
        or observed_node_writes != expected_node_writes
        or observed_relationship_writes != expected_relationship_writes
    ):
        _raise_error(CustomerGraphReadbackErrorCode.WRITE_EVIDENCE_MISMATCH)
    return evidence


def _expected_commands(
    batch: CustomerNeo4jCommandBatch,
) -> tuple[Neo4jNodeUpsertCommand, tuple[Neo4jNodeUpsertCommand, ...]]:
    """Resolve exactly one Customer command and sorted account commands."""
    customer_commands = tuple(
        command for command in batch.node_commands if command.node_mapping_id == "graph.customer.v1"
    )
    account_commands = tuple(
        sorted(
            (
                command
                for command in batch.node_commands
                if command.node_mapping_id == "graph.customer_account.v1"
            ),
            key=lambda command: command.parameters.key,
        )
    )
    if len(customer_commands) != 1:
        _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
    if (
        not account_commands
        or len(account_commands) > _MAX_EXPECTED_ACCOUNTS
        or len({item.parameters.key for item in account_commands}) != len(account_commands)
    ):
        _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
    return customer_commands[0], account_commands


def _expected_provenance(command: Neo4jNodeUpsertCommand) -> dict[str, object]:
    """Extract mandatory provenance from one validated node command."""
    expected: dict[str, object] = {}
    for name in _MANDATORY_PROVENANCE:
        value = command.parameters.properties.get(name)
        if value is None:
            _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
        expected[name] = value
    if expected["canonical_key"] != command.parameters.key:
        _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
    return expected


def _record_value(record: Mapping[str, object], name: str) -> object:
    """Read one required record field without leaking driver internals."""
    try:
        value: object = record[name]
        return value
    except (KeyError, TypeError, IndexError) as error:
        raise CustomerGraphReadbackError(
            CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID
        ) from error


def _strict_int(value: object) -> int:
    """Validate one strict non-negative integer result."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
    return value


def _strict_string(value: object) -> str:
    """Validate one non-blank string result."""
    if not isinstance(value, str) or not value or value.strip() != value:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
    return value


def _strict_utc_datetime(value: object) -> datetime:
    """Parse one Neo4j ISO timestamp and normalize it to UTC."""
    text = _strict_string(value)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CustomerGraphReadbackError(
            CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
    return parsed.astimezone(UTC)


def _expected_datetime(value: object) -> datetime:
    """Validate one expected UTC datetime from command parameters."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _raise_error(CustomerGraphReadbackErrorCode.COMMAND_BATCH_INVALID)
    return value.astimezone(UTC)


def _node_readback(
    *,
    record: Mapping[str, object],
    label: str,
    key: str,
    expected: dict[str, object],
) -> CustomerNodeReadback:
    """Validate one exact node record against mandatory provenance."""
    match_count = _strict_int(_record_value(record, "match_count"))
    if match_count != 1:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID)
    observed_key_name = "customer_key" if label == "Customer" else "account_key"
    observed_key = _strict_string(_record_value(record, observed_key_name))
    if observed_key != key:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
    strings = {
        name: _strict_string(_record_value(record, name))
        for name in _MANDATORY_PROVENANCE
        if name not in {"graph_synced_at", "source_updated_at"}
    }
    graph_synced_at = _strict_utc_datetime(_record_value(record, "graph_synced_at"))
    source_updated_at = _strict_utc_datetime(_record_value(record, "source_updated_at"))
    for name, observed in strings.items():
        expected_value = expected[name]
        if not isinstance(expected_value, str) or observed != expected_value:
            _raise_error(CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH)
    if graph_synced_at != _expected_datetime(expected["graph_synced_at"]):
        _raise_error(CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH)
    if source_updated_at != _expected_datetime(expected["source_updated_at"]):
        _raise_error(CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH)
    try:
        sync_run_id = UUID(strings["sync_run_id"])
    except ValueError as error:
        raise CustomerGraphReadbackError(
            CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID
        ) from error
    return CustomerNodeReadback(
        label=label,
        key=key,
        match_count=match_count,
        canonical_key=strings["canonical_key"],
        configuration_digest=strings["configuration_digest"],
        graph_synced_at=graph_synced_at,
        identity_quality=strings["identity_quality"],
        mapping_version=strings["mapping_version"],
        source_asset=strings["source_asset"],
        source_database=strings["source_database"],
        source_record_id=strings["source_record_id"],
        source_system=strings["source_system"],
        source_updated_at=source_updated_at,
        sync_run_id=sync_run_id,
    )


def _relationship_readback(
    record: Mapping[str, object],
    customer_key: str,
    account_key: str,
) -> CustomerRelationshipReadback:
    """Validate one exact directed HAS_ACCOUNT result."""
    match_count = _strict_int(_record_value(record, "match_count"))
    if match_count != 1:
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID)
    observed_account_key = _strict_string(_record_value(record, "account_key"))
    source_key = _strict_string(_record_value(record, "source_key"))
    target_key = _strict_string(_record_value(record, "target_key"))
    if (
        observed_account_key != account_key
        or source_key != customer_key
        or target_key != account_key
    ):
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
    return CustomerRelationshipReadback(
        relationship_type="HAS_ACCOUNT",
        source_key=source_key,
        target_key=target_key,
        match_count=match_count,
    )


def validate_customer_graph_snapshot_records(
    *,
    batch: CustomerNeo4jCommandBatch,
    customer_record: Mapping[str, object],
    account_records: tuple[Mapping[str, object], ...],
    relationship_records: tuple[Mapping[str, object], ...],
) -> tuple[
    CustomerNodeReadback,
    tuple[CustomerNodeReadback, ...],
    tuple[CustomerRelationshipReadback, ...],
]:
    """Validate fixed-query records against one exact command batch."""
    checked_batch = _validate_batch(batch)
    if not isinstance(customer_record, Mapping) or any(
        not isinstance(record, Mapping) for record in (*account_records, *relationship_records)
    ):
        _raise_error(CustomerGraphReadbackErrorCode.INVALID_INPUT)
    customer_command, account_commands = _expected_commands(checked_batch)
    account_keys = tuple(command.parameters.key for command in account_commands)
    if len(account_records) != len(account_keys) or len(relationship_records) != len(account_keys):
        _raise_error(CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID)
    customer = _node_readback(
        record=customer_record,
        label="Customer",
        key=customer_command.parameters.key,
        expected=_expected_provenance(customer_command),
    )
    account_expected = {
        command.parameters.key: _expected_provenance(command) for command in account_commands
    }
    accounts: list[CustomerNodeReadback] = []
    for expected_key, record in zip(
        account_keys,
        account_records,
        strict=True,
    ):
        returned_key = _strict_string(_record_value(record, "account_key"))
        if returned_key != expected_key:
            _raise_error(CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID)
        accounts.append(
            _node_readback(
                record=record,
                label="CustomerAccount",
                key=expected_key,
                expected=account_expected[expected_key],
            )
        )
    relationships = tuple(
        _relationship_readback(
            record,
            customer_command.parameters.key,
            expected_key,
        )
        for expected_key, record in zip(
            account_keys,
            relationship_records,
            strict=True,
        )
    )
    return customer, tuple(accounts), relationships


def _map_driver_error(error: BaseException) -> CustomerGraphReadbackError:
    """Map Neo4j failures to bounded safe codes."""
    if isinstance(error, AuthError):
        return CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.AUTH_FAILED)
    if isinstance(error, (ServiceUnavailable, SessionExpired)):
        return CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.CONNECTION_FAILED)
    if isinstance(error, IncompleteCommit):
        return CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.COMMIT_OUTCOME_UNKNOWN)
    if isinstance(error, Neo4jError):
        code = error.code or ""
        if "Timeout" in code or "TimedOut" in code:
            return CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.TIMEOUT)
    return CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.READ_EXECUTION_FAILED)


async def _rollback(transaction: AsyncTransaction) -> None:
    """Rollback one explicit read transaction and expose rollback failure."""
    if transaction.closed():
        return
    try:
        await transaction.rollback()
    except asyncio.CancelledError:
        transaction.cancel()
        raise
    except (DriverError, Neo4jError) as error:
        raise CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.ROLLBACK_FAILED) from error


async def _close_session(session: AsyncSession) -> None:
    """Close the short-lived session without taking driver ownership."""
    try:
        await session.close()
    except asyncio.CancelledError:
        session.cancel()
        raise
    except (DriverError, Neo4jError) as error:
        raise _map_driver_error(error) from error


class CustomerGraphReadbackValidator:
    """Injected fixed-query validator for one Customer graph command batch."""

    def __init__(self, driver: AsyncDriver) -> None:
        """Store one lifespan-owned driver without taking ownership of it."""
        self._driver = driver

    async def read_back(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        write_evidence: CustomerNeo4jDataWriteEvidence,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerGraphReadbackEvidence:
        """Read and validate Customer nodes and HAS_ACCOUNT edges exactly once."""
        checked_batch = _validate_batch(batch)
        checked_database = _validate_database(database)
        transaction_timeout, operation_timeout = _validate_timeouts(
            transaction_timeout_seconds,
            operation_timeout_seconds,
        )
        checked_write = _validate_write_evidence(
            write_evidence,
            checked_batch,
            checked_database,
        )
        customer_command, account_commands = _expected_commands(checked_batch)
        account_keys = tuple(command.parameters.key for command in account_commands)
        started_at = datetime.now(UTC)
        try:
            session = self._driver.session(
                database=checked_database,
                default_access_mode=READ_ACCESS,
                bookmarks=Bookmarks.from_raw_values(checked_write.output_bookmarks),
                fetch_size=max(1, len(account_keys)),
                disable_auto_commit_retries=True,
            )
        except (DriverError, Neo4jError) as error:
            raise _map_driver_error(error) from error
        transaction: AsyncTransaction | None = None
        committed = False
        try:
            async with asyncio.timeout(operation_timeout):
                transaction = await session.begin_transaction(
                    metadata={
                        "component": "customer-graph-readback",
                        "sync_run_id": str(checked_batch.sync_run_id),
                        "command_batch_digest": checked_batch.command_batch_digest,
                    },
                    timeout=transaction_timeout,
                )
                customer_result = await transaction.run(
                    CUSTOMER_READBACK_CYPHER,
                    {"customer_key": customer_command.parameters.key},
                )
                customer_records = await customer_result.fetch(2)
                if len(customer_records) != 1:
                    _raise_error(CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID)
                account_result = await transaction.run(
                    CUSTOMER_ACCOUNT_READBACK_CYPHER,
                    {"account_keys": list(account_keys)},
                )
                account_records = await account_result.fetch(len(account_keys) + 1)
                relationship_result = await transaction.run(
                    HAS_ACCOUNT_READBACK_CYPHER,
                    {
                        "customer_key": customer_command.parameters.key,
                        "account_keys": list(account_keys),
                    },
                )
                relationship_records = await relationship_result.fetch(len(account_keys) + 1)
                customer, account_tuple, relationships = validate_customer_graph_snapshot_records(
                    batch=checked_batch,
                    customer_record=customer_records[0],
                    account_records=tuple(account_records),
                    relationship_records=tuple(relationship_records),
                )
                await transaction.commit()
                committed = True
        except asyncio.CancelledError:
            if transaction is not None:
                transaction.cancel()
            session.cancel()
            raise
        except TimeoutError as error:
            if transaction is not None:
                transaction.cancel()
            session.cancel()
            raise CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.TIMEOUT) from error
        except CustomerGraphReadbackError:
            if transaction is not None and not committed:
                await _rollback(transaction)
            raise
        except (DriverError, Neo4jError) as error:
            if transaction is not None and not committed:
                if isinstance(error, IncompleteCommit):
                    raise _map_driver_error(error) from error
                await _rollback(transaction)
            raise _map_driver_error(error) from error
        finally:
            if not session.closed():
                await _close_session(session)
        completed_at = datetime.now(UTC)
        return CustomerGraphReadbackEvidence.create(
            command_batch_digest=checked_batch.command_batch_digest,
            write_evidence_digest=checked_write.evidence_digest,
            execution_plan_digest=checked_batch.execution_plan_digest,
            sync_run_id=checked_batch.sync_run_id,
            database=checked_database,
            transaction_timeout_seconds=transaction_timeout,
            operation_timeout_seconds=operation_timeout,
            started_at=started_at,
            completed_at=completed_at,
            customer=customer,
            customer_accounts=account_tuple,
            has_account_relationships=relationships,
        )


def assert_customer_graph_idempotency(
    first: CustomerGraphReadbackEvidence,
    second: CustomerGraphReadbackEvidence,
    *,
    first_write_evidence_digest: str,
    second_write_evidence_digest: str,
) -> CustomerGraphIdempotencyEvidence:
    """Prove that the same command batch preserved exact graph state twice."""
    if not isinstance(first, CustomerGraphReadbackEvidence) or not isinstance(
        second,
        CustomerGraphReadbackEvidence,
    ):
        _raise_error(CustomerGraphReadbackErrorCode.INVALID_INPUT)
    try:
        checked_first = CustomerGraphReadbackEvidence.model_validate(
            first.model_dump(mode="python")
        )
        checked_second = CustomerGraphReadbackEvidence.model_validate(
            second.model_dump(mode="python")
        )
    except ValidationError as error:
        raise CustomerGraphReadbackError(CustomerGraphReadbackErrorCode.INVALID_INPUT) from error
    if (
        first_write_evidence_digest != checked_first.write_evidence_digest
        or second_write_evidence_digest != checked_second.write_evidence_digest
        or checked_first.command_batch_digest != checked_second.command_batch_digest
        or checked_first.execution_plan_digest != checked_second.execution_plan_digest
        or checked_first.sync_run_id != checked_second.sync_run_id
        or checked_first.database != checked_second.database
        or checked_first.snapshot_digest != checked_second.snapshot_digest
    ):
        _raise_error(CustomerGraphReadbackErrorCode.IDEMPOTENCY_MISMATCH)
    expected_customer_count = 1
    expected_account_count = len(checked_first.customer_accounts)
    expected_relationship_count = expected_account_count
    first_counts = (
        checked_first.customer_count,
        checked_first.customer_account_count,
        checked_first.relationship_count,
    )
    second_counts = (
        checked_second.customer_count,
        checked_second.customer_account_count,
        checked_second.relationship_count,
    )
    expected_counts = (
        expected_customer_count,
        expected_account_count,
        expected_relationship_count,
    )
    if first_counts != expected_counts or second_counts != expected_counts:
        _raise_error(CustomerGraphReadbackErrorCode.IDEMPOTENCY_MISMATCH)
    unsigned_payload: dict[str, object] = {
        "validator_version": READBACK_VERSION,
        "execution_plan_digest": checked_first.execution_plan_digest,
        "command_batch_digest": checked_first.command_batch_digest,
        "first_write_evidence_digest": first_write_evidence_digest,
        "second_write_evidence_digest": second_write_evidence_digest,
        "first_readback_evidence_digest": checked_first.evidence_digest,
        "second_readback_evidence_digest": checked_second.evidence_digest,
        "snapshot_digest": checked_first.snapshot_digest,
        "expected_customer_count": expected_customer_count,
        "expected_customer_account_count": expected_account_count,
        "expected_relationship_count": expected_relationship_count,
        "first_customer_count": first_counts[0],
        "first_customer_account_count": first_counts[1],
        "first_relationship_count": first_counts[2],
        "second_customer_count": second_counts[0],
        "second_customer_account_count": second_counts[1],
        "second_relationship_count": second_counts[2],
        "idempotent": True,
    }
    payload = {
        **unsigned_payload,
        "evidence_digest": _sha256(
            _IDEMPOTENCY_DIGEST_DOMAIN,
            unsigned_payload,
        ),
    }
    return CustomerGraphIdempotencyEvidence.model_validate(payload)
