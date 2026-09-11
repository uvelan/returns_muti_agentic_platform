"""CFG-6 scope item 6a / brief "Tests to add" 10.

`workers/integration_outbox.py` used to build its outbox dispatcher table
once, in `run()`, from `support_ticket_mode`/`support_ticket_base_url` and the
`omc`/`freight` dependency modes -- all four now release-governed via
`deployment`. `_DependencyDispatcherParticipant` rebuilds the three
config-governed topics behind the same activation boundary the AI route pool
uses; this proves the rebuild reaches the live `IntegrationOutboxDispatcher`
with no restart, and that unrelated (static) topics are never touched.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from return_platform.configuration.runtime_activation import ActivationContext
from return_platform.configuration.settings import Settings
from return_platform.operations.integrations.outbox import (
    HttpJsonDispatcher,
    HttpTicketDispatcher,
    IntegrationOutboxDispatcher,
    TopicDispatcher,
)
from return_platform.workers.integration_outbox import (
    _DEPENDENCY_TOPICS,
    _dependency_topic_dispatchers,
    _DependencyDispatcherParticipant,
)


class _FakeSimulationService:
    """A `DependencySimulationService` stand-in: `SimulationTopicDispatcher`'s
    constructor only stores the reference, never calls it."""


class _FakeMongoCollection:
    pass


class _FakeMongoDatabase:
    def __getitem__(self, _name: str) -> _FakeMongoCollection:
        return _FakeMongoCollection()


class _FakeMongoClient:
    """`IntegrationOutboxDispatcher.__init__` only ever does
    `client[database][collection]`; nothing here is called otherwise.
    """

    def __getitem__(self, _name: str) -> _FakeMongoDatabase:
        return _FakeMongoDatabase()


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "omc_dependency_mode": "SIMULATED",
        "parcel_dependency_mode": "SIMULATED",
        "freight_dependency_mode": "SIMULATED",
        "lsi_dependency_mode": "SIMULATED",
        "support_ticket_mode": "INTERNAL",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _context(settings: Settings) -> ActivationContext:
    """A minimal `ActivationContext` -- only `.settings` is read by
    `_DependencyDispatcherParticipant.prepare`."""
    return cast(
        ActivationContext,
        type("Context", (), {"settings": settings})(),
    )


def test_dependency_topic_dispatchers_reflect_release_governed_modes() -> None:
    http_client = cast(httpx.AsyncClient, object())
    simulation_service = cast(Any, _FakeSimulationService())

    simulated = _dependency_topic_dispatchers(
        _settings(), simulation_service=simulation_service, http_client=http_client
    )
    assert set(simulated) == {"omc.return.create", "carrier.return.book"}

    real_with_urls = _dependency_topic_dispatchers(
        _settings(
            omc_dependency_mode="REAL",
            omc_command_base_url="https://omc.invalid",
            freight_dependency_mode="REAL",
            carrier_booking_base_url="https://carrier.invalid",
        ),
        simulation_service=simulation_service,
        http_client=http_client,
    )
    assert isinstance(real_with_urls["omc.return.create"], HttpJsonDispatcher)
    assert isinstance(real_with_urls["carrier.return.book"], HttpJsonDispatcher)

    ticketed = _dependency_topic_dispatchers(
        _settings(
            support_ticket_mode="INTERNAL_WITH_EXTERNAL_MIRROR",
            support_ticket_base_url="https://ticket.invalid",
        ),
        simulation_service=simulation_service,
        http_client=http_client,
    )
    assert isinstance(ticketed["return-support.ticket.create"], HttpTicketDispatcher)


@pytest.mark.asyncio
async def test_outbox_dispatchers_rebuild_on_dependency_mode_change() -> None:
    """The worker participant swaps the dispatcher for `omc.return.create`
    when the release flips `deployment.dependencies.omc` -- no restart, and
    the static (unrelated) topic is left exactly as it was.
    """
    http_client = cast(httpx.AsyncClient, object())
    simulation_service = cast(Any, _FakeSimulationService())
    static_marker = object()
    static_dispatchers: dict[str, TopicDispatcher] = {
        "return-support.message.classify": cast(TopicDispatcher, static_marker)
    }

    worker = IntegrationOutboxDispatcher(
        cast(Any, _FakeMongoClient()),
        _settings(),
        {
            **static_dispatchers,
            **_dependency_topic_dispatchers(
                _settings(), simulation_service=simulation_service, http_client=http_client
            ),
        },
        worker_id="test-worker",
    )
    # Bypass Mongo entirely: this test only cares about `_dispatchers`, and
    # `IntegrationOutboxDispatcher.__init__` only touches Mongo to build a
    # collection handle it never calls here.
    initial_dispatcher = worker._dispatchers["omc.return.create"]  # noqa: SLF001

    participant = _DependencyDispatcherParticipant(
        worker,
        static_dispatchers=static_dispatchers,
        simulation_service=simulation_service,
        http_client=http_client,
    )

    changed_settings = _settings(
        omc_dependency_mode="REAL", omc_command_base_url="https://omc.invalid"
    )
    prepared = await participant.prepare(_context(changed_settings))
    assert prepared is not None
    participant.publish(prepared)

    rebuilt = worker._dispatchers  # noqa: SLF001
    assert isinstance(rebuilt["omc.return.create"], HttpJsonDispatcher)
    assert rebuilt["omc.return.create"] is not initial_dispatcher
    # The static topic this participant does not own is untouched.
    assert rebuilt["return-support.message.classify"] is static_marker
    assert set(_DEPENDENCY_TOPICS) == {
        "return-support.ticket.create",
        "omc.return.create",
        "carrier.return.book",
    }


@pytest.mark.asyncio
async def test_dependency_dispatcher_participant_never_returns_none() -> None:
    """Unlike a participant with its own activation pointer, this one always
    has a replacement to publish -- there is nothing to compare against.
    """
    http_client = cast(httpx.AsyncClient, object())
    simulation_service = cast(Any, _FakeSimulationService())
    worker = IntegrationOutboxDispatcher(cast(Any, _FakeMongoClient()), _settings(), {})
    participant = _DependencyDispatcherParticipant(
        worker,
        static_dispatchers={},
        simulation_service=simulation_service,
        http_client=http_client,
    )

    prepared = await participant.prepare(_context(_settings()))

    assert prepared is not None
