"""Unified Data Console inventory API tests."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.configuration.settings import Settings
from return_platform.data_console.api.inventory import Neo4jInventory
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.data_governance.inventory.contracts import (
    MongoDBInventory,
    SQLServerInventory,
)
from return_platform.data_governance.inventory.sqlserver import SQLServerInventoryError
from return_platform.main import create_app
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import DependencyErrorCode

_OBSERVED_AT = datetime(2026, 7, 22, 17, tzinfo=UTC)


@pytest.fixture
def inventory_client(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> Iterator[tuple[TestClient, RuntimeResources]]:
    app: FastAPI = create_app(custom_settings=test_settings)
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    app.state.resources = resources
    with TestClient(app) as client:
        app.state.resources = resources
        yield client, resources
    resources.sql_manager.executor.shutdown(wait=False, cancel_futures=True)


@pytest.mark.usefixtures("isolated_lifespan_dependencies")
def test_inventory_returns_all_healthy_engines(
    inventory_client: tuple[TestClient, RuntimeResources],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = inventory_client

    async def sql_inventory(**kwargs: Any) -> SQLServerInventory:
        del kwargs
        return SQLServerInventory(database_name="returns", observed_at=_OBSERVED_AT, schemas=())

    async def mongo_inventory(**kwargs: Any) -> MongoDBInventory:
        del kwargs
        return MongoDBInventory(database_name="platform", observed_at=_OBSERVED_AT, collections=())

    async def neo_inventory(resources: RuntimeResources) -> Neo4jInventory:
        del resources
        return Neo4jInventory(labels=("Customer",), relationship_types=("HAS_ACCOUNT",))

    monkeypatch.setattr(
        "return_platform.data_console.api.inventory.get_sqlserver_inventory",
        sql_inventory,
    )
    monkeypatch.setattr(
        "return_platform.data_console.api.inventory.get_mongodb_inventory",
        mongo_inventory,
    )
    monkeypatch.setattr(
        "return_platform.data_console.api.inventory._get_neo4j_inventory",
        neo_inventory,
    )

    response = client.get("/data-console/v1/inventory")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["partial"] is False
    assert body["data"]["sqlserver"]["database_name"] == "returns"
    assert body["data"]["mongodb"]["database_name"] == "platform"
    assert body["data"]["neo4j"]["labels"] == ["Customer"]


@pytest.mark.usefixtures("isolated_lifespan_dependencies")
def test_inventory_preserves_healthy_results_on_partial_failure(
    inventory_client: tuple[TestClient, RuntimeResources],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = inventory_client

    async def sql_inventory(**kwargs: Any) -> SQLServerInventory:
        del kwargs
        raise SQLServerInventoryError(
            code=DependencyErrorCode.TIMEOUT,
            message="SQL Server metadata inventory timed out.",
        )

    async def mongo_inventory(**kwargs: Any) -> MongoDBInventory:
        del kwargs
        return MongoDBInventory(database_name="platform", observed_at=_OBSERVED_AT, collections=())

    async def neo_inventory(resources: RuntimeResources) -> Neo4jInventory:
        del resources
        raise TimeoutError

    monkeypatch.setattr(
        "return_platform.data_console.api.inventory.get_sqlserver_inventory",
        sql_inventory,
    )
    monkeypatch.setattr(
        "return_platform.data_console.api.inventory.get_mongodb_inventory",
        mongo_inventory,
    )
    monkeypatch.setattr(
        "return_platform.data_console.api.inventory._get_neo4j_inventory",
        neo_inventory,
    )

    response = client.get("/data-console/v1/inventory")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["partial"] is True
    assert body["data"]["sqlserver"] is None
    assert body["data"]["mongodb"]["database_name"] == "platform"
    assert body["data"]["neo4j"] is None
    assert [warning["source"] for warning in body["meta"]["warnings"]] == [
        "SQLSERVER",
        "NEO4J",
    ]
