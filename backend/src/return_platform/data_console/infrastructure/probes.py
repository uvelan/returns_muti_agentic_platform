import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from math import ceil
from time import perf_counter
from typing import cast

import pymssql
from fastapi import Request
from neo4j.exceptions import AuthError, ServiceUnavailable
from pymongo.errors import ConnectionFailure, OperationFailure
from redis.exceptions import (
    AuthenticationError as RedisAuthenticationError,
)
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)

from return_platform.configuration.settings import Settings
from return_platform.resources import (
    DependencyHealthCheckFailedError,
    DependencyNotInitializedError,
    RuntimeResources,
)
from return_platform.shared.contracts import (
    DependencyErrorCode,
    DependencyProbeResult,
    DependencyStatus,
)

logger = logging.getLogger("return_platform.probes")


type ProbeCallable = Callable[[], Awaitable[None]]


def _get_resources(request: Request) -> RuntimeResources:
    return cast(RuntimeResources, request.app.state.resources)


def _get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _probe_result(
    *,
    status: DependencyStatus,
    latency_ms: int | None = None,
    error_code: DependencyErrorCode | None = None,
    safe_message: str | None = None,
) -> DependencyProbeResult:
    return DependencyProbeResult(
        status=status,
        latency_ms=latency_ms,
        checked_at=datetime.now(UTC),
        error_code=error_code,
        safe_message=safe_message,
    )


async def _measure_probe(
    probe: ProbeCallable,
    source_name: str,
    timeout_seconds: float,
) -> DependencyProbeResult:
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    try:
        async with asyncio.timeout(timeout_seconds):
            await probe()

        return _probe_result(
            status=DependencyStatus.HEALTHY,
            latency_ms=int((loop.time() - started_at) * 1000),
        )
    except DependencyNotInitializedError:
        return _probe_result(
            status=DependencyStatus.UNAVAILABLE,
            error_code=DependencyErrorCode.UNINITIALIZED,
            safe_message="Dependency is not initialized.",
        )
    except DependencyHealthCheckFailedError:
        return _probe_result(
            status=DependencyStatus.UNAVAILABLE,
            error_code=DependencyErrorCode.HEALTH_CHECK_FAILED,
            safe_message="Dependency reported an unhealthy status.",
        )
    except TimeoutError:
        logger.error(
            "dependency_probe_timed_out",
            extra={"dependency": source_name},
        )
        return _probe_result(
            status=DependencyStatus.UNAVAILABLE,
            error_code=DependencyErrorCode.TIMEOUT,
            safe_message="Dependency did not respond within the timeout.",
        )
    except Exception as exc:
        logger.exception(
            "dependency_probe_failed",
            extra={
                "dependency": source_name,
                "error_type": type(exc).__name__,
            },
        )

        error_code, safe_message = _classify_async_probe_error(exc)

        return _probe_result(
            status=DependencyStatus.UNAVAILABLE,
            error_code=error_code,
            safe_message=safe_message,
        )


def _classify_async_probe_error(
    exc: Exception,
) -> tuple[DependencyErrorCode, str]:
    if isinstance(exc, AuthError):
        return (
            DependencyErrorCode.AUTH_FAILED,
            "Authentication was rejected.",
        )

    if isinstance(exc, OperationFailure):
        if exc.code == 18:
            return (
                DependencyErrorCode.AUTH_FAILED,
                "Authentication was rejected.",
            )

        return (
            DependencyErrorCode.QUERY_FAILED,
            "MongoDB operation failed.",
        )

    if isinstance(exc, RedisAuthenticationError):
        return (
            DependencyErrorCode.AUTH_FAILED,
            "Authentication was rejected.",
        )

    if isinstance(
        exc,
        (
            ConnectionFailure,
            ServiceUnavailable,
            RedisConnectionError,
        ),
    ):
        return (
            DependencyErrorCode.CONNECTION_REFUSED,
            "Connection was refused or the service is unavailable.",
        )

    return (
        DependencyErrorCode.UNKNOWN_ERROR,
        "An unexpected dependency error occurred.",
    )


async def probe_mongodb(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)

    async def ping() -> None:
        mongo = resources.mongo

        if mongo is None:
            raise DependencyNotInitializedError

        await mongo.admin.command("ping")

    async def execute() -> DependencyProbeResult:
        return await _measure_probe(
            ping,
            "mongodb",
            settings.probe_timeout_seconds,
        )

    return await resources.execute_single_flight_probe(
        key="mongodb",
        probe_coro=execute,
        ttl_seconds=2.0,
    )


