from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from return_platform.configuration.cli import bootstrap_graph_configuration
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import (
    DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
    DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
)
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY


class _Driver:
    async def verify_connectivity(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _Repository:
    async def get_active_release(self) -> SimpleNamespace:
        return SimpleNamespace(release_id="active-release-1")


@pytest.mark.asyncio
async def test_if_missing_reuses_active_release_without_ai_validation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        neo4j_uri="bolt://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password=SecretStr("secret"),
    )

    async def resolve_settings(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return settings, object()

    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "resolve_runtime_settings_from_vault",
        resolve_settings,
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration.AsyncGraphDatabase,
        "driver",
        lambda *_args, **_kwargs: _Driver(),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "Neo4jConfigurationGraphRepository",
        lambda _driver: _Repository(),
    )
    ai_bootstrap = AsyncMock(
        side_effect=AssertionError("AI bootstrap validation must not run"),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    await bootstrap_graph_configuration.main(if_missing=True)

    assert ai_bootstrap.await_count == 0
    assert "graph_configuration_status=EXISTING" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ai_validation_is_skipped_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = SimpleNamespace()
    ai_bootstrap = AsyncMock(
        side_effect=AssertionError("live AI validation must be opt-in"),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    result = await bootstrap_graph_configuration._prepare_return_configuration(
        validate_ai=False,
        settings=SimpleNamespace(),
        resolver=SimpleNamespace(),
        loaded_ai_gateway=SimpleNamespace(),
        configuration=configuration,
    )

    assert result is configuration
    assert ai_bootstrap.await_count == 0
    assert "ai_bootstrap_validation=SKIPPED" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ai_validation_runs_when_explicitly_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = SimpleNamespace()
    validated = SimpleNamespace()
    ai_bootstrap = AsyncMock(return_value=validated)
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "build_bootstrap_runtime_configuration",
        ai_bootstrap,
    )

    result = await bootstrap_graph_configuration._prepare_return_configuration(
        validate_ai=True,
        settings=SimpleNamespace(),
        resolver=SimpleNamespace(),
        loaded_ai_gateway=SimpleNamespace(),
        configuration=configuration,
    )

    assert result is validated
    ai_bootstrap.assert_awaited_once()


class _CarryForwardRepository:
    """Just enough graph to watch what the publish writes.

    A release exists and is RELEASED, which is the state every deployment is in
    after its first boot -- and the state in which the carry-forward path runs.
    """

    def __init__(self, active_payload: dict[str, Any]) -> None:
        self.active_payload = active_payload
        self.saved: dict[str, dict[str, Any]] = {}
        self.promotions: list[tuple[str, str]] = []

    async def get_active_release(self) -> SimpleNamespace:
        return SimpleNamespace(release_id="active-release-1", status="RELEASED")

    async def get_domain_config(self, _release_id: str, domain_key: str) -> dict[str, Any] | None:
        return self.active_payload if domain_key == RETURN_PLATFORM_DOMAIN_KEY else None

    async def get_all_domain_configs(self, _release_id: str) -> dict[str, Any]:
        return {RETURN_PLATFORM_DOMAIN_KEY: self.active_payload}

    async def get_release(self, release_id: str) -> SimpleNamespace | None:
        if release_id not in self.saved:
            return None
        status = "VALIDATED" if ("VALIDATED", release_id) in self.promotions else "DRAFT"
        return SimpleNamespace(release_id=release_id, status=status)

    async def save_draft_domain(
        self,
        release_id: str,
        domain_key: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
    ) -> None:
        self.saved.setdefault(release_id, {})[domain_key] = payload

    async def get_head_revision(self) -> int:
        return 7

    async def promote_release(
        self,
        release_id: str,
        target_status: str,
        *,
        actor_id: str,
        expected_head_revision: int | None = None,
    ) -> None:
        self.promotions.append((target_status, release_id))


def _bootstrap_settings() -> SimpleNamespace:
    return SimpleNamespace(
        neo4j_uri="bolt://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password=SecretStr("secret"),
        return_configuration_path=DEFAULT_RETURN_CONFIGURATION_PATH,
        ai_gateway_configuration_path=DEFAULT_AI_GATEWAY_CONFIGURATION_PATH,
        dependency_simulation_configuration_path=(DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH),
    )


def _install_bootstrap_doubles(
    monkeypatch: pytest.MonkeyPatch,
    repository: _CarryForwardRepository,
) -> None:
    async def resolve_settings(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return _bootstrap_settings(), object()

    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "resolve_runtime_settings_from_vault",
        resolve_settings,
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration.AsyncGraphDatabase,
        "driver",
        lambda *_args, **_kwargs: _Driver(),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration,
        "Neo4jConfigurationGraphRepository",
        lambda _driver: repository,
    )


@pytest.mark.asyncio
async def test_a_key_the_active_release_predates_is_adopted_from_the_packaged_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a new setting can never reach a deployment.

    `copilot.order_discovery_agent_id` was added to the packaged YAML, the
    endpoint that serves it was correct, and `/api/runtime-config` still answered
    `null` -- because the active release predated the key, was carried forward
    whole, and republished the model default over the top of it.
    """
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    older_release_payload = packaged.model_dump(mode="json")
    del older_release_payload["copilot"]
    repository = _CarryForwardRepository(older_release_payload)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["copilot"] == {
        "order_discovery_agent_id": "order-discovery-agent",
        # The model default: an empty column list means the deployment has
        # not chosen candidate-table columns and the client falls back.
        "candidate_columns": [],
    }


@pytest.mark.asyncio
async def test_the_active_release_still_wins_for_every_key_it_carries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The merge must not undo an operator's edits, which is the whole reason
    the release is carried forward at all."""
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    older_release_payload = packaged.model_dump(mode="json")
    del older_release_payload["copilot"]
    operator_queues = ["OPERATOR-EDITED-QUEUE"]
    older_release_payload["support"]["queues"] = operator_queues
    repository = _CarryForwardRepository(older_release_payload)
    _install_bootstrap_doubles(monkeypatch, repository)

    await bootstrap_graph_configuration.main()

    published = next(iter(repository.saved.values()))[RETURN_PLATFORM_DOMAIN_KEY]
    assert published["support"]["queues"] == operator_queues
    assert list(packaged.support.queues) != operator_queues
