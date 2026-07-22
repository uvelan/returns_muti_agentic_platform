import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from neo4j import AsyncDriver
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.shared.contracts import DependencyProbeResult

logger = logging.getLogger("return_platform.resources")


class DependencyNotInitializedError(Exception):
    """Raised when a dependency has not been initialized."""


class DependencyHealthCheckFailedError(Exception):
    """Raised when a dependency explicitly reports an unhealthy state."""


class AsyncValkeyClient(Protocol):
    """Minimum asynchronous Valkey operations required by the application."""

    async def ping(self) -> bool:
        """Check whether Valkey is available."""
        ...

    async def aclose(self) -> None:
        """Close the Valkey client and its connection pool."""
        ...


@dataclass(frozen=True, slots=True)
class CachedProbeResult:
    """Immutable cached infrastructure-probe result."""

    stored_at: float
    value: DependencyProbeResult


@dataclass(slots=True)
class SQLProbeManager:
    """Own the bounded executor and active SQL Server probe future."""

    active_future: asyncio.Future[int] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="sql_probe_worker",
        )
    )


@dataclass(slots=True)
class RuntimeResources:
    """Immutable collection of initialized application resources."""

    settings: Settings
    catalog: LoadedAssetCatalog
    mongo: AsyncMongoClient[dict[str, object]] | None = None
    neo4j: AsyncDriver | None = None
    valkey: AsyncValkeyClient | None = None
    temporal: Client | None = None

    sql_manager: SQLProbeManager = field(default_factory=SQLProbeManager)

    probe_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    probe_cache: dict[str, CachedProbeResult] = field(default_factory=dict)

    async def execute_single_flight_probe(
        self,
        *,
        key: str,
        probe_coro: Callable[[], Awaitable[DependencyProbeResult]],
        ttl_seconds: float = 2.0,
    ) -> DependencyProbeResult:
        """
        Execute one probe at a time for a dependency.

        Concurrent callers wait for the same dependency lock. A recent result is
        returned from the short-lived cache instead of executing another probe.
        """
        normalized_key = key.strip().lower()

        if not normalized_key:
            raise ValueError("Probe key must not be blank.")

        if ttl_seconds <= 0:
            raise ValueError("Probe cache TTL must be greater than zero.")

        lock = self.probe_locks.get(normalized_key)

        if lock is None:
            lock = asyncio.Lock()
            self.probe_locks[normalized_key] = lock

        async with lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            cached_result = self.probe_cache.get(normalized_key)

            if cached_result is not None and now - cached_result.stored_at < ttl_seconds:
                return cached_result.value

            result = await probe_coro()

            self.probe_cache[normalized_key] = CachedProbeResult(
                stored_at=loop.time(),
                value=result,
            )

            return result

    def clear_probe_cache(self, key: str | None = None) -> None:
        """Clear one cached probe result or the complete probe cache."""
        if key is None:
            self.probe_cache.clear()
            return

        normalized_key = key.strip().lower()

        if normalized_key:
            self.probe_cache.pop(normalized_key, None)


async def _close_mongodb(resources: RuntimeResources) -> None:
    mongo = resources.mongo

    if mongo is None:
        return

    try:
        await mongo.close()
    except Exception as exc:
        logger.exception(
            "dependency_shutdown_failed",
            extra={
                "dependency": "mongodb",
                "error_type": type(exc).__name__,
            },
        )


async def _close_neo4j(resources: RuntimeResources) -> None:
    neo4j = resources.neo4j

    if neo4j is None:
        return

    try:
        await neo4j.close()
    except Exception as exc:
        logger.exception(
            "dependency_shutdown_failed",
            extra={
                "dependency": "neo4j",
                "error_type": type(exc).__name__,
            },
        )


async def _close_valkey(resources: RuntimeResources) -> None:
    valkey = resources.valkey

    if valkey is None:
        return

    try:
        await valkey.aclose()
    except Exception as exc:
        logger.exception(
            "dependency_shutdown_failed",
            extra={
                "dependency": "valkey",
                "error_type": type(exc).__name__,
            },
        )


async def _observe_active_sql_probe(resources: RuntimeResources) -> None:
    active_future = resources.sql_manager.active_future

    if active_future is None:
        return

    if active_future.done():
        try:
            error = active_future.exception()
        except asyncio.CancelledError:
            logger.info(
                "sql_probe_cancelled_before_shutdown",
                extra={"dependency": "sqlserver"},
            )
            return

        if error is not None:
            logger.error(
                "sql_probe_completed_with_error_before_shutdown",
                extra={
                    "dependency": "sqlserver",
                    "error_type": type(error).__name__,
                },
            )

        return

    try:
        await asyncio.wait_for(
            asyncio.shield(active_future),
            timeout=2.0,
        )
    except TimeoutError:
        logger.warning(
            "sql_probe_still_running_during_shutdown",
            extra={"dependency": "sqlserver"},
        )
    except asyncio.CancelledError:
        logger.info(
            "sql_probe_cancelled_during_shutdown",
            extra={"dependency": "sqlserver"},
        )
    except Exception as exc:
        logger.exception(
            "sql_probe_failed_during_shutdown",
            extra={
                "dependency": "sqlserver",
                "error_type": type(exc).__name__,
            },
        )


def _shutdown_sql_executor(resources: RuntimeResources) -> None:
    try:
        resources.sql_manager.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
    except Exception as exc:
        logger.exception(
            "dependency_shutdown_failed",
            extra={
                "dependency": "sqlserver_executor",
                "error_type": type(exc).__name__,
            },
        )


async def close_resources(resources: RuntimeResources) -> None:
    """Close initialized resources without interrupting teardown."""
    await _close_mongodb(resources)
    await _close_neo4j(resources)
    await _close_valkey(resources)
    await _observe_active_sql_probe(resources)

    _shutdown_sql_executor(resources)

    resources.probe_cache.clear()
    resources.probe_locks.clear()
