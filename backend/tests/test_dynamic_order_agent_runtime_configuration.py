from pathlib import Path

import pytest

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.runtime_factory import (
    dynamic_order_agent_enabled,
)


def test_dynamic_agent_enablement_comes_from_settings(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    monkeypatch.setenv("DYNAMIC_ORDER_AGENT_ENABLED", "true")
    disabled = test_settings.model_copy(update={"dynamic_order_agent_enabled": False})
    assert dynamic_order_agent_enabled(disabled) is False

    enabled = test_settings.model_copy(update={"dynamic_order_agent_enabled": True})
    assert dynamic_order_agent_enabled(enabled) is True


def test_dynamic_schema_path_is_resolved_from_repository(
    test_settings: Settings,
) -> None:
    path = test_settings.dynamic_knowledge_schema_path
    assert isinstance(path, Path)
    assert path.is_absolute()
    assert path.name == "active-schema.return-order.yaml"
