"""Read-only bounded MongoDB adapter for one Customer CDM document."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Never

from bson import BSON
from bson.errors import InvalidDocument
from pydantic import TypeAdapter, ValidationError
from pymongo.errors import (
    ConnectionFailure,
    InvalidName,
    OperationFailure,
    PyMongoError,
)

from return_platform.canonical.base import CanonicalIdentifier, UtcDateTime
from return_platform.data_platform.mapping.compiler import CompiledSourceAssetPlan
from return_platform.data_platform.mapping.contracts import SourceLifecycle
from return_platform.data_platform.mapping.normalizer import SourceDocumentEvidence
from return_platform.shared.governance import (
    AllowedOperation,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)
from return_platform.source_connectors.mongodb import fetch_one

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient


__all__ = [
    "CUSTOMER_FIND_COMMENT",
    "MAX_CUSTOMER_SOURCE_TIMEOUT_SECONDS",
    "MIN_CUSTOMER_SOURCE_TIMEOUT_SECONDS",
    "CustomerMongoSourceAdapter",
    "CustomerMongoSourceError",
    "CustomerMongoSourceErrorCode",
    "FetchedCustomerSourceDocument",
]

MIN_CUSTOMER_SOURCE_TIMEOUT_SECONDS: Final = 0.05
MAX_CUSTOMER_SOURCE_TIMEOUT_SECONDS: Final = 30.0
MAX_CUSTOMER_SOURCE_DOCUMENT_DEPTH: Final = 100
MAX_CUSTOMER_SOURCE_DOCUMENT_NODES: Final = 250_000
CUSTOMER_FIND_COMMENT: Final = "return-platform:customer-cdm:find-by-id:v1"
_CUSTOMER_SOURCE_SYSTEM: Final = "CUSTOMER_CDM"
_MONGO_AUTH_ERROR_CODES: Final = frozenset({13, 18})
_ASCII_CONTROL_LIMIT: Final = 32
_CANONICAL_IDENTIFIER_ADAPTER: Final = TypeAdapter(CanonicalIdentifier)
_UTC_DATETIME_ADAPTER: Final = TypeAdapter(UtcDateTime)

type MongoDocument = dict[str, object]
type FrozenMongoDocument = Mapping[str, object]


class CustomerMongoSourceErrorCode(StrEnum):
    """Stable safe error codes for one Customer MongoDB lookup."""

    INVALID_INPUT = "INVALID_INPUT"
    SOURCE_PLAN_INVALID = "SOURCE_PLAN_INVALID"
    SOURCE_DOCUMENT_NOT_FOUND = "SOURCE_DOCUMENT_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILED = "AUTH_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    QUERY_FAILED = "QUERY_FAILED"
    SOURCE_DOCUMENT_INVALID = "SOURCE_DOCUMENT_INVALID"


_SAFE_MESSAGES: Final = {
    CustomerMongoSourceErrorCode.INVALID_INPUT: ("Customer source lookup inputs are invalid."),
    CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID: (
        "The compiled Customer source plan is incompatible with this adapter."
    ),
    CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_NOT_FOUND: (
        "The requested Customer source document was not found."
    ),
    CustomerMongoSourceErrorCode.TIMEOUT: (
        "The Customer source lookup exceeded its bounded timeout."
    ),
    CustomerMongoSourceErrorCode.AUTH_FAILED: ("MongoDB rejected the configured source principal."),
    CustomerMongoSourceErrorCode.CONNECTION_FAILED: ("The Customer MongoDB source is unavailable."),
    CustomerMongoSourceErrorCode.QUERY_FAILED: ("The Customer source lookup failed."),
    CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID: (
        "The Customer source returned an invalid document."
    ),
}


class CustomerMongoSourceError(RuntimeError):
    """Safe public failure for one bounded Customer source lookup."""

    def __init__(self, code: CustomerMongoSourceErrorCode) -> None:
        """Initialize one bounded source-adapter error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_source_error(code: CustomerMongoSourceErrorCode) -> Never:
    """Raise one safe source-adapter error."""
    raise CustomerMongoSourceError(code)


