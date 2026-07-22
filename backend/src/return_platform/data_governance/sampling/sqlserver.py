"""Catalog-authorized, bounded SQL Server data sampling."""

import asyncio
import math
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from types import TracebackType
from typing import Final, Protocol, Self, cast

import pymssql

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

MetadataRow = Mapping[str, object]

_APPLICATION_NAME: Final = "return-platform-data-sampling"

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


class _DictionaryCursor(Protocol):
    """Typed boundary for a dictionary-returning PyMSSQL cursor."""

    def __enter__(self) -> Self:
        """Enter the cursor context."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Exit and close the cursor context."""

    def execute(
        self,
        operation: str,
    ) -> None:
        """Execute one SQL statement."""

    def fetchmany(
        self,
        size: int | None = None,
    ) -> list[MetadataRow]:
        """Fetch at most the requested number of dictionary rows."""


class _DictionaryConnection(Protocol):
    """Typed boundary for a dictionary-returning PyMSSQL connection."""

    def __enter__(self) -> Self:
        """Enter the connection context."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Exit and close the connection context."""

    def cursor(self) -> _DictionaryCursor:
        """Create a dictionary-returning cursor."""


class SQLServerSamplingError(RuntimeError):
    """Safe SQL Server sampling failure exposed outside this module."""

    code: DependencyErrorCode

    def __init__(
        self,
        *,
        code: DependencyErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


def _escape_identifier(
    identifier: str,
) -> str:
    """Escape one identifier for SQL Server bracket quoting."""

    return identifier.replace(
        "]",
        "]]",
    )


def _build_bounded_query(
    *,
    namespace: str,
    object_name: str,
    max_rows: int,
) -> str:
    """Build a catalog-controlled and explicitly bounded SELECT."""

    escaped_namespace = _escape_identifier(
        namespace,
    )
    escaped_object_name = _escape_identifier(
        object_name,
    )

    return f"SELECT TOP ({max_rows}) * FROM [{escaped_namespace}].[{escaped_object_name}];"


def _normalize_driver_error(
    error: BaseException,
) -> str:
    """Normalize driver arguments for private error classification."""

    return " ".join(str(argument) for argument in error.args).casefold()


def _map_operational_error(
    error: pymssql.OperationalError,
    *,
    query_started: bool,
) -> SQLServerSamplingError:
    """Map a raw operational failure to a safe public error."""

    normalized = _normalize_driver_error(
        error,
    )

    if any(marker in normalized for marker in _AUTHENTICATION_MARKERS):
        return SQLServerSamplingError(
            code=DependencyErrorCode.AUTH_FAILED,
            message="SQL Server authentication was rejected.",
        )

    if any(marker in normalized for marker in _TIMEOUT_MARKERS):
        return SQLServerSamplingError(
            code=DependencyErrorCode.TIMEOUT,
            message="SQL Server sampling timed out.",
        )

    if any(marker in normalized for marker in _CONNECTION_MARKERS):
        return SQLServerSamplingError(
            code=DependencyErrorCode.CONNECTION_REFUSED,
            message="SQL Server is unavailable.",
        )

    if query_started:
        return SQLServerSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="SQL Server sampling query failed.",
        )

    return SQLServerSamplingError(
        code=DependencyErrorCode.UNKNOWN_ERROR,
        message="SQL Server sampling connection failed.",
    )


def _validate_connection_target(
    *,
    host: str,
    port: int,
    user: str,
) -> None:
    """Reject malformed connection configuration before execution."""

    if not host:
        raise ValueError(
            "SQL Server host must not be empty.",
        )

    if host != host.strip():
        raise ValueError(
            "SQL Server host must not contain surrounding whitespace.",
        )

    if not 1 <= port <= 65_535:
        raise ValueError(
            "SQL Server port must be between 1 and 65535.",
        )

    if not user:
        raise ValueError(
            "SQL Server user must not be empty.",
        )

    if user != user.strip():
        raise ValueError(
            "SQL Server user must not contain surrounding whitespace.",
        )


