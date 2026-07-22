"""Tests for catalog-authorized bounded SQL Server sampling."""

import asyncio
import threading
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, cast
from unittest.mock import MagicMock, patch

import pymssql
import pytest

from return_platform.data_governance.sampling.authorization import (
    SamplingAuthorizationCode,
    SamplingAuthorizationError,
)
from return_platform.data_governance.sampling.contracts import (
    SampledRow,
    SampleValueKind,
)
from return_platform.data_governance.sampling.sqlserver import (
    SQLServerSamplingError,
    get_sqlserver_sample,
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

_CONNECT_TARGET = "return_platform.data_governance.sampling.sqlserver.pymssql.connect"

_FETCH_TARGET = "return_platform.data_governance.sampling.sqlserver._fetch_sqlserver_sample_sync"


@pytest.fixture
def sql_executor() -> Iterator[ThreadPoolExecutor]:
    """Provide the existing bounded SQL executor substitute."""

    executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="sql-sampling-test",
    )

    try:
        yield executor
    finally:
        executor.shutdown(
            wait=True,
            cancel_futures=True,
        )


@pytest.fixture
def mocked_sqlserver() -> Iterator[
    tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ]
]:
    """Provide a dictionary-returning connection and cursor."""

    with patch(
        _CONNECT_TARGET,
    ) as connect_mock:
        connection = MagicMock()
        cursor = MagicMock()

        connect_mock.return_value = connection

        connection.__enter__.return_value = connection
        connection.__exit__.return_value = None
        connection.cursor.return_value = cursor

        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = None
        cursor.fetchmany.return_value = []

        yield (
            connect_mock,
            connection,
            cursor,
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
    """Create a sampling policy."""

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


def _sqlserver_asset(
    *,
    asset_id: str = "source.sqlserver.users",
    database: str = "return_platform",
    namespace: str = "dbo",
    object_name: str = "users",
    object_kind: ObjectKind = ObjectKind.TABLE,
    sampling: SamplingConfig | None = None,
) -> AssetCatalogEntry:
    """Create a catalog-authorized SQL Server asset."""

    return AssetCatalogEntry(
        asset_id=asset_id,
        store=DataStoreType.SQLSERVER,
        database=database,
        namespace=namespace,
        object_name=object_name,
        object_kind=object_kind,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=(sampling if sampling is not None else _sampling_policy()),
    )


def _mongodb_asset() -> AssetCatalogEntry:
    """Create a catalog-authorized MongoDB asset."""

    return AssetCatalogEntry(
        asset_id="source.mongodb.sessions",
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace=None,
        object_name="sessions",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=_sampling_policy(),
    )


def _catalog(
    *assets: AssetCatalogEntry,
    version: Literal["1.0"] = "1.0",
) -> AssetCatalog:
    """Create an immutable asset catalog."""

    return AssetCatalog(
        version=version,
        assets=assets,
    )


@pytest.mark.asyncio
async def test_sampling_uses_catalog_identity_and_database(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Use only the physical identity resolved from the catalog."""

    connect_mock, _, cursor = mocked_sqlserver

    asset = _sqlserver_asset(
        database="catalog_database",
        namespace="sales",
        object_name="customers",
    )
    catalog = _catalog(
        asset,
    )

    cursor.fetchmany.return_value = [
        {
            "customer_id": 101,
            "name": "Jane Doe",
        },
    ]

    sample = await get_sqlserver_sample(
        catalog=catalog,
        asset_id=asset.asset_id,
        host="localhost",
        port=1433,
        user="sampling_user",
        password="secret",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    assert sample.catalog_version == "1.0"
    assert sample.asset_id == asset.asset_id
    assert sample.store == DataStoreType.SQLSERVER
    assert sample.database == "catalog_database"
    assert sample.namespace == "sales"
    assert sample.object_name == "customers"
    assert sample.object_kind == ObjectKind.TABLE
    assert sample.row_limit == 5
    assert sample.row_count == 1
    assert sample.ordering_guaranteed is False

    utc_offset = sample.sampled_at.utcoffset()

    assert utc_offset is not None
    assert utc_offset.total_seconds() == 0

    connection_arguments = connect_mock.call_args.kwargs

    assert connection_arguments["database"] == "catalog_database"
    assert connection_arguments["server"] == "localhost"
    assert connection_arguments["port"] == "1433"
    assert connection_arguments["user"] == "sampling_user"
    assert connection_arguments["password"] == "secret"
    assert connection_arguments["as_dict"] is True
    assert connection_arguments["autocommit"] is True
    assert connection_arguments["read_only"] is True
    assert connection_arguments["charset"] == "UTF-8"
    assert connection_arguments["appname"] == "return-platform-data-sampling"

    cursor.execute.assert_called_once_with(
        "SELECT TOP (5) * FROM [sales].[customers];",
    )
    cursor.fetchmany.assert_called_once_with(
        6,
    )


@pytest.mark.asyncio
async def test_sampling_escapes_cataloged_identifiers(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Bracket-escape closing brackets in cataloged identifiers."""

    _, _, cursor = mocked_sqlserver

    asset = _sqlserver_asset(
        asset_id="source.sqlserver.archived_users",
        namespace="sales]archive",
        object_name="user]history",
    )

    await get_sqlserver_sample(
        catalog=_catalog(asset),
        asset_id=asset.asset_id,
        host="localhost",
        port=1433,
        user="sampling_user",
        password="secret",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    cursor.execute.assert_called_once_with(
        "SELECT TOP (5) * FROM [sales]]archive].[user]]history];",
    )


@pytest.mark.asyncio
async def test_sampling_applies_shared_sanitization(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Apply case-insensitive redaction and safe value conversion."""

    _, _, cursor = mocked_sqlserver

    asset = _sqlserver_asset()

    cursor.fetchmany.return_value = [
        {
            "id": 1,
            "Email": "customer@example.com",
            "name": "Jane Doe",
            "payload": b"\x00\x01",
            "nested": {
                "unsafe": "value",
            },
        },
    ]

    sample = await get_sqlserver_sample(
        catalog=_catalog(asset),
        asset_id=asset.asset_id,
        host="localhost",
        port=1433,
        user="sampling_user",
        password="secret",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    row = sample.rows[0]

    identifier = row.get_field(
        "id",
    )
    email = row.get_field(
        "Email",
    )
    name = row.get_field(
        "name",
    )
    payload = row.get_field(
        "payload",
    )
    nested = row.get_field(
        "nested",
    )

    assert identifier is not None
    assert identifier.value == 1
    assert identifier.value_kind == SampleValueKind.INTEGER

    assert email is not None
    assert email.value == "[REDACTED]"
    assert email.value_kind == SampleValueKind.REDACTED

    assert name is not None
    assert name.value == "Jane Doe"
    assert name.value_kind == SampleValueKind.TEXT

    assert payload is not None
    assert payload.value == "[BINARY DATA]"
    assert payload.value_kind == SampleValueKind.BINARY

    assert nested is not None
    assert nested.value == "[UNSUPPORTED TYPE]"
    assert nested.value_kind == SampleValueKind.UNSUPPORTED


@pytest.mark.asyncio
async def test_sqlserver_view_can_be_sampled_when_catalog_authorized(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Permit catalog-authorized SQL Server views."""

    _, _, cursor = mocked_sqlserver

    asset = _sqlserver_asset(
        asset_id="source.sqlserver.customer_summary",
        object_name="customer_summary",
        object_kind=ObjectKind.VIEW,
    )

    sample = await get_sqlserver_sample(
        catalog=_catalog(asset),
        asset_id=asset.asset_id,
        host="localhost",
        port=1433,
        user="sampling_user",
        password="secret",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    assert sample.object_kind == ObjectKind.VIEW

    cursor.execute.assert_called_once_with(
        "SELECT TOP (5) * FROM [dbo].[customer_summary];",
    )


@pytest.mark.asyncio
async def test_unknown_asset_does_not_open_connection(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject an unknown asset before scheduling SQL work."""

    connect_mock, _, _ = mocked_sqlserver

    with pytest.raises(
        SamplingAuthorizationError,
        match="not present in the catalog",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(
                _sqlserver_asset(),
            ),
            asset_id="source.sqlserver.unknown",
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.ASSET_NOT_FOUND)
    connect_mock.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_sampling_does_not_open_connection(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a catalog asset whose sampling policy is disabled."""

    connect_mock, _, _ = mocked_sqlserver

    asset = _sqlserver_asset(
        sampling=_sampling_policy(
            enabled=False,
        ),
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="Sampling is disabled",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.SAMPLING_DISABLED)
    connect_mock.assert_not_called()


@pytest.mark.asyncio
async def test_mongodb_asset_cannot_authorize_sqlserver_query(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a different store before opening SQL Server."""

    connect_mock, _, _ = mocked_sqlserver

    asset = _mongodb_asset()

    with pytest.raises(
        SamplingAuthorizationError,
        match="does not belong to the requested store",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.STORE_MISMATCH)
    connect_mock.assert_not_called()


@pytest.mark.asyncio
async def test_more_rows_than_authorized_is_rejected(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a driver result exceeding the catalog row limit."""

    _, _, cursor = mocked_sqlserver

    asset = _sqlserver_asset(
        sampling=_sampling_policy(
            max_rows=2,
        ),
    )

    cursor.fetchmany.return_value = [
        {
            "id": 1,
        },
        {
            "id": 2,
        },
        {
            "id": 3,
        },
    ]

    with pytest.raises(
        SQLServerSamplingError,
        match="more rows than",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)
    cursor.fetchmany.assert_called_once_with(
        3,
    )


@pytest.mark.asyncio
async def test_malformed_driver_row_is_safely_rejected(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject non-mapping rows without exposing driver internals."""

    _, _, cursor = mocked_sqlserver

    invalid_rows = cast(
        list[Mapping[str, object]],
        [
            123,
        ],
    )
    cursor.fetchmany.return_value = invalid_rows

    asset = _sqlserver_asset()

    with pytest.raises(
        SQLServerSamplingError,
        match="invalid sample data",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver_error", "expected_code"),
    [
        (
            pymssql.OperationalError(
                "Login failed for sensitive-user. Error 18456.",
            ),
            DependencyErrorCode.AUTH_FAILED,
        ),
        (
            pymssql.OperationalError(
                "DB-Lib error 20009: unable to connect.",
            ),
            DependencyErrorCode.CONNECTION_REFUSED,
        ),
        (
            pymssql.OperationalError(
                "Unknown low-level connection failure.",
            ),
            DependencyErrorCode.UNKNOWN_ERROR,
        ),
    ],
)
async def test_connection_failures_are_safely_mapped(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
    driver_error: pymssql.OperationalError,
    expected_code: DependencyErrorCode,
) -> None:
    """Map connection failures without exposing raw details."""

    connect_mock, _, _ = mocked_sqlserver
    connect_mock.side_effect = driver_error

    asset = _sqlserver_asset()

    with pytest.raises(
        SQLServerSamplingError,
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == expected_code
    assert str(driver_error) not in str(error_info.value)
    assert "sensitive-user" not in str(error_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver_error", "expected_code"),
    [
        (
            pymssql.OperationalError(
                "Query timeout expired against sensitive table.",
            ),
            DependencyErrorCode.TIMEOUT,
        ),
        (
            pymssql.OperationalError(
                "Unexpected query execution failure.",
            ),
            DependencyErrorCode.QUERY_FAILED,
        ),
        (
            pymssql.DatabaseError(
                "Invalid object name dbo.users.",
            ),
            DependencyErrorCode.QUERY_FAILED,
        ),
    ],
)
async def test_query_failures_are_safely_mapped(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
    driver_error: pymssql.Error,
    expected_code: DependencyErrorCode,
) -> None:
    """Map query failures without exposing schema details."""

    _, _, cursor = mocked_sqlserver
    cursor.execute.side_effect = driver_error

    asset = _sqlserver_asset()

    with pytest.raises(
        SQLServerSamplingError,
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == expected_code
    assert str(driver_error) not in str(error_info.value)
    assert "dbo.users" not in str(error_info.value)


@pytest.mark.asyncio
async def test_executor_timeout_is_safely_mapped(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Return TIMEOUT while the blocking worker completes separately."""

    worker_started = threading.Event()
    release_worker = threading.Event()

    def slow_fetch(
        **_: object,
    ) -> tuple[SampledRow, ...]:
        worker_started.set()
        release_worker.wait(
            timeout=1.0,
        )
        return ()

    asset = _sqlserver_asset()

    try:
        with patch(
            _FETCH_TARGET,
            side_effect=slow_fetch,
        ):
            with pytest.raises(
                SQLServerSamplingError,
                match="executor timed out",
            ) as error_info:
                await get_sqlserver_sample(
                    catalog=_catalog(asset),
                    asset_id=asset.asset_id,
                    host="localhost",
                    port=1433,
                    user="sampling_user",
                    password="secret",
                    timeout_seconds=0.01,
                    executor=sql_executor,
                )

        assert worker_started.is_set()
        assert error_info.value.code == (DependencyErrorCode.TIMEOUT)
    finally:
        release_worker.set()


@pytest.mark.asyncio
async def test_caller_cancellation_is_preserved(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Propagate task cancellation without converting it to TIMEOUT."""

    loop = asyncio.get_running_loop()
    worker_started = asyncio.Event()
    release_worker = threading.Event()

    def blocking_fetch(
        **_: object,
    ) -> tuple[SampledRow, ...]:
        loop.call_soon_threadsafe(
            worker_started.set,
        )
        release_worker.wait(
            timeout=1.0,
        )
        return ()

    asset = _sqlserver_asset()

    try:
        with patch(
            _FETCH_TARGET,
            side_effect=blocking_fetch,
        ):
            task = asyncio.create_task(
                get_sqlserver_sample(
                    catalog=_catalog(asset),
                    asset_id=asset.asset_id,
                    host="localhost",
                    port=1433,
                    user="sampling_user",
                    password="secret",
                    timeout_seconds=5.0,
                    executor=sql_executor,
                ),
            )

            await worker_started.wait()
            task.cancel()

            with pytest.raises(
                asyncio.CancelledError,
            ):
                await task
    finally:
        release_worker.set()


@pytest.mark.asyncio
async def test_shutdown_executor_is_safely_rejected() -> None:
    """Map an unavailable executor to UNINITIALIZED."""

    executor = ThreadPoolExecutor(
        max_workers=1,
    )
    executor.shutdown(
        wait=True,
        cancel_futures=True,
    )

    asset = _sqlserver_asset()

    with pytest.raises(
        SQLServerSamplingError,
        match="executor is unavailable",
    ) as error_info:
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host="localhost",
            port=1433,
            user="sampling_user",
            password="secret",
            timeout_seconds=2.0,
            executor=executor,
        )

    assert error_info.value.code == (DependencyErrorCode.UNINITIALIZED)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "port", "user", "timeout_seconds"),
    [
        (
            "",
            1433,
            "sampling_user",
            2.0,
        ),
        (
            " localhost",
            1433,
            "sampling_user",
            2.0,
        ),
        (
            "localhost",
            0,
            "sampling_user",
            2.0,
        ),
        (
            "localhost",
            65_536,
            "sampling_user",
            2.0,
        ),
        (
            "localhost",
            1433,
            "",
            2.0,
        ),
        (
            "localhost",
            1433,
            " sampling_user",
            2.0,
        ),
        (
            "localhost",
            1433,
            "sampling_user",
            0.0,
        ),
        (
            "localhost",
            1433,
            "sampling_user",
            float("nan"),
        ),
        (
            "localhost",
            1433,
            "sampling_user",
            float("inf"),
        ),
    ],
)
async def test_invalid_execution_configuration_is_rejected(
    mocked_sqlserver: tuple[
        MagicMock,
        MagicMock,
        MagicMock,
    ],
    sql_executor: ThreadPoolExecutor,
    host: str,
    port: int,
    user: str,
    timeout_seconds: float,
) -> None:
    """Reject malformed execution configuration before SQL access."""

    connect_mock, _, _ = mocked_sqlserver

    asset = _sqlserver_asset()

    with pytest.raises(ValueError):
        await get_sqlserver_sample(
            catalog=_catalog(asset),
            asset_id=asset.asset_id,
            host=host,
            port=port,
            user=user,
            password="secret",
            timeout_seconds=timeout_seconds,
            executor=sql_executor,
        )

    connect_mock.assert_not_called()
