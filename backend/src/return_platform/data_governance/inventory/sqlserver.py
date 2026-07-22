"""SQL Server metadata collection through an isolated synchronous executor."""

import asyncio
import math
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Final, Protocol, Self, cast

import pymssql
from pydantic import ValidationError

from return_platform.data_governance.inventory.contracts import (
    SQLServerColumnMetadata,
    SQLServerDataTypeMetadata,
    SQLServerInventory,
    SQLServerRowCountSource,
    SQLServerSchemaMetadata,
    SQLServerTableMetadata,
    SQLServerViewMetadata,
)
from return_platform.shared.contracts import DependencyErrorCode

_DATABASE_NAME_QUERY: Final = """
SELECT DB_NAME() AS database_name;
"""

_TABLES_QUERY: Final = """
SELECT
    t.object_id,
    s.schema_id,
    s.name AS schema_name,
    t.name AS table_name,
    COALESCE(
        SUM(CONVERT(bigint, p.rows)),
        CONVERT(bigint, 0)
    ) AS approximate_row_count
FROM sys.tables AS t
INNER JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
LEFT JOIN sys.partitions AS p
    ON p.object_id = t.object_id
    AND p.index_id IN (0, 1)
WHERE t.is_ms_shipped = 0
GROUP BY
    t.object_id,
    s.schema_id,
    s.name,
    t.name
ORDER BY
    s.schema_id ASC,
    t.object_id ASC;
"""

_VIEWS_QUERY: Final = """
SELECT
    v.object_id,
    s.schema_id,
    s.name AS schema_name,
    v.name AS view_name
FROM sys.views AS v
INNER JOIN sys.schemas AS s
    ON s.schema_id = v.schema_id
WHERE v.is_ms_shipped = 0
ORDER BY
    s.schema_id ASC,
    v.object_id ASC;
"""

_COLUMNS_QUERY: Final = """
SELECT
    c.object_id,
    c.column_id,
    c.name AS column_name,
    type_schema.name AS data_type_schema,
    data_type.name AS data_type_name,
    CONVERT(int, data_type.is_user_defined) AS is_user_defined,
    c.max_length AS max_length_bytes,
    c.precision,
    c.scale,
    CONVERT(int, c.is_nullable) AS is_nullable,
    CONVERT(int, c.is_identity) AS is_identity,
    CONVERT(int, c.is_computed) AS is_computed,
    c.collation_name
FROM sys.columns AS c
INNER JOIN sys.objects AS sql_object
    ON sql_object.object_id = c.object_id
INNER JOIN sys.types AS data_type
    ON data_type.user_type_id = c.user_type_id
INNER JOIN sys.schemas AS type_schema
    ON type_schema.schema_id = data_type.schema_id
WHERE
    sql_object.type IN ('U', 'V')
    AND sql_object.is_ms_shipped = 0
ORDER BY
    c.object_id ASC,
    c.column_id ASC;
"""

_AUTHENTICATION_MARKERS: Final = (
    "login failed",
    "authentication failed",
    "18456",
)

_TIMEOUT_MARKERS: Final = (
    "query timeout",
    "timeout expired",
    "timed out",
)

_CONNECTION_MARKERS: Final = (
    "20009",
    "connection refused",
    "server is unavailable",
    "adaptive server is unavailable",
    "unable to connect",
    "connection failed",
)


MetadataRow = Mapping[str, object]


class _DictionaryCursor(Protocol):
    """Typed boundary for the dictionary-based PyMSSQL cursor."""

    def __enter__(self) -> Self:
        """Enter the cursor context."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Exit the cursor context."""

    def execute(self, operation: str) -> None:
        """Execute one SQL statement."""

    def fetchall(self) -> list[MetadataRow]:
        """Return all rows from the current result set."""


class _DictionaryConnection(Protocol):
    """Typed boundary for the dictionary-based PyMSSQL connection."""

    def __enter__(self) -> Self:
        """Enter the connection context."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Exit the connection context."""

    def cursor(self) -> _DictionaryCursor:
        """Create a dictionary-based cursor."""


