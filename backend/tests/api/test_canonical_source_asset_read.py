"""`GET /api/config/sources/{source_id}/assets/{asset_id}` -- the field list.

`metadata.fields` is the only place the platform publishes what columns or keys
an asset actually carries, and the handler that returns it has existed all along
on `/data-console/v1/inventory/{engine}/{asset_id}` -- a prefix Wave F1
unmounted deliberately and `test_no_versioned_data_console_path_is_mounted`
keeps unmounted. So the capability was written, tested and unreachable.

What this module owns is the canonical surface over it: that the route is served,
that it still delegates rather than growing a second implementation, that
nesting the asset under its source is a claim the route actually checks, and
that the gate is the source-read capability rather than every principal who may
read a return.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.configuration.api.router import router
from return_platform.configuration.settings import BACKEND_ROOT, Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.data_platform.schema_registry import load_schema_registry
from return_platform.resources import RuntimeResources
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_REGISTRY = load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")


def _client_for(
    test_settings: Settings, loaded_empty_catalog: LoadedAssetCatalog, *role_names: str
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.resources = RuntimeResources(
        settings=test_settings,
        catalog=loaded_empty_catalog,
        schema_registry=_REGISTRY,
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(
    test_settings: Settings, loaded_empty_catalog: LoadedAssetCatalog
) -> Iterator[TestClient]:
    yield from _client_for(test_settings, loaded_empty_catalog, r.CONSOLE_VIEWER)


@pytest.fixture
def sql_asset_id(test_settings: Settings) -> str:
    """A SQL Server asset the registry really declares.

    Taken from the registry rather than named, so this file does not become a
    second place the asset inventory is written down.
    """
    del test_settings
    return next(asset.asset_id for asset in _REGISTRY.assets if asset.engine == "SQLSERVER")


def test_the_asset_read_answers_with_the_field_list(client: TestClient, sql_asset_id: str) -> None:
    """The reason the route exists. Without `fields` this is a 404 with extra steps."""
    response = client.get(f"/api/config/sources/sqlserver/assets/{sql_asset_id}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["assetId"] == sql_asset_id
    assert data["engine"] == "SQL_SERVER"
    assert data["metadata"]["fields"], "the asset detail carries no field list"
    assert {"database", "namespace", "authoritative"} <= set(data["metadata"])


def test_an_asset_that_belongs_to_another_source_is_not_found(
    client: TestClient, sql_asset_id: str
) -> None:
    """The nesting is a claim, so it is checked.

    The delegated handler is keyed by engine and predates the nesting, so a route
    that passed the asset id straight through would answer 200 for
    `/sources/neo4j/assets/<a SQL table>` -- a successful read through a path
    asserting a relationship that does not exist.
    """
    response = client.get(f"/api/config/sources/neo4j/assets/{sql_asset_id}")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "ASSET_NOT_IN_SOURCE"


def test_an_unknown_source_is_reported_as_the_source_being_unknown(client: TestClient) -> None:
    """Distinct from an unknown asset: they need different things done about them."""
    response = client.get("/api/config/sources/no-such-source/assets/anything")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"


def test_an_unknown_asset_within_a_real_source_is_not_found(client: TestClient) -> None:
    response = client.get("/api/config/sources/sqlserver/assets/no_such_table")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "ASSET_NOT_IN_SOURCE"


def test_the_gate_is_the_source_read_capability(
    test_settings: Settings, loaded_empty_catalog: LoadedAssetCatalog, sql_asset_id: str
) -> None:
    """A return associate may read a return; they may not read the source catalogue.

    `config.source.read` maps to the console roles, which is the right audience
    for an asset's whole field list -- and it is a grant the console can ask
    `/api/principal` about before offering the screen.
    """
    for client in _client_for(test_settings, loaded_empty_catalog, r.RETURN_ASSOCIATE):
        response = client.get(f"/api/config/sources/sqlserver/assets/{sql_asset_id}")

    assert response.status_code == 403, response.text


def test_the_route_is_mounted_on_the_application() -> None:
    """The handler existed for months and was served by nothing. Pin the mount."""
    from return_platform.main import create_app

    paths = create_app().openapi()["paths"]

    assert "/api/config/sources/{source_id}/assets/{asset_id}" in paths


def test_no_versioned_data_console_path_came_back_with_it() -> None:
    """Exposing the handler must not re-register its old router.

    `configuration/api/sources.py` still declares `/data-console/v1`, and
    `include_router`-ing it would republish three routes nobody decided to serve.
    The canonical surface reaches the handler by Python import instead.
    """
    from return_platform.main import create_app

    served = create_app().openapi()["paths"]

    assert not [path for path in served if path.startswith("/data-console")]


def test_the_asset_read_delegates_rather_than_reimplementing() -> None:
    """A canonical surface over one implementation, like the two reads beside it."""
    source = (
        BACKEND_ROOT / "src" / "return_platform" / "configuration" / "api" / "router.py"
    ).read_text(encoding="utf-8")

    assert "await console_get_inventory_detail(" in source
