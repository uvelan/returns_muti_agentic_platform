"""The factory produces a resolver, and says honestly what it can serve.

Contracts.md sect. 9, brief item 1. Two kinds of guarantee here:

* **the factory decides, the wiring site does not** -- asserted by building
  against a real released configuration and reading the rung inventory and the
  trigger set off the result, not by checking that a constructor accepted;
* **an unserviceable rung is absent** -- asserted against the *compiled graph*,
  because "the tool rung is unreachable" is a claim about topology and a
  docstring cannot be wrong in a way a test notices.

The Mongo-backed collaborators are constructed against a client that is never
awaited: every assertion below is about what was *decided*, and a factory that
needed a live database to tell you which rungs it built would be a factory
nobody could check.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.configuration.support_resolver_configuration import (
    SupportResolverConfiguration,
)
from return_platform.operations.return_support.resolution_ladder import (
    RUNG_FACTS,
    build_resolution_ladder,
    compiled_rungs,
)
from return_platform.operations.return_support.resolution_state import RUNG_GRAPH, RUNG_TOOL
from return_platform.operations.return_support.resolver_composition import (
    RESOLVE_TASK_ID,
    TriggerIntentNotInTaxonomyError,
    build_resolving_classify_dispatcher,
    build_support_resolution_ladder,
)


class _StubTask:
    promptVersion = "resolve-2026-08-31"
    allowedProviders = ("anthropic",)


class _StubInvoker:
    task = _StubTask()


def _configuration(**resolver: Any) -> ReturnPlatformConfiguration:
    """The **released** `production.yaml`, with only the resolver block varied.

    Loading the real document rather than hand-building a configuration object:
    a factory checked against a synthetic configuration proves nothing about the
    one the platform actually ships, and the pairing this module cares about --
    trigger intents against the released taxonomy -- is exactly the pairing a
    hand-built object would let drift.
    """
    released = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    if not resolver:
        return released
    return released.model_copy(
        update={
            "support_resolver": released.support_resolver.model_copy(update=dict(resolver))
            if "trigger_intents" not in resolver
            else SupportResolverConfiguration(
                **{**released.support_resolver.model_dump(), **resolver}
            )
        }
    )


class TestTheFactoryDecidesTheRungInventory:
    def test_this_build_can_serve_the_fact_rung_and_only_the_fact_rung(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The honest inventory, read off the object the factory returned.

        Not asserted as "graph_read is None" -- that would pass for a factory
        that supplied a port doing nothing. `compiled_rungs` is the topology.
        """
        resolver = _built(monkeypatch)
        deps = resolver._deps  # noqa: SLF001 - the inventory is the assertion
        assert compiled_rungs(deps) == (RUNG_FACTS,)
        assert deps.graph_rung_available is False
        assert deps.tool_rung_available is False

    def test_the_compiled_graph_contains_no_graph_or_tool_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _built(monkeypatch)
        nodes = set(build_resolution_ladder(resolver._deps).nodes)  # noqa: SLF001
        assert "sync_graph" not in nodes
        assert "resolve_from_graph" not in nodes
        assert "route_tool" not in nodes

    def test_the_released_tool_bindings_are_empty_and_the_rung_agrees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both halves of "genuinely unreachable, and visibly so".

        The config default and the topology are asserted together, because
        either alone is a half-truth: bindings could be empty while a rung was
        compiled in and refusing, or a rung could be absent while the config
        implied one was available.
        """
        resolver = _built(monkeypatch)
        deps = resolver._deps  # noqa: SLF001
        assert deps.configuration.tool_bindings == ()
        assert RUNG_TOOL not in compiled_rungs(deps)
        assert RUNG_GRAPH not in compiled_rungs(deps)

    def test_the_invoker_is_bound_to_the_released_resolve_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And re-reads the release rather than capturing it.

        Asserted by *moving* the released prompt version after construction: a
        value captured at build time would keep the old one.
        """
        resolver = _built(monkeypatch)
        invoker = resolver._deps.resolver  # noqa: SLF001
        assert invoker.release_id == "resolve-2026-08-31"
        _StubTask.promptVersion = "resolve-2026-09-01"
        try:
            assert invoker.release_id == "resolve-2026-09-01"
            assert invoker.prompt_version == "resolve-2026-09-01"
        finally:
            _StubTask.promptVersion = "resolve-2026-08-31"

    def test_the_task_id_is_the_one_sect_10_names(self) -> None:
        assert RESOLVE_TASK_ID == "support.question.resolve.v1"