async def probe_source_mongodb(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)

    async def ping() -> None:
        source_mongo = resources.source_mongo
        if source_mongo is None:
            raise DependencyNotInitializedError
        await source_mongo[settings.source_mongo_database].command("ping")

    async def execute() -> DependencyProbeResult:
        return await _measure_probe(
            ping,
            "source_mongodb",
            settings.probe_timeout_seconds,
        )

    return await resources.execute_single_flight_probe(
        key="source_mongodb",
        probe_coro=execute,
        ttl_seconds=2.0,
    )


async def probe_sqlserver(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)
    manager = resources.sql_manager

    driver_timeout_seconds = max(
        1,
        ceil(settings.probe_timeout_seconds),
    )

    def ping_sync() -> int:
        started_at = perf_counter()

        with pymssql.connect(
            server=settings.sqlserver_host,
            user=settings.sqlserver_user,
            password=settings.sqlserver_password.get_secret_value(),
            database=settings.sqlserver_database,
            login_timeout=driver_timeout_seconds,
            timeout=driver_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()

        if row is None or row[0] != 1:
            raise DependencyHealthCheckFailedError(
                "SQL Server returned an unexpected health-check result."
            )

        return int((perf_counter() - started_at) * 1000)

    async def execute() -> DependencyProbeResult:
        async with manager.lock:
            active_future = manager.active_future

            if active_future is not None and not active_future.done():
                future = active_future
            else:
                future = asyncio.get_running_loop().run_in_executor(
                    manager.executor,
                    ping_sync,
                )
                manager.active_future = future

        try:
            latency_ms = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=settings.probe_timeout_seconds,
            )
        except TimeoutError:
            logger.error(
                "dependency_probe_timed_out",
                extra={"dependency": "sqlserver"},
            )
            return _probe_result(
                status=DependencyStatus.UNAVAILABLE,
                error_code=DependencyErrorCode.TIMEOUT,
                safe_message="SQL Server did not respond within the timeout.",
            )
        except DependencyHealthCheckFailedError:
            return _probe_result(
                status=DependencyStatus.UNAVAILABLE,
                error_code=DependencyErrorCode.HEALTH_CHECK_FAILED,
                safe_message="SQL Server returned an unexpected probe result.",
            )
        except pymssql.OperationalError as exc:
            logger.exception(
                "dependency_probe_failed",
                extra={
                    "dependency": "sqlserver",
                    "error_type": type(exc).__name__,
                },
            )
            return _probe_result(
                status=DependencyStatus.UNAVAILABLE,
                error_code=DependencyErrorCode.QUERY_FAILED,
                safe_message="SQL Server operation failed.",
            )
        except Exception as exc:
            logger.exception(
                "dependency_probe_failed",
                extra={
                    "dependency": "sqlserver",
                    "error_type": type(exc).__name__,
                },
            )
            return _probe_result(
                status=DependencyStatus.UNAVAILABLE,
                error_code=DependencyErrorCode.UNKNOWN_ERROR,
                safe_message="An unexpected SQL Server error occurred.",
            )

        return _probe_result(
            status=DependencyStatus.HEALTHY,
            latency_ms=latency_ms,
        )

    return await resources.execute_single_flight_probe(
        key="sqlserver",
        probe_coro=execute,
        ttl_seconds=2.0,
    )


async def probe_neo4j(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)

    async def ping() -> None:
        neo4j = resources.neo4j

        if neo4j is None:
            raise DependencyNotInitializedError

        await neo4j.verify_connectivity()

    async def execute() -> DependencyProbeResult:
        return await _measure_probe(
            ping,
            "neo4j",
            settings.probe_timeout_seconds,
        )

    return await resources.execute_single_flight_probe(
        key="neo4j",
        probe_coro=execute,
        ttl_seconds=2.0,
    )


async def probe_valkey(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)

    async def ping() -> None:
        valkey = resources.valkey

        if valkey is None:
            raise DependencyNotInitializedError

        await valkey.ping()

    async def execute() -> DependencyProbeResult:
        return await _measure_probe(
            ping,
            "valkey",
            settings.probe_timeout_seconds,
        )

    return await resources.execute_single_flight_probe(
        key="valkey",
        probe_coro=execute,
        ttl_seconds=2.0,
    )


async def probe_temporal(request: Request) -> DependencyProbeResult:
    resources = _get_resources(request)
    settings = _get_settings(request)

    async def ping() -> None:
        temporal = resources.temporal

        if temporal is None:
            raise DependencyNotInitializedError

        healthy = await temporal.service_client.check_health(
            timeout=timedelta(
                seconds=settings.probe_timeout_seconds,
            )
        )

        if not healthy:
            raise DependencyHealthCheckFailedError("Temporal reported an unhealthy status.")

    async def execute() -> DependencyProbeResult:
        return await _measure_probe(
            ping,
            "temporal",
            settings.probe_timeout_seconds,
        )

    return await resources.execute_single_flight_probe(
        key="temporal",
        probe_coro=execute,
        ttl_seconds=2.0,
    )
