"""Tests for MongoDB metadata inventory collection."""

import asyncio
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)

from return_platform.data_governance.inventory.contracts import (
    MongoDBInventory,
)
from return_platform.data_governance.inventory.mongodb import (
    MongoDBInventoryError,
    get_mongodb_inventory,
)
from return_platform.shared.contracts import DependencyErrorCode

MongoDocument = dict[str, Any]
IndexDocument = Mapping[str, object]

_OPERATION_COMMENT = "return-platform-data-governance-inventory"


class _AsyncIndexCursor:
    """Deterministic async cursor substitute for index metadata."""

    def __init__(
        self,
        documents: list[IndexDocument],
    ) -> None:
        self._documents = iter(documents)
        self.entered = False
        self.closed = False

    async def __aenter__(self) -> Self:
        """Enter the async cursor context."""

        self.entered = True
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the async cursor context."""

        del exception_type
        del exception
        del traceback

        self.closed = True

    def __aiter__(self) -> Self:
        """Return the asynchronous iterator."""

        return self

    async def __anext__(self) -> IndexDocument:
        """Return the next index document."""

        try:
            return next(self._documents)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def mocked_mongodb() -> Iterator[
    tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ]
]:
    """Provide a lifespan-owned MongoDB client substitute."""

    client_mock = MagicMock()
    database_mock = MagicMock()
    collections: dict[str, MagicMock] = {}

    database_mock.name = "return_platform"
    database_mock.list_collection_names = AsyncMock()
    database_mock.get_collection.side_effect = collections.__getitem__

    client_mock.get_default_database.return_value = database_mock

    client = cast(
        AsyncMongoClient[MongoDocument],
        client_mock,
    )

    yield (
        client,
        client_mock,
        database_mock,
        collections,
    )


def _configure_collection(
    collections: dict[str, MagicMock],
    *,
    name: str,
    approximate_document_count: object,
    indexes: tuple[IndexDocument, ...],
) -> tuple[MagicMock, _AsyncIndexCursor]:
    """Configure one asynchronous MongoDB collection substitute."""

    index_cursor = _AsyncIndexCursor(
        list(indexes),
    )
    collection_mock = MagicMock()

    collection_mock.estimated_document_count = AsyncMock(
        return_value=approximate_document_count,
    )
    collection_mock.list_indexes = AsyncMock(
        return_value=index_cursor,
    )

    collections[name] = collection_mock

    return collection_mock, index_cursor


@pytest.mark.asyncio
async def test_empty_inventory_is_successful(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Treat zero visible user collections as a valid inventory."""

    (
        client,
        client_mock,
        database_mock,
        _,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "system.profile",
    ]

    inventory = await get_mongodb_inventory(
        client=client,
        timeout_seconds=2.0,
    )

    assert inventory.database_name == "return_platform"
    assert inventory.collections == ()
    assert inventory.collection_count == 0
    assert inventory.index_count == 0
    assert inventory.is_empty is True
    assert inventory.observed_at.tzinfo is UTC

    client_mock.get_default_database.assert_called_once_with()
    client_mock.list_database_names.assert_not_called()
    client_mock.close.assert_not_called()

    database_mock.list_collection_names.assert_awaited_once_with(
        filter={"type": "collection"},
        comment=_OPERATION_COMMENT,
    )
    database_mock.get_collection.assert_not_called()


