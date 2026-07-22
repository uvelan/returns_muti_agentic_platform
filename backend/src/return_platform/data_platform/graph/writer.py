"""Explicit no-retry Neo4j writer for the Customer foundation graph slice."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, LiteralString, Never, Protocol, Self
from uuid import UUID

from neo4j import WRITE_ACCESS, AsyncDriver, AsyncTransaction, Bookmarks
from neo4j.exceptions import (
    AuthError,
    DriverError,
    IncompleteCommit,
    Neo4jError,
    ResultNotSingleError,
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
    CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
    CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
    CUSTOMER_CONSTRAINT_CYPHER,
    CUSTOMER_NODE_UPSERT_CYPHER,
    HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
    CustomerNeo4jCommandBatch,
    Neo4jConstraintCommand,
    Neo4jNodeUpsertCommand,
    Neo4jRelationshipUpsertCommand,
)

__all__ = [
    "CustomerNeo4jDataWriteEvidence",
    "CustomerNeo4jWriter",
    "Neo4jConstraintWriteEvidence",
    "Neo4jNodeWriteEvidence",
    "Neo4jRelationshipWriteEvidence",
    "Neo4jSchemaPreparationEvidence",
    "Neo4jWriteError",
    "Neo4jWriteErrorCode",
    "Neo4jWritePhase",
    "SystemUtcClock",
    "UtcClock",
]

WRITER_VERSION: Final = "1.0"
_EVIDENCE_DIGEST_DOMAIN: Final = "return-platform:customer-neo4j-writer:v1"
_DATABASE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_TIMEOUT_CODE_PARTS: Final = (
    "Timeout",
    "TimedOut",
    "TransactionTerminated",
)

_MIN_TIMEOUT_SECONDS: Final = 0.05
_MAX_TRANSACTION_TIMEOUT_SECONDS: Final = 300.0
_MAX_OPERATION_TIMEOUT_SECONDS: Final = 600.0

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
Neo4jBookmarkValue = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=4096,
        pattern=r"^[\x21-\x7E]+$",
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


class Neo4jWritePhase(StrEnum):
    """Explicit writer phase."""

    SCHEMA = "SCHEMA"
    DATA = "DATA"


class Neo4jWriteErrorCode(StrEnum):
    """Stable safe writer failure codes."""

    INVALID_INPUT = "INVALID_INPUT"
    DATABASE_INVALID = "DATABASE_INVALID"
    TIMEOUT_INVALID = "TIMEOUT_INVALID"
    COMMAND_BATCH_INVALID = "COMMAND_BATCH_INVALID"
    SCHEMA_EVIDENCE_MISMATCH = "SCHEMA_EVIDENCE_MISMATCH"
    AUTH_FAILED = "AUTH_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    BOOKMARK_MISSING = "BOOKMARK_MISSING"
    TIMEOUT = "TIMEOUT"
    RESULT_CARDINALITY_INVALID = "RESULT_CARDINALITY_INVALID"
    RESULT_VALUE_INVALID = "RESULT_VALUE_INVALID"
    SCHEMA_EXECUTION_FAILED = "SCHEMA_EXECUTION_FAILED"
    DATA_EXECUTION_FAILED = "DATA_EXECUTION_FAILED"
    COMMIT_OUTCOME_UNKNOWN = "COMMIT_OUTCOME_UNKNOWN"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


_SAFE_MESSAGES: Final = {
    Neo4jWriteErrorCode.INVALID_INPUT: "Neo4j writer inputs are invalid.",
    Neo4jWriteErrorCode.DATABASE_INVALID: ("The configured Neo4j database name is invalid."),
    Neo4jWriteErrorCode.TIMEOUT_INVALID: ("Neo4j writer timeout values are invalid."),
    Neo4jWriteErrorCode.COMMAND_BATCH_INVALID: ("The Customer Neo4j command batch is invalid."),
    Neo4jWriteErrorCode.SCHEMA_EVIDENCE_MISMATCH: (
        "Schema preparation evidence does not match the data command batch."
    ),
    Neo4jWriteErrorCode.AUTH_FAILED: "Neo4j authentication or authorization failed.",
    Neo4jWriteErrorCode.CONNECTION_FAILED: "Neo4j is unavailable.",
    Neo4jWriteErrorCode.BOOKMARK_MISSING: (
        "Neo4j did not return the causal bookmark required by this phase."
    ),
    Neo4jWriteErrorCode.TIMEOUT: "Neo4j execution exceeded its bounded timeout.",
    Neo4jWriteErrorCode.RESULT_CARDINALITY_INVALID: (
        "A Neo4j write command returned an invalid record count."
    ),
    Neo4jWriteErrorCode.RESULT_VALUE_INVALID: (
        "A Neo4j write command returned unexpected identity evidence."
    ),
    Neo4jWriteErrorCode.SCHEMA_EXECUTION_FAILED: ("Neo4j schema preparation failed."),
    Neo4jWriteErrorCode.DATA_EXECUTION_FAILED: ("The Neo4j Customer data transaction failed."),
    Neo4jWriteErrorCode.COMMIT_OUTCOME_UNKNOWN: (
        "The Neo4j commit outcome is unknown after connection loss."
    ),
    Neo4jWriteErrorCode.ROLLBACK_FAILED: (
        "The Neo4j transaction could not be rolled back cleanly."
    ),
}


class Neo4jWriteError(RuntimeError):
    """Safe writer failure with explicit phase classification."""

    def __init__(
        self,
        code: Neo4jWriteErrorCode,
        phase: Neo4jWritePhase,
    ) -> None:
        """Initialize one safe writer error."""
        self.code = code
        self.phase = phase
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(
    code: Neo4jWriteErrorCode,
    phase: Neo4jWritePhase,
) -> Never:
    """Raise one safe writer error."""
    raise Neo4jWriteError(code, phase)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic contract error."""
    raise PydanticCustomError(error_type, message)