@dataclass(frozen=True, slots=True)
class FetchedCustomerSourceDocument:
    """Immutable result of one exact governed Customer document lookup."""

    source_id: str
    catalog_asset_id: str
    database: str
    collection: str
    document: FrozenMongoDocument
    evidence: SourceDocumentEvidence


@dataclass(slots=True)
class _FreezeBudget:
    """Mutable internal source-document traversal budget."""

    nodes: int = 0

    def consume(self) -> None:
        """Consume one bounded structural node."""
        self.nodes += 1
        if self.nodes > MAX_CUSTOMER_SOURCE_DOCUMENT_NODES:
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)


def _validate_timeout_seconds(value: object) -> float:
    """Validate one strict finite bounded operation timeout."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _raise_source_error(CustomerMongoSourceErrorCode.INVALID_INPUT)
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout < MIN_CUSTOMER_SOURCE_TIMEOUT_SECONDS
        or timeout > MAX_CUSTOMER_SOURCE_TIMEOUT_SECONDS
    ):
        _raise_source_error(CustomerMongoSourceErrorCode.INVALID_INPUT)
    return timeout


def _validate_source_document_id(value: object) -> str:
    """Validate one strict canonical source-document identifier."""
    try:
        return _CANONICAL_IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise CustomerMongoSourceError(CustomerMongoSourceErrorCode.INVALID_INPUT) from exc


def _validate_observed_at(value: object) -> UtcDateTime:
    """Validate one timezone-aware observation timestamp."""
    try:
        return _UTC_DATETIME_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise CustomerMongoSourceError(CustomerMongoSourceErrorCode.INVALID_INPUT) from exc


def _validate_namespace_component(value: object) -> str:
    """Validate one configured MongoDB database or collection name."""
    if not isinstance(value, str):
        _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID)
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or "\x00" in normalized
        or any(ord(character) < _ASCII_CONTROL_LIMIT for character in normalized)
    ):
        _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID)
    return normalized


def _resolve_source_plan(
    source_plan: object,
) -> tuple[str, str, str, str]:
    """Resolve and revalidate one compiled governed Customer source plan."""
    if not isinstance(source_plan, CompiledSourceAssetPlan):
        _raise_source_error(CustomerMongoSourceErrorCode.INVALID_INPUT)

    definition = source_plan.definition
    asset = source_plan.catalog_asset
    if (
        definition.source_system != _CUSTOMER_SOURCE_SYSTEM
        or definition.lifecycle is not SourceLifecycle.ACTIVE
        or not definition.required_for_sync
        or definition.catalog_asset_id != asset.asset_id
        or asset.ownership is not OwnershipClass.SOURCE_SYSTEM
        or not asset.authoritative
        or asset.store is not DataStoreType.MONGODB
        or asset.object_kind is not ObjectKind.COLLECTION
        or set(asset.allowed_operations) != {AllowedOperation.READ}
        or asset.namespace is not None
    ):
        _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID)

    database = _validate_namespace_component(asset.database)
    collection = _validate_namespace_component(asset.object_name)
    return definition.source_id, asset.asset_id, database, collection


def _freeze_source_value(
    value: object,
    *,
    budget: _FreezeBudget,
    active_container_ids: set[int],
    depth: int,
) -> object:
    """Detach and recursively freeze one BSON-compatible source value."""
    budget.consume()
    if depth > MAX_CUSTOMER_SOURCE_DOCUMENT_DEPTH:
        _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)
        active_container_ids.add(container_id)
        try:
            frozen: dict[str, object] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)
                frozen[key] = _freeze_source_value(
                    child,
                    budget=budget,
                    active_container_ids=active_container_ids,
                    depth=depth + 1,
                )
            return MappingProxyType(frozen)
        finally:
            active_container_ids.remove(container_id)

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        container_id = id(value)
        if container_id in active_container_ids:
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)
        active_container_ids.add(container_id)
        try:
            return tuple(
                _freeze_source_value(
                    child,
                    budget=budget,
                    active_container_ids=active_container_ids,
                    depth=depth + 1,
                )
                for child in value
            )
        finally:
            active_container_ids.remove(container_id)

    return value


def _encode_document(document: MongoDocument) -> bytes:
    """Encode one returned document as deterministic exact BSON evidence."""
    try:
        return BSON.encode(document)
    except (InvalidDocument, OverflowError, TypeError, ValueError) as exc:
        raise CustomerMongoSourceError(
            CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID
        ) from exc


def _freeze_document(document: MongoDocument) -> FrozenMongoDocument:
    """Return one detached recursively immutable source document."""
    frozen = _freeze_source_value(
        document,
        budget=_FreezeBudget(),
        active_container_ids=set(),
        depth=0,
    )
    if not isinstance(frozen, Mapping):
        _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)
    return frozen


def _classify_pymongo_error(exc: PyMongoError) -> CustomerMongoSourceErrorCode:
    """Map one driver exception to a stable safe source error code."""
    if exc.timeout:
        return CustomerMongoSourceErrorCode.TIMEOUT
    if isinstance(exc, OperationFailure) and exc.code in _MONGO_AUTH_ERROR_CODES:
        return CustomerMongoSourceErrorCode.AUTH_FAILED
    if isinstance(exc, ConnectionFailure):
        return CustomerMongoSourceErrorCode.CONNECTION_FAILED
    return CustomerMongoSourceErrorCode.QUERY_FAILED


class CustomerMongoSourceAdapter:
    """Read one Customer CDM document through an injected async Mongo client."""

    __slots__ = ("_client", "_operation_timeout_seconds", "_server_max_time_ms")

    def __init__(
        self,
        client: AsyncMongoClient[MongoDocument],
        *,
        operation_timeout_seconds: float,
    ) -> None:
        """Initialize one adapter without taking ownership of the client."""
        if client is None:
            _raise_source_error(CustomerMongoSourceErrorCode.INVALID_INPUT)
        timeout = _validate_timeout_seconds(operation_timeout_seconds)
        self._client = client
        self._operation_timeout_seconds = timeout
        self._server_max_time_ms = max(1, math.ceil(timeout * 1000))

    async def fetch_by_id(
        self,
        *,
        source_plan: CompiledSourceAssetPlan,
        source_document_id: str,
        observed_at: UtcDateTime,
    ) -> FetchedCustomerSourceDocument:
        """Fetch one exact `_id` under server and client-side time bounds."""
        normalized_id = _validate_source_document_id(source_document_id)
        normalized_observed_at = _validate_observed_at(observed_at)
        source_id, catalog_asset_id, database, collection = _resolve_source_plan(source_plan)

        try:
            mongo_database = self._client[database]
            async with asyncio.timeout(self._operation_timeout_seconds):
                document = await fetch_one(
                    mongo_database,
                    collection_name=collection,
                    key={"_id": normalized_id},
                    max_time_ms=self._server_max_time_ms,
                    comment=CUSTOMER_FIND_COMMENT,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise CustomerMongoSourceError(CustomerMongoSourceErrorCode.TIMEOUT) from exc
        except InvalidName as exc:
            raise CustomerMongoSourceError(
                CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID
            ) from exc
        except PyMongoError as exc:
            raise CustomerMongoSourceError(_classify_pymongo_error(exc)) from exc

        if document is None:
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_NOT_FOUND)
        if not isinstance(document, dict):
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)

        returned_id = document.get("_id")
        if not isinstance(returned_id, str) or returned_id != normalized_id:
            _raise_source_error(CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID)

        frozen_document = _freeze_document(document)
        encoded = _encode_document(document)
        evidence = SourceDocumentEvidence(
            source_document_id=normalized_id,
            source_hash=hashlib.sha256(encoded).hexdigest(),
            observed_at=normalized_observed_at,
        )
        return FetchedCustomerSourceDocument(
            source_id=source_id,
            catalog_asset_id=catalog_asset_id,
            database=database,
            collection=collection,
            document=frozen_document,
            evidence=evidence,
        )
