"""MongoDB metadata collection using the lifespan-owned async client."""

import asyncio
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
)

from return_platform.data_governance.inventory.contracts import (
    MongoCollectionMetadata,
    MongoDBInventory,
    MongoIndexDirection,
    MongoIndexKeyMetadata,
    MongoIndexMetadata,
)
from return_platform.shared.contracts import DependencyErrorCode

MongoDocument = dict[str, Any]
MetadataDocument = Mapping[str, object]

_OPERATION_COMMENT: Final = "return-platform-data-governance-inventory"

_COLLECTION_FILTER: Final[dict[str, object]] = {
    "type": "collection",
}

_AUTHENTICATION_FAILURE_CODE: Final = 18
_AUTHORIZATION_FAILURE_CODE: Final = 13


class MongoDBInventoryError(RuntimeError):
    """Safe MongoDB inventory failure exposed outside this module."""

    code: DependencyErrorCode

    def __init__(
        self,
        *,
        code: DependencyErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


class _MongoMetadataMappingError(ValueError):
    """Internal failure caused by malformed MongoDB metadata."""


def _read_required_value(
    document: MetadataDocument,
    field_name: str,
) -> object:
    """Read a required metadata field."""

    try:
        return document[field_name]
    except KeyError as error:
        raise _MongoMetadataMappingError(
            f"MongoDB metadata is missing {field_name!r}.",
        ) from error


def _read_required_text(
    document: MetadataDocument,
    field_name: str,
) -> str:
    """Read an exact, nonblank metadata identifier."""

    value = _read_required_value(
        document,
        field_name,
    )

    if not isinstance(value, str):
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} is not text.",
        )

    if not value:
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} is empty.",
        )

    if value != value.strip():
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} contains surrounding whitespace.",
        )

    return value


def _read_optional_boolean(
    document: MetadataDocument,
    field_name: str,
    *,
    default: bool = False,
) -> bool:
    """Read an optional Boolean without truthiness coercion."""

    if field_name not in document:
        return default

    value = document[field_name]

    if not isinstance(value, bool):
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} is not Boolean.",
        )

    return value


def _read_optional_nonnegative_integer(
    document: MetadataDocument,
    field_name: str,
) -> int | None:
    """Read an optional nonnegative integer."""

    if field_name not in document:
        return None

    value = document[field_name]

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} is not a nonnegative integer.",
        )

    return value


def _read_required_mapping(
    document: MetadataDocument,
    field_name: str,
) -> Mapping[object, object]:
    """Read a required mapping-valued metadata field."""

    value = _read_required_value(
        document,
        field_name,
    )

    if not isinstance(value, Mapping):
        raise _MongoMetadataMappingError(
            f"MongoDB metadata field {field_name!r} is not a mapping.",
        )

    return value


def _read_index_direction(
    value: object,
) -> MongoIndexDirection:
    """Decode one supported MongoDB index direction."""

    if isinstance(value, bool):
        raise _MongoMetadataMappingError(
            "MongoDB index direction must not be Boolean.",
        )

    if isinstance(value, int):
        if value == 1:
            return 1

        if value == -1:
            return -1

        raise _MongoMetadataMappingError(
            "MongoDB index direction integer is unsupported.",
        )

    if isinstance(value, str):
        if value == "2d":
            return "2d"

        if value == "2dsphere":
            return "2dsphere"

        if value == "hashed":
            return "hashed"

        if value == "text":
            return "text"

    raise _MongoMetadataMappingError(
        "MongoDB index direction is unsupported.",
    )


def _decode_index_keys(
    index_document: MetadataDocument,
) -> tuple[MongoIndexKeyMetadata, ...]:
    """Decode index keys while preserving compound-index order."""

    key_document = _read_required_mapping(
        index_document,
        "key",
    )
    keys: list[MongoIndexKeyMetadata] = []

    for field_name, direction in key_document.items():
        if not isinstance(field_name, str):
            raise _MongoMetadataMappingError(
                "MongoDB index key field is not text.",
            )

        if not field_name:
            raise _MongoMetadataMappingError(
                "MongoDB index key field is empty.",
            )

        if field_name != field_name.strip():
            raise _MongoMetadataMappingError(
                "MongoDB index key field contains surrounding whitespace.",
            )

        keys.append(
            MongoIndexKeyMetadata(
                field_name=field_name,
                direction=_read_index_direction(
                    direction,
                ),
            ),
        )

    if not keys:
        raise _MongoMetadataMappingError(
            "MongoDB index contains no keys.",
        )

    return tuple(keys)


def _has_partial_filter(
    index_document: MetadataDocument,
) -> bool:
    """Return whether a valid partial-filter document is present."""

    field_name = "partialFilterExpression"

    if field_name not in index_document:
        return False

    value = index_document[field_name]

    if not isinstance(value, Mapping):
        raise _MongoMetadataMappingError(
            "MongoDB partial filter metadata is not a mapping.",
        )

    return True


def _decode_index(
    index_document: MetadataDocument,
) -> MongoIndexMetadata:
    """Decode one raw index document."""

    return MongoIndexMetadata(
        name=_read_required_text(
            index_document,
            "name",
        ),
        is_unique=_read_optional_boolean(
            index_document,
            "unique",
        ),
        is_sparse=_read_optional_boolean(
            index_document,
            "sparse",
        ),
        is_hidden=_read_optional_boolean(
            index_document,
            "hidden",
        ),
        expire_after_seconds=(
            _read_optional_nonnegative_integer(
                index_document,
                "expireAfterSeconds",
            )
        ),
        has_partial_filter=_has_partial_filter(
            index_document,
        ),
        keys=_decode_index_keys(
            index_document,
        ),
    )


