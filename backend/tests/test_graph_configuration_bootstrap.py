from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from return_platform.configuration.cli import bootstrap_graph_configuration


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
