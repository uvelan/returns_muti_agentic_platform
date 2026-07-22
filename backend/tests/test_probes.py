"""Tests for probe caching and single-flight execution."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import (
    DependencyProbeResult,
    DependencyStatus,
)


@pytest.fixture
def resources(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> Iterator[RuntimeResources]:
    """Provide a fresh resource container with isolated probe state."""

    runtime_resources = RuntimeResources(
        settings=test_settings,
        catalog=loaded_empty_catalog,
    )

    try:
        yield runtime_resources
    finally:
        runtime_resources.sql_manager.executor.shutdown(
            wait=True,
            cancel_futures=True,
        )


def _healthy_probe_result(
    *,
    latency_ms: int = 10,
) -> DependencyProbeResult:
    """Create a deterministic healthy probe result."""

    return DependencyProbeResult(
        status=DependencyStatus.HEALTHY,
        latency_ms=latency_ms,
        checked_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_single_flight_probe_executes_only_once(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def mock_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        # Keep the first probe active while concurrent callers wait.
        await asyncio.sleep(0.05)

        return _healthy_probe_result(
            latency_ms=15,
        )

    tasks = [
        resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=2.0,
        )
        for _ in range(10)
    ]

    results = await asyncio.gather(
        *tasks,
    )

    assert call_count == 1
    assert len(results) == 10

    for result in results:
        assert (
            result.status
            is DependencyStatus.HEALTHY
        )
        assert result.latency_ms == 15

    first_result = results[0]

    assert all(
        result is first_result
        for result in results
    )


@pytest.mark.asyncio
async def test_probe_cache_expires_after_ttl(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def mock_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        return _healthy_probe_result()

    first_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=0.01,
        )
    )

    assert call_count == 1

    await asyncio.sleep(0.02)

    second_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=0.01,
        )
    )

    assert call_count == 2
    assert second_result is not first_result


@pytest.mark.asyncio
async def test_explicit_cache_clearing(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def mock_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        return _healthy_probe_result()

    first_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    assert call_count == 1

    resources.clear_probe_cache(
        key="mock_database",
    )

    second_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    assert call_count == 2
    assert second_result is not first_result


@pytest.mark.asyncio
async def test_probe_result_is_reused_before_ttl_expires(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def mock_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        return _healthy_probe_result()

    first_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    second_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    assert call_count == 1
    assert second_result is first_result


@pytest.mark.asyncio
async def test_probe_keys_are_normalized(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def mock_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        return _healthy_probe_result()

    first_result = (
        await resources.execute_single_flight_probe(
            key="  MOCK_DATABASE  ",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    second_result = (
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )
    )

    assert call_count == 1
    assert second_result is first_result


@pytest.mark.asyncio
async def test_blank_probe_key_is_rejected(
    resources: RuntimeResources,
) -> None:
    async def mock_probe() -> DependencyProbeResult:
        return _healthy_probe_result()

    with pytest.raises(
        ValueError,
        match="Probe key must not be blank",
    ):
        await resources.execute_single_flight_probe(
            key="   ",
            probe_coro=mock_probe,
            ttl_seconds=5.0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ttl_seconds",
    [
        0.0,
        -1.0,
    ],
)
async def test_non_positive_ttl_is_rejected(
    resources: RuntimeResources,
    ttl_seconds: float,
) -> None:
    async def mock_probe() -> DependencyProbeResult:
        return _healthy_probe_result()

    with pytest.raises(
        ValueError,
        match=(
            "Probe cache TTL must be greater than zero"
        ),
    ):
        await resources.execute_single_flight_probe(
            key="mock_database",
            probe_coro=mock_probe,
            ttl_seconds=ttl_seconds,
        )


@pytest.mark.asyncio
async def test_failed_probe_is_not_cached(
    resources: RuntimeResources,
) -> None:
    call_count = 0

    async def failing_probe() -> DependencyProbeResult:
        nonlocal call_count
        call_count += 1

        raise RuntimeError(
            "simulated probe failure"
        )

    for _ in range(2):
        with pytest.raises(
            RuntimeError,
            match="simulated probe failure",
        ):
            await resources.execute_single_flight_probe(
                key="mock_database",
                probe_coro=failing_probe,
                ttl_seconds=5.0,
            )

    assert call_count == 2
    assert (
        "mock_database"
        not in resources.probe_cache
    )
