"""Read-only Platform MongoDB queries for Customer graph evidence."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Never, Protocol, Self
from uuid import UUID

from pydantic import Field, JsonValue, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from pymongo import DESCENDING, AsyncMongoClient, ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import (
    AutoReconnect,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    PyMongoError,
)
from pymongo.read_concern import ReadConcern

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.graph.evidence_repository import (
    CustomerGraphEvidenceDocument,
    CustomerGraphEvidencePersistenceError,
)
from return_platform.data_platform.graph.sandbox import CustomerGraphSandboxReport

__all__ = [
    "CustomerGraphEvidenceCursor",
    "CustomerGraphEvidenceFullView",
    "CustomerGraphEvidenceInspectionPage",
    "CustomerGraphEvidenceQueryError",
    "CustomerGraphEvidenceQueryErrorCode",
    "CustomerGraphEvidenceQueryRepository",
    "CustomerGraphEvidenceSummary",
    "decode_customer_graph_evidence_cursor",
    "encode_customer_graph_evidence_cursor",
]

_CURSOR_VERSION: Final = "1.0"
_CURSOR_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_DOCUMENT_ID_PATTERN: Final = re.compile(
    r"^CUSTOMER_GRAPH_SANDBOX:[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_DATABASE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_COLLECTION_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$")
_MIN_OPERATION_TIMEOUT_SECONDS: Final = 0.05
_MAX_OPERATION_TIMEOUT_SECONDS: Final = 300.0
_MAX_PAGE_SIZE: Final = 100
_AUTH_ERROR_CODES: Final = frozenset({13, 18})
_SERVER_TIMEOUT_ERROR_CODE: Final = 50
_EVIDENCE_TYPE: Final[Literal["CUSTOMER_GRAPH_SANDBOX_RUN"]] = "CUSTOMER_GRAPH_SANDBOX_RUN"
_EVIDENCE_CLASSIFICATION: Final[Literal["SANDBOX_VALIDATED"]] = "SANDBOX_VALIDATED"

PageSize = Annotated[int, Field(strict=True, ge=1, le=_MAX_PAGE_SIZE)]
EpochMicroseconds = Annotated[int, Field(strict=True, ge=0)]

_SUMMARY_PROJECTION: Final[dict[str, int]] = {
    "_id": 1,
    "schema_version": 1,
    "evidence_type": 1,
    "report_digest": 1,
    "document_digest": 1,
    "sync_run_id": 1,
    "executed_at": 1,
    "executed_at_epoch_microseconds": 1,
    "source_document_id": 1,
    "source_hash": 1,
    "configuration_digest": 1,
    "execution_plan_digest": 1,
    "command_batch_digest": 1,
    "report_payload.evidence_classification": 1,
    "report_payload.expected_customer_count": 1,
    "report_payload.expected_customer_account_count": 1,
    "report_payload.expected_relationship_count": 1,
    "report_payload.execution.idempotency.idempotent": 1,
}
_SUMMARY_SORT: Final[tuple[tuple[str, int], ...]] = (
    ("executed_at_epoch_microseconds", DESCENDING),
    ("_id", DESCENDING),
)


class CustomerGraphEvidenceQueryErrorCode(StrEnum):
    """Stable safe read-only graph-evidence query error codes."""

    INVALID_INPUT = "INVALID_INPUT"
    CURSOR_INVALID = "CURSOR_INVALID"
    AUTH_FAILED = "AUTH_FAILED"
    TIMEOUT = "TIMEOUT"
    QUERY_FAILED = "QUERY_FAILED"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"


_SAFE_MESSAGES: Final = {
    CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT: ("Graph-evidence query input is invalid."),
    CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID: (
        "Graph-evidence pagination cursor is invalid."
    ),
    CustomerGraphEvidenceQueryErrorCode.AUTH_FAILED: (
        "Platform MongoDB authentication or authorization failed."
    ),
    CustomerGraphEvidenceQueryErrorCode.TIMEOUT: (
        "Graph-evidence query exceeded its bounded timeout."
    ),
    CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED: ("Graph-evidence query failed."),
    CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID: (
        "Stored graph evidence failed integrity validation."
    ),
}


class CustomerGraphEvidenceQueryError(RuntimeError):
    """Safe graph-evidence query error with a stable public code."""

    def __init__(self, code: CustomerGraphEvidenceQueryErrorCode) -> None:
        """Initialize one safe graph-evidence query error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: CustomerGraphEvidenceQueryErrorCode) -> Never:
    """Raise one safe graph-evidence query error."""
    raise CustomerGraphEvidenceQueryError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one input-hidden Pydantic model error."""
    raise PydanticCustomError(error_type, message)


class CustomerGraphEvidenceCursor(CanonicalBaseModel):
    """Canonical seek-pagination position."""

    version: Literal["1.0"]
    executed_at_epoch_microseconds: EpochMicroseconds
    document_id: CanonicalIdentifier


def _canonical_cursor_bytes(
    payload: CustomerGraphEvidenceCursor,
) -> bytes:
    """Serialize one cursor payload canonically."""
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_customer_graph_evidence_cursor(
    *,
    executed_at_epoch_microseconds: int,
    document_id: str,
) -> str:
    """Encode one opaque deterministic seek cursor."""
    try:
        payload = CustomerGraphEvidenceCursor(
            version=_CURSOR_VERSION,
            executed_at_epoch_microseconds=executed_at_epoch_microseconds,
            document_id=document_id,
        )
    except ValidationError as error:
        raise CustomerGraphEvidenceQueryError(
            CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID
        ) from error
    encoded = base64.urlsafe_b64encode(_canonical_cursor_bytes(payload))
    return encoded.rstrip(b"=").decode("ascii")


def decode_customer_graph_evidence_cursor(
    cursor: str | None,
) -> CustomerGraphEvidenceCursor | None:
    """Decode and strictly validate one canonical seek cursor."""
    if cursor is None:
        return None
    if not isinstance(cursor, str) or _CURSOR_PATTERN.fullmatch(cursor) is None:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID)
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = CustomerGraphEvidenceCursor.model_validate_json(decoded)
    except (binascii.Error, UnicodeEncodeError, ValidationError) as error:
        raise CustomerGraphEvidenceQueryError(
            CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID
        ) from error
    if decoded != _canonical_cursor_bytes(payload):
        _raise_error(CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID)
    if _DOCUMENT_ID_PATTERN.fullmatch(payload.document_id) is None:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID)
    return payload


class CustomerGraphEvidenceSummary(CanonicalBaseModel):
    """Safe bounded graph-evidence summary for operator inspection."""

    schema_version: VersionReference
    evidence_type: Literal["CUSTOMER_GRAPH_SANDBOX_RUN"]
    document_id: CanonicalIdentifier
    report_digest: Sha256Digest
    document_digest: Sha256Digest
    sync_run_id: UUID
    executed_at: UtcDateTime
    executed_at_epoch_microseconds: EpochMicroseconds
    source_document_id: CanonicalIdentifier
    source_hash: Sha256Digest
    configuration_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    command_batch_digest: Sha256Digest
    evidence_classification: Literal["SANDBOX_VALIDATED"]
    expected_customer_count: Annotated[int, Field(strict=True, ge=0)]
    expected_customer_account_count: Annotated[int, Field(strict=True, ge=0)]
    expected_relationship_count: Annotated[int, Field(strict=True, ge=0)]
    idempotent: bool

    @model_validator(mode="after")
    def validate_summary_binding(self) -> Self:
        """Require canonical identity and timestamp ordering-key binding."""
        if self.document_id != f"CUSTOMER_GRAPH_SANDBOX:{self.sync_run_id}":
            _raise_model_error(
                "graph_evidence_identity_mismatch",
                "Graph-evidence identity fields do not match.",
            )
        if self.executed_at_epoch_microseconds != _epoch_microseconds(self.executed_at):
            _raise_model_error(
                "graph_evidence_timestamp_mismatch",
                "Graph-evidence timestamp fields do not match.",
            )
        return self

    @classmethod
    def from_document(
        cls,
        document: CustomerGraphEvidenceDocument,
    ) -> Self:
        """Create a safe summary from one validated full document."""
        try:
            report = CustomerGraphSandboxReport.model_validate_json(
                json.dumps(
                    document.report_payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except ValidationError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
            ) from error
        try:
            return cls(
                schema_version=document.schema_version,
                evidence_type=document.evidence_type,
                document_id=document.document_id,
                report_digest=document.report_digest,
                document_digest=document.document_digest,
                sync_run_id=document.sync_run_id,
                executed_at=document.executed_at,
                executed_at_epoch_microseconds=(document.executed_at_epoch_microseconds),
                source_document_id=document.source_document_id,
                source_hash=document.source_hash,
                configuration_digest=document.configuration_digest,
                execution_plan_digest=document.execution_plan_digest,
                command_batch_digest=document.command_batch_digest,
                evidence_classification=report.evidence_classification,
                expected_customer_count=report.expected_customer_count,
                expected_customer_account_count=(report.expected_customer_account_count),
                expected_relationship_count=(report.expected_relationship_count),
                idempotent=report.execution.idempotency.idempotent,
            )
        except ValidationError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
            ) from error

    @classmethod
    def from_projection(cls, document: Mapping[str, object]) -> Self:
        """Validate one fixed MongoDB summary projection."""
        report_payload = _required_mapping(document, "report_payload")
        execution = _required_mapping(report_payload, "execution")
        idempotency = _required_mapping(execution, "idempotency")
        try:
            return cls(
                schema_version=_required_string(document, "schema_version"),
                evidence_type=_required_evidence_type(
                    document,
                    "evidence_type",
                ),
                document_id=_required_string(document, "_id"),
                report_digest=_required_string(document, "report_digest"),
                document_digest=_required_string(document, "document_digest"),
                sync_run_id=_required_uuid(document, "sync_run_id"),
                executed_at=_required_datetime(document, "executed_at"),
                executed_at_epoch_microseconds=_required_integer(
                    document,
                    "executed_at_epoch_microseconds",
                ),
                source_document_id=_required_string(
                    document,
                    "source_document_id",
                ),
                source_hash=_required_string(document, "source_hash"),
                configuration_digest=_required_string(
                    document,
                    "configuration_digest",
                ),
                execution_plan_digest=_required_string(
                    document,
                    "execution_plan_digest",
                ),
                command_batch_digest=_required_string(
                    document,
                    "command_batch_digest",
                ),
                evidence_classification=(
                    _required_evidence_classification(
                        report_payload,
                        "evidence_classification",
                    )
                ),
                expected_customer_count=_required_integer(
                    report_payload,
                    "expected_customer_count",
                ),
                expected_customer_account_count=_required_integer(
                    report_payload,
                    "expected_customer_account_count",
                ),
                expected_relationship_count=_required_integer(
                    report_payload,
                    "expected_relationship_count",
                ),
                idempotent=_required_boolean(idempotency, "idempotent"),
            )
        except ValidationError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
            ) from error


class CustomerGraphEvidenceFullView(CanonicalBaseModel):
    """Complete validated evidence view for developer inspection."""

    summary: CustomerGraphEvidenceSummary
    schema_evidence_digest: Sha256Digest
    first_write_evidence_digest: Sha256Digest
    second_write_evidence_digest: Sha256Digest
    first_readback_evidence_digest: Sha256Digest
    second_readback_evidence_digest: Sha256Digest
    idempotency_evidence_digest: Sha256Digest
    report_payload: dict[str, JsonValue]

    @classmethod
    def from_document(
        cls,
        document: CustomerGraphEvidenceDocument,
    ) -> Self:
        """Create a complete view from one validated evidence document."""
        return cls(
            summary=CustomerGraphEvidenceSummary.from_document(document),
            schema_evidence_digest=document.schema_evidence_digest,
            first_write_evidence_digest=document.first_write_evidence_digest,
            second_write_evidence_digest=document.second_write_evidence_digest,
            first_readback_evidence_digest=(document.first_readback_evidence_digest),
            second_readback_evidence_digest=(document.second_readback_evidence_digest),
            idempotency_evidence_digest=(document.idempotency_evidence_digest),
            report_payload=dict(document.report_payload),
        )


class CustomerGraphEvidenceInspectionPage(CanonicalBaseModel):
    """One bounded page of graph-evidence summaries."""

    items: tuple[CustomerGraphEvidenceSummary, ...]
    next_cursor: str | None
    has_more: bool
    page_size: PageSize


class _GraphEvidenceQueryCollectionPort(Protocol):
    """Narrow asynchronous read-only collection boundary."""

    async def find_one(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int] | None,
    ) -> Mapping[str, object] | None:
        """Read one exact document through a code-owned filter."""
        ...

    async def find_many(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int],
        sort: list[tuple[str, int]],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Read one bounded sorted page through a code-owned filter."""
        ...


