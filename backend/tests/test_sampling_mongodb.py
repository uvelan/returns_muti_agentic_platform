"""Tests for catalog-authorized bounded MongoDB sampling."""

import asyncio
from collections.abc import Iterator
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import MagicMock, patch

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)

from return_platform.data_governance.sampling.authorization import (
    SamplingAuthorizationCode,
    SamplingAuthorizationError,
)
from return_platform.data_governance.sampling.contracts import (
    SampleValueKind,
)
from return_platform.data_governance.sampling.mongodb import (
    MongoDBSamplingError,
    get_mongodb_sample,
)
from return_platform.shared.contracts import DependencyErrorCode
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
    SamplingConfig,
)

MongoDocument = dict[str, Any]

_OPERATION_COMMENT = "return-platform-data-sampling"

_FETCH_TARGET = "return_platform.data_governance.sampling.mongodb._fetch_mongodb_sample"


class _AsyncSampleCursor:
    """Deterministic async MongoDB cursor substitute."""

    def __init__(
        self,
        *,
        documents: list[MongoDocument] | None = None,
        error: PyMongoError | None = None,
    ) -> None:
        self._documents = documents if documents is not None else []
        self._error = error

        self.entered = False
        self.closed = False
        self.requested_lengths: list[int | None] = []

    async def __aenter__(self) -> Self:
        """Enter the cursor context."""

        self.entered = True
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the cursor context."""

        del exception_type
        del exception
        del traceback

        self.closed = True

    async def to_list(
        self,
        *,
        length: int | None = None,
    ) -> list[MongoDocument]:
        """Return configured documents or raise a configured failure."""

        self.requested_lengths.append(
            length,
        )

        if self._error is not None:
            raise self._error

        return self._documents


@pytest.fixture
def mocked_mongodb() -> Iterator[
    tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ]
]:
    """Provide a lifespan-owned MongoDB client substitute."""

    client_mock = MagicMock()
    database_mock = MagicMock()
    collection_mock = MagicMock()

    database_mock.name = "return_platform"
    database_mock.get_collection.return_value = collection_mock

    client_mock.get_default_database.return_value = database_mock

    client = cast(
        AsyncMongoClient[MongoDocument],
        client_mock,
    )

    yield (
        client,
        client_mock,
        database_mock,
        collection_mock,
    )


def _sampling_policy(
    *,
    enabled: bool = True,
    max_rows: int = 5,
    redact_fields: tuple[str, ...] = (
        "email",
        "phone",
    ),
) -> SamplingConfig:
    """Create a catalog sampling policy."""

    if not enabled:
        return SamplingConfig(
            enabled=False,
            max_rows=0,
            redact_fields=(),
        )

    return SamplingConfig(
        enabled=True,
        max_rows=max_rows,
        redact_fields=redact_fields,
    )


def _mongodb_asset(
    *,
    asset_id: str = "source.mongodb.sessions",
    database: str = "return_platform",
    object_name: str = "sessions",
    sampling: SamplingConfig | None = None,
) -> AssetCatalogEntry:
    """Create a catalog-authorized MongoDB collection."""

    return AssetCatalogEntry(
        asset_id=asset_id,
        store=DataStoreType.MONGODB,
        database=database,
        namespace=None,
        object_name=object_name,
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=(sampling if sampling is not None else _sampling_policy()),
    )


def _sqlserver_asset() -> AssetCatalogEntry:
    """Create a catalog-authorized SQL Server table."""

    return AssetCatalogEntry(
        asset_id="source.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=_sampling_policy(),
    )


def _catalog(
    *assets: AssetCatalogEntry,
) -> AssetCatalog:
    """Create an immutable asset catalog."""

    return AssetCatalog(
        version="1.0",
        assets=assets,
    )


@pytest.mark.asyncio
async def test_sampling_uses_catalog_identity_and_configured_database(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Use only the catalog identity and configured MongoDB database."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    asset = _mongodb_asset(
        object_name="return_sessions",
    )
    catalog = _catalog(
        asset,
    )

    cursor = _AsyncSampleCursor(
        documents=[
            {
                "_id": 101,
                "status": "OPEN",
            },
        ],
    )
    collection_mock.find.return_value = cursor

    sample = await get_mongodb_sample(
        catalog=catalog,
        asset_id=asset.asset_id,
        client=client,
        timeout_seconds=2.0,
    )

    assert sample.catalog_version == "1.0"
    assert sample.asset_id == asset.asset_id
    assert sample.store == DataStoreType.MONGODB
    assert sample.database == "return_platform"
    assert sample.namespace is None
    assert sample.object_name == "return_sessions"
    assert sample.object_kind == ObjectKind.COLLECTION
    assert sample.row_limit == 5
    assert sample.row_count == 1
    assert sample.ordering_guaranteed is False

    utc_offset = sample.sampled_at.utcoffset()

    assert utc_offset is not None
    assert utc_offset.total_seconds() == 0

    client_mock.get_default_database.assert_called_once_with()
    client_mock.list_database_names.assert_not_called()
    client_mock.close.assert_not_called()

    database_mock.get_collection.assert_called_once_with(
        "return_sessions",
    )

    collection_mock.find.assert_called_once_with(
        filter={},
        limit=6,
        batch_size=6,
        max_time_ms=2000,
        comment=_OPERATION_COMMENT,
    )

    assert cursor.requested_lengths == [
        6,
    ]
    assert cursor.entered is True
    assert cursor.closed is True


@pytest.mark.asyncio
async def test_sampling_applies_shared_sanitization(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Apply redaction and safe conversion to MongoDB documents."""

    (
        client,
        _,
        _,
        collection_mock,
    ) = mocked_mongodb

    asset = _mongodb_asset()

    cursor = _AsyncSampleCursor(
        documents=[
            {
                "_id": 1,
                "Email": "customer@example.com",
                "status": "OPEN",
                "payload": b"\x00\x01",
                "nested": {
                    "secret": "value",
                },
                "tags": [
                    "priority",
                ],
            },
        ],
    )
    collection_mock.find.return_value = cursor

    sample = await get_mongodb_sample(
        catalog=_catalog(asset),
        asset_id=asset.asset_id,
        client=client,
        timeout_seconds=2.0,
    )

    row = sample.rows[0]

    identifier = row.get_field(
        "_id",
    )
    email = row.get_field(
        "Email",
    )
    status = row.get_field(
        "status",
    )
    payload = row.get_field(
        "payload",
    )
    nested = row.get_field(
        "nested",
    )
    tags = row.get_field(
        "tags",
    )

    assert identifier is not None
    assert identifier.value == 1
    assert identifier.value_kind == SampleValueKind.INTEGER

    assert email is not None
    assert email.value == "[REDACTED]"
    assert email.value_kind == SampleValueKind.REDACTED

    assert status is not None
    assert status.value == "OPEN"
    assert status.value_kind == SampleValueKind.TEXT

    assert payload is not None
    assert payload.value == "[BINARY DATA]"
    assert payload.value_kind == SampleValueKind.BINARY

    assert nested is not None
    assert nested.value == "[UNSUPPORTED TYPE]"
    assert nested.value_kind == SampleValueKind.UNSUPPORTED

    assert tags is not None
    assert tags.value == "[UNSUPPORTED TYPE]"
    assert tags.value_kind == SampleValueKind.UNSUPPORTED