@pytest.mark.asyncio
async def test_inventory_mapping_is_deterministic(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Sort collections and indexes while preserving compound-key order."""

    (
        client,
        client_mock,
        database_mock,
        collections,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "zeta_events",
        "system.profile",
        "return_sessions",
    ]

    return_sessions, return_sessions_cursor = _configure_collection(
        collections,
        name="return_sessions",
        approximate_document_count=42,
        indexes=(
            {
                "name": "tenant_created_at",
                "key": {
                    "tenant_id": 1,
                    "created_at": -1,
                },
                "unique": True,
                "sparse": False,
                "hidden": True,
                "expireAfterSeconds": 3600,
                "partialFilterExpression": {
                    "status": "OPEN",
                },
            },
            {
                "name": "_id_",
                "key": {
                    "_id": 1,
                },
            },
        ),
    )

    zeta_events, zeta_events_cursor = _configure_collection(
        collections,
        name="zeta_events",
        approximate_document_count=0,
        indexes=(
            {
                "name": "_id_",
                "key": {
                    "_id": 1,
                },
                "unique": False,
            },
        ),
    )

    inventory = await get_mongodb_inventory(
        client=client,
        timeout_seconds=2.0,
    )

    assert inventory.collection_count == 2
    assert inventory.index_count == 3
    assert inventory.is_empty is False

    assert [collection.name for collection in inventory.collections] == [
        "return_sessions",
        "zeta_events",
    ]

    session_collection = inventory.collections[0]

    assert session_collection.approximate_document_count == 42
    assert [index.name for index in session_collection.indexes] == [
        "_id_",
        "tenant_created_at",
    ]

    identity_index = session_collection.indexes[0]

    assert identity_index.is_unique is False
    assert identity_index.is_sparse is False
    assert identity_index.is_hidden is False
    assert identity_index.expire_after_seconds is None
    assert identity_index.has_partial_filter is False
    assert identity_index.keys[0].field_name == "_id"
    assert identity_index.keys[0].direction == 1

    compound_index = session_collection.indexes[1]

    assert compound_index.is_unique is True
    assert compound_index.is_sparse is False
    assert compound_index.is_hidden is True
    assert compound_index.expire_after_seconds == 3600
    assert compound_index.has_partial_filter is True

    assert [key.field_name for key in compound_index.keys] == [
        "tenant_id",
        "created_at",
    ]
    assert [key.direction for key in compound_index.keys] == [
        1,
        -1,
    ]

    assert return_sessions_cursor.entered is True
    assert return_sessions_cursor.closed is True
    assert zeta_events_cursor.entered is True
    assert zeta_events_cursor.closed is True

    return_sessions.estimated_document_count.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )
    return_sessions.list_indexes.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )
    zeta_events.estimated_document_count.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )
    zeta_events.list_indexes.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )

    client_mock.list_database_names.assert_not_called()
    client_mock.close.assert_not_called()


@pytest.mark.asyncio
async def test_only_configured_database_is_inspected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Never enumerate unrelated MongoDB databases."""

    (
        client,
        client_mock,
        database_mock,
        _,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = []

    await get_mongodb_inventory(
        client=client,
        timeout_seconds=2.0,
    )

    client_mock.get_default_database.assert_called_once_with()
    client_mock.list_database_names.assert_not_called()
    client_mock.close.assert_not_called()


@pytest.mark.asyncio
async def test_collection_discovery_is_metadata_only(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Use metadata operations without querying application documents."""

    (
        client,
        _,
        database_mock,
        collections,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "return_sessions",
    ]

    collection_mock, _ = _configure_collection(
        collections,
        name="return_sessions",
        approximate_document_count=7,
        indexes=(
            {
                "name": "_id_",
                "key": {
                    "_id": 1,
                },
            },
        ),
    )

    await get_mongodb_inventory(
        client=client,
        timeout_seconds=2.0,
    )

    database_mock.list_collection_names.assert_awaited_once_with(
        filter={"type": "collection"},
        comment=_OPERATION_COMMENT,
    )
    collection_mock.estimated_document_count.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )
    collection_mock.list_indexes.assert_awaited_once_with(
        comment=_OPERATION_COMMENT,
    )

    collection_mock.find.assert_not_called()
    collection_mock.find_one.assert_not_called()
    collection_mock.count_documents.assert_not_called()
    collection_mock.aggregate.assert_not_called()


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
                "Not authorized to execute listCollections.",
                code=13,
            ),
            DependencyErrorCode.QUERY_FAILED,
        ),
        (
            ServerSelectionTimeoutError(
                "Sensitive MongoDB server selection details.",
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
                "Unexpected internal MongoDB driver details.",
            ),
            DependencyErrorCode.UNKNOWN_ERROR,
        ),
    ],
)
async def test_driver_failures_are_safely_mapped(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
    driver_error: PyMongoError,
    expected_code: DependencyErrorCode,
) -> None:
    """Map raw driver failures without exposing internal details."""

    (
        client,
        _,
        database_mock,
        _,
    ) = mocked_mongodb

    database_mock.list_collection_names.side_effect = driver_error

    with pytest.raises(
        MongoDBInventoryError,
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    public_message = str(error_info.value)

    assert error_info.value.code == expected_code
    assert str(driver_error) not in public_message
    assert "sensitive-user" not in public_message
    assert "internal-mongodb.example" not in public_message


@pytest.mark.asyncio
async def test_database_without_configured_name_is_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Map a URI without a default database to UNINITIALIZED."""

    (
        client,
        client_mock,
        database_mock,
        _,
    ) = mocked_mongodb

    client_mock.get_default_database.side_effect = ConfigurationError(
        "No default database name defined.",
    )

    with pytest.raises(
        MongoDBInventoryError,
        match="database is not configured",
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.UNINITIALIZED)
    database_mock.list_collection_names.assert_not_awaited()
    client_mock.close.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_collection_name_is_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Reject malformed collection metadata."""

    (
        client,
        _,
        database_mock,
        _,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        123,
    ]

    with pytest.raises(
        MongoDBInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
async def test_malformed_document_count_is_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Reject Boolean document counts instead of treating them as integers."""

    (
        client,
        _,
        database_mock,
        collections,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "return_sessions",
    ]

    _configure_collection(
        collections,
        name="return_sessions",
        approximate_document_count=True,
        indexes=(
            {
                "name": "_id_",
                "key": {
                    "_id": 1,
                },
            },
        ),
    )

    with pytest.raises(
        MongoDBInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
async def test_unsupported_index_direction_is_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Reject unsupported index direction metadata."""

    (
        client,
        _,
        database_mock,
        collections,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "return_sessions",
    ]

    _configure_collection(
        collections,
        name="return_sessions",
        approximate_document_count=0,
        indexes=(
            {
                "name": "invalid_direction",
                "key": {
                    "session_id": 0,
                },
            },
        ),
    )

    with pytest.raises(
        MongoDBInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
async def test_duplicate_collection_names_are_safely_rejected(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Reject duplicate collection metadata returned by the server."""

    (
        client,
        _,
        database_mock,
        collections,
    ) = mocked_mongodb

    database_mock.list_collection_names.return_value = [
        "return_sessions",
        "return_sessions",
    ]

    _configure_collection(
        collections,
        name="return_sessions",
        approximate_document_count=0,
        indexes=(
            {
                "name": "_id_",
                "key": {
                    "_id": 1,
                },
            },
        ),
    )

    with pytest.raises(
        MongoDBInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=2.0,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
async def test_operation_timeout_is_safely_mapped(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Bound total inventory execution with the requested timeout."""

    client, _, _, _ = mocked_mongodb

    async def slow_fetch(
        *,
        client: AsyncMongoClient[MongoDocument],
    ) -> MongoDBInventory:
        del client

        await asyncio.sleep(1.0)

        return MongoDBInventory(
            database_name="return_platform",
            observed_at=datetime.now(UTC),
        )

    target = "return_platform.data_governance.inventory.mongodb._fetch_mongodb_inventory"

    with (
        patch(
            target,
            side_effect=slow_fetch,
        ) as fetch_mock,
        pytest.raises(
            MongoDBInventoryError,
            match="metadata inventory timed out",
        ) as error_info,
    ):
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=0.01,
        )

    assert error_info.value.code == (DependencyErrorCode.TIMEOUT)
    fetch_mock.assert_called_once_with(
        client=client,
    )


@pytest.mark.asyncio
async def test_caller_cancellation_is_preserved(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
) -> None:
    """Propagate caller cancellation rather than mapping it to TIMEOUT."""

    client, _, _, _ = mocked_mongodb

    fetch_started = asyncio.Event()

    async def blocking_fetch(
        *,
        client: AsyncMongoClient[MongoDocument],
    ) -> MongoDBInventory:
        del client

        fetch_started.set()
        await asyncio.Event().wait()

        raise AssertionError(
            "Cancelled inventory operation continued unexpectedly.",
        )

    target = "return_platform.data_governance.inventory.mongodb._fetch_mongodb_inventory"

    with patch(
        target,
        side_effect=blocking_fetch,
    ):
        task = asyncio.create_task(
            get_mongodb_inventory(
                client=client,
                timeout_seconds=5.0,
            ),
        )

        await fetch_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
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
async def test_invalid_timeout_is_rejected_before_execution(
    mocked_mongodb: tuple[
        AsyncMongoClient[MongoDocument],
        MagicMock,
        MagicMock,
        dict[str, MagicMock],
    ],
    timeout_seconds: float,
) -> None:
    """Reject invalid timeouts before starting metadata work."""

    client, _, _, _ = mocked_mongodb

    target = "return_platform.data_governance.inventory.mongodb._fetch_mongodb_inventory"

    with (
        patch(
            target,
            new_callable=AsyncMock,
        ) as fetch_mock,
        pytest.raises(
            ValueError,
            match="timeout must be a finite positive number",
        ),
    ):
        await get_mongodb_inventory(
            client=client,
            timeout_seconds=timeout_seconds,
        )

    fetch_mock.assert_not_awaited()
