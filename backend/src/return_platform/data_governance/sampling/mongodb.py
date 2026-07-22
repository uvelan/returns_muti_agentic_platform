"""Catalog-authorized, bounded MongoDB data sampling."""

import asyncio
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
)

from return_platform.data_governance.sampling.authorization import (
    authorize_sampling_asset,
)
from return_platform.data_governance.sampling.contracts import (
    AssetSample,
    SampledRow,
)
from return_platform.data_governance.sampling.sanitization import (
    SamplingSanitizationError,
    normalize_redaction_fields,
    sanitize_sample_row,
)
from return_platform.shared.contracts import DependencyErrorCode
from return_platform.shared.governance import (
    AssetCatalog,
    DataStoreType,
)

MongoDocument = dict[str, Any]

_OPERATION_COMMENT: Final = "return-platform-data-sampling"

_AUTHENTICATION_FAILURE_CODE: Final = 18
_AUTHORIZATION_FAILURE_CODE: Final = 13


class MongoDBSamplingError(RuntimeError):
    """Safe MongoDB sampling failure exposed outside this module."""

    code: DependencyErrorCode

    def __init__(
        self,
        *,
        code: DependencyErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _validate_timeout(
    timeout_seconds: float,
) -> None:
    """Require a finite positive sampling timeout."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            "MongoDB sampling timeout must be a finite positive number.",
        )


def _get_configured_database(
    *,
    client: AsyncMongoClient[MongoDocument],
    expected_database_name: str,
) -> AsyncDatabase[MongoDocument]:
    """Resolve and verify the database encoded in the MongoDB URI."""

    try:
        database = client.get_default_database()
    except ConfigurationError:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.UNINITIALIZED,
            message="MongoDB database is not configured.",
        ) from None

    if database.name != expected_database_name:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.UNINITIALIZED,
            message=("Configured MongoDB database does not match the catalog asset."),
        )

    return database


def _map_pymongo_error(
    error: PyMongoError,
) -> MongoDBSamplingError:
    """Map a raw PyMongo failure to a safe public error."""

    if error.timeout:
        return MongoDBSamplingError(
            code=DependencyErrorCode.TIMEOUT,
            message="MongoDB sampling timed out.",
        )

    if isinstance(
        error,
        OperationFailure,
    ):
        if error.code == _AUTHENTICATION_FAILURE_CODE:
            return MongoDBSamplingError(
                code=DependencyErrorCode.AUTH_FAILED,
                message="MongoDB authentication was rejected.",
            )

        if error.code == _AUTHORIZATION_FAILURE_CODE:
            return MongoDBSamplingError(
                code=DependencyErrorCode.QUERY_FAILED,
                message="MongoDB sampling access was not authorized.",
            )

        return MongoDBSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="MongoDB sampling query failed.",
        )

    if isinstance(
        error,
        ConnectionFailure,
    ):
        return MongoDBSamplingError(
            code=DependencyErrorCode.CONNECTION_REFUSED,
            message="MongoDB is unavailable.",
        )

    return MongoDBSamplingError(
        code=DependencyErrorCode.UNKNOWN_ERROR,
        message="MongoDB sampling failed.",
    )


def _sanitize_documents(
    *,
    raw_documents: list[MongoDocument],
    normalized_redaction_fields: frozenset[str],
    max_rows: int,
) -> tuple[SampledRow, ...]:
    """Sanitize sampled documents and enforce the authorized limit."""

    if len(raw_documents) > max_rows:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message=("MongoDB returned more documents than the authorized sampling limit."),
        )

    rows: list[SampledRow] = []

    try:
        for raw_document in raw_documents:
            if not isinstance(
                raw_document,
                Mapping,
            ):
                raise SamplingSanitizationError(
                    "MongoDB returned a malformed sample document.",
                )

            rows.append(
                sanitize_sample_row(
                    row=raw_document,
                    normalized_redaction_fields=(normalized_redaction_fields),
                ),
            )
    except SamplingSanitizationError:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="MongoDB returned invalid sample data.",
        ) from None

    return tuple(
        rows,
    )


async def _fetch_mongodb_sample(
    *,
    database: AsyncDatabase[MongoDocument],
    collection_name: str,
    max_rows: int,
    max_time_ms: int,
    normalized_redaction_fields: frozenset[str],
) -> tuple[SampledRow, ...]:
    """Execute one bounded, read-only MongoDB find operation."""

    collection = database.get_collection(
        collection_name,
    )

    try:
        cursor = collection.find(
            filter={},
            limit=max_rows + 1,
            batch_size=max_rows + 1,
            max_time_ms=max_time_ms,
            comment=_OPERATION_COMMENT,
        )

        async with cursor:
            raw_documents = await cursor.to_list(
                length=max_rows + 1,
            )
    except PyMongoError as error:
        raise _map_pymongo_error(
            error,
        ) from None

    return _sanitize_documents(
        raw_documents=raw_documents,
        normalized_redaction_fields=(normalized_redaction_fields),
        max_rows=max_rows,
    )


async def get_mongodb_sample(
    *,
    catalog: AssetCatalog,
    asset_id: str,
    client: AsyncMongoClient[MongoDocument],
    timeout_seconds: float,
) -> AssetSample:
    """Return a catalog-authorized and sanitized MongoDB sample."""

    _validate_timeout(
        timeout_seconds,
    )

    asset = authorize_sampling_asset(
        catalog=catalog,
        asset_id=asset_id,
        expected_store=DataStoreType.MONGODB,
    )

    database = _get_configured_database(
        client=client,
        expected_database_name=asset.database,
    )

    try:
        normalized_redaction_fields = normalize_redaction_fields(
            asset.sampling.redact_fields,
        )
    except SamplingSanitizationError:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.UNKNOWN_ERROR,
            message=("Authorized MongoDB sampling policy is invalid."),
        ) from None

    max_rows = asset.sampling.max_rows
    max_time_ms = max(
        1,
        math.ceil(
            timeout_seconds * 1_000,
        ),
    )

    try:
        async with asyncio.timeout(
            timeout_seconds,
        ):
            rows = await _fetch_mongodb_sample(
                database=database,
                collection_name=asset.object_name,
                max_rows=max_rows,
                max_time_ms=max_time_ms,
                normalized_redaction_fields=(normalized_redaction_fields),
            )
    except TimeoutError:
        raise MongoDBSamplingError(
            code=DependencyErrorCode.TIMEOUT,
            message="MongoDB sampling operation timed out.",
        ) from None

    return AssetSample(
        catalog_version=catalog.version,
        asset_id=asset.asset_id,
        store=asset.store,
        database=asset.database,
        namespace=None,
        object_name=asset.object_name,
        object_kind=asset.object_kind,
        sampled_at=datetime.now(UTC),
        row_limit=max_rows,
        ordering_guaranteed=False,
        rows=rows,
    )