def _validate_collection_name(
    collection_name: object,
) -> str:
    """Validate a collection name returned by listCollections."""

    if not isinstance(collection_name, str):
        raise _MongoMetadataMappingError(
            "MongoDB collection name is not text.",
        )

    if not collection_name:
        raise _MongoMetadataMappingError(
            "MongoDB collection name is empty.",
        )

    if collection_name != collection_name.strip():
        raise _MongoMetadataMappingError(
            "MongoDB collection name contains surrounding whitespace.",
        )

    return collection_name


def _validate_document_count(
    value: object,
) -> int:
    """Validate an estimated collection document count."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _MongoMetadataMappingError(
            "MongoDB estimated document count is invalid.",
        )

    return value


async def _collect_indexes(
    *,
    database: AsyncDatabase[MongoDocument],
    collection_name: str,
) -> tuple[MongoIndexMetadata, ...]:
    """Collect and deterministically order one collection's indexes."""

    collection = database.get_collection(
        collection_name,
    )
    indexes: list[MongoIndexMetadata] = []

    async with await collection.list_indexes(
        comment=_OPERATION_COMMENT,
    ) as cursor:
        async for index_document in cursor:
            indexes.append(
                _decode_index(
                    index_document,
                ),
            )

    indexes.sort(
        key=lambda index: index.name,
    )

    return tuple(indexes)


async def _collect_collection(
    *,
    database: AsyncDatabase[MongoDocument],
    collection_name: str,
) -> MongoCollectionMetadata:
    """Collect metadata for one visible user collection."""

    collection = database.get_collection(
        collection_name,
    )
    raw_document_count = await collection.estimated_document_count(
        comment=_OPERATION_COMMENT,
    )

    return MongoCollectionMetadata(
        name=collection_name,
        approximate_document_count=(
            _validate_document_count(
                raw_document_count,
            )
        ),
        indexes=await _collect_indexes(
            database=database,
            collection_name=collection_name,
        ),
    )


def _map_pymongo_error(
    error: PyMongoError,
) -> MongoDBInventoryError:
    """Map a raw PyMongo failure to a safe public error."""

    if error.timeout:
        return MongoDBInventoryError(
            code=DependencyErrorCode.TIMEOUT,
            message="MongoDB metadata retrieval timed out.",
        )

    if isinstance(error, OperationFailure):
        if error.code == _AUTHENTICATION_FAILURE_CODE:
            return MongoDBInventoryError(
                code=DependencyErrorCode.AUTH_FAILED,
                message="MongoDB authentication was rejected.",
            )

        if error.code == _AUTHORIZATION_FAILURE_CODE:
            return MongoDBInventoryError(
                code=DependencyErrorCode.QUERY_FAILED,
                message="MongoDB metadata access was not authorized.",
            )

        return MongoDBInventoryError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="MongoDB metadata query failed.",
        )

    if isinstance(error, ConnectionFailure):
        return MongoDBInventoryError(
            code=DependencyErrorCode.CONNECTION_REFUSED,
            message="MongoDB is unavailable.",
        )

    return MongoDBInventoryError(
        code=DependencyErrorCode.UNKNOWN_ERROR,
        message="MongoDB metadata retrieval failed.",
    )


def _get_configured_database(
    client: AsyncMongoClient[MongoDocument],
) -> AsyncDatabase[MongoDocument]:
    """Resolve the database encoded in the configured MongoDB URI."""

    try:
        return client.get_default_database()
    except ConfigurationError:
        raise MongoDBInventoryError(
            code=DependencyErrorCode.UNINITIALIZED,
            message="MongoDB database is not configured.",
        ) from None


async def _fetch_mongodb_inventory(
    *,
    client: AsyncMongoClient[MongoDocument],
) -> MongoDBInventory:
    """Collect metadata from only the configured MongoDB database."""

    database = _get_configured_database(
        client,
    )

    try:
        raw_collection_names = await database.list_collection_names(
            filter=_COLLECTION_FILTER,
            comment=_OPERATION_COMMENT,
        )

        collection_names = [
            _validate_collection_name(
                collection_name,
            )
            for collection_name in raw_collection_names
        ]

        visible_collection_names = sorted(
            collection_name
            for collection_name in collection_names
            if not collection_name.startswith("system.")
        )

        collections: list[MongoCollectionMetadata] = []

        for collection_name in visible_collection_names:
            collections.append(
                await _collect_collection(
                    database=database,
                    collection_name=collection_name,
                ),
            )

        return MongoDBInventory(
            database_name=database.name,
            observed_at=datetime.now(UTC),
            collections=tuple(collections),
        )
    except PyMongoError as error:
        raise _map_pymongo_error(error) from None
    except (
        _MongoMetadataMappingError,
        ValidationError,
    ):
        raise MongoDBInventoryError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="MongoDB returned invalid metadata.",
        ) from None


def _validate_timeout(
    timeout_seconds: float,
) -> None:
    """Require a finite positive timeout."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            "MongoDB timeout must be a finite positive number.",
        )


async def get_mongodb_inventory(
    *,
    client: AsyncMongoClient[MongoDocument],
    timeout_seconds: float,
) -> MongoDBInventory:
    """Retrieve MongoDB metadata through the existing async client."""

    _validate_timeout(
        timeout_seconds,
    )

    try:
        async with asyncio.timeout(
            timeout_seconds,
        ):
            return await _fetch_mongodb_inventory(
                client=client,
            )
    except TimeoutError:
        raise MongoDBInventoryError(
            code=DependencyErrorCode.TIMEOUT,
            message="MongoDB metadata inventory timed out.",
        ) from None
