"""API tests for versioned graph-backed runtime configuration."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.data_console.api.configuration import router
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal


@pytest.fixture
def configuration_client(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = test_settings
    app.state.graph_configuration_repository = InMemoryConfigurationGraphRepository()
    app.state.return_configuration = load_return_configuration(
        test_settings.return_configuration_path
    )
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    app.state.resources = resources

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="configuration-admin",
            roles=frozenset({"console_admin"}),
        )
        request.state.correlation_id = "configuration-api-test"
        return await call_next(request)

    async def accept_receipts(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "return_platform.data_console.api.configuration.verify_runtime_validation_receipts",
        accept_receipts,
    )
    return TestClient(app)


def test_configuration_release_lifecycle_and_revision_conflict(
    configuration_client: TestClient,
) -> None:
    client = configuration_client

    initial = client.get("/data-console/v1/configuration/active-snapshot")
    assert initial.status_code == 200
    assert initial.json()["data"]["source"] == "VERSION_CONTROLLED_BASELINE"
    assert initial.json()["data"]["head_revision"] == 0

    created = client.post(
        "/data-console/v1/configuration/releases",
        json={"release_id": "release-api-v1", "from_active": True},
    )
    assert created.status_code == 201
    assert created.json()["data"]["status"] == "DRAFT"
    assert "RETURN_PLATFORM" in created.json()["data"]["domains"]

    validated = client.post(
        "/data-console/v1/configuration/releases/release-api-v1/promote",
        json={"status": "VALIDATED"},
    )
    assert validated.status_code == 200

    immutable_edit = client.put(
        "/data-console/v1/configuration/releases/release-api-v1/domains/RETURN_PLATFORM",
        json={"payload": created.json()["data"]["domains"]["RETURN_PLATFORM"]},
    )
    assert immutable_edit.status_code == 409

    published = client.post(
        "/data-console/v1/configuration/releases/release-api-v1/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "RELEASED"
    assert published.json()["data"]["head_revision"] == 1

    active = client.get("/data-console/v1/configuration/active-snapshot")
    assert active.status_code == 200
    assert active.json()["data"]["source"] == "NEO4J_CONFIGURATION_GRAPH"
    assert active.json()["data"]["release_id"] == "release-api-v1"
    assert active.json()["data"]["head_revision"] == 1

    second = client.post(
        "/data-console/v1/configuration/releases",
        json={"release_id": "release-api-v2", "from_active": True},
    )
    assert second.status_code == 201
    assert (
        client.post(
            "/data-console/v1/configuration/releases/release-api-v2/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )

    conflict = client.post(
        "/data-console/v1/configuration/releases/release-api-v2/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CONFIGURATION_REVISION_CONFLICT"
