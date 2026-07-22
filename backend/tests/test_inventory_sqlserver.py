"""Tests for SQL Server metadata inventory collection."""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pymssql
import pytest

from return_platform.data_governance.inventory.contracts import (
    SQLServerInventory,
)
from return_platform.data_governance.inventory.sqlserver import (
    SQLServerInventoryError,
    get_sqlserver_inventory,
)
from return_platform.shared.contracts import DependencyErrorCode


@pytest.fixture
def sql_executor() -> Iterator[ThreadPoolExecutor]:
    """Provide the bounded executor required by SQL Server operations."""

    executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="sql-inventory-test",
    )

    try:
        yield executor
    finally:
        executor.shutdown(
            wait=True,
            cancel_futures=True,
        )


@pytest.fixture
def mocked_sqlserver() -> Iterator[tuple[MagicMock, MagicMock]]:
    """Provide a mocked dictionary connection and cursor."""

    target = "return_platform.data_governance.inventory.sqlserver.pymssql.connect"

    with patch(target) as connect_mock:
        connection = MagicMock()
        cursor = MagicMock()

        connect_mock.return_value = connection

        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        cursor.__enter__.return_value = cursor

        yield connect_mock, cursor


@pytest.mark.asyncio
async def test_empty_inventory_is_successful(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Treat a database with no visible user objects as valid."""

    connect_mock, cursor = mocked_sqlserver

    cursor.fetchall.side_effect = [
        [{"database_name": "return_platform"}],
        [],
        [],
        [],
    ]

    inventory = await get_sqlserver_inventory(
        host="localhost",
        port=1433,
        user="inventory_user",
        password="test-secret",
        database="configured_database",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    assert inventory.database_name == "return_platform"
    assert inventory.schemas == ()
    assert inventory.table_count == 0
    assert inventory.view_count == 0
    assert inventory.is_empty is True
    assert inventory.observed_at.tzinfo is UTC

    connect_mock.assert_called_once_with(
        server="localhost",
        port="1433",
        user="inventory_user",
        password="test-secret",
        database="configured_database",
        login_timeout=2,
        timeout=2,
        charset="UTF-8",
        as_dict=True,
        autocommit=True,
        appname="return-platform-data-governance",
    )


@pytest.mark.asyncio
async def test_inventory_mapping_is_deterministic(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Normalize unordered driver rows into physical-ID order."""

    _, cursor = mocked_sqlserver

    cursor.fetchall.side_effect = [
        [{"database_name": "return_platform"}],
        [
            {
                "object_id": 300,
                "schema_id": 7,
                "schema_name": "audit",
                "table_name": "events",
                "approximate_row_count": 27,
            },
            {
                "object_id": 200,
                "schema_id": 5,
                "schema_name": "dbo",
                "table_name": "returns",
                "approximate_row_count": 11,
            },
            {
                "object_id": 100,
                "schema_id": 5,
                "schema_name": "dbo",
                "table_name": "orders",
                "approximate_row_count": 42,
            },
        ],
        [
            {
                "object_id": 400,
                "schema_id": 5,
                "schema_name": "dbo",
                "view_name": "open_returns",
            },
        ],
        [
            {
                "object_id": 100,
                "column_id": 2,
                "column_name": "customer_name",
                "data_type_schema": "sys",
                "data_type_name": "nvarchar",
                "is_user_defined": 0,
                "max_length_bytes": 200,
                "precision": 0,
                "scale": 0,
                "is_nullable": 1,
                "is_identity": 0,
                "is_computed": 0,
                "collation_name": ("SQL_Latin1_General_CP1_CI_AS"),
            },
            {
                "object_id": 400,
                "column_id": 1,
                "column_name": "return_id",
                "data_type_schema": "sys",
                "data_type_name": "bigint",
                "is_user_defined": False,
                "max_length_bytes": 8,
                "precision": 19,
                "scale": 0,
                "is_nullable": False,
                "is_identity": False,
                "is_computed": False,
                "collation_name": None,
            },
            {
                "object_id": 100,
                "column_id": 1,
                "column_name": "order_id",
                "data_type_schema": "sys",
                "data_type_name": "bigint",
                "is_user_defined": 0,
                "max_length_bytes": 8,
                "precision": 19,
                "scale": 0,
                "is_nullable": 0,
                "is_identity": 1,
                "is_computed": 0,
                "collation_name": None,
            },
        ],
    ]

    inventory = await get_sqlserver_inventory(
        host="localhost",
        port=1433,
        user="inventory_user",
        password="test-secret",
        database="return_platform",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    assert inventory.table_count == 3
    assert inventory.view_count == 1

    assert [schema.schema_id for schema in inventory.schemas] == [5, 7]

    dbo_schema = inventory.schemas[0]

    assert dbo_schema.name == "dbo"
    assert [table.object_id for table in dbo_schema.tables] == [100, 200]
    assert [view.object_id for view in dbo_schema.views] == [400]

    orders_table = dbo_schema.tables[0]

    assert orders_table.name == "orders"
    assert orders_table.approximate_row_count == 42
    assert [column.column_id for column in orders_table.columns] == [1, 2]

    order_id_column = orders_table.columns[0]

    assert order_id_column.name == "order_id"
    assert order_id_column.is_identity is True
    assert order_id_column.is_nullable is False
    assert order_id_column.data_type.schema_name == "sys"
    assert order_id_column.data_type.name == "bigint"

    customer_name_column = orders_table.columns[1]

    assert customer_name_column.is_nullable is True
    assert customer_name_column.collation_name == ("SQL_Latin1_General_CP1_CI_AS")

    audit_schema = inventory.schemas[1]

    assert audit_schema.name == "audit"
    assert [table.object_id for table in audit_schema.tables] == [300]


@pytest.mark.asyncio
async def test_queries_are_metadata_only(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject accidental application-data scans or modifications."""

    _, cursor = mocked_sqlserver

    cursor.fetchall.side_effect = [
        [{"database_name": "return_platform"}],
        [],
        [],
        [],
    ]

    await get_sqlserver_inventory(
        host="localhost",
        port=1433,
        user="inventory_user",
        password="test-secret",
        database="return_platform",
        timeout_seconds=2.0,
        executor=sql_executor,
    )

    executed_queries = tuple(call.args[0].casefold() for call in cursor.execute.call_args_list)

    assert len(executed_queries) == 4

    combined_queries = "\n".join(executed_queries)

    assert "db_name()" in combined_queries
    assert "sys.tables" in combined_queries
    assert "sys.views" in combined_queries
    assert "sys.columns" in combined_queries
    assert "sys.types" in combined_queries
    assert "sys.partitions" in combined_queries

    assert "count(" not in combined_queries
    assert "insert into" not in combined_queries
    assert "update " not in combined_queries
    assert "delete from" not in combined_queries
    assert "merge " not in combined_queries
    assert "truncate table" not in combined_queries
    assert "drop table" not in combined_queries
    assert "alter table" not in combined_queries


@pytest.mark.asyncio
async def test_collection_runs_in_supplied_executor(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Run synchronous collection on the explicit bounded executor."""

    worker_thread_names: list[str] = []

    def fetch_stub(
        *_arguments: object,
    ) -> SQLServerInventory:
        worker_thread_names.append(
            threading.current_thread().name,
        )

        return SQLServerInventory(
            database_name="return_platform",
            observed_at=datetime.now(UTC),
        )

    target = "return_platform.data_governance.inventory.sqlserver._fetch_sqlserver_metadata_sync"

    with patch(
        target,
        side_effect=fetch_stub,
    ) as fetch_mock:
        inventory = await get_sqlserver_inventory(
            host="localhost",
            port=1433,
            user="inventory_user",
            password="test-secret",
            database="return_platform",
            timeout_seconds=0.2,
            executor=sql_executor,
        )

    assert inventory.is_empty is True
    assert worker_thread_names
    assert worker_thread_names[0].startswith(
        "sql-inventory-test",
    )

    fetch_mock.assert_called_once_with(
        "localhost",
        1433,
        "inventory_user",
        "test-secret",
        "return_platform",
        1,
    )


@pytest.mark.asyncio
async def test_executor_timeout_is_safely_mapped(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Return TIMEOUT while allowing the worker to terminate later."""

    worker_started = threading.Event()
    release_worker = threading.Event()

    def slow_fetch(
        *_arguments: object,
    ) -> SQLServerInventory:
        worker_started.set()
        release_worker.wait(timeout=1.0)

        return SQLServerInventory(
            database_name="return_platform",
            observed_at=datetime.now(UTC),
        )

    target = "return_platform.data_governance.inventory.sqlserver._fetch_sqlserver_metadata_sync"

    try:
        with (
            patch(
                target,
                side_effect=slow_fetch,
            ),
            pytest.raises(
                SQLServerInventoryError,
                match="metadata inventory timed out",
            ) as error_info,
        ):
            await get_sqlserver_inventory(
                host="localhost",
                port=1433,
                user="inventory_user",
                password="test-secret",
                database="return_platform",
                timeout_seconds=0.01,
                executor=sql_executor,
            )

        assert worker_started.is_set()
        assert error_info.value.code == (DependencyErrorCode.TIMEOUT)
    finally:
        release_worker.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver_message", "expected_code"),
    [
        (
            "Login failed for user; error 18456",
            DependencyErrorCode.AUTH_FAILED,
        ),
        (
            "Query timeout expired",
            DependencyErrorCode.TIMEOUT,
        ),
        (
            "DB-Lib error message 20009",
            DependencyErrorCode.CONNECTION_REFUSED,
        ),
        (
            "Unclassified operational failure",
            DependencyErrorCode.UNKNOWN_ERROR,
        ),
    ],
)
async def test_operational_failures_are_safely_mapped(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
    driver_message: str,
    expected_code: DependencyErrorCode,
) -> None:
    """Map raw operational failures without exposing details."""

    connect_mock, _ = mocked_sqlserver
    connect_mock.side_effect = pymssql.OperationalError(
        driver_message,
    )

    with pytest.raises(
        SQLServerInventoryError,
    ) as error_info:
        await get_sqlserver_inventory(
            host="internal-database.example",
            port=1433,
            user="inventory_user",
            password="sensitive-password",
            database="return_platform",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    public_message = str(error_info.value)

    assert error_info.value.code == expected_code
    assert driver_message not in public_message
    assert "sensitive-password" not in public_message
    assert "internal-database.example" not in public_message


@pytest.mark.asyncio
async def test_query_failure_is_safely_mapped(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Map query failures without returning raw SQL errors."""

    _, cursor = mocked_sqlserver
    cursor.execute.side_effect = pymssql.DatabaseError(
        "Invalid object name with internal details",
    )

    with pytest.raises(
        SQLServerInventoryError,
        match="metadata query failed",
    ) as error_info:
        await get_sqlserver_inventory(
            host="localhost",
            port=1433,
            user="inventory_user",
            password="test-secret",
            database="return_platform",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)
    assert "Invalid object name" not in str(error_info.value)


@pytest.mark.asyncio
async def test_malformed_metadata_is_safely_rejected(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject permissive Boolean coercion from malformed rows."""

    _, cursor = mocked_sqlserver

    cursor.fetchall.side_effect = [
        [{"database_name": "return_platform"}],
        [
            {
                "object_id": 100,
                "schema_id": 5,
                "schema_name": "dbo",
                "table_name": "orders",
                "approximate_row_count": 0,
            },
        ],
        [],
        [
            {
                "object_id": 100,
                "column_id": 1,
                "column_name": "order_id",
                "data_type_schema": "sys",
                "data_type_name": "bigint",
                "is_user_defined": 0,
                "max_length_bytes": 8,
                "precision": 19,
                "scale": 0,
                "is_nullable": "0",
                "is_identity": 1,
                "is_computed": 0,
                "collation_name": None,
            },
        ],
    ]

    with pytest.raises(
        SQLServerInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_sqlserver_inventory(
            host="localhost",
            port=1433,
            user="inventory_user",
            password="test-secret",
            database="return_platform",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


@pytest.mark.asyncio
async def test_unknown_column_object_is_safely_rejected(
    mocked_sqlserver: tuple[MagicMock, MagicMock],
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a column referencing an unobserved SQL object."""

    _, cursor = mocked_sqlserver

    cursor.fetchall.side_effect = [
        [{"database_name": "return_platform"}],
        [],
        [],
        [
            {
                "object_id": 999,
                "column_id": 1,
                "column_name": "orphaned_column",
                "data_type_schema": "sys",
                "data_type_name": "int",
                "is_user_defined": 0,
                "max_length_bytes": 4,
                "precision": 10,
                "scale": 0,
                "is_nullable": 0,
                "is_identity": 0,
                "is_computed": 0,
                "collation_name": None,
            },
        ],
    ]

    with pytest.raises(
        SQLServerInventoryError,
        match="returned invalid metadata",
    ) as error_info:
        await get_sqlserver_inventory(
            host="localhost",
            port=1433,
            user="inventory_user",
            password="test-secret",
            database="return_platform",
            timeout_seconds=2.0,
            executor=sql_executor,
        )

    assert error_info.value.code == (DependencyErrorCode.QUERY_FAILED)


async def _assert_invalid_arguments_rejected(
    *,
    sql_executor: ThreadPoolExecutor,
    expected_message: str,
    host: str = "localhost",
    port: int = 1433,
    user: str = "inventory_user",
    password: str = "test-secret",
    database: str = "return_platform",
    timeout_seconds: float = 2.0,
) -> None:
    """Assert invalid arguments fail before SQL execution."""

    target = "return_platform.data_governance.inventory.sqlserver._fetch_sqlserver_metadata_sync"

    with (
        patch(target) as fetch_mock,
        pytest.raises(
            ValueError,
            match=expected_message,
        ),
    ):
        await get_sqlserver_inventory(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            timeout_seconds=timeout_seconds,
            executor=sql_executor,
        )

    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_blank_host_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a blank SQL Server host."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        host="   ",
        expected_message="host must not be blank",
    )


@pytest.mark.asyncio
async def test_invalid_port_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject an out-of-range SQL Server port."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        port=0,
        expected_message="port must be between",
    )


@pytest.mark.asyncio
async def test_boolean_port_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject Boolean values passed as SQL Server ports."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        port=True,
        expected_message="port must be between",
    )


@pytest.mark.asyncio
async def test_blank_user_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a blank SQL Server user."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        user="",
        expected_message="user must not be blank",
    )


@pytest.mark.asyncio
async def test_blank_password_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a blank SQL Server password."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        password="",
        expected_message="password must not be blank",
    )


@pytest.mark.asyncio
async def test_blank_database_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a blank SQL Server database."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        database=" ",
        expected_message="database must not be blank",
    )


@pytest.mark.asyncio
async def test_non_finite_timeout_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a non-finite SQL Server timeout."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        timeout_seconds=float("nan"),
        expected_message=("timeout must be a finite positive number"),
    )


@pytest.mark.asyncio
async def test_infinite_timeout_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject an infinite SQL Server timeout."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        timeout_seconds=float("inf"),
        expected_message=("timeout must be a finite positive number"),
    )


@pytest.mark.asyncio
async def test_non_positive_timeout_is_rejected_before_execution(
    sql_executor: ThreadPoolExecutor,
) -> None:
    """Reject a non-positive SQL Server timeout."""

    await _assert_invalid_arguments_rejected(
        sql_executor=sql_executor,
        timeout_seconds=0.0,
        expected_message=("timeout must be a finite positive number"),
    )