class _PyMongoGraphEvidenceQueryCollection:
    """Typed adapter isolating the PyMongo asynchronous query surface."""

    def __init__(
        self,
        collection: AsyncCollection[dict[str, object]],
    ) -> None:
        """Store one read-configured collection."""
        self._collection = collection

    async def find_one(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int] | None,
    ) -> Mapping[str, object] | None:
        """Read one exact document from the primary."""
        return await self._collection.find_one(
            filter_document,
            projection=projection,
        )

    async def find_many(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int],
        sort: list[tuple[str, int]],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Read one bounded seek page from the primary."""
        cursor = self._collection.find(
            filter_document,
            projection=projection,
            sort=sort,
            limit=limit,
        )
        documents = await cursor.to_list(length=limit)
        return tuple(documents)


class CustomerGraphEvidenceQueryRepository:
    """Execute fixed bounded read-only graph-evidence queries."""

    def __init__(
        self,
        collection: _GraphEvidenceQueryCollectionPort,
        *,
        operation_timeout_seconds: float,
    ) -> None:
        """Store the collection and bounded query timeout."""
        if (
            not isinstance(operation_timeout_seconds, float)
            or operation_timeout_seconds < _MIN_OPERATION_TIMEOUT_SECONDS
            or operation_timeout_seconds > _MAX_OPERATION_TIMEOUT_SECONDS
        ):
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
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
        """Create a read-only repository from a lifespan-owned client."""
        if (
            not isinstance(database, str)
            or _DATABASE_PATTERN.fullmatch(database) is None
            or not isinstance(collection, str)
            or collection.startswith("system.")
            or "$" in collection
            or _COLLECTION_PATTERN.fullmatch(collection) is None
        ):
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
        base_collection: AsyncCollection[dict[str, object]] = client[database][collection]
        read_collection = base_collection.with_options(
            read_concern=ReadConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
        )
        return cls(
            _PyMongoGraphEvidenceQueryCollection(read_collection),
            operation_timeout_seconds=operation_timeout_seconds,
        )

    async def _find_one(
        self,
        filter_document: dict[str, object],
        *,
        projection: dict[str, int] | None = None,
    ) -> Mapping[str, object] | None:
        """Execute one bounded exact lookup without retry orchestration."""
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                return await self._collection.find_one(
                    filter_document,
                    projection=projection,
                )
        except asyncio.CancelledError:
            raise
        except (ExecutionTimeout, NetworkTimeout, TimeoutError) as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.TIMEOUT
            ) from error
        except AutoReconnect as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED
            ) from error
        except OperationFailure as error:
            raise CustomerGraphEvidenceQueryError(_operation_failure_code(error)) from error
        except PyMongoError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED
            ) from error

    async def _find_many(
        self,
        filter_document: dict[str, object],
        *,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Execute one bounded sorted seek query without retries."""
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                return await self._collection.find_many(
                    filter_document,
                    projection=dict(_SUMMARY_PROJECTION),
                    sort=list(_SUMMARY_SORT),
                    limit=limit,
                )
        except asyncio.CancelledError:
            raise
        except (ExecutionTimeout, NetworkTimeout, TimeoutError) as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.TIMEOUT
            ) from error
        except AutoReconnect as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED
            ) from error
        except OperationFailure as error:
            raise CustomerGraphEvidenceQueryError(_operation_failure_code(error)) from error
        except PyMongoError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED
            ) from error

    async def _validated_document(
        self,
        filter_document: dict[str, object],
    ) -> CustomerGraphEvidenceDocument | None:
        """Read and integrity-check one full immutable document."""
        stored = await self._find_one(filter_document)
        if stored is None:
            return None
        try:
            return CustomerGraphEvidenceDocument.from_mongo_document(stored)
        except CustomerGraphEvidencePersistenceError as error:
            raise CustomerGraphEvidenceQueryError(
                CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
            ) from error

    async def get_by_document_id(
        self,
        document_id: str,
    ) -> CustomerGraphEvidenceDocument | None:
        """Read one full evidence document by exact canonical ID."""
        if not isinstance(document_id, str) or _DOCUMENT_ID_PATTERN.fullmatch(document_id) is None:
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
        return await self._validated_document({"_id": document_id})

    async def get_by_sync_run_id(
        self,
        sync_run_id: UUID,
    ) -> CustomerGraphEvidenceDocument | None:
        """Read one full evidence document by exact sync-run identity."""
        if not isinstance(sync_run_id, UUID):
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
        return await self.get_by_document_id(f"CUSTOMER_GRAPH_SANDBOX:{sync_run_id}")

    async def get_by_report_digest(
        self,
        report_digest: str,
    ) -> CustomerGraphEvidenceDocument | None:
        """Read one full evidence document by exact report digest."""
        if not isinstance(report_digest, str) or _SHA256_PATTERN.fullmatch(report_digest) is None:
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
        return await self._validated_document({"report_digest": report_digest})

    async def list_summaries(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> CustomerGraphEvidenceInspectionPage:
        """List one bounded newest-first page using seek pagination."""
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size < 1
            or page_size > _MAX_PAGE_SIZE
        ):
            _raise_error(CustomerGraphEvidenceQueryErrorCode.INVALID_INPUT)
        position = decode_customer_graph_evidence_cursor(cursor)
        filter_document = _list_filter(position)
        documents = await self._find_many(
            filter_document,
            limit=page_size + 1,
        )
        has_more = len(documents) > page_size
        selected = documents[:page_size]
        items = tuple(
            CustomerGraphEvidenceSummary.from_projection(document) for document in selected
        )
        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = encode_customer_graph_evidence_cursor(
                executed_at_epoch_microseconds=(last_item.executed_at_epoch_microseconds),
                document_id=last_item.document_id,
            )
        return CustomerGraphEvidenceInspectionPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            page_size=page_size,
        )


