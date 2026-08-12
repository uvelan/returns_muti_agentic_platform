"""Where a MANUAL handoff waits is a configuration choice, not an accident.

The provider was picked by whichever wiring the process happened to have: a
durable store if one was constructed, the filesystem otherwise. That is fine
until an operator is told to watch the AI Control Center for a prompt that is
actually sitting in `.manual_llm/` on a container's disk -- they wait forever,
and nothing anywhere says why.

These pin the two properties that make the choice trustworthy: it is explicit,
and neither mode silently becomes the other.
"""

from __future__ import annotations

import pytest

from return_platform.ai.providers.durable_interception import DurableInterceptionProvider
from return_platform.ai.providers.manual import ManualFileProvider
from return_platform.ai.routing.routes import _manual_provider
from return_platform.configuration.settings import Settings


class _Store:
    """Stands in for an interception store. Only its presence is under test."""


def _settings(choice: str) -> Settings:
    return Settings(environment="test", ai_manual_handoff=choice)


def test_file_is_the_filesystem_even_when_a_store_exists() -> None:
    """Asking for FILE and getting the store back would be the same bug reversed."""
    provider = _manual_provider(_settings("FILE"), _Store())

    assert isinstance(provider, ManualFileProvider)


def test_ui_is_the_durable_store() -> None:
    provider = _manual_provider(_settings("UI"), _Store())

    assert isinstance(provider, DurableInterceptionProvider)


def test_ui_without_a_store_is_refused_rather_than_downgraded() -> None:
    """The failure this whole setting exists to prevent.

    A silent fall back to the filesystem leaves an operator watching a console
    that will never show the prompt, with no error to explain it.
    """
    with pytest.raises(ValueError, match="interception store"):
        _manual_provider(_settings("UI"), None)


def test_auto_prefers_the_store_and_falls_back_to_the_filesystem() -> None:
    """The historical behaviour, kept for `pytest` and scripts with no Mongo."""
    assert isinstance(_manual_provider(_settings("AUTO"), _Store()), DurableInterceptionProvider)
    assert isinstance(_manual_provider(_settings("AUTO"), None), ManualFileProvider)


def test_the_default_is_auto() -> None:
    """Nothing that already works may change behaviour by upgrading."""
    assert Settings(environment="test").ai_manual_handoff == "AUTO"


def test_an_unknown_mode_is_rejected_at_configuration_time() -> None:
    """Not at the first handoff, which is hours later and in a worker's log."""
    with pytest.raises(ValueError):
        Settings(environment="test", ai_manual_handoff="CONSOLE")


def test_a_live_route_rebuild_keeps_the_store() -> None:
    """A configuration release must not move the handoff out from under anyone.

    `RuntimeConfigurationActivator.refresh` rebuilds routes and swaps them into
    the live pool. It used to call `build_routes` without the interception
    store, so any unrelated release silently rebuilt MANUAL as the filesystem
    provider while an operator was watching the console.
    """
    import inspect

    from return_platform.configuration import runtime_activation

    source = inspect.getsource(runtime_activation.RuntimeConfigurationActivator.refresh)
    assert "interception_store=" in source, (
        "the route rebuild dropped the interception store; a live release would "
        "downgrade MANUAL from the console to the filesystem"
    )