class SQLServerInventoryError(RuntimeError):
    """Safe SQL Server inventory failure exposed outside this module."""

    code: DependencyErrorCode

    def __init__(
        self,
        *,
        code: DependencyErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


class _MetadataMappingError(ValueError):
    """Internal failure caused by malformed or inconsistent metadata rows."""


@dataclass(frozen=True, slots=True)
class _ObservedTableRow:
    """Strictly decoded table row."""

    object_id: int
    schema_id: int
    schema_name: str
    table_name: str
    approximate_row_count: int


@dataclass(frozen=True, slots=True)
class _ObservedViewRow:
    """Strictly decoded view row."""

    object_id: int
    schema_id: int
    schema_name: str
    view_name: str


@dataclass(frozen=True, slots=True)
class _ObservedColumnRow:
    """Strictly decoded column row."""

    object_id: int
    metadata: SQLServerColumnMetadata


@dataclass(slots=True)
class _SchemaBuilder:
    """Mutable internal builder used before immutable validation."""

    name: str
    tables: list[SQLServerTableMetadata] = field(default_factory=list)
    views: list[SQLServerViewMetadata] = field(default_factory=list)


def _read_required_value(
    row: MetadataRow,
    field_name: str,
) -> object:
    """Read a required field without allowing missing keys."""

    try:
        return row[field_name]
    except KeyError as error:
        raise _MetadataMappingError(
            f"SQL Server metadata row is missing {field_name!r}.",
        ) from error


def _read_integer(
    row: MetadataRow,
    field_name: str,
) -> int:
    """Read an integer without string or Boolean coercion."""

    value = _read_required_value(row, field_name)

    if isinstance(value, bool) or not isinstance(value, int):
        raise _MetadataMappingError(
            f"SQL Server metadata field {field_name!r} is not an integer.",
        )

    return value


def _read_boolean(
    row: MetadataRow,
    field_name: str,
) -> bool:
    """Read a SQL bit represented as a Boolean or integer zero/one."""

    value = _read_required_value(row, field_name)

    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in (0, 1):
        return value == 1

    raise _MetadataMappingError(
        f"SQL Server metadata field {field_name!r} is not Boolean.",
    )


def _read_text(
    row: MetadataRow,
    field_name: str,
) -> str:
    """Read and normalize a required nonblank text value."""

    value = _read_required_value(row, field_name)

    if not isinstance(value, str):
        raise _MetadataMappingError(
            f"SQL Server metadata field {field_name!r} is not text.",
        )

    normalized = value.strip()

    if not normalized:
        raise _MetadataMappingError(
            f"SQL Server metadata field {field_name!r} is blank.",
        )

    return normalized


def _read_optional_text(
    row: MetadataRow,
    field_name: str,
) -> str | None:
    """Read an optional nonblank SQL text value."""

    value = _read_required_value(row, field_name)

    if value is None:
        return None

    if not isinstance(value, str):
        raise _MetadataMappingError(
            f"SQL Server metadata field {field_name!r} is not text.",
        )

    normalized = value.strip()

    if not normalized:
        raise _MetadataMappingError(
            f"SQL Server metadata field {field_name!r} is blank.",
        )

    return normalized


def _execute_query(
    cursor: _DictionaryCursor,
    query: str,
) -> list[MetadataRow]:
    """Execute one fixed metadata query and return dictionary rows."""

    cursor.execute(query)
    return cursor.fetchall()


def _decode_database_name(
    rows: list[MetadataRow],
) -> str:
    """Decode the physical database selected by the connection."""

    if len(rows) != 1:
        raise _MetadataMappingError(
            "SQL Server did not return exactly one database identity row.",
        )

    return _read_text(rows[0], "database_name")


def _decode_tables(
    rows: list[MetadataRow],
) -> list[_ObservedTableRow]:
    """Decode and deterministically order table rows."""

    decoded = [
        _ObservedTableRow(
            object_id=_read_integer(metadata_row, "object_id"),
            schema_id=_read_integer(metadata_row, "schema_id"),
            schema_name=_read_text(metadata_row, "schema_name"),
            table_name=_read_text(metadata_row, "table_name"),
            approximate_row_count=_read_integer(
                metadata_row,
                "approximate_row_count",
            ),
        )
        for metadata_row in rows
    ]

    decoded.sort(
        key=lambda table_row: (
            table_row.schema_id,
            table_row.object_id,
        ),
    )

    return decoded


def _decode_views(
    rows: list[MetadataRow],
) -> list[_ObservedViewRow]:
    """Decode and deterministically order view rows."""

    decoded = [
        _ObservedViewRow(
            object_id=_read_integer(metadata_row, "object_id"),
            schema_id=_read_integer(metadata_row, "schema_id"),
            schema_name=_read_text(metadata_row, "schema_name"),
            view_name=_read_text(metadata_row, "view_name"),
        )
        for metadata_row in rows
    ]

    decoded.sort(
        key=lambda view_row: (
            view_row.schema_id,
            view_row.object_id,
        ),
    )

    return decoded


def _decode_columns(
    rows: list[MetadataRow],
) -> list[_ObservedColumnRow]:
    """Decode columns into immutable metadata contracts."""

    decoded = [
        _ObservedColumnRow(
            object_id=_read_integer(metadata_row, "object_id"),
            metadata=SQLServerColumnMetadata(
                column_id=_read_integer(
                    metadata_row,
                    "column_id",
                ),
                name=_read_text(
                    metadata_row,
                    "column_name",
                ),
                data_type=SQLServerDataTypeMetadata(
                    schema_name=_read_text(
                        metadata_row,
                        "data_type_schema",
                    ),
                    name=_read_text(
                        metadata_row,
                        "data_type_name",
                    ),
                    is_user_defined=_read_boolean(
                        metadata_row,
                        "is_user_defined",
                    ),
                    max_length_bytes=_read_integer(
                        metadata_row,
                        "max_length_bytes",
                    ),
                    precision=_read_integer(
                        metadata_row,
                        "precision",
                    ),
                    scale=_read_integer(
                        metadata_row,
                        "scale",
                    ),
                ),
                is_nullable=_read_boolean(
                    metadata_row,
                    "is_nullable",
                ),
                is_identity=_read_boolean(
                    metadata_row,
                    "is_identity",
                ),
                is_computed=_read_boolean(
                    metadata_row,
                    "is_computed",
                ),
                collation_name=_read_optional_text(
                    metadata_row,
                    "collation_name",
                ),
            ),
        )
        for metadata_row in rows
    ]

    decoded.sort(
        key=lambda column_row: (
            column_row.object_id,
            column_row.metadata.column_id,
        ),
    )

    return decoded


def _get_schema_builder(
    builders: dict[int, _SchemaBuilder],
    schema_names: dict[str, int],
    *,
    schema_id: int,
    schema_name: str,
) -> _SchemaBuilder:
    """Return a consistent schema builder or reject identity collisions."""

    known_schema_id = schema_names.get(schema_name)

    if known_schema_id is not None and known_schema_id != schema_id:
        raise _MetadataMappingError(
            "SQL Server returned one schema name for multiple schema IDs.",
        )

    builder = builders.get(schema_id)

    if builder is None:
        builder = _SchemaBuilder(name=schema_name)
        builders[schema_id] = builder
        schema_names[schema_name] = schema_id
        return builder

    if builder.name != schema_name:
        raise _MetadataMappingError(
            "SQL Server returned one schema ID with multiple names.",
        )

    return builder


def _build_inventory(
    *,
    database_name: str,
    table_rows: list[_ObservedTableRow],
    view_rows: list[_ObservedViewRow],
    column_rows: list[_ObservedColumnRow],
) -> SQLServerInventory:
    """Build an immutable inventory from strictly decoded metadata rows."""

    table_object_ids = tuple(table_row.object_id for table_row in table_rows)
    view_object_ids = tuple(view_row.object_id for view_row in view_rows)
    all_object_ids = (
        *table_object_ids,
        *view_object_ids,
    )

    if len(set(all_object_ids)) != len(all_object_ids):
        raise _MetadataMappingError(
            "SQL Server returned duplicate object IDs.",
        )

    known_object_ids = set(all_object_ids)
    columns_by_object: dict[int, list[SQLServerColumnMetadata]] = {}

    for column_row in column_rows:
        if column_row.object_id not in known_object_ids:
            raise _MetadataMappingError(
                "SQL Server returned a column for an unknown object.",
            )

        columns_by_object.setdefault(
            column_row.object_id,
            [],
        ).append(column_row.metadata)

    schema_builders: dict[int, _SchemaBuilder] = {}
    schema_names: dict[str, int] = {}

    for table_row in table_rows:
        builder = _get_schema_builder(
            schema_builders,
            schema_names,
            schema_id=table_row.schema_id,
            schema_name=table_row.schema_name,
        )

        builder.tables.append(
            SQLServerTableMetadata(
                object_id=table_row.object_id,
                name=table_row.table_name,
                approximate_row_count=(table_row.approximate_row_count),
                row_count_source=(SQLServerRowCountSource.SYS_PARTITIONS),
                columns=tuple(
                    columns_by_object.get(
                        table_row.object_id,
                        (),
                    ),
                ),
            ),
        )

    for view_row in view_rows:
        builder = _get_schema_builder(
            schema_builders,
            schema_names,
            schema_id=view_row.schema_id,
            schema_name=view_row.schema_name,
        )

        builder.views.append(
            SQLServerViewMetadata(
                object_id=view_row.object_id,
                name=view_row.view_name,
                columns=tuple(
                    columns_by_object.get(
                        view_row.object_id,
                        (),
                    ),
                ),
            ),
        )

    schemas = tuple(
        SQLServerSchemaMetadata(
            schema_id=schema_id,
            name=builder.name,
            tables=tuple(builder.tables),
            views=tuple(builder.views),
        )
        for schema_id, builder in sorted(schema_builders.items())
    )

    return SQLServerInventory(
        database_name=database_name,
        observed_at=datetime.now(UTC),
        schemas=schemas,
    )


def _normalize_driver_error(
    error: BaseException,
) -> str:
    """Normalize driver arguments only for private classification."""

    return " ".join(str(argument) for argument in error.args).casefold()


def _map_operational_error(
    error: pymssql.OperationalError,
) -> SQLServerInventoryError:
    """Map a raw operational failure to a safe public error."""

    normalized = _normalize_driver_error(error)

    if any(marker in normalized for marker in _AUTHENTICATION_MARKERS):
        return SQLServerInventoryError(
            code=DependencyErrorCode.AUTH_FAILED,
            message="SQL Server authentication was rejected.",
        )

    if any(marker in normalized for marker in _TIMEOUT_MARKERS):
        return SQLServerInventoryError(
            code=DependencyErrorCode.TIMEOUT,
            message="SQL Server metadata retrieval timed out.",
        )

    if any(marker in normalized for marker in _CONNECTION_MARKERS):
        return SQLServerInventoryError(
            code=DependencyErrorCode.CONNECTION_REFUSED,
            message="SQL Server is unavailable.",
        )

    return SQLServerInventoryError(
        code=DependencyErrorCode.UNKNOWN_ERROR,
        message="SQL Server metadata retrieval failed.",
    )


def _fetch_sqlserver_metadata_sync(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout_seconds: int,
) -> SQLServerInventory:
    """Execute fixed SQL Server metadata queries synchronously."""

    try:
        raw_connection = pymssql.connect(
            server=host,
            port=str(port),
            user=user,
            password=password,
            database=database,
            login_timeout=timeout_seconds,
            timeout=timeout_seconds,
            charset="UTF-8",
            as_dict=True,
            autocommit=True,
            appname="return-platform-data-governance",
        )
    except pymssql.OperationalError as error:
        raise _map_operational_error(error) from None
    except pymssql.Error:
        raise SQLServerInventoryError(
            code=DependencyErrorCode.UNKNOWN_ERROR,
            message="SQL Server connection failed.",
        ) from None

    connection = cast(
        _DictionaryConnection,
        raw_connection,
    )

    try:
        with connection:
            cursor = connection.cursor()

            with cursor:
                database_rows = _execute_query(
                    cursor,
                    _DATABASE_NAME_QUERY,
                )
                table_rows = _execute_query(
                    cursor,
                    _TABLES_QUERY,
                )
                view_rows = _execute_query(
                    cursor,
                    _VIEWS_QUERY,
                )
                column_rows = _execute_query(
                    cursor,
                    _COLUMNS_QUERY,
                )
    except pymssql.OperationalError as error:
        raise _map_operational_error(error) from None
    except pymssql.Error:
        raise SQLServerInventoryError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="SQL Server metadata query failed.",
        ) from None

    try:
        return _build_inventory(
            database_name=_decode_database_name(
                database_rows,
            ),
            table_rows=_decode_tables(
                table_rows,
            ),
            view_rows=_decode_views(
                view_rows,
            ),
            column_rows=_decode_columns(
                column_rows,
            ),
        )
    except (_MetadataMappingError, ValidationError):
        raise SQLServerInventoryError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="SQL Server returned invalid metadata.",
        ) from None