def _list_filter(
    cursor: CustomerGraphEvidenceCursor | None,
) -> dict[str, object]:
    """Build the only approved seek-pagination filter."""
    if cursor is None:
        return {}
    return {
        "$or": [
            {"executed_at_epoch_microseconds": {"$lt": cursor.executed_at_epoch_microseconds}},
            {
                "executed_at_epoch_microseconds": (cursor.executed_at_epoch_microseconds),
                "_id": {"$lt": cursor.document_id},
            },
        ]
    }


def _operation_failure_code(
    error: OperationFailure,
) -> CustomerGraphEvidenceQueryErrorCode:
    """Map one MongoDB server failure to a safe query code."""
    if error.code in _AUTH_ERROR_CODES:
        return CustomerGraphEvidenceQueryErrorCode.AUTH_FAILED
    if error.code == _SERVER_TIMEOUT_ERROR_CODE:
        return CustomerGraphEvidenceQueryErrorCode.TIMEOUT
    return CustomerGraphEvidenceQueryErrorCode.QUERY_FAILED


def _required_mapping(
    document: Mapping[str, object],
    name: str,
) -> dict[str, object]:
    """Read one required string-keyed nested mapping."""
    value = document.get(name)
    if not isinstance(value, Mapping):
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
        normalized[key] = item
    return normalized


