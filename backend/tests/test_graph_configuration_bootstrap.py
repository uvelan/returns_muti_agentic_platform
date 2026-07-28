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
