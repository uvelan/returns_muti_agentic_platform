"""A nearly-valid answer is repaired by the model that wrote it, not discarded.

The failure this file holds shut, in the order the evidence arrived:

A STANDARD reasoning route spent 37.9 seconds producing a structurally correct
`AgentAction` envelope that was one *conditional* field short of valid -- an
`action_type` of `GRAPH_QUERY` with no `query_plan`, which
`AgentAction.validate_action_payload` rejects with `missing payload for action
type GRAPH_QUERY`. The whole reply was thrown away and the dispatcher failed over
to a different model, which knew nothing about the defect.

Two mechanisms had to both be wrong for that to happen, and both were:

* The Order Agent's own repair path could not be entered. `correct_action` takes
  an `AgentAction`, and all three of its call sites in `graph_nodes` pass one that
  already parsed and then failed a *business* check. A `ValidationError` raised
  *inside* `AgentAction.model_validate` produces no object to pass, so a
  parse-time failure was structurally unable to reach the CORRECT_ACTION mode that
  exists to fix exactly this.
* The boundary would not re-ask the route. `RESPONSE_INVALID` is in
  `_TERMINAL_FOR_ROUTE`, whose premise is "this route will fail the same way if
  asked again" -- true of a byte-identical retry, and false once
  `on_response_invalid` rebuilds the payload. With
  `retry.maximumAttemptsPerRoute` at 1 in `config/ai_gateway.yaml`, no inner
  retry existed to reach that premise anyway.

So the assertions below come in two layers. The dispatcher cases prove the
*generic* mechanism -- one extra ask, only when the question actually changed,
counted against every existing ceiling. The Order Agent cases prove the
*vocabulary* -- that the extra ask arrives as `mode=CORRECT_ACTION` carrying the
rejected body in `invalidActionJson`, which is a payload the packaged prompt
already has a rule for and `allowedInputKeys` already permits.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, model_validator

from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    DispatchDecision,
    DispatchObserver,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
    InterceptionVerdict,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredOutputInvoker,
    parse_structured_response,
)
from return_platform.ai.providers import ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
    StandardReasoningUnavailable,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentTurnContext,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"

_ORDER_AGENT_TASK = "ORDER_AGENT_REASONING_V1"


# ---------------------------------------------------------------------------
# A response model with a *conditional* requirement, which is the whole point
# ---------------------------------------------------------------------------


class _Answer(BaseModel):
    """Mirrors the shape of the real defect rather than a generic bad parse.

    `AgentAction` rejects a `GRAPH_QUERY` with no `query_plan` from a
    `model_validator(mode="after")`, so the object is fully constructed and only
    then refused. That is why the reply looked correct in a log and why the
    diagnosis is worth sending back: a model told which conditional field it
    missed has to change one key, not re-reason the turn.
    """

    kind: str
    plan: str | None = None

    @model_validator(mode="after")
    def _plan_required_for_query(self) -> _Answer:
        if self.kind == "QUERY" and self.plan is None:
            raise ValueError("missing plan for kind QUERY")
        return self


_NEARLY_VALID = '{"kind":"QUERY"}'
_VALID = '{"kind":"QUERY","plan":"orders-by-customer"}'


class _ScriptedProvider:
    """Answers from a script, and records every request it was handed.

    The requests are the assertion surface: "the route was asked again with the
    diagnosis" is a claim about the bytes a provider received, and checking
    anything earlier would test the test.
    """

    configured = True
    name = "GOOGLE"

    def __init__(self, model: str, *texts: str) -> None:
        self.model = model
        self._texts = list(texts)
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        text = self._texts[min(len(self.requests) - 1, len(self._texts) - 1)]
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=text,
            input_tokens=10,
            cached_input_tokens=None,
            output_tokens=5,
            total_tokens=15,
        )

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [dict(request.user_payload) for request in self.requests]


class _RecordingObserver(DispatchObserver):
    """Only the two events these cases are about."""

    def __init__(self) -> None:
        self.corrections: list[tuple[str, int]] = []
        self.exhaustion: list[tuple[int, str, dict[str, int]]] = []

    async def on_correction_retry(
        self, *, route: AIRoute, attempt: int, error: BaseException | None
    ) -> None:
        del error
        self.corrections.append((route.route_id, attempt))

    async def on_exhausted(self, *, attempts: int, last_error: str, breakdown: Any) -> None:
        self.exhaustion.append((attempts, last_error, dict(breakdown)))


def _settings(**overrides: Any) -> Settings:
    return Settings.model_construct(
        environment="test",
        ai_gateway_configuration_path=CONFIG,
        ai_timeout_seconds=2.0,
        ai_global_timeout_seconds=10.0,
        ai_max_payload_bytes=32_768,
        ai_provider_order="GOOGLE,NVIDIA,SIMULATOR",
        ai_requests_per_minute=600,
        **overrides,
    )


def _route(provider: _ScriptedProvider, *, model_priority: int) -> AIRoute:
    return AIRoute(
        route_id=f"google/{provider.model}/key-1",
        provider_name=provider.name,
        model=provider.model,
        credential_id="key-1",
        credential_fingerprint="test",
        tier=ModelTier.STANDARD,
        provider=provider,
        provider_priority=0,
        model_priority=model_priority,
        credential_priority=0,
    )


def _dispatcher(*providers: _ScriptedProvider) -> tuple[FinalDispatcher, Any]:
    configuration = load_ai_gateway_configuration(CONFIG).configuration
    routes = tuple(
        _route(provider, model_priority=index) for index, provider in enumerate(providers)
    )
    pool = AIRoutePool(routes, configuration)
    dispatcher = FinalDispatcher(settings=_settings(), route_pool=pool, interception=ALLOW_ALL)
    return dispatcher, configuration


def _request(configuration: Any, **overrides: Any) -> DispatchRequest:
    # A real configured task rather than a fixture, so the retry ceilings under
    # test are the ones the repository actually ships (`maximumAttemptsPerRoute:
    # 1`, `maximumTotalAttempts: 6`). A hand-built task would only prove the
    # mechanism can be made to work in principle.
    task = configuration.tasks[_ORDER_AGENT_TASK]
    return DispatchRequest(
        task_id=_ORDER_AGENT_TASK,
        task=task,
        system_prompt="answer in json",
        payload={"mode": "DECIDE", "contextJson": "{}", "validationError": ""},
        request_digest="digest-1",
        estimated_tokens=8,
        **overrides,
    )


def _diagnose(*, payload: Any, error: BaseException, response_text: str) -> dict[str, Any]:
    return {
        **payload,
        "mode": "CORRECT_ACTION",
        "invalidActionJson": response_text,
        "validationError": str(error),
    }


def _validate(response: ProviderResponse) -> _Answer:
    return parse_structured_response(response.text, _Answer)


# ---------------------------------------------------------------------------
# The generic mechanism, at the boundary that owns failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_route_that_nearly_answered_is_asked_again_before_any_other() -> None:
    """The failover that was happening, and must not.

    The first route is the one that did the work: it produced the envelope, it
    holds the prompt cache, and it is one field from correct. Moving to a
    different model discards all of that and asks a stranger the original
    question -- which is what `breakdown=RESPONSE_INVALID` on a turn that took
    nearly forty seconds actually meant.
    """
    first = _ScriptedProvider("model-a", _NEARLY_VALID, _VALID)
    second = _ScriptedProvider("model-b", _VALID)
    dispatcher, configuration = _dispatcher(first, second)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-1",
        validate=_validate,
        observer=observer,
    )

    assert outcome.succeeded
    assert outcome.value is not None and outcome.value.plan == "orders-by-customer"
    # Two asks of the *same* route, and the second route never reached at all.
    assert len(first.requests) == 2
    assert second.requests == []
    assert outcome.route is not None and outcome.route.model == "model-a"
    # The retry is announced as a repair rather than looking like a blind
    # duplicate: two `attempt_started` lines naming one model is exactly what a
    # reader previously misread as the loop spinning.
    assert observer.corrections == [("google/model-a/key-1", 1)]


@pytest.mark.asyncio
async def test_the_second_ask_carries_the_diagnosis_and_the_rejected_body() -> None:
    """The retry is a different question, which is what licenses it at all.

    `_TERMINAL_FOR_ROUTE` is right to hold `RESPONSE_INVALID` for a byte-identical
    retry. The only reason asking again is not a waste is that these three fields
    changed, so they are asserted rather than assumed -- and everything else is
    asserted *unchanged*, because a retry that also altered the question would be
    a second turn wearing the first one's identity.
    """
    provider = _ScriptedProvider("model-a", _NEARLY_VALID, _VALID)
    dispatcher, configuration = _dispatcher(provider)

    await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-2",
        validate=_validate,
        observer=DispatchObserver(),
    )

    first, second = provider.payloads
    assert first["validationError"] == ""
    assert "invalidActionJson" not in first
    assert second["mode"] == "CORRECT_ACTION"
    # The model's own words back, verbatim: the cheapest repair is a patch of the
    # text it already produced.
    assert second["invalidActionJson"] == _NEARLY_VALID
    # The real exception, naming the conditional field. A synthesised summary
    # would be worse than nothing -- the model would repair the summary.
    assert "missing plan for kind QUERY" in second["validationError"]
    assert second["contextJson"] == first["contextJson"]
    # Never recomputed: the digest is what ties one dispatch's attempt rows
    # together and what a resumable interception identity is derived from.
    assert provider.requests[0].system_prompt == provider.requests[1].system_prompt


@pytest.mark.asyncio
async def test_the_repair_is_offered_once_and_then_the_loop_fails_over() -> None:
    """A model that cannot fix its own output when told exactly what was wrong
    with it will not fix it when told twice.

    So the grant is one per route, and the fallback after it is the failover that
    existed before -- unchanged, including which route answers.
    """
    first = _ScriptedProvider("model-a", _NEARLY_VALID)
    second = _ScriptedProvider("model-b", _VALID)
    dispatcher, configuration = _dispatcher(first, second)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-3",
        validate=_validate,
        observer=observer,
    )

    assert outcome.succeeded
    assert outcome.route is not None and outcome.route.model == "model-b"
    assert len(first.requests) == 2, "the repair was offered exactly once"
    assert len(second.requests) == 1
    assert len(observer.corrections) == 1
    # The correction attempt is a real attempt: it is numbered, it is priced, and
    # it is in the failure tally. A retry the breakdown could not see is how
    # `attempts=` came to be unreconcilable with the attempt rows.
    assert outcome.attempts == 3
    assert outcome.failure_summary == {"RESPONSE_INVALID": 2}


@pytest.mark.asyncio
async def test_a_dispatch_with_no_diagnosis_hook_fails_over_exactly_as_before() -> None:
    """The regression guard for every caller that has not opted in.

    `on_response_invalid` is gated on a task declaring `validationError` in
    `allowedInputKeys`, so today the Graph Analyzer has no hook. Without one there
    is no corrected question, so there is nothing to re-ask -- and the loop must
    behave byte-for-byte as it did.
    """
    first = _ScriptedProvider("model-a", _NEARLY_VALID)
    second = _ScriptedProvider("model-b", _VALID)
    dispatcher, configuration = _dispatcher(first, second)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration),
        trace_id="trace-4",
        validate=_validate,
        observer=observer,
    )

    assert outcome.succeeded
    assert len(first.requests) == 1, "no diagnosis, so no second ask"
    assert len(second.requests) == 1
    assert observer.corrections == []
    assert outcome.attempts == 2


@pytest.mark.asyncio
async def test_a_hook_that_raises_buys_no_retry() -> None:
    """A broken diagnosis must not become a doubled provider bill.

    `_diagnosed` already swallows a raising hook so failover survives it. What is
    asserted here is the consequence for the grant: the licence to re-ask is "the
    payload changed", and a hook that blew up changed nothing.
    """

    def explode(*, payload: Any, error: BaseException, response_text: str) -> dict[str, Any]:
        del payload, error, response_text
        raise RuntimeError("the diagnosis builder has a bug")

    first = _ScriptedProvider("model-a", _NEARLY_VALID)
    second = _ScriptedProvider("model-b", _VALID)
    dispatcher, configuration = _dispatcher(first, second)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=explode),
        trace_id="trace-5",
        validate=_validate,
        observer=observer,
    )

    assert outcome.succeeded
    assert len(first.requests) == 1
    assert observer.corrections == []


@pytest.mark.asyncio
async def test_a_hook_that_adds_nothing_buys_no_retry() -> None:
    """The other half of the same rule, and the reason identity is the test.

    A hook is free to decide this particular failure is not worth re-asking
    about. Returning the payload it was handed says so, and must not be read as a
    corrected question -- otherwise every hookless-in-practice caller silently
    doubles its attempts on a bad day.
    """

    def unchanged(*, payload: Any, error: BaseException, response_text: str) -> Any:
        del error, response_text
        return payload

    provider = _ScriptedProvider("model-a", _NEARLY_VALID)
    dispatcher, configuration = _dispatcher(provider)
    observer = _RecordingObserver()

    await dispatcher.dispatch(
        _request(configuration, on_response_invalid=unchanged),
        trace_id="trace-6",
        validate=_validate,
        observer=observer,
    )

    assert len(provider.requests) == 1
    assert observer.corrections == []


#: Four rejections that are wrong in four *distinguishable* ways, so each one
#: produces a diagnosis that differs from the one before it. Without that the
#: rebuilt payload is identical to what the previous route already carried, which
#: is deliberately read as "the question did not change" -- see
#: `test_a_route_handed_an_already_diagnosed_question_is_not_asked_it_twice`.
_FOUR_DISTINCT_REJECTIONS = (
    '{"kind":"QUERY"}',
    '{"kind":"QUERY","plan":null}',
    '{"kind":"QUERY","note":"b"}',
    '{"kind":"QUERY","note":"c"}',
)


@pytest.mark.asyncio
async def test_the_repair_never_outruns_the_turn_budget() -> None:
    """An extra ask is still an ask, and both ceilings still bind it.

    A correction that could run past `maximumTotalAttempts` would trade a 503 the
    caller can retry for a request that does not come back, which is the failure
    mode the global deadline exists for. Four routes each earning a repair would
    be eight attempts; the shipped ceiling is six, so the loop stops at six -- and
    the tally it reports has to add up to that, because
    `..._model_attempts_exhausted attempts=N breakdown=...` is the one line an
    operator reads to decide whether one thing failed six times or six things
    failed once.
    """
    providers = [
        _ScriptedProvider(f"model-{index}", text)
        for index, text in enumerate(_FOUR_DISTINCT_REJECTIONS)
    ]
    dispatcher, configuration = _dispatcher(*providers)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-7",
        validate=_validate,
        observer=observer,
    )

    assert not outcome.succeeded
    assert outcome.attempts == 6
    assert sum(outcome.failure_summary.values()) == 6
    assert observer.exhaustion == [(6, "RESPONSE_INVALID", {"RESPONSE_INVALID": 6})]
    # Three routes asked, the fourth never reached: the budget ran out mid-list
    # rather than the repair quietly stealing an extra pass over the whole set.
    assert [len(provider.requests) for provider in providers] == [2, 2, 2, 0]


@pytest.mark.asyncio
async def test_a_route_handed_an_already_diagnosed_question_is_not_asked_it_twice() -> None:
    """The property that keeps this from doubling every failing turn's bill.

    `state.payload` carries the diagnosis forward, so the *second* route's first
    attempt already asks the corrected question. If it fails the same way, the
    rebuilt payload is byte-identical to the one it was just sent -- so there is
    no corrected question left to ask, and `_diagnosed` says so by handing back
    the same object. Four routes rejecting identically therefore cost five
    attempts, not eight: one repair, on the route that had something new to be
    told.
    """
    providers = [_ScriptedProvider(f"model-{index}", _NEARLY_VALID) for index in range(4)]
    dispatcher, configuration = _dispatcher(*providers)
    observer = _RecordingObserver()

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-10",
        validate=_validate,
        observer=observer,
    )

    assert not outcome.succeeded
    assert [len(provider.requests) for provider in providers] == [2, 1, 1, 1]
    assert len(observer.corrections) == 1
    assert outcome.attempts == 5
    assert outcome.failure_summary == {"RESPONSE_INVALID": 5}


@pytest.mark.asyncio
async def test_a_route_that_spent_its_repair_is_not_re_queued_by_the_escalation() -> None:
    """Two asks per route, total -- not two and then a third at another tier.

    `ORDER_AGENT_REASONING_V1` sets `allowTierEscalation`, and where both tiers
    resolve to one route the escalated pass re-queues the identical model. The
    comment in `dispatch` deliberately exempts `RESPONSE_INVALID` from the
    TIMEOUT suppression because on a keyless deployment the second ask is how the
    operator learns what was wrong with their answer. That second ask now happens
    on the route itself, so escalating would be the *third* -- a third hold in
    front of the same person, holding the same question.
    """
    provider = _ScriptedProvider("model-a", _NEARLY_VALID)
    configuration = load_ai_gateway_configuration(CONFIG).configuration
    standard = _route(provider, model_priority=0)
    lightweight = AIRoute(
        # The same `route_id`: this is the identity of the thing that would be
        # called, and it is what makes both tiers "one route" in a MANUAL
        # deployment.
        route_id=standard.route_id,
        provider_name=standard.provider_name,
        model=standard.model,
        credential_id=standard.credential_id,
        credential_fingerprint=standard.credential_fingerprint,
        tier=ModelTier.LIGHTWEIGHT,
        provider=provider,
        provider_priority=0,
        model_priority=0,
        credential_priority=0,
    )
    pool = AIRoutePool((standard, lightweight), configuration)
    dispatcher = FinalDispatcher(settings=_settings(), route_pool=pool, interception=ALLOW_ALL)

    outcome = await dispatcher.dispatch(
        _request(
            configuration,
            on_response_invalid=_diagnose,
            allow_tier_escalation=True,
        ),
        trace_id="trace-8",
        validate=_validate,
        observer=DispatchObserver(),
    )

    assert not outcome.succeeded
    assert len(provider.requests) == 2, "the escalation must not ask a third time"


# ---------------------------------------------------------------------------
# The Order Agent's vocabulary, end to end through the real gateway
# ---------------------------------------------------------------------------


def _agent_context() -> AgentTurnContext:
    return AgentTurnContext(
        conversation_id="conv-repair-1",
        client_turn_id="turn-1",
        agent_id="order-discovery-agent",
        user_message="I need to return something from order CQ363350",
        as_of=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        session_timezone="UTC",
        schema_version="v1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="p1",
        prompt_version="pv1",
        compact_schema={"capabilities": ["order-discovery"]},
        conversation_state={},
    )


#: An envelope that parses as JSON, constructs as an `AgentAction`, and is then
#: refused by `validate_action_payload` -- the live failure, reproduced by shape
#: rather than described.
_MISSING_QUERY_PLAN = json.dumps(
    {
        "business_capability": "order-discovery",
        "action_type": ActionType.GRAPH_QUERY.value,
        "decision_summary": "Look the order up by its number.",
    }
)

_REPAIRED = json.dumps(
    {
        "business_capability": "order-discovery",
        "action_type": ActionType.CLARIFY.value,
        "decision_summary": "Confirm the number before querying.",
        "response": {
            "status": "NEEDS_CLARIFICATION",
            "business_capability": "order-discovery",
            "statements": [],
            "suggestions": [],
            "requested_input": "Is CQ363350 the order number on the receipt?",
        },
    }
)


def _agent_gateway(provider: _ScriptedProvider) -> RoutePoolReasoningModelGateway:
    configuration = load_ai_gateway_configuration(CONFIG).configuration
    settings = _settings()
    pool = AIRoutePool((_route(provider, model_priority=0),), configuration)
    return RoutePoolReasoningModelGateway(
        settings=settings,
        configuration=configuration,
        route_pool=pool,
        task_id=_ORDER_AGENT_TASK,
    )


@pytest.mark.asyncio
async def test_a_parse_time_validation_error_now_reaches_correct_action_mode() -> None:
    """The gap that made the correction mechanism unreachable.

    `correct_action` in `model_gateway` takes an `AgentAction`, and all three
    `_invoke_correction` call sites in `graph_nodes` hand it one that parsed and
    then failed a guard. A `ValidationError` from
    `AgentAction.validate_action_payload` has no object to hand over, so
    CORRECT_ACTION could not be reached for the most common real failure -- the
    reply was counted as a failed provider attempt and the router moved on.

    Nothing was configured to fix this: `mode`, `invalidActionJson` and
    `validationError` are already in `allowedInputKeys` for the base task and all
    five stage tasks, and `not-asking-twice` already ends "In correction modes,
    repair only the validation error supplied."
    """
    provider = _ScriptedProvider("model-a", _MISSING_QUERY_PLAN, _REPAIRED)
    gateway = _agent_gateway(provider)

    result = await gateway.decide(_agent_context())

    assert result.action.action_type is ActionType.CLARIFY
    assert len(provider.requests) == 2

    first, second = provider.payloads
    assert first["mode"] == "DECIDE"
    assert first["invalidActionJson"] == ""
    assert first["validationError"] == ""

    assert second["mode"] == "CORRECT_ACTION"
    # The body the model actually produced, so the repair is a patch rather than a
    # re-derivation of a turn that already took most of a minute. Compared as
    # parsed JSON rather than as bytes because `redact_payload` descends into
    # JSON-encoded strings and re-emits them -- key order and separators are the
    # masker's, and asserting on them would make this a test of the masker.
    assert json.loads(second["invalidActionJson"]) == json.loads(_MISSING_QUERY_PLAN)
    # `describe_parse_failure`'s output: the exception type -- which separates "not
    # JSON at all" from "JSON the schema refused" -- and pydantic's own text
    # naming the conditional field.
    assert second["validationError"].startswith("ValidationError:")
    assert "missing payload for action type GRAPH_QUERY" in second["validationError"]
    # The question itself is untouched. A repair that also moved the goalposts
    # would be a different turn.
    assert second["contextJson"] == first["contextJson"]


@pytest.mark.asyncio
async def test_a_reply_the_model_cannot_repair_still_fails_the_turn() -> None:
    """The honest outcome when the repair does not work.

    A reasoning loop must never be handed a fabricated action, so an exhausted
    dispatch raises exactly as it always did. What changed is only that the model
    was asked -- once -- before the turn was given up on.
    """
    provider = _ScriptedProvider("model-a", _MISSING_QUERY_PLAN)
    gateway = _agent_gateway(provider)

    with pytest.raises(StandardReasoningUnavailable):
        await gateway.decide(_agent_context())

    assert len(provider.requests) == 2
    assert provider.payloads[1]["mode"] == "CORRECT_ACTION"


@pytest.mark.asyncio
async def test_the_correction_retry_is_visible_in_the_log_stream_it_was_missed_in() -> None:
    """The bug was diagnosed from `order_agent_*` log lines, so the repair says so
    there too.

    Without a line of its own, two consecutive `order_agent_model_attempt_started`
    entries naming one provider read as the loop spinning -- which is how
    `breakdown=RESPONSE_INVALIDx2` came to be interpreted as two independent
    models failing rather than one answer nobody asked about.
    """
    provider = _ScriptedProvider("model-a", _MISSING_QUERY_PLAN, _REPAIRED)
    gateway = _agent_gateway(provider)
    logger = logging.getLogger("return_platform.dynamic_knowledge.model_gateway")

    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    sink = _Sink(level=logging.INFO)
    logger.addHandler(sink)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        await gateway.decide(_agent_context())
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous)

    retries = [
        record
        for record in records
        if record.getMessage().startswith("order_agent_response_correction_retry")
    ]
    assert len(retries) == 1
    # Read off `extra`, not the message text, because that is the form a log
    # aggregator indexes -- the two fields an operator would filter the stream by.
    assert retries[0].__dict__["model"] == "model-a"
    assert retries[0].__dict__["error_type"] == "ValidationError"


# ---------------------------------------------------------------------------
# The invoker keeps its generic default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_caller_that_supplies_no_hook_still_gets_the_generic_diagnosis() -> None:
    """`structured_invocation` must not have lost the behaviour it already had.

    The generic hook fills `validationError` and nothing else, because that is the
    only key every opted-in task declares. It is what makes the retry a different
    question for a caller with no vocabulary of its own -- and therefore what
    earns that caller the extra ask too.
    """
    provider = _ScriptedProvider("model-a", _NEARLY_VALID, _VALID)
    configuration = load_ai_gateway_configuration(CONFIG).configuration
    pool = AIRoutePool((_route(provider, model_priority=0),), configuration)
    invoker: StructuredOutputInvoker[_Answer] = StructuredOutputInvoker(
        settings=_settings(),
        configuration=configuration,
        route_pool=pool,
        task_id=_ORDER_AGENT_TASK,
        response_model=_Answer,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        interception=ALLOW_ALL,
    )

    invocation = await invoker.invoke(
        payload={"mode": "DECIDE", "contextJson": "{}", "validationError": ""},
        size_probe="{}",
        log_context={},
    )

    assert invocation.value.plan == "orders-by-customer"
    first, second = provider.payloads
    assert first["validationError"] == ""
    assert second["validationError"].startswith("ValidationError:")
    # The generic hook has no key for a rejected body and does not invent one: a
    # field the task's prompt has never mentioned is the same defect as the empty
    # diagnosis it replaced.
    assert "invalidActionJson" not in second
    assert second["mode"] == "DECIDE"


class _CountingPolicy(InterceptionPolicy):
    """Permissive, but it counts. A subclass rather than a patch of `ALLOW_ALL`,
    which is a module-level singleton every other dispatch in the process shares."""

    def __init__(self) -> None:
        self.verdicts: list[str] = []

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        verdict = await super().decide(request)
        self.verdicts.append(verdict.decision.value)
        return verdict


@pytest.mark.asyncio
async def test_the_dispatch_decision_is_still_the_only_way_to_a_provider() -> None:
    """A guard on the shape of the change, not the behaviour.

    The correction retry lives *inside* `_attempt_routes`, after the single
    interception decision, so it cannot become a second entry into the boundary
    that skips the gate -- which is the mistake a "call the invoker again in
    correction mode" implementation would have made: a second `dispatch` means a
    second C7 verdict, a second attempt series and a second global deadline. One
    dispatch, one verdict, however many attempts.
    """
    provider = _ScriptedProvider("model-a", _NEARLY_VALID, _VALID)
    configuration = load_ai_gateway_configuration(CONFIG).configuration
    pool = AIRoutePool((_route(provider, model_priority=0),), configuration)
    policy = _CountingPolicy()
    dispatcher = FinalDispatcher(settings=_settings(), route_pool=pool, interception=policy)

    outcome = await dispatcher.dispatch(
        _request(configuration, on_response_invalid=_diagnose),
        trace_id="trace-9",
        validate=_validate,
        observer=DispatchObserver(),
    )

    assert outcome.succeeded
    assert len(provider.requests) == 2
    assert policy.verdicts == [DispatchDecision.ALLOW_PROVIDER.value]