def _required_string(document: Mapping[str, object], name: str) -> str:
    """Read one required non-empty string."""
    value = document.get(name)
    if not isinstance(value, str) or not value:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return value


def _required_evidence_type(
    document: Mapping[str, object],
    name: str,
) -> Literal["CUSTOMER_GRAPH_SANDBOX_RUN"]:
    """Read the only supported graph-evidence type."""
    value = _required_string(document, name)
    if value != _EVIDENCE_TYPE:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return _EVIDENCE_TYPE


def _required_evidence_classification(
    document: Mapping[str, object],
    name: str,
) -> Literal["SANDBOX_VALIDATED"]:
    """Read the only supported evidence classification."""
    value = _required_string(document, name)
    if value != _EVIDENCE_CLASSIFICATION:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return _EVIDENCE_CLASSIFICATION


def _required_integer(document: Mapping[str, object], name: str) -> int:
    """Read one required non-negative strict integer."""
    value = document.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return value


def _required_boolean(document: Mapping[str, object], name: str) -> bool:
    """Read one required strict boolean."""
    value = document.get(name)
    if not isinstance(value, bool):
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return value


def _required_uuid(document: Mapping[str, object], name: str) -> UUID:
    """Read one required canonical UUID string."""
    value = _required_string(document, name)
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise CustomerGraphEvidenceQueryError(
            CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
        ) from error
    if str(parsed) != value:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return parsed


def _required_datetime(
    document: Mapping[str, object],
    name: str,
) -> datetime:
    """Read one required timezone-aware UTC timestamp string."""
    value = _required_string(document, name)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CustomerGraphEvidenceQueryError(
            CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    utc_value = parsed.astimezone(UTC)
    canonical = utc_value.isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )
    if value != canonical:
        _raise_error(CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)
    return utc_value


def _epoch_microseconds(value: datetime) -> int:
    """Return an exact non-floating UTC ordering key."""
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400 * 1_000_000 + delta.seconds * 1_000_000 + delta.microseconds