class UtcClock(Protocol):
    """Injectable UTC execution clock."""

    def now(self) -> datetime:
        """Return one timezone-aware UTC timestamp."""
        ...


class SystemUtcClock:
    """Production wall clock implementation."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)


class Neo4jConstraintWriteEvidence(CanonicalBaseModel):
    """Successful execution evidence for one schema command."""

    command_id: UUID
    constraint_name: CanonicalIdentifier


class Neo4jNodeWriteEvidence(CanonicalBaseModel):
    """Successful identity evidence for one node MERGE command."""

    command_id: UUID
    node_mapping_id: CanonicalIdentifier
    key: CanonicalIdentifier


class Neo4jRelationshipWriteEvidence(CanonicalBaseModel):
    """Successful identity evidence for one relationship MERGE command."""

    command_id: UUID
    relationship_mapping_id: CanonicalIdentifier
    source_key: CanonicalIdentifier
    target_key: CanonicalIdentifier


class Neo4jSchemaPreparationEvidence(CanonicalBaseModel):
    """Committed schema-phase evidence."""

    writer_version: VersionReference
    command_batch_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    evidence_digest: Sha256Digest
    sync_run_id: UUID
    database: Neo4jDatabaseName
    transaction_timeout_seconds: TransactionTimeoutSeconds
    operation_timeout_seconds: OperationTimeoutSeconds
    started_at: UtcDateTime
    completed_at: UtcDateTime
    committed: bool
    bookmarks: tuple[Neo4jBookmarkValue, ...]
    constraint_writes: tuple[Neo4jConstraintWriteEvidence, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Reject incomplete or tampered schema evidence."""
        if not self.committed:
            _raise_model_error(
                "neo4j_schema_not_committed",
                "schema evidence must represent one committed transaction",
            )
        if self.completed_at < self.started_at:
            _raise_model_error(
                "neo4j_schema_time_invalid",
                "schema completion cannot precede schema start",
            )
        if self.operation_timeout_seconds <= self.transaction_timeout_seconds:
            _raise_model_error(
                "neo4j_schema_timeout_order_invalid",
                "operation timeout must not be shorter than transaction timeout",
            )
        if not self.bookmarks or tuple(sorted(set(self.bookmarks))) != self.bookmarks:
            _raise_model_error(
                "neo4j_schema_bookmarks_invalid",
                "schema bookmarks must be non-empty, unique, and sorted",
            )
        if len(self.constraint_writes) != 2:
            _raise_model_error(
                "neo4j_schema_command_count_invalid",
                "Customer schema evidence must contain exactly two constraints",
            )
        if tuple(item.constraint_name for item in self.constraint_writes) != (
            "customer_customer_key_unique",
            "customer_account_account_key_unique",
        ):
            _raise_model_error(
                "neo4j_schema_constraint_order_invalid",
                "schema evidence must preserve fixed Customer constraint order",
            )
        if len({item.command_id for item in self.constraint_writes}) != 2:
            _raise_model_error(
                "neo4j_schema_command_duplicate",
                "schema command evidence IDs must be unique",
            )
        if self.evidence_digest != _schema_evidence_digest(self):
            _raise_model_error(
                "neo4j_schema_evidence_digest_mismatch",
                "schema evidence digest does not match its contents",
            )
        return self


