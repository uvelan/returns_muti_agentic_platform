"""`/health/ready` reports configuration validity as one of its dependencies.

Plan sect. 5.4 is explicit that this joins the existing probe set rather than
becoming a parallel mechanism, and that is the property worth holding: a monitor
already scraping `/health/ready` and tripping on any unavailable dependency
trips on a dangling Copilot agent mapping too, without being told about a new
endpoint or a new field.

So these tests assert both halves. The six network probes are still reported and
still decide readiness; `configuration` is a seventh entry in the same map, with
the same `DependencyProbeResult` shape, and an unhealthy one makes the whole
endpoint 503 exactly as an unreachable Mongo does. What the probe result cannot
carry -- *which* check failed -- is named in the readiness body's `configuration`
block, because "the agent mapping is dangling" and "no eligibility policy is
published" have different operators and different fixes.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.main import create_app
from return_platform.resources import RuntimeResources
from return_platform.shared.contracts import DependencyProbeResult, DependencyStatus

_NETWORK_PROBES = (
    "probe_mongodb",
    "probe_source_mongodb",
    "probe_sqlserver",
    "probe_neo4j",
    "probe_valkey",
    "probe_temporal",
)


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture(scope="module")
def shipped_agent_policy_ids() -> tuple[str, ...]:
    return tuple(load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH).agent_policies)


@pytest.fixture
def readiness_app(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    """An app whose six network probes are healthy and whose lifespan never runs.

    The network probes are stubbed because this file is about the seventh one;
    the configuration probe itself is left real, so what is exercised is the
    production code path from `app.state.return_configuration` through
    `evaluate_configuration_health` to the response body.
    """
    app = create_app(custom_settings=test_settings)
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    app.state.resources = resources

    async def healthy_probe(*_args: object, **_kwargs: object) -> DependencyProbeResult:
        return DependencyProbeResult(
            status=DependencyStatus.HEALTHY,
            latency_ms=1,
            checked_at=datetime.now(UTC),
        )

    for name in _NETWORK_PROBES:
        monkeypatch.setattr(f"return_platform.main.{name}", healthy_probe)

    try:
        yield app
    finally:
        resources.sql_manager.executor.shutdown(wait=False, cancel_futures=True)


def _install(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    configuration: ReturnPlatformConfiguration,
    agent_policy_ids: Collection[str],
) -> None:
    """Put a configuration and an active schema in front of the real probe."""
    app.state.return_configuration = LoadedReturnConfiguration(
        configuration=configuration,
        path=DEFAULT_RETURN_CONFIGURATION_PATH,
        sha256="0" * 64,
    )

    async def known_ids(*_args: object, **_kwargs: object) -> Collection[str]:
        return agent_policy_ids

    # Patched at the probe's own module, which is where it is called from.
    # Resolving it for real would need a published schema release in Mongo.
    monkeypatch.setattr(
        "return_platform.api.dependency_probes.resolve_known_agent_policy_ids",
        known_ids,
    )


@pytest.fixture
def client(readiness_app: FastAPI) -> Iterator[TestClient]:
    test_client = TestClient(readiness_app)
    try:
        yield test_client
    finally:
        test_client.close()


def test_a_healthy_configuration_leaves_readiness_untouched(
    readiness_app: FastAPI,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """Every existing probe is still reported, and the endpoint is still 200."""
    _install(
        readiness_app,
        monkeypatch,
        configuration=shipped_configuration,
        agent_policy_ids=shipped_agent_policy_ids,
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["dependencies"]) == {
        "mongodb",
        "source_mongodb",
        "sqlserver",
        "neo4j",
        "valkey",
        "temporal",
        "configuration",
    }
    assert all(item["status"] == "HEALTHY" for item in body["dependencies"].values())
    assert body["configuration"]["healthy"] is True
    assert body["configuration"]["failed_checks"] == []
    # The block the endpoint already carried is unchanged.
    assert set(body["configuration"]) >= {"release_id", "source"}


@pytest.mark.parametrize(
    ("agent_id", "expected_fragment"),
    [
        (None, "is not configured"),
        ("order_discovery", "names no agent policy"),
    ],
    ids=["unset", "dangling"],
)
def test_a_bad_agent_mapping_makes_the_configuration_probe_unhealthy(
    readiness_app: FastAPI,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
    agent_id: str | None,
    expected_fragment: str,
) -> None:
    """Unset and dangling are one defect from the associate's seat and both show here."""
    _install(
        readiness_app,
        monkeypatch,
        configuration=shipped_configuration.model_copy(
            update={
                "copilot": shipped_configuration.copilot.model_copy(
                    update={"order_discovery_agent_id": agent_id}
                )
            }
        ),
        agent_policy_ids=shipped_agent_policy_ids,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    configuration_probe = body["dependencies"]["configuration"]
    assert configuration_probe["status"] == "UNAVAILABLE"
    assert configuration_probe["error_code"] == "HEALTH_CHECK_FAILED"
    assert "COPILOT_AGENT_CONFIGURATION_INVALID" in configuration_probe["safe_message"]
    # Every network probe is still healthy, so configuration alone took it down.
    assert all(
        body["dependencies"][name]["status"] == "HEALTHY"
        for name in body["dependencies"]
        if name != "configuration"
    )
    assert body["configuration"]["healthy"] is False
    assert [failure["check"] for failure in body["configuration"]["failed_checks"]] == [
        "COPILOT_AGENT_BINDING"
    ]
    assert expected_fragment in body["configuration"]["failed_checks"][0]["message"]


def test_an_absent_eligibility_policy_is_reported_as_its_own_check(
    readiness_app: FastAPI,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """Distinct from a bad agent mapping, and named as an operational failure."""
    _install(
        readiness_app,
        monkeypatch,
        configuration=shipped_configuration.model_copy(update={"return_eligibility_policy": None}),
        agent_policy_ids=shipped_agent_policy_ids,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    failed = body["configuration"]["failed_checks"]
    assert [failure["code"] for failure in failed] == ["RETURN_ELIGIBILITY_POLICY_MISSING"]
    assert "REVIEW_REQUIRED" not in body["dependencies"]["configuration"]["safe_message"]


def test_both_failures_are_reported_together(
    readiness_app: FastAPI,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    _install(
        readiness_app,
        monkeypatch,
        configuration=shipped_configuration.model_copy(
            update={
                "copilot": shipped_configuration.copilot.model_copy(
                    update={"order_discovery_agent_id": None}
                ),
                "return_eligibility_policy": None,
            }
        ),
        agent_policy_ids=shipped_agent_policy_ids,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert [failure["code"] for failure in body["configuration"]["failed_checks"]] == [
        "COPILOT_AGENT_CONFIGURATION_INVALID",
        "RETURN_ELIGIBILITY_POLICY_MISSING",
    ]


def test_an_unloaded_configuration_is_uninitialized_rather_than_invalid(
    readiness_app: FastAPI,
    client: TestClient,
) -> None:
    """A process that has not finished booting has not been misconfigured.

    Reported as `UNINITIALIZED`, the same code every other probe uses for a
    dependency that is not there yet, so a restart does not page an operator
    about a configuration defect that does not exist.
    """
    response = client.get("/health/ready")

    assert response.status_code == 503
    configuration_probe = response.json()["dependencies"]["configuration"]
    assert configuration_probe["status"] == "UNAVAILABLE"
    assert configuration_probe["error_code"] == "UNINITIALIZED"
    assert response.json()["configuration"]["failed_checks"] == []
