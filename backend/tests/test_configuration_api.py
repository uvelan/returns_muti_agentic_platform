"""API tests for versioned graph-backed runtime configuration."""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.ai_gateway.configuration import load_ai_gateway_configuration
from return_platform.configuration.api.releases import router
from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
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
    app.state.ai_gateway_configuration = load_ai_gateway_configuration(
        test_settings.ai_gateway_configuration_path
    )
    app.state.dependency_simulation_configuration = load_dependency_simulation_configuration(
        test_settings.dependency_simulation_configuration_path
    )
    activation_resources = RuntimeResources(
        settings=test_settings.model_copy(update={"vault_enabled": False}),
        catalog=loaded_empty_catalog,
    )
    app.state.runtime_configuration_activator = RuntimeConfigurationActivator(
        app_state=app.state,
        repository=app.state.graph_configuration_repository,
        baseline_path=test_settings.return_configuration_path,
        ai_gateway_baseline_path=test_settings.ai_gateway_configuration_path,
        dependency_simulation_baseline_path=(
            test_settings.dependency_simulation_configuration_path
        ),
        resources=activation_resources,
        refresh_interval_seconds=0,
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

    # The receipt check moved with the promotion body in W4.2: the router now
    # delegates to `promote_configuration_release`, so this patches where the
    # call actually lives rather than where it used to be imported.
    monkeypatch.setattr(
        "return_platform.configuration.application.release_promotion"
        ".verify_runtime_validation_receipts",
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
    assert published.status_code == 200, published.text
    assert published.json()["data"]["status"] == "RELEASED"
    assert published.json()["data"]["head_revision"] == 1
    assert published.json()["data"]["runtime_activation"]["release_id"] == "release-api-v1"

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


def test_partial_agent_behavior_edit_activates_without_restart(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    created = client.post(
        "/data-console/v1/configuration/releases",
        json={"release_id": "agent-behavior-v21", "from_active": True},
    )
    assert created.status_code == 201

    patched = client.patch(
        ("/data-console/v1/configuration/releases/agent-behavior-v21/domains/RETURN_PLATFORM"),
        json={
            "patch": {
                "agents": {
                    "order_discovery": {
                        "version": "2.1",
                        "human_confirmation_required": True,
                    }
                }
            }
        },
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["payload"]["agents"]["order_discovery"]["version"] == "2.1"

    validated = client.post(
        "/data-console/v1/configuration/releases/agent-behavior-v21/promote",
        json={"status": "VALIDATED"},
    )
    assert validated.status_code == 200
    published = client.post(
        "/data-console/v1/configuration/releases/agent-behavior-v21/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200, published.text

    test_app = cast(FastAPI, client.app)
    active_loaded = test_app.state.return_configuration
    active_snapshot = test_app.state.return_configuration_snapshot
    assert active_loaded.configuration.agents["order_discovery"].version == "2.1"
    assert active_snapshot.release_id == "agent-behavior-v21"
    assert active_snapshot.head_revision == 1


def test_partial_edit_rejects_invalid_complete_configuration(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    assert (
        client.post(
            "/data-console/v1/configuration/releases",
            json={"release_id": "invalid-agent-behavior", "from_active": True},
        ).status_code
        == 201
    )

    invalid = client.patch(
        ("/data-console/v1/configuration/releases/invalid-agent-behavior/domains/RETURN_PLATFORM"),
        json={"patch": {"agents": {"order_discovery": None}}},
    )

    assert invalid.status_code == 422
    detail = client.get("/data-console/v1/configuration/releases/invalid-agent-behavior")
    assert "order_discovery" in detail.json()["data"]["domains"]["RETURN_PLATFORM"]["agents"]


def test_ai_prompts_and_simulation_behavior_activate_from_graph(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    release_id = "all-behavior-domains-v1"
    created = client.post(
        "/data-console/v1/configuration/releases",
        json={"release_id": release_id, "from_active": True},
    )
    assert created.status_code == 201
    assert {
        "RETURN_PLATFORM",
        "AI_GATEWAY",
        "DEPENDENCY_SIMULATION",
    } <= set(created.json()["data"]["domains"])

    prompt = (
        "Evaluate eligibility using only supplied operational facts and return the required "
        "structured decision for human review."
    )
    ai_patch = client.patch(
        f"/data-console/v1/configuration/releases/{release_id}/domains/AI_GATEWAY",
        json={
            "patch": {
                "tasks": {
                    "RETURN_ELIGIBILITY_V1": {
                        "promptVersion": "return-eligibility-runtime-v3",
                        "systemPrompt": prompt,
                    }
                }
            }
        },
    )
    assert ai_patch.status_code == 200

    banner = "SIMULATION: graph-controlled dependency behavior is active."
    simulation_patch = client.patch(
        (f"/data-console/v1/configuration/releases/{release_id}/domains/DEPENDENCY_SIMULATION"),
        json={"patch": {"modeBanner": banner}},
    )
    assert simulation_patch.status_code == 200

    assert (
        client.post(
            f"/data-console/v1/configuration/releases/{release_id}/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )
    published = client.post(
        f"/data-console/v1/configuration/releases/{release_id}/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200, published.text

    test_app = cast(FastAPI, client.app)
    active_ai = test_app.state.ai_gateway_configuration.configuration
    active_simulation = test_app.state.dependency_simulation_configuration.configuration
    assert active_ai.tasks["RETURN_ELIGIBILITY_V1"].promptVersion == (
        "return-eligibility-runtime-v3"
    )
    assert active_ai.tasks["RETURN_ELIGIBILITY_V1"].systemPrompt == prompt
    assert active_simulation.modeBanner == banner
    assert test_app.state.ai_gateway_route_pool is not None