class CustomerNeo4jDataWriteEvidence(CanonicalBaseModel):
    """Committed atomic Customer data-transaction evidence."""

    writer_version: VersionReference
    command_batch_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    schema_evidence_digest: Sha256Digest
    evidence_digest: Sha256Digest
    sync_run_id: UUID
    database: Neo4jDatabaseName
    transaction_timeout_seconds: TransactionTimeoutSeconds
    operation_timeout_seconds: OperationTimeoutSeconds
    started_at: UtcDateTime
    completed_at: UtcDateTime
    committed: bool
    input_bookmarks: tuple[Neo4jBookmarkValue, ...]
    output_bookmarks: tuple[Neo4jBookmarkValue, ...]
    node_writes: tuple[Neo4jNodeWriteEvidence, ...]
    relationship_writes: tuple[Neo4jRelationshipWriteEvidence, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Reject incomplete or tampered data-write evidence."""
        if not self.committed:
            _raise_model_error(
                "neo4j_data_not_committed",
                "data evidence must represent one committed transaction",
            )
        if self.completed_at < self.started_at:
            _raise_model_error(
                "neo4j_data_time_invalid",
                "data completion cannot precede data start",
            )
        if self.operation_timeout_seconds <= self.transaction_timeout_seconds:
            _raise_model_error(
                "neo4j_data_timeout_order_invalid",
                "operation timeout must not be shorter than transaction timeout",
            )
        if not self.input_bookmarks or (
            tuple(sorted(set(self.input_bookmarks))) != self.input_bookmarks
        ):
            _raise_model_error(
                "neo4j_data_input_bookmarks_invalid",
                "data input bookmarks must be non-empty, unique, and sorted",
            )
        if not self.output_bookmarks or (
            tuple(sorted(set(self.output_bookmarks))) != self.output_bookmarks
        ):
            _raise_model_error(
                "neo4j_data_output_bookmarks_invalid",
                "data output bookmarks must be non-empty, unique, and sorted",
            )
        command_ids = tuple(item.command_id for item in self.node_writes) + tuple(
            item.command_id for item in self.relationship_writes
        )
        if len(command_ids) != len(set(command_ids)):
            _raise_model_error(
                "neo4j_data_command_duplicate",
                "data-write command evidence IDs must be unique",
            )
        if self.evidence_digest != _data_evidence_digest(self):
            _raise_model_error(
                "neo4j_data_evidence_digest_mismatch",
                "data-write evidence digest does not match its contents",
            )
        return self


def _utc_text(value: datetime) -> str:
    """Encode one UTC timestamp deterministically."""
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _digest(payload: dict[str, object]) -> str:
    """Hash one deterministic evidence payload."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(_EVIDENCE_DIGEST_DOMAIN.encode("ascii"))
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.hexdigest()


def _schema_evidence_payload(
    evidence: Neo4jSchemaPreparationEvidence,
) -> dict[str, object]:
    """Return schema evidence excluding its own digest."""
    return {
        "phase": Neo4jWritePhase.SCHEMA.value,
        "writer_version": evidence.writer_version,
        "command_batch_digest": evidence.command_batch_digest,
        "execution_plan_digest": evidence.execution_plan_digest,
        "sync_run_id": str(evidence.sync_run_id),
        "database": evidence.database,
        "transaction_timeout_seconds": evidence.transaction_timeout_seconds,
        "operation_timeout_seconds": evidence.operation_timeout_seconds,
        "started_at": _utc_text(evidence.started_at),
        "completed_at": _utc_text(evidence.completed_at),
        "committed": evidence.committed,
        "bookmarks": list(evidence.bookmarks),
        "constraint_writes": [
            {
                "command_id": str(item.command_id),
                "constraint_name": item.constraint_name,
            }
            for item in evidence.constraint_writes
        ],
    }


def _schema_evidence_digest(evidence: Neo4jSchemaPreparationEvidence) -> str:
    """Calculate one schema evidence digest."""
    return _digest(_schema_evidence_payload(evidence))


def _data_evidence_payload(
    evidence: CustomerNeo4jDataWriteEvidence,
) -> dict[str, object]:
    """Return data-write evidence excluding its own digest."""
    return {
        "phase": Neo4jWritePhase.DATA.value,
        "writer_version": evidence.writer_version,
        "command_batch_digest": evidence.command_batch_digest,
        "execution_plan_digest": evidence.execution_plan_digest,
        "schema_evidence_digest": evidence.schema_evidence_digest,
        "sync_run_id": str(evidence.sync_run_id),
        "database": evidence.database,
        "transaction_timeout_seconds": evidence.transaction_timeout_seconds,
        "operation_timeout_seconds": evidence.operation_timeout_seconds,
        "started_at": _utc_text(evidence.started_at),
        "completed_at": _utc_text(evidence.completed_at),
        "committed": evidence.committed,
        "input_bookmarks": list(evidence.input_bookmarks),
        "output_bookmarks": list(evidence.output_bookmarks),
        "node_writes": [
            {
                "command_id": str(item.command_id),
                "node_mapping_id": item.node_mapping_id,
                "key": item.key,
            }
            for item in evidence.node_writes
        ],
        "relationship_writes": [
            {
                "command_id": str(item.command_id),
                "relationship_mapping_id": item.relationship_mapping_id,
                "source_key": item.source_key,
                "target_key": item.target_key,
            }
            for item in evidence.relationship_writes
        ],
    }


def _data_evidence_digest(evidence: CustomerNeo4jDataWriteEvidence) -> str:
    """Calculate one data-write evidence digest."""
    return _digest(_data_evidence_payload(evidence))


def _validate_database(database: object, phase: Neo4jWritePhase) -> str:
    """Validate one explicit database name before driver access."""
    if not isinstance(database, str):
        _raise_error(Neo4jWriteErrorCode.DATABASE_INVALID, phase)
    normalized = database.strip()
    if normalized != database or _DATABASE_PATTERN.fullmatch(normalized) is None:
        _raise_error(Neo4jWriteErrorCode.DATABASE_INVALID, phase)
    return normalized


def _validate_timeouts(
    transaction_timeout_seconds: object,
    operation_timeout_seconds: object,
    phase: Neo4jWritePhase,
) -> tuple[float, float]:
    """Validate bounded server and outer-operation timeouts."""
    if (
        isinstance(transaction_timeout_seconds, bool)
        or not isinstance(transaction_timeout_seconds, float)
        or isinstance(operation_timeout_seconds, bool)
        or not isinstance(operation_timeout_seconds, float)
    ):
        _raise_error(Neo4jWriteErrorCode.TIMEOUT_INVALID, phase)
    transaction_timeout = transaction_timeout_seconds
    operation_timeout = operation_timeout_seconds
    valid = (
        math.isfinite(transaction_timeout)
        and math.isfinite(operation_timeout)
        and _MIN_TIMEOUT_SECONDS <= transaction_timeout <= _MAX_TRANSACTION_TIMEOUT_SECONDS
        and _MIN_TIMEOUT_SECONDS <= operation_timeout <= _MAX_OPERATION_TIMEOUT_SECONDS
        and operation_timeout > transaction_timeout
    )
    if not valid:
        _raise_error(Neo4jWriteErrorCode.TIMEOUT_INVALID, phase)
    return transaction_timeout, operation_timeout


def _validate_batch(batch: object, phase: Neo4jWritePhase) -> CustomerNeo4jCommandBatch:
    """Reject foreign or corrupted command-batch input."""
    if not isinstance(batch, CustomerNeo4jCommandBatch):
        _raise_error(Neo4jWriteErrorCode.COMMAND_BATCH_INVALID, phase)
    try:
        return CustomerNeo4jCommandBatch.model_validate(batch.model_dump(mode="python"))
    except ValidationError as error:
        raise Neo4jWriteError(
            Neo4jWriteErrorCode.COMMAND_BATCH_INVALID,
            phase,
        ) from error


def _constraint_query(command: Neo4jConstraintCommand) -> LiteralString:
    """Resolve one validated constraint to a code-owned literal query."""
    if (
        command.constraint_name == "customer_customer_key_unique"
        and command.cypher == CUSTOMER_CONSTRAINT_CYPHER
    ):
        return CUSTOMER_CONSTRAINT_CYPHER
    if (
        command.constraint_name == "customer_account_account_key_unique"
        and command.cypher == CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER
    ):
        return CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER
    _raise_error(Neo4jWriteErrorCode.COMMAND_BATCH_INVALID, Neo4jWritePhase.SCHEMA)


def _node_query(command: Neo4jNodeUpsertCommand) -> LiteralString:
    """Resolve one validated node command to a literal query."""
    if command.node_mapping_id == "graph.customer.v1" and (
        command.cypher == CUSTOMER_NODE_UPSERT_CYPHER
    ):
        return CUSTOMER_NODE_UPSERT_CYPHER
    if command.node_mapping_id == "graph.customer_account.v1" and (
        command.cypher == CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER
    ):
        return CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER
    _raise_error(Neo4jWriteErrorCode.COMMAND_BATCH_INVALID, Neo4jWritePhase.DATA)


def _relationship_query(command: Neo4jRelationshipUpsertCommand) -> LiteralString:
    """Resolve the validated HAS_ACCOUNT command to a literal query."""
    if (
        command.relationship_mapping_id == "graph.customer.has_account.v1"
        and command.cypher == HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER
    ):
        return HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER
    _raise_error(Neo4jWriteErrorCode.COMMAND_BATCH_INVALID, Neo4jWritePhase.DATA)


def _transaction_metadata(
    batch: CustomerNeo4jCommandBatch,
    phase: Neo4jWritePhase,
) -> dict[str, str]:
    """Create safe transaction metadata without source values or secrets."""
    return {
        "component": "return-platform",
        "operation": f"customer-graph-{phase.value.lower()}",
        "writer_version": WRITER_VERSION,
        "sync_run_id": str(batch.sync_run_id),
        "command_batch_digest": batch.command_batch_digest,
    }


def _is_server_timeout(error: Neo4jError) -> bool:
    """Classify server-side timeout and termination error codes."""
    code = error.code or ""
    return any(part in code for part in _TIMEOUT_CODE_PARTS)


def _map_driver_error(
    error: BaseException,
    phase: Neo4jWritePhase,
) -> Neo4jWriteError:
    """Map one known Neo4j driver failure to a safe public error."""
    if isinstance(error, Neo4jWriteError):
        return error
    if isinstance(error, AuthError):
        return Neo4jWriteError(Neo4jWriteErrorCode.AUTH_FAILED, phase)
    if isinstance(error, IncompleteCommit):
        return Neo4jWriteError(
            Neo4jWriteErrorCode.COMMIT_OUTCOME_UNKNOWN,
            phase,
        )
    if isinstance(error, (ServiceUnavailable, SessionExpired)):
        return Neo4jWriteError(Neo4jWriteErrorCode.CONNECTION_FAILED, phase)
    if isinstance(error, ResultNotSingleError):
        return Neo4jWriteError(
            Neo4jWriteErrorCode.RESULT_CARDINALITY_INVALID,
            phase,
        )
    if isinstance(error, Neo4jError):
        if _is_server_timeout(error):
            return Neo4jWriteError(Neo4jWriteErrorCode.TIMEOUT, phase)
        code = (
            Neo4jWriteErrorCode.SCHEMA_EXECUTION_FAILED
            if phase is Neo4jWritePhase.SCHEMA
            else Neo4jWriteErrorCode.DATA_EXECUTION_FAILED
        )
        return Neo4jWriteError(code, phase)
    if isinstance(error, DriverError):
        code = (
            Neo4jWriteErrorCode.SCHEMA_EXECUTION_FAILED
            if phase is Neo4jWritePhase.SCHEMA
            else Neo4jWriteErrorCode.DATA_EXECUTION_FAILED
        )
        return Neo4jWriteError(code, phase)
    raise error


async def _rollback_transaction(
    transaction: AsyncTransaction,
    phase: Neo4jWritePhase,
) -> None:
    """Rollback one explicit transaction without hiding rollback failure."""
    if transaction.closed():
        return
    try:
        await transaction.rollback()
    except asyncio.CancelledError:
        transaction.cancel()
        raise
    except (DriverError, Neo4jError) as error:
        raise Neo4jWriteError(Neo4jWriteErrorCode.ROLLBACK_FAILED, phase) from error


def _clock_now(clock: UtcClock, phase: Neo4jWritePhase) -> datetime:
    """Return one validated UTC clock value."""
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        _raise_error(Neo4jWriteErrorCode.INVALID_INPUT, phase)
    return value.astimezone(UTC)


def _validate_schema_evidence(
    evidence: object,
    phase: Neo4jWritePhase,
) -> Neo4jSchemaPreparationEvidence:
    """Reject foreign or corrupted schema evidence."""
    if not isinstance(evidence, Neo4jSchemaPreparationEvidence):
        _raise_error(Neo4jWriteErrorCode.SCHEMA_EVIDENCE_MISMATCH, phase)
    try:
        return Neo4jSchemaPreparationEvidence.model_validate(evidence.model_dump(mode="python"))
    except ValidationError as error:
        raise Neo4jWriteError(
            Neo4jWriteErrorCode.SCHEMA_EVIDENCE_MISMATCH,
            phase,
        ) from error


class CustomerNeo4jWriter:
    """Injected no-retry writer for the Customer foundation graph slice."""

    def __init__(
        self,
        driver: AsyncDriver,
        *,
        clock: UtcClock | None = None,
    ) -> None:
        """Store the lifespan-owned driver without taking ownership of it."""
        self._driver = driver
        self._clock = clock if clock is not None else SystemUtcClock()

    async def prepare_schema(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> Neo4jSchemaPreparationEvidence:
        """Execute the two idempotent constraints in one explicit transaction."""
        phase = Neo4jWritePhase.SCHEMA
        checked_batch = _validate_batch(batch, phase)
        checked_database = _validate_database(database, phase)
        transaction_timeout, operation_timeout = _validate_timeouts(
            transaction_timeout_seconds,
            operation_timeout_seconds,
            phase,
        )
        started_at = _clock_now(self._clock, phase)
        writes: list[Neo4jConstraintWriteEvidence] = []
        schema_bookmarks: tuple[str, ...] = ()
        try:
            session = self._driver.session(
                database=checked_database,
                default_access_mode=WRITE_ACCESS,
                fetch_size=1,
                disable_auto_commit_retries=True,
            )
        except (DriverError, Neo4jError) as error:
            raise _map_driver_error(error, phase) from error
        try:
            async with asyncio.timeout(operation_timeout):
                try:
                    transaction = await session.begin_transaction(
                        metadata=_transaction_metadata(checked_batch, phase),
                        timeout=transaction_timeout,
                    )
                    try:
                        for command in checked_batch.constraint_commands:
                            result = await transaction.run(
                                _constraint_query(command),
                                command.to_driver_parameters(),
                            )
                            await result.consume()
                            writes.append(
                                Neo4jConstraintWriteEvidence(
                                    command_id=command.command_id,
                                    constraint_name=command.constraint_name,
                                )
                            )
                        await transaction.commit()
                        schema_bookmarks = tuple(
                            sorted((await session.last_bookmarks()).raw_values)
                        )
                        if not schema_bookmarks:
                            _raise_error(
                                Neo4jWriteErrorCode.BOOKMARK_MISSING,
                                phase,
                            )
                    except asyncio.CancelledError:
                        transaction.cancel()
                        raise
                    except IncompleteCommit:
                        transaction.cancel()
                        raise
                    except (DriverError, Neo4jError, Neo4jWriteError):
                        await _rollback_transaction(transaction, phase)
                        raise
                    finally:
                        if not transaction.closed():
                            await transaction.close()
                finally:
                    await session.close()
        except asyncio.CancelledError:
            session.cancel()
            raise
        except TimeoutError as error:
            raise Neo4jWriteError(Neo4jWriteErrorCode.TIMEOUT, phase) from error
        except (DriverError, Neo4jError, Neo4jWriteError) as error:
            raise _map_driver_error(error, phase) from error

        completed_at = _clock_now(self._clock, phase)
        provisional = Neo4jSchemaPreparationEvidence.model_construct(
            writer_version=WRITER_VERSION,
            command_batch_digest=checked_batch.command_batch_digest,
            execution_plan_digest=checked_batch.execution_plan_digest,
            evidence_digest="0" * 64,
            sync_run_id=checked_batch.sync_run_id,
            database=checked_database,
            transaction_timeout_seconds=transaction_timeout,
            operation_timeout_seconds=operation_timeout,
            started_at=started_at,
            completed_at=completed_at,
            committed=True,
            bookmarks=schema_bookmarks,
            constraint_writes=tuple(writes),
        )
        return Neo4jSchemaPreparationEvidence(
            writer_version=WRITER_VERSION,
            command_batch_digest=checked_batch.command_batch_digest,
            execution_plan_digest=checked_batch.execution_plan_digest,
            evidence_digest=_schema_evidence_digest(provisional),
            sync_run_id=checked_batch.sync_run_id,
            database=checked_database,
            transaction_timeout_seconds=transaction_timeout,
            operation_timeout_seconds=operation_timeout,
            started_at=started_at,
            completed_at=completed_at,
            committed=True,
            bookmarks=schema_bookmarks,
            constraint_writes=tuple(writes),
        )

    async def write_data(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        schema_evidence: Neo4jSchemaPreparationEvidence,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerNeo4jDataWriteEvidence:
        """Write nodes and relationships atomically in one explicit transaction."""
        phase = Neo4jWritePhase.DATA
        checked_batch = _validate_batch(batch, phase)
        checked_schema_evidence = _validate_schema_evidence(
            schema_evidence,
            phase,
        )
        checked_database = _validate_database(database, phase)
        transaction_timeout, operation_timeout = _validate_timeouts(
            transaction_timeout_seconds,
            operation_timeout_seconds,
            phase,
        )
        if (
            checked_schema_evidence.command_batch_digest != checked_batch.command_batch_digest
            or checked_schema_evidence.execution_plan_digest != checked_batch.execution_plan_digest
            or checked_schema_evidence.sync_run_id != checked_batch.sync_run_id
            or checked_schema_evidence.database != checked_database
            or not checked_schema_evidence.committed
        ):
            _raise_error(Neo4jWriteErrorCode.SCHEMA_EVIDENCE_MISMATCH, phase)

        started_at = _clock_now(self._clock, phase)
        node_writes: list[Neo4jNodeWriteEvidence] = []
        relationship_writes: list[Neo4jRelationshipWriteEvidence] = []
        output_bookmarks: tuple[str, ...] = ()
        input_bookmarks = checked_schema_evidence.bookmarks
        try:
            session = self._driver.session(
                database=checked_database,
                bookmarks=Bookmarks.from_raw_values(input_bookmarks),
                default_access_mode=WRITE_ACCESS,
                fetch_size=1,
                disable_auto_commit_retries=True,
            )
        except (DriverError, Neo4jError) as error:
            raise _map_driver_error(error, phase) from error
        try:
            async with asyncio.timeout(operation_timeout):
                try:
                    transaction = await session.begin_transaction(
                        metadata=_transaction_metadata(checked_batch, phase),
                        timeout=transaction_timeout,
                    )
                    try:
                        for node_command in checked_batch.node_commands:
                            result = await transaction.run(
                                _node_query(node_command),
                                node_command.parameters.to_driver_parameters(),
                            )
                            record = await result.single(strict=True)
                            if record is None:
                                _raise_error(
                                    Neo4jWriteErrorCode.RESULT_CARDINALITY_INVALID,
                                    phase,
                                )
                            observed_key = record["key"]
                            if (
                                not isinstance(observed_key, str)
                                or observed_key != node_command.parameters.key
                            ):
                                _raise_error(
                                    Neo4jWriteErrorCode.RESULT_VALUE_INVALID,
                                    phase,
                                )
                            await result.consume()
                            node_writes.append(
                                Neo4jNodeWriteEvidence(
                                    command_id=node_command.command_id,
                                    node_mapping_id=node_command.node_mapping_id,
                                    key=observed_key,
                                )
                            )

                        for relationship_command in checked_batch.relationship_commands:
                            result = await transaction.run(
                                _relationship_query(relationship_command),
                                relationship_command.parameters.to_driver_parameters(),
                            )
                            record = await result.single(strict=True)
                            if record is None:
                                _raise_error(
                                    Neo4jWriteErrorCode.RESULT_CARDINALITY_INVALID,
                                    phase,
                                )
                            observed_source = record["source_key"]
                            observed_target = record["target_key"]
                            if (
                                not isinstance(observed_source, str)
                                or not isinstance(observed_target, str)
                                or observed_source != relationship_command.parameters.source_key
                                or observed_target != relationship_command.parameters.target_key
                            ):
                                _raise_error(
                                    Neo4jWriteErrorCode.RESULT_VALUE_INVALID,
                                    phase,
                                )
                            await result.consume()
                            relationship_writes.append(
                                Neo4jRelationshipWriteEvidence(
                                    command_id=relationship_command.command_id,
                                    relationship_mapping_id=(
                                        relationship_command.relationship_mapping_id
                                    ),
                                    source_key=observed_source,
                                    target_key=observed_target,
                                )
                            )
                        await transaction.commit()
                        output_bookmarks = tuple(
                            sorted((await session.last_bookmarks()).raw_values)
                        )
                        if not output_bookmarks:
                            _raise_error(
                                Neo4jWriteErrorCode.BOOKMARK_MISSING,
                                phase,
                            )
                    except asyncio.CancelledError:
                        transaction.cancel()
                        raise
                    except IncompleteCommit:
                        transaction.cancel()
                        raise
                    except (DriverError, Neo4jError, Neo4jWriteError):
                        await _rollback_transaction(transaction, phase)
                        raise
                    finally:
                        if not transaction.closed():
                            await transaction.close()
                finally:
                    await session.close()
        except asyncio.CancelledError:
            session.cancel()
            raise
        except TimeoutError as error:
            raise Neo4jWriteError(Neo4jWriteErrorCode.TIMEOUT, phase) from error
        except (DriverError, Neo4jError, Neo4jWriteError) as error:
            raise _map_driver_error(error, phase) from error

        completed_at = _clock_now(self._clock, phase)
        provisional = CustomerNeo4jDataWriteEvidence.model_construct(
            writer_version=WRITER_VERSION,
            command_batch_digest=checked_batch.command_batch_digest,
            execution_plan_digest=checked_batch.execution_plan_digest,
            schema_evidence_digest=checked_schema_evidence.evidence_digest,
            evidence_digest="0" * 64,
            sync_run_id=checked_batch.sync_run_id,
            database=checked_database,
            transaction_timeout_seconds=transaction_timeout,
            operation_timeout_seconds=operation_timeout,
            started_at=started_at,
            completed_at=completed_at,
            committed=True,
            input_bookmarks=input_bookmarks,
            output_bookmarks=output_bookmarks,
            node_writes=tuple(node_writes),
            relationship_writes=tuple(relationship_writes),
        )
        return CustomerNeo4jDataWriteEvidence(
            writer_version=WRITER_VERSION,
            command_batch_digest=checked_batch.command_batch_digest,
            execution_plan_digest=checked_batch.execution_plan_digest,
            schema_evidence_digest=checked_schema_evidence.evidence_digest,
            evidence_digest=_data_evidence_digest(provisional),
            sync_run_id=checked_batch.sync_run_id,
            database=checked_database,
            transaction_timeout_seconds=transaction_timeout,
            operation_timeout_seconds=operation_timeout,
            started_at=started_at,
            completed_at=completed_at,
            committed=True,
            input_bookmarks=input_bookmarks,
            output_bookmarks=output_bookmarks,
            node_writes=tuple(node_writes),
            relationship_writes=tuple(relationship_writes),
        )