@pytest.mark.asyncio
async def test_unknown_asset_does_not_access_mongodb(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject an unknown asset before accessing the client."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    with pytest.raises(
        SamplingAuthorizationError,
        match="not present in the catalog",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(
                _mongodb_asset(),
            ),
            asset_id="source.mongodb.unknown",
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.ASSET_NOT_FOUND)

    client_mock.get_default_database.assert_not_called()
    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_sampling_does_not_access_mongodb(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject a catalog asset whose sampling policy is disabled."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    asset = _mongodb_asset(
        sampling=_sampling_policy(
            enabled=False,
        ),
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="Sampling is disabled",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.SAMPLING_DISABLED)

    client_mock.get_default_database.assert_not_called()
    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()


@pytest.mark.asyncio
async def test_sqlserver_asset_cannot_authorize_mongodb_query(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject a SQL Server asset before accessing MongoDB."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    asset = _sqlserver_asset()

    with pytest.raises(
        SamplingAuthorizationError,
        match="does not belong to the requested store",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.STORE_MISMATCH)

    client_mock.get_default_database.assert_not_called()
    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()


@pytest.mark.asyncio
async def test_configured_database_must_match_catalog_database(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Prevent a catalog asset from authorizing another database."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    database_mock.name = "different_database"

    asset = _mongodb_asset(
        database="return_platform",
    )

    with pytest.raises(
        MongoDBSamplingError,
        match="does not match the catalog asset",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.UNINITIALIZED)

    client_mock.get_default_database.assert_called_once_with()
    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()
    client_mock.list_database_names.assert_not_called()
    client_mock.close.assert_not_called()


@pytest.mark.asyncio
async def test_missing_default_database_is_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject a MongoDB URI without a configured database."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    client_mock.get_default_database.side_effect = ConfigurationError(
        "No default database defined in sensitive URI.",
    )

    asset = _mongodb_asset()

    with pytest.raises(
        MongoDBSamplingError,
        match="database is not configured",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.UNINITIALIZED)

    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()
    client_mock.close.assert_not_called()


@pytest.mark.asyncio
async def test_more_documents_than_authorized_is_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject results exceeding the catalog document limit."""

    (
        client,
        _,
        _,
        collection_mock,
    ) = mocked_mongodb

    asset = _mongodb_asset(
        sampling=_sampling_policy(
            max_rows=2,
        ),
    )

    cursor = _AsyncSampleCursor(
        documents=[
            {
                "_id": 1,
            },
            {
                "_id": 2,
            },
            {
                "_id": 3,
            },
        ],
    )
    collection_mock.find.return_value = cursor

    with pytest.raises(
        MongoDBSamplingError,
        match="more documents than",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)

    collection_mock.find.assert_called_once_with(
        filter={},
        limit=3,
        batch_size=3,
        max_time_ms=2000,
        comment=_OPERATION_COMMENT,
    )

    assert cursor.requested_lengths == [
        3,
    ]
    assert cursor.closed is True


@pytest.mark.asyncio
async def test_malformed_document_is_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Reject non-mapping documents without exposing internals."""

    (
        client,
        _,
        _,
        collection_mock,
    ) = mocked_mongodb

    invalid_documents = cast(
        list[MongoDocument],
        [
            123,
        ],
    )

    cursor = _AsyncSampleCursor(
        documents=invalid_documents,
    )
    collection_mock.find.return_value = cursor

    asset = _mongodb_asset()

    with pytest.raises(
        MongoDBSamplingError,
        match="invalid sample data",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)
    assert cursor.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver_error", "expected_code"),
    [
        (
            OperationFailure(
                "Authentication failed for sensitive-user.",
                code=18,
            ),
            DependencyErrorCode.AUTH_FAILED,
        ),
        (
            OperationFailure(
                "Not authorized to read sensitive collection.",
                code=13,
            ),
            DependencyErrorCode.QUERY_FAILED,
        ),
        (
            ServerSelectionTimeoutError(
                "Sensitive MongoDB topology details.",
            ),
            DependencyErrorCode.TIMEOUT,
        ),
        (
            ConnectionFailure(
                "Connection refused by internal-mongodb.example.",
            ),
            DependencyErrorCode.CONNECTION_REFUSED,
        ),
        (
            PyMongoError(
                "Unexpected sensitive driver details.",
            ),
            DependencyErrorCode.UNKNOWN_ERROR,
        ),
    ],
)
async def test_find_failures_are_safely_mapped(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    driver_error: PyMongoError,
    expected_code: DependencyErrorCode,
) -> None:
    """Map find failures without exposing raw driver details."""

    (
        client,
        _,
        _,
        collection_mock,
    ) = mocked_mongodb

    collection_mock.find.side_effect = driver_error

    asset = _mongodb_asset()

    with pytest.raises(
        MongoDBSamplingError,
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    public_message = str(error_info.value)

    assert error_info.value.code == expected_code
    assert str(driver_error) not in public_message
    assert "sensitive-user" not in public_message
    assert "internal-mongodb.example" not in public_message


@pytest.mark.asyncio
async def test_cursor_failure_is_safely_mapped_and_closed(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Map cursor retrieval failures and close the cursor."""

    (
        client,
        _,
        _,
        collection_mock,
    ) = mocked_mongodb

    driver_error = OperationFailure(
        "Sensitive collection query failed.",
        code=2,
    )

    cursor = _AsyncSampleCursor(
        error=driver_error,
    )
    collection_mock.find.return_value = cursor

    asset = _mongodb_asset()

    with pytest.raises(
        MongoDBSamplingError,
        match="sampling query failed",
    ) as error_info:
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)
    assert str(driver_error) not in str(error_info.value)
    assert cursor.entered is True
    assert cursor.closed is True


@pytest.mark.asyncio
async def test_operation_timeout_is_safely_mapped(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Bound the complete MongoDB sampling operation."""

    (
        client,
        _,
        _,
        _,
    ) = mocked_mongodb

    async def blocking_fetch(
        **_: object,
    ) -> tuple[()]:
        await asyncio.Event().wait()
        return ()

    asset = _mongodb_asset()

    with (
        patch(
            _FETCH_TARGET,
            side_effect=blocking_fetch,
        ) as fetch_mock,
        pytest.raises(
            MongoDBSamplingError,
            match="operation timed out",
        ) as error_info,
    ):
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=0.01,
        )

    assert error_info.value.code == (DependencyErrorCode.TIMEOUT)
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_cancellation_is_preserved(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
) -> None:
    """Propagate task cancellation instead of mapping it to TIMEOUT."""

    (
        client,
        _,
        _,
        _,
    ) = mocked_mongodb

    fetch_started = asyncio.Event()

    async def blocking_fetch(
        **_: object,
    ) -> tuple[()]:
        fetch_started.set()
        await asyncio.Event().wait()
        return ()

    asset = _mongodb_asset()

    with patch(
        _FETCH_TARGET,
        side_effect=blocking_fetch,
    ):
        task = asyncio.create_task(
            get_mongodb_sample(
                catalog=_catalog(asset),
                asset_id=asset.asset_id,
                client=client,
                timeout_seconds=5.0,
            ),
        )

        await fetch_started.wait()
        task.cancel()

        with pytest.raises(
            asyncio.CancelledError,
        ):
            await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout_seconds",
    [
        0.0,
        -1.0,
        float("nan"),
        float("inf"),
    ],
)
async def test_invalid_timeout_is_rejected_before_mongodb_access(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    timeout_seconds: float,
) -> None:
    """Reject invalid timeouts before authorization or client access."""

    (
        client,
        client_mock,
        database_mock,
        collection_mock,
    ) = mocked_mongodb

    asset = _mongodb_asset()

    with pytest.raises(
        ValueError,
        match="finite positive number",
    ):
        await get_mongodb_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            client=client,
            timeout_seconds=timeout_seconds,
        )

    client_mock.get_default_database.assert_not_called()
    database_mock.get_collection.assert_not_called()
    collection_mock.find.assert_not_called()