class TestATriggerIntentTheClassifierCannotProduceIsRefused:
    def test_an_intent_outside_the_released_taxonomy_fails_at_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Because at runtime it is invisible.

        `coerce_intent` forces every classification into the taxonomy or into
        `other`, so a trigger intent outside it matches nothing. The resolver
        would be configured, wired, deployed and inert, and every test would
        still pass.
        """
        with pytest.raises(TriggerIntentNotInTaxonomyError) as raised:
            _built(monkeypatch, trigger_intents=("info_request", "escalation_request"))
        assert "escalation_request" in str(raised.value)

    def test_the_default_trigger_set_is_inside_the_default_taxonomy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pairing that must hold for the shipped defaults to do anything."""
        released = _configuration()
        resolver = _built(monkeypatch)
        taxonomy = released.support_ingress.normalized_intents()
        assert set(released.support_resolver.trigger_intents) <= taxonomy
        assert resolver._deps.intent_taxonomy == taxonomy  # noqa: SLF001

    def test_an_empty_trigger_set_still_builds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Disabling resolution is a legitimate deployment, not a misconfiguration."""
        assert _built(monkeypatch, trigger_intents=()) is not None


class TestTheWiringSiteGetsOneCall:
    def test_the_dispatcher_comes_back_beside_the_topic_it_registers_under(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table keyed by the wrong constant is a queue nothing ever drains."""
        from return_platform.operations.return_support.ingress_store import (
            SUPPORT_MESSAGE_CLASSIFY_TOPIC,
        )

        topic, dispatcher = _built_dispatcher(monkeypatch)
        assert topic == SUPPORT_MESSAGE_CLASSIFY_TOPIC
        assert hasattr(dispatcher, "dispatch")

    def test_the_dispatcher_carries_the_released_trigger_intents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, dispatcher = _built_dispatcher(monkeypatch, trigger_intents=("info_request",))
        assert dispatcher._trigger_intents == ("info_request",)  # noqa: SLF001


# ------------------------------------------------------------------- harness


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the two things that would reach a network or a database.

    `StructuredOutputInvoker` and `AIRoutePool` only: everything else the
    factory builds is constructed against the Mongo client without touching it,
    which is exactly the property that makes the factory checkable.
    """
    import return_platform.operations.return_support.resolver_composition as module

    monkeypatch.setattr(module, "StructuredOutputInvoker", lambda **_: _StubInvoker())
    monkeypatch.setattr(module, "AIRoutePool", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "build_routes", lambda _settings: ())


def _built(monkeypatch: pytest.MonkeyPatch, **resolver: Any) -> Any:
    _patch(monkeypatch)
    return build_support_resolution_ladder(
        settings=_settings(),
        mongo=_mongo(),
        return_configuration=_configuration(**resolver),
        ai_gateway=_gateway(),
        interception=object(),
        checkpointer=None,
    )


def _built_dispatcher(monkeypatch: pytest.MonkeyPatch, **resolver: Any) -> Any:
    _patch(monkeypatch)
    import return_platform.operations.return_support.resolver_composition as module

    monkeypatch.setattr(
        module,
        "build_support_message_classify_dispatcher",
        lambda **_: ("return-case.support-message.classify", object()),
    )
    return build_resolving_classify_dispatcher(
        settings=_settings(),
        mongo=_mongo(),
        return_configuration=_configuration(**resolver),
        ai_gateway=_gateway(),
        interception=object(),
        checkpointer=None,
    )


def _settings() -> Any:
    from return_platform.configuration.settings import Settings

    return Settings()


class _Collection:
    """Enough of a collection for a constructor to hold onto, and nothing more."""

    def __getitem__(self, name: str) -> _Collection:
        return self


class _Database:
    def __getitem__(self, name: str) -> _Collection:
        return _Collection()


class _Mongo:
    """A client double, deliberately not a real `AsyncMongoClient`.

    `test_the_normal_suite_never_needs_live_infrastructure` refuses a normal-run
    module that constructs one -- and it is right to, even with
    `connect=false`: the rule is that nothing in the ordinary run can be taken
    down by a datastore being slow. This module has no business talking to Mongo
    anyway; every assertion is about what the factory *decided*, and needing a
    live database to find out which rungs were built would make the factory
    uncheckable.
    """

    def __getitem__(self, name: str) -> _Database:
        return _Database()


def _mongo() -> Any:
    return _Mongo()


def _gateway() -> Any:
    class _Configuration:
        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {}

    class _Loaded:
        configuration = _Configuration()

    return _Loaded()