def _validate_timeout(
    timeout_seconds: float,
) -> None:
    """Require a finite positive operation timeout."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            "SQL Server sampling timeout must be a finite positive number.",
        )


def _sanitize_rows(
    *,
    raw_rows: list[MetadataRow],
    normalized_redaction_fields: frozenset[str],
    max_rows: int,
) -> tuple[SampledRow, ...]:
    """Sanitize returned rows and defensively enforce the row limit."""

    if len(raw_rows) > max_rows:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message=("SQL Server returned more rows than the authorized sampling limit."),
        )

    sanitized_rows: list[SampledRow] = []

    try:
        for raw_row in raw_rows:
            if not isinstance(
                raw_row,
                Mapping,
            ):
                raise SamplingSanitizationError(
                    "SQL Server returned a malformed sample row.",
                )

            sanitized_rows.append(
                sanitize_sample_row(
                    row=raw_row,
                    normalized_redaction_fields=(normalized_redaction_fields),
                ),
            )
    except SamplingSanitizationError:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="SQL Server returned invalid sample data.",
        ) from None

    return tuple(
        sanitized_rows,
    )


def _fetch_sqlserver_sample_sync(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    namespace: str,
    object_name: str,
    max_rows: int,
    normalized_redaction_fields: frozenset[str],
    driver_timeout_seconds: int,
) -> tuple[SampledRow, ...]:
    """Execute one bounded read-only SQL Server sample operation."""

    query = _build_bounded_query(
        namespace=namespace,
        object_name=object_name,
        max_rows=max_rows,
    )

    try:
        raw_connection = pymssql.connect(
            server=host,
            port=str(port),
            user=user,
            password=password,
            database=database,
            login_timeout=driver_timeout_seconds,
            timeout=driver_timeout_seconds,
            charset="UTF-8",
            as_dict=True,
            autocommit=True,
            appname=_APPLICATION_NAME,
            read_only=True,
        )
    except pymssql.OperationalError as error:
        raise _map_operational_error(
            error,
            query_started=False,
        ) from None
    except pymssql.Error:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.UNKNOWN_ERROR,
            message="SQL Server sampling connection failed.",
        ) from None

    connection = cast(
        _DictionaryConnection,
        raw_connection,
    )

    try:
        with connection:
            cursor = connection.cursor()

            with cursor:
                cursor.execute(
                    query,
                )

                raw_rows = cursor.fetchmany(
                    max_rows + 1,
                )
    except pymssql.OperationalError as error:
        raise _map_operational_error(
            error,
            query_started=True,
        ) from None
    except pymssql.Error:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.QUERY_FAILED,
            message="SQL Server sampling query failed.",
        ) from None

    return _sanitize_rows(
        raw_rows=raw_rows,
        normalized_redaction_fields=(normalized_redaction_fields),
        max_rows=max_rows,
    )


def _consume_background_result(
    future: asyncio.Future[tuple[SampledRow, ...]],
) -> None:
    """Consume a late executor result after timeout or cancellation."""

    if future.cancelled():
        return

    future.exception()


async def get_sqlserver_sample(
    *,
    catalog: AssetCatalog,
    asset_id: str,
    host: str,
    port: int,
    user: str,
    password: str,
    timeout_seconds: float,
    executor: ThreadPoolExecutor,
) -> AssetSample:
    """Return a catalog-authorized and sanitized SQL Server sample."""

    _validate_connection_target(
        host=host,
        port=port,
        user=user,
    )
    _validate_timeout(
        timeout_seconds,
    )

    asset = authorize_sampling_asset(
        catalog=catalog,
        asset_id=asset_id,
        expected_store=DataStoreType.SQLSERVER,
    )

    namespace = asset.namespace

    if namespace is None:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.UNKNOWN_ERROR,
            message=("Authorized SQL Server sampling asset has no namespace."),
        )

    max_rows = asset.sampling.max_rows
    normalized_redaction_fields = normalize_redaction_fields(
        asset.sampling.redact_fields,
    )
    driver_timeout_seconds = max(
        1,
        math.ceil(
            timeout_seconds,
        ),
    )

    worker_operation = partial(
        _fetch_sqlserver_sample_sync,
        host=host,
        port=port,
        user=user,
        password=password,
        database=asset.database,
        namespace=namespace,
        object_name=asset.object_name,
        max_rows=max_rows,
        normalized_redaction_fields=(normalized_redaction_fields),
        driver_timeout_seconds=driver_timeout_seconds,
    )

    loop = asyncio.get_running_loop()

    try:
        worker = loop.run_in_executor(
            executor,
            worker_operation,
        )
    except RuntimeError:
        raise SQLServerSamplingError(
            code=DependencyErrorCode.UNINITIALIZED,
            message="SQL Server sampling executor is unavailable.",
        ) from None

    try:
        rows = await asyncio.wait_for(
            asyncio.shield(
                worker,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        worker.add_done_callback(
            _consume_background_result,
        )

        raise SQLServerSamplingError(
            code=DependencyErrorCode.TIMEOUT,
            message="SQL Server sampling executor timed out.",
        ) from None
    except asyncio.CancelledError:
        worker.add_done_callback(
            _consume_background_result,
        )
        raise

    return AssetSample(
        catalog_version=catalog.version,
        asset_id=asset.asset_id,
        store=asset.store,
        database=asset.database,
        namespace=namespace,
        object_name=asset.object_name,
        object_kind=asset.object_kind,
        sampled_at=datetime.now(UTC),
        row_limit=max_rows,
        ordering_guaranteed=False,
        rows=rows,
    )
