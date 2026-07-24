"""Tests for liveness, readiness, and correlation-ID behavior."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.main import create_app
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import DependencyProbeResult, DependencyStatus


def _required_environment_variable(
    name: str,
) -> str:
    """
    Return a required environment variable without exposing its value.

    Tests fail immediately with only the variable name when configuration is
    missing. Secret values are never included in error messages.
    """

    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")

    return value


@pytest.fixture
def test_settings(
    empty_catalog_path: Path,
) -> Settings:
    """Provide valid test settings using environment-backed secrets."""

    mongo_username = quote(
        _required_environment_variable("MONGO_ROOT_USERNAME"),
        safe="",
    )

    mongo_password = quote(
        _required_environment_variable("MONGO_ROOT_PASSWORD"),
        safe="",
    )

    return Settings(
        catalog_path=empty_catalog_path,
        environment="test",
        frontend_cors_origin=AnyHttpUrl("http://localhost:5173"),
        mongo_dsn=SecretStr(
            f"mongodb://{mongo_username}:{mongo_password}"
            "@localhost:27017/return_platform"
            "?authSource=admin"
        ),
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password=SecretStr(_required_environment_variable("GRAPH_PASSWORD")),
        valkey_host="localhost",
        valkey_password=SecretStr(_required_environment_variable("VALKEY_PASSWORD")),
        temporal_target="localhost:7233",
        sqlserver_host="localhost",
        sqlserver_user="sa",
        sqlserver_password=SecretStr(_required_environment_variable("MSSQL_SA_PASSWORD")),
        sqlserver_database="test_db",
    )


@pytest.fixture
def test_app(
    test_settings: Settings,
) -> FastAPI:
    """Create an application using isolated test settings."""

    return create_app(custom_settings=test_settings)


@pytest.fixture
def client(
    test_app: FastAPI,
) -> Iterator[TestClient]:
    """
    Provide a client without entering the application lifespan.

    These health tests do not connect to external infrastructure.
    """

    test_client = TestClient(test_app)

    try:
        yield test_client
    finally:
        test_client.close()


def test_liveness_endpoint_returns_200(
    client: TestClient,
) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "alive",
    }


def test_readiness_endpoint_fails_without_lifespan(
    client: TestClient,
) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not initialized",
    }


def test_readiness_endpoint_succeeds_with_resources(
    test_settings: Settings,
    test_app: FastAPI,
    client: TestClient,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = RuntimeResources(
        settings=test_settings,
        catalog=loaded_empty_catalog,
    )

    test_app.state.resources = resources

    async def healthy_probe(*_args: object, **_kwargs: object) -> DependencyProbeResult:
        return DependencyProbeResult(
            status=DependencyStatus.HEALTHY,
            latency_ms=1,
            checked_at=datetime.now(UTC),
        )

    for name in (
        "probe_mongodb",
        "probe_source_mongodb",
        "probe_sqlserver",
        "probe_neo4j",
        "probe_valkey",
        "probe_temporal",
    ):
        monkeypatch.setattr(f"return_platform.main.{name}", healthy_probe)

    try:
        response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["catalog"] == {"version": "1.0", "asset_count": 0}
        assert set(body["dependencies"]) == {
            "mongodb",
            "source_mongodb",
            "sqlserver",
            "neo4j",
            "valkey",
            "temporal",
        }
        assert all(item["status"] == "HEALTHY" for item in body["dependencies"].values())
    finally:
        resources.sql_manager.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        if hasattr(
            test_app.state,
            "resources",
        ):
            del test_app.state.resources


def test_correlation_id_is_preserved(
    client: TestClient,
) -> None:
    correlation_id = "12345678-1234-5678-1234-567812345678"

    response = client.get(
        "/health/live",
        headers={
            "X-Correlation-ID": correlation_id,
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == correlation_id


def test_invalid_correlation_id_is_replaced(
    client: TestClient,
) -> None:
    invalid_correlation_id = "not-a-real-uuid"

    response = client.get(
        "/health/live",
        headers={
            "X-Correlation-ID": invalid_correlation_id,
        },
    )

    generated_correlation_id = response.headers["X-Correlation-ID"]

    assert response.status_code == 200
    assert generated_correlation_id != invalid_correlation_id
    assert str(uuid.UUID(generated_correlation_id)) == generated_correlation_id


def test_missing_correlation_id_is_generated(
    client: TestClient,
) -> None:
    response = client.get("/health/live")

    generated_correlation_id = response.headers["X-Correlation-ID"]

    assert response.status_code == 200
    assert str(uuid.UUID(generated_correlation_id)) == generated_correlation_id