def _consume_background_result(
    future: asyncio.Future[SQLServerInventory],
) -> None:
    """Retrieve a late worker result after timeout or cancellation."""

    if future.cancelled():
        return

    future.exception()


def _validate_connection_arguments(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout_seconds: float,
) -> None:
    """Reject invalid arguments before scheduling the worker."""

    if not host.strip():
        raise ValueError("SQL Server host must not be blank.")

    if isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError(
            "SQL Server port must be between 1 and 65535.",
        )

    if not user.strip():
        raise ValueError("SQL Server user must not be blank.")

    if not password:
        raise ValueError("SQL Server password must not be blank.")

    if not database.strip():
        raise ValueError(
            "SQL Server database must not be blank.",
        )

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            "SQL Server timeout must be a finite positive number.",
        )


async def get_sqlserver_inventory(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout_seconds: float,
    executor: ThreadPoolExecutor,
) -> SQLServerInventory:
    """Retrieve metadata through the supplied bounded executor."""

    _validate_connection_arguments(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        timeout_seconds=timeout_seconds,
    )

    driver_timeout_seconds = max(
        1,
        math.ceil(timeout_seconds),
    )

    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(
        executor,
        _fetch_sqlserver_metadata_sync,
        host,
        port,
        user,
        password,
        database,
        driver_timeout_seconds,
    )

    try:
        return await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        worker.add_done_callback(
            _consume_background_result,
        )
        raise SQLServerInventoryError(
            code=DependencyErrorCode.TIMEOUT,
            message="SQL Server metadata inventory timed out.",
        ) from None
    except asyncio.CancelledError:
        worker.add_done_callback(
            _consume_background_result,
        )
        raise
