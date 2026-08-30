"""The single composition entry point the integration agent wires (phase 1b).

The worker's job becomes two lines:

    topic, dispatcher = build_support_message_classify_dispatcher(
        settings=settings,
        mongo=client,
        return_configuration=runtime.return_configuration.configuration,
        ai_gateway=runtime.ai_gateway_configuration,
        interception=ALLOW_ALL,
    )
    dispatchers[topic] = dispatcher

What is asserted here is that the thing it returns is **whole** -- every port
supplied, every derived identity real. The two gaps this phase closed were both
"a port that was never passed" and "an identity that had to be typed", and both
were invisible because the tests supplied what production did not. So this test
reaches into the constructed object and checks the ports are the production
classes, not that construction merely did not raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from return_platform.ai.gateway.final_dispatch import ALLOW_ALL
from return_platform.ai.routing.tasks import load_ai_gateway_configuration
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.operations.return_support.analysis_wiring import (
    StructuredStageInvoker,
    SupportMessageClassifyDispatcher,
    derive_routing_policy_version,
)
from return_platform.operations.return_support.composition import (
    CLASSIFY_TASK_ID,
    EXTRACT_TASK_ID,
    build_support_message_analyser,
    build_support_message_classify_dispatcher,
)
from return_platform.operations.return_support.ingress_store import (
    SUPPORT_MESSAGE_CLASSIFY_TOPIC,
    DurableSupportIngressStore,
)
from return_platform.operations.return_support.message_classification import (
    AGENT_ID,
    OMC_RETURN_UPDATE_TOPIC,
    SupportMessageAnalyser,
)
from return_platform.operations.return_support.omc_mirror import DurableOmcMirror
from return_platform.operations.return_support.relay import SupportTranscriptRelay
from tests.operations.mongo_double import FakeClient

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def built(test_settings: Settings) -> SupportMessageAnalyser:
    return build_support_message_analyser(
        settings=test_settings,
        mongo=FakeClient(),  # type: ignore[arg-type]
        return_configuration=load_return_configuration(
            BACKEND / "config" / "returns" / "production.yaml"
        ).configuration,
        ai_gateway=load_ai_gateway_configuration(BACKEND / "config" / "ai_gateway.yaml"),
        interception=ALLOW_ALL,
    )


def test_every_port_the_analyser_needs_is_supplied_by_the_factory(
    built: SupportMessageAnalyser,
) -> None:
    """Named one at a time, and by production class.

    `omc` in particular: it defaulted to `None` for the whole of phase 1, so an
    analyser missing its mirror was a perfectly constructible object. Asserting
    "not None" would not be enough either -- what the wiring site owes is the
    durable mirror, not something truthy.
    """
    assert isinstance(built._omc, DurableOmcMirror)  # noqa: SLF001
    # And it is wired to the topic contracts sect. 5 names. A mirror pointed at
    # a topic nothing dispatches writes its rows, returns its keys, and delivers
    # nothing -- a silence indistinguishable from the missing adapter item B was
    # raised for. Found blind by fault injection (C8) and closed here.
    assert built._omc._topic == OMC_RETURN_UPDATE_TOPIC  # noqa: SLF001
    assert built._omc._actor_id == AGENT_ID  # noqa: SLF001
    assert isinstance(built._relay, SupportTranscriptRelay)  # noqa: SLF001
    assert isinstance(built._classifier, StructuredStageInvoker)  # noqa: SLF001
    assert isinstance(built._extractor, StructuredStageInvoker)  # noqa: SLF001
    # The scoped-fact append is S1's bound method, not a re-implementation.
    assert built._append_scoped_fact_once.__name__ == "append_scoped_fact_once"  # noqa: SLF001


def test_the_two_stages_are_bound_to_the_two_released_support_tasks(
    built: SupportMessageAnalyser,
) -> None:
    """Right tasks, right way round.

    Both stages take the same envelope and the same payload shape, so a factory
    that bound the extractor to the classify task would run, answer, and write
    an extraction produced by a classification prompt.
    """
    assert built._classifier.release_id != built._extractor.release_id  # noqa: SLF001
    released = load_ai_gateway_configuration(BACKEND / "config" / "ai_gateway.yaml").configuration
    assert built._classifier.release_id == released.tasks[CLASSIFY_TASK_ID].promptVersion  # noqa: SLF001
    assert built._extractor.release_id == released.tasks[EXTRACT_TASK_ID].promptVersion  # noqa: SLF001


def test_the_stages_report_the_routing_policy_of_the_released_document(
    built: SupportMessageAnalyser,
) -> None:
    """Item C, end to end through the factory.

    Compared against the version derived independently from the file on disk
    rather than against the adapter's own second call: two reads of one property
    agree by construction and would agree just as well if the property returned
    a constant.
    """
    released = load_ai_gateway_configuration(BACKEND / "config" / "ai_gateway.yaml").configuration
    assert built._classifier.routing_policy_version == derive_routing_policy_version(  # noqa: SLF001
        released, released.tasks[CLASSIFY_TASK_ID]
    )
    assert built._extractor.routing_policy_version == derive_routing_policy_version(  # noqa: SLF001
        released, released.tasks[EXTRACT_TASK_ID]
    )
    assert (
        built._classifier.routing_policy_version  # noqa: SLF001
        != built._extractor.routing_policy_version  # noqa: SLF001
    )


def test_the_dispatcher_factory_returns_the_topic_beside_the_dispatcher(
    test_settings: Settings,
) -> None:
    """The registration line, whole.

    The topic comes back with the dispatcher because a table keyed by the wrong
    constant fails *silently* -- an unregistered topic simply never dispatches,
    and the queue grows with nobody reading an error.
    """
    topic, dispatcher = build_support_message_classify_dispatcher(
        settings=test_settings,
        mongo=FakeClient(),  # type: ignore[arg-type]
        return_configuration=load_return_configuration(
            BACKEND / "config" / "returns" / "production.yaml"
        ).configuration,
        ai_gateway=load_ai_gateway_configuration(BACKEND / "config" / "ai_gateway.yaml"),
        interception=ALLOW_ALL,
    )
    assert topic == SUPPORT_MESSAGE_CLASSIFY_TOPIC
    assert isinstance(dispatcher, SupportMessageClassifyDispatcher)
    assert isinstance(dispatcher._ingress, DurableSupportIngressStore)  # noqa: SLF001
    assert isinstance(dispatcher._analyser, SupportMessageAnalyser)  # noqa: SLF001


def test_interception_has_no_default_on_either_factory() -> None:
    """AI-01's rule, kept at this boundary too.

    The defect AI-01 records was not a missing mechanism -- it was a mechanism
    two of three callers never opted into, because the parameter had a default.
    A factory that quietly defaulted `interception` would reintroduce exactly
    that for the third caller.
    """
    import inspect

    for factory in (build_support_message_analyser, build_support_message_classify_dispatcher):
        parameter = inspect.signature(factory).parameters["interception"]
        assert parameter.default is inspect.Parameter.empty, factory.__name__
