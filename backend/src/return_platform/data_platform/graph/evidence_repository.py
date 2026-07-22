"""Immutable Platform MongoDB persistence for Customer graph evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Never, Protocol, Self
from uuid import UUID

from bson import BSON
from bson.errors import InvalidDocument
from pydantic import Field, JsonValue, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from pymongo import (
    ASCENDING,
    DESCENDING,
    AsyncMongoClient,
    IndexModel,
    ReadPreference,
)
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import (
    AutoReconnect,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    PyMongoError,
)
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.graph.sandbox import CustomerGraphSandboxReport

__all__ = [
    "CustomerGraphEvidenceDocument",
    "CustomerGraphEvidencePersistenceError",
    "CustomerGraphEvidencePersistenceErrorCode",
    "CustomerGraphEvidencePersistenceReceipt",
    "CustomerGraphEvidencePersistenceStatus",
    "CustomerGraphEvidenceRepository",
]

_EVIDENCE_SCHEMA_VERSION: Final = "1.0"
_EVIDENCE_TYPE: Final = "CUSTOMER_GRAPH_SANDBOX_RUN"
_DOCUMENT_DIGEST_DOMAIN: Final = "return-platform:platform-mongodb:customer-graph-evidence:v1"
_DATABASE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_COLLECTION_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$")
_MAX_BSON_DOCUMENT_BYTES: Final = 15_000_000
_MIN_OPERATION_TIMEOUT_SECONDS: Final = 0.05
_MAX_OPERATION_TIMEOUT_SECONDS: Final = 300.0
_MICROSECONDS_PER_SECOND: Final = 1_000_000
_SECONDS_PER_DAY: Final = 86_400
_AUTH_ERROR_CODES: Final = frozenset({13, 18})
_SERVER_TIMEOUT_ERROR_CODE: Final = 50
_WRITE_CONCERN_FAILED_ERROR_CODE: Final = 64

ExecutedAtEpochMicroseconds = Annotated[
    int,
    Field(strict=True, ge=0),
]


class CustomerGraphEvidencePersistenceStatus(StrEnum):
    """Stable persistence outcome status."""

    CREATED = "CREATED"
    ALREADY_PRESENT = "ALREADY_PRESENT"


class CustomerGraphEvidencePersistenceErrorCode(StrEnum):
    """Stable safe Platform MongoDB persistence failure codes."""

    INVALID_INPUT = "INVALID_INPUT"
    DOCUMENT_INVALID = "DOCUMENT_INVALID"
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    INDEX_PREPARATION_FAILED = "INDEX_PREPARATION_FAILED"
    AUTH_FAILED = "AUTH_FAILED"
    TIMEOUT = "TIMEOUT"
    IMMUTABLE_CONFLICT = "IMMUTABLE_CONFLICT"
    WRITE_FAILED = "WRITE_FAILED"
    READBACK_FAILED = "READBACK_FAILED"
    WRITE_OUTCOME_UNKNOWN = "WRITE_OUTCOME_UNKNOWN"


_SAFE_MESSAGES: Final = {
    CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT: (
        "Platform graph-evidence persistence inputs are invalid."
    ),
    CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID: (
        "The graph-evidence document is invalid."
    ),
    CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_TOO_LARGE: (
        "The graph-evidence document exceeds the bounded BSON size."
    ),
    CustomerGraphEvidencePersistenceErrorCode.INDEX_PREPARATION_FAILED: (
        "Platform graph-evidence index preparation failed."
    ),
    CustomerGraphEvidencePersistenceErrorCode.AUTH_FAILED: (
        "Platform MongoDB authentication or authorization failed."
    ),
    CustomerGraphEvidencePersistenceErrorCode.TIMEOUT: (
        "Platform graph-evidence persistence exceeded its bounded timeout."
    ),
    CustomerGraphEvidencePersistenceErrorCode.IMMUTABLE_CONFLICT: (
        "A different immutable graph-evidence document already exists."
    ),
    CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED: (
        "Platform graph-evidence persistence failed."
    ),
    CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED: (
        "Persisted graph evidence could not be read back exactly."
    ),
    CustomerGraphEvidencePersistenceErrorCode.WRITE_OUTCOME_UNKNOWN: (
        "The Platform MongoDB graph-evidence write outcome is unknown."
    ),
}


class CustomerGraphEvidencePersistenceError(RuntimeError):
    """Safe persistence error with a stable public code."""

    def __init__(self, code: CustomerGraphEvidencePersistenceErrorCode) -> None:
        """Initialize one safe persistence error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: CustomerGraphEvidencePersistenceErrorCode) -> Never:
    """Raise one safe persistence error."""
    raise CustomerGraphEvidencePersistenceError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic contract error."""
    raise PydanticCustomError(error_type, message)


def _canonical_json(payload: object) -> bytes:
    """Serialize one digest payload deterministically."""
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    """Hash one domain-separated graph-evidence document payload."""
    digest = hashlib.sha256(usedforsecurity=False)
    domain = _DOCUMENT_DIGEST_DOMAIN.encode("ascii")
    digest.update(len(domain).to_bytes(4, "big"))
    digest.update(domain)
    encoded = _canonical_json(payload)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.hexdigest()


class CustomerGraphEvidenceDocument(CanonicalBaseModel):
    """One immutable aggregate persisted for a successful Customer graph run."""

    schema_version: VersionReference
    evidence_type: Literal["CUSTOMER_GRAPH_SANDBOX_RUN"]
    document_id: CanonicalIdentifier
    report_digest: Sha256Digest
    document_digest: Sha256Digest
    sync_run_id: UUID
    executed_at: UtcDateTime
    executed_at_epoch_microseconds: ExecutedAtEpochMicroseconds
    source_document_id: CanonicalIdentifier
    source_hash: Sha256Digest
    configuration_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    command_batch_digest: Sha256Digest
    schema_evidence_digest: Sha256Digest
    first_write_evidence_digest: Sha256Digest
    second_write_evidence_digest: Sha256Digest
    first_readback_evidence_digest: Sha256Digest
    second_readback_evidence_digest: Sha256Digest
    idempotency_evidence_digest: Sha256Digest
    report_payload: dict[str, JsonValue]

    @classmethod
    def create(cls, report: CustomerGraphSandboxReport) -> Self:
        """Create one digest-bound BSON-safe aggregate from a validated report."""
        if not isinstance(report, CustomerGraphSandboxReport):
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT)
        try:
            checked_report = CustomerGraphSandboxReport.model_validate(
                report.model_dump(mode="python")
            )
        except ValidationError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID
            ) from error

        report_payload_raw = checked_report.model_dump(mode="json")
        report_payload = {str(key): value for key, value in report_payload_raw.items()}
        document_id = f"CUSTOMER_GRAPH_SANDBOX:{checked_report.sync_run_id}"
        digest_payload = _document_creation_payload(
            document_id=document_id,
            report=checked_report,
            report_payload=report_payload,
        )
        model_payload: dict[str, object] = {
            "schema_version": _EVIDENCE_SCHEMA_VERSION,
            "evidence_type": _EVIDENCE_TYPE,
            "document_id": document_id,
            "report_digest": checked_report.report_digest,
            "document_digest": _sha256(digest_payload),
            "sync_run_id": checked_report.sync_run_id,
            "executed_at": checked_report.executed_at,
            "executed_at_epoch_microseconds": _epoch_microseconds(checked_report.executed_at),
            "source_document_id": checked_report.source_document_id,
            "source_hash": checked_report.source_hash,
            "configuration_digest": checked_report.configuration_digest,
            "execution_plan_digest": checked_report.execution_plan_digest,
            "command_batch_digest": checked_report.command_batch_digest,
            "schema_evidence_digest": (checked_report.execution.schema_evidence_digest),
            "first_write_evidence_digest": (checked_report.execution.first_write_evidence_digest),
            "second_write_evidence_digest": (checked_report.execution.second_write_evidence_digest),
            "first_readback_evidence_digest": (
                checked_report.execution.first_readback.evidence_digest
            ),
            "second_readback_evidence_digest": (
                checked_report.execution.second_readback.evidence_digest
            ),
            "idempotency_evidence_digest": (checked_report.execution.idempotency.evidence_digest),
            "report_payload": report_payload,
        }
        return cls.model_validate(model_payload)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        """Require exact report reconstruction and immutable digest binding."""
        if self.document_id != f"CUSTOMER_GRAPH_SANDBOX:{self.sync_run_id}":
            _raise_model_error(
                "customer_graph_evidence_document_id_invalid",
                "document_id must be derived from sync_run_id",
            )
        if self.executed_at_epoch_microseconds != _epoch_microseconds(self.executed_at):
            _raise_model_error(
                "customer_graph_evidence_execution_sort_key_invalid",
                "executed_at_epoch_microseconds must match executed_at",
            )
        try:
            report = CustomerGraphSandboxReport.model_validate_json(
                _canonical_json(self.report_payload)
            )
        except ValidationError as error:
            raise PydanticCustomError(
                "customer_graph_evidence_report_invalid",
                "report_payload must contain valid sandbox report evidence",
            ) from error
        if _report_binding(report) != _document_binding(self):
            _raise_model_error(
                "customer_graph_evidence_report_binding_invalid",
                "top-level graph evidence must match the embedded report",
            )
        if self.document_digest != _document_digest(self):
            _raise_model_error(
                "customer_graph_evidence_digest_invalid",
                "document_digest does not match graph-evidence contents",
            )
        return self

    def to_mongo_document(self) -> dict[str, object]:
        """Return one exact JSON-compatible MongoDB aggregate document."""
        payload = self.model_dump(mode="json")
        payload["executed_at"] = _utc_text(self.executed_at)
        document: dict[str, object] = {
            "_id": self.document_id,
            **{str(key): value for key, value in payload.items()},
        }
        try:
            encoded = BSON.encode(document)
        except InvalidDocument as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID
            ) from error
        if len(encoded) > _MAX_BSON_DOCUMENT_BYTES:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_TOO_LARGE)
        return document

    @classmethod
    def from_mongo_document(
        cls,
        document: Mapping[str, object],
    ) -> Self:
        """Reconstruct and validate one exact persisted aggregate."""
        normalized = _string_keyed_document(document)
        stored_id = normalized.get("_id")
        payload = {key: value for key, value in normalized.items() if key != "_id"}
        if not isinstance(stored_id, str) or payload.get("document_id") != stored_id:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID)
        try:
            evidence = cls.model_validate_json(_canonical_json(payload))
        except ValidationError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID
            ) from error
        if evidence.to_mongo_document() != normalized:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID)
        return evidence


class CustomerGraphEvidencePersistenceReceipt(CanonicalBaseModel):
    """Immutable receipt for one exact Platform MongoDB persistence outcome."""

    status: CustomerGraphEvidencePersistenceStatus
    document_id: CanonicalIdentifier
    report_digest: Sha256Digest
    document_digest: Sha256Digest


class _GraphEvidenceUpdateResult(Protocol):
    """Narrow update result surface consumed by the repository."""

    @property
    def upserted_id(self) -> object | None:
        """Return the inserted identifier, when an insert occurred."""
        ...

    @property
    def matched_count(self) -> int:
        """Return the number of matched documents."""
        ...

    @property
    def modified_count(self) -> int:
        """Return the number of modified documents."""
        ...


class _GraphEvidenceCollectionPort(Protocol):
    """Narrow asynchronous collection boundary used by the repository."""

    async def create_indexes(self, indexes: list[IndexModel]) -> list[str]:
        """Create the fixed code-owned graph-evidence indexes."""
        ...

    async def update_one(
        self,
        filter_document: dict[str, object],
        update_document: dict[str, object],
        /,
        *,
        upsert: bool,
    ) -> _GraphEvidenceUpdateResult:
        """Perform one deterministic immutable upsert attempt."""
        ...

    async def find_one(
        self,
        filter_document: dict[str, object],
        /,
    ) -> Mapping[str, object] | None:
        """Read one persisted graph-evidence document by exact identity."""
        ...


class _PyMongoGraphEvidenceCollection:
    """Typed adapter isolating the full PyMongo collection surface."""

    def __init__(
        self,
        collection: AsyncCollection[dict[str, object]],
    ) -> None:
        """Store the fully configured PyMongo collection."""
        self._collection = collection

    async def create_indexes(self, indexes: list[IndexModel]) -> list[str]:
        """Create code-owned indexes through the injected collection."""
        return await self._collection.create_indexes(indexes)

    async def update_one(
        self,
        filter_document: dict[str, object],
        update_document: dict[str, object],
        /,
        *,
        upsert: bool,
    ) -> _GraphEvidenceUpdateResult:
        """Execute one immutable upsert without retry orchestration."""
        return await self._collection.update_one(
            filter_document,
            update_document,
            upsert=upsert,
        )

    async def find_one(
        self,
        filter_document: dict[str, object],
        /,
    ) -> Mapping[str, object] | None:
        """Read one exact aggregate from the primary."""
        return await self._collection.find_one(filter_document)


class CustomerGraphEvidenceRepository:
    """Persist one immutable Customer graph evidence aggregate atomically."""

    def __init__(
        self,
        collection: _GraphEvidenceCollectionPort,
        *,
        operation_timeout_seconds: float,
    ) -> None:
        """Store the injected collection and bounded operation timeout."""
        if (
            not isinstance(operation_timeout_seconds, float)
            or operation_timeout_seconds < _MIN_OPERATION_TIMEOUT_SECONDS
            or operation_timeout_seconds > _MAX_OPERATION_TIMEOUT_SECONDS
        ):
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT)
        self._collection = collection
        self._operation_timeout_seconds = operation_timeout_seconds

    @classmethod
    def from_client(
        cls,
        client: AsyncMongoClient[dict[str, object]],
        *,
        database: str,
        collection: str,
        operation_timeout_seconds: float,
    ) -> Self:
        """Create the repository from one lifespan-owned AsyncMongoClient."""
        if (
            not isinstance(database, str)
            or _DATABASE_PATTERN.fullmatch(database) is None
            or not isinstance(collection, str)
            or collection.startswith("system.")
            or "$" in collection
            or _COLLECTION_PATTERN.fullmatch(collection) is None
        ):
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT)
        base_collection: AsyncCollection[dict[str, object]] = client[database][collection]
        durable_collection = base_collection.with_options(
            read_concern=ReadConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
            write_concern=WriteConcern(w="majority", j=True),
        )
        return cls(
            _PyMongoGraphEvidenceCollection(durable_collection),
            operation_timeout_seconds=operation_timeout_seconds,
        )

    async def prepare_indexes(self) -> tuple[str, ...]:
        """Create fixed indexes required for immutable evidence lookup."""
        indexes = [
            IndexModel(
                [("report_digest", ASCENDING)],
                name="ux_graph_evidence_report_digest",
                unique=True,
            ),
            IndexModel(
                [("sync_run_id", ASCENDING)],
                name="ux_graph_evidence_sync_run_id",
                unique=True,
            ),
            IndexModel(
                [("executed_at_epoch_microseconds", DESCENDING)],
                name="ix_graph_evidence_executed_at_epoch_us",
            ),
            IndexModel(
                [
                    ("executed_at_epoch_microseconds", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name=("ix_graph_evidence_executed_at_epoch_us_document_id"),
            ),
            IndexModel(
                [
                    ("source_document_id", ASCENDING),
                    ("executed_at_epoch_microseconds", DESCENDING),
                ],
                name="ix_graph_evidence_source_executed_epoch_us",
            ),
        ]
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                names = await self._collection.create_indexes(indexes)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.TIMEOUT
            ) from error
        except OperationFailure as error:
            code = (
                CustomerGraphEvidencePersistenceErrorCode.AUTH_FAILED
                if error.code in {13, 18}
                else CustomerGraphEvidencePersistenceErrorCode.INDEX_PREPARATION_FAILED
            )
            raise CustomerGraphEvidencePersistenceError(code) from error
        except PyMongoError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.INDEX_PREPARATION_FAILED
            ) from error
        expected_names = tuple(str(index.document["name"]) for index in indexes)
        returned_names = tuple(names)
        if returned_names != expected_names:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INDEX_PREPARATION_FAILED)
        return returned_names

    async def _write_once(
        self,
        *,
        filter_document: dict[str, object],
        update_document: dict[str, object],
    ) -> _GraphEvidenceUpdateResult:
        """Execute one no-retry write and preserve unknown outcomes."""
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                return await self._collection.update_one(
                    filter_document,
                    update_document,
                    upsert=True,
                )
        except asyncio.CancelledError:
            raise
        except DuplicateKeyError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.IMMUTABLE_CONFLICT
            ) from error
        except (AutoReconnect, NetworkTimeout, TimeoutError) as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
            ) from error
        except ExecutionTimeout as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.TIMEOUT
            ) from error
        except OperationFailure as error:
            code = _operation_failure_code(error, write_phase=True)
            raise CustomerGraphEvidencePersistenceError(code) from error
        except PyMongoError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED
            ) from error

    async def _read_back(
        self,
        document_id: str,
    ) -> Mapping[str, object] | None:
        """Read back from the primary without retrying a failed read."""
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                return await self._collection.find_one({"_id": document_id})
        except asyncio.CancelledError:
            raise
        except (AutoReconnect, ExecutionTimeout, NetworkTimeout, TimeoutError) as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED
            ) from error
        except OperationFailure as error:
            code = _operation_failure_code(error, write_phase=False)
            raise CustomerGraphEvidencePersistenceError(code) from error
        except PyMongoError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED
            ) from error

    async def get_by_sync_run_id(
        self,
        sync_run_id: UUID,
    ) -> CustomerGraphEvidenceDocument | None:
        """Read and validate one immutable aggregate by canonical run identity."""
        if not isinstance(sync_run_id, UUID):
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT)
        document_id = f"CUSTOMER_GRAPH_SANDBOX:{sync_run_id}"
        stored = await self._read_back(document_id)
        if stored is None:
            return None
        return CustomerGraphEvidenceDocument.from_mongo_document(stored)

    async def persist(
        self,
        evidence: CustomerGraphEvidenceDocument,
    ) -> CustomerGraphEvidencePersistenceReceipt:
        """Insert one immutable aggregate or accept an exact existing copy."""
        if not isinstance(evidence, CustomerGraphEvidenceDocument):
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.INVALID_INPUT)
        try:
            checked = CustomerGraphEvidenceDocument.model_validate(
                evidence.model_dump(mode="python")
            )
        except ValidationError as error:
            raise CustomerGraphEvidencePersistenceError(
                CustomerGraphEvidencePersistenceErrorCode.DOCUMENT_INVALID
            ) from error
        mongo_document = checked.to_mongo_document()
        filter_document: dict[str, object] = {
            "_id": checked.document_id,
            "document_digest": checked.document_digest,
        }
        update_document: dict[str, object] = {
            "$setOnInsert": mongo_document,
        }
        result = await self._write_once(
            filter_document=filter_document,
            update_document=update_document,
        )
        _validate_update_result(result, checked.document_id)
        stored_evidence = await self.get_by_sync_run_id(checked.sync_run_id)
        if stored_evidence is None:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED)
        if stored_evidence != checked:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.IMMUTABLE_CONFLICT)
        status = (
            CustomerGraphEvidencePersistenceStatus.CREATED
            if result.upserted_id == checked.document_id
            else CustomerGraphEvidencePersistenceStatus.ALREADY_PRESENT
        )
        return CustomerGraphEvidencePersistenceReceipt(
            status=status,
            document_id=checked.document_id,
            report_digest=checked.report_digest,
            document_digest=checked.document_digest,
        )


def _document_creation_payload(
    *,
    document_id: str,
    report: CustomerGraphSandboxReport,
    report_payload: dict[str, JsonValue],
) -> dict[str, object]:
    """Build the canonical JSON-compatible unsigned aggregate payload."""
    return {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "evidence_type": _EVIDENCE_TYPE,
        "document_id": document_id,
        "report_digest": report.report_digest,
        "sync_run_id": str(report.sync_run_id),
        "executed_at": _utc_text(report.executed_at),
        "executed_at_epoch_microseconds": _epoch_microseconds(report.executed_at),
        "source_document_id": report.source_document_id,
        "source_hash": report.source_hash,
        "configuration_digest": report.configuration_digest,
        "execution_plan_digest": report.execution_plan_digest,
        "command_batch_digest": report.command_batch_digest,
        "schema_evidence_digest": report.execution.schema_evidence_digest,
        "first_write_evidence_digest": (report.execution.first_write_evidence_digest),
        "second_write_evidence_digest": (report.execution.second_write_evidence_digest),
        "first_readback_evidence_digest": (report.execution.first_readback.evidence_digest),
        "second_readback_evidence_digest": (report.execution.second_readback.evidence_digest),
        "idempotency_evidence_digest": (report.execution.idempotency.evidence_digest),
        "report_payload": report_payload,
    }


def _document_unsigned_payload(
    evidence: CustomerGraphEvidenceDocument,
) -> dict[str, object]:
    """Build the unsigned JSON-compatible document payload."""
    payload = evidence.model_dump(mode="json", exclude={"document_digest"})
    payload["executed_at"] = _utc_text(evidence.executed_at)
    return {str(key): value for key, value in payload.items()}


def _document_digest(evidence: CustomerGraphEvidenceDocument) -> str:
    """Calculate immutable aggregate integrity."""
    return _sha256(_document_unsigned_payload(evidence))


def _utc_text(value: datetime) -> str:
    """Encode one UTC timestamp with fixed microsecond precision."""
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _epoch_microseconds(value: datetime) -> int:
    """Return an exact non-floating UTC ordering key."""
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return (
        delta.days * _SECONDS_PER_DAY * _MICROSECONDS_PER_SECOND
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )


def _report_binding(report: CustomerGraphSandboxReport) -> tuple[object, ...]:
    """Return all report fields duplicated at the aggregate top level."""
    return (
        report.report_digest,
        report.sync_run_id,
        report.executed_at,
        report.source_document_id,
        report.source_hash,
        report.configuration_digest,
        report.execution_plan_digest,
        report.command_batch_digest,
        report.execution.schema_evidence_digest,
        report.execution.first_write_evidence_digest,
        report.execution.second_write_evidence_digest,
        report.execution.first_readback.evidence_digest,
        report.execution.second_readback.evidence_digest,
        report.execution.idempotency.evidence_digest,
    )


def _document_binding(
    evidence: CustomerGraphEvidenceDocument,
) -> tuple[object, ...]:
    """Return aggregate fields that must match the embedded report."""
    return (
        evidence.report_digest,
        evidence.sync_run_id,
        evidence.executed_at,
        evidence.source_document_id,
        evidence.source_hash,
        evidence.configuration_digest,
        evidence.execution_plan_digest,
        evidence.command_batch_digest,
        evidence.schema_evidence_digest,
        evidence.first_write_evidence_digest,
        evidence.second_write_evidence_digest,
        evidence.first_readback_evidence_digest,
        evidence.second_readback_evidence_digest,
        evidence.idempotency_evidence_digest,
    )


def _operation_failure_code(
    error: OperationFailure,
    *,
    write_phase: bool,
) -> CustomerGraphEvidencePersistenceErrorCode:
    """Map one server failure without exposing server text."""
    if error.code in _AUTH_ERROR_CODES:
        return CustomerGraphEvidencePersistenceErrorCode.AUTH_FAILED
    if error.code == _SERVER_TIMEOUT_ERROR_CODE:
        return CustomerGraphEvidencePersistenceErrorCode.TIMEOUT
    if write_phase and (
        error.code == _WRITE_CONCERN_FAILED_ERROR_CODE
        or error.has_error_label("RetryableWriteError")
        or error.has_error_label("UnknownTransactionCommitResult")
    ):
        return CustomerGraphEvidencePersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
    return (
        CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED
        if write_phase
        else CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED
    )


def _validate_update_result(
    result: _GraphEvidenceUpdateResult,
    expected_document_id: str,
) -> None:
    """Require one exact immutable insert or exact replay outcome."""
    if result.modified_count != 0:
        _raise_error(CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED)
    if result.upserted_id is None:
        if result.matched_count != 1:
            _raise_error(CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED)
        return
    if result.matched_count != 0 or result.upserted_id != expected_document_id:
        _raise_error(CustomerGraphEvidencePersistenceErrorCode.WRITE_FAILED)


def _string_keyed_document(
    document: Mapping[str, object],
) -> dict[str, object]:
    """Reject non-string BSON keys before exact comparison."""
    if not all(isinstance(key, str) for key in document):
        _raise_error(CustomerGraphEvidencePersistenceErrorCode.READBACK_FAILED)
    return {str(key): value for key, value in document.items()}
