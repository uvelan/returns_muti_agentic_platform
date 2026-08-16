"""A Copilot turn with no provider credential is *held*, not failed.

The symptom was a 503 on every conversational turn:

    All ORDER_AGENT_REASONING_V1 routes and lightweight fallbacks failed
    attempts=0  last_error=PROVIDER_UNAVAILABLE  failures=none

`attempts=0` is the whole diagnosis. Nothing was tried, because nothing was
*constructible*: with no `*_API_KEY` and no Vault reference, `_provider_credentials`
returns an empty tuple for GOOGLE and NVIDIA, so the model x credential loop in
`build_routes` produces no route for either -- and SIMULATOR contributes only a
LIGHTWEIGHT eligibility route that this STANDARD task does not permit. An empty
candidate set is not a failure the failover loop can report, so it reported the
initial state of `_LoopState` and gave up.

Nothing in the *task* was wrong. `ORDER_AGENT_REASONING_V1` has always listed
MANUAL in `allowedProviders`, `_provider_models` has always offered MANUAL at
every tier, and `routes.py` has always exempted it from the credential
requirement. The one missing piece was `ai_provider_order`, which named only
providers that need a key. So the fix is configuration, and these are the
assertions that keep it fixed -- a route set is the kind of thing that is
correct until an unrelated edit to an env file makes it silently empty again.

What is *not* relaxed anywhere here: the human's answer takes the identical path
a model's would. It is parsed against `AgentAction`, it passes `inspect_output`,
and it is reported as MANUAL / manual-human-v1 so an evaluation set built from
traces can never absorb it as model output.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.routing.routes import build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    load_ai_gateway_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
    StandardReasoningUnavailable,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentTurnContext,
)

_TASK_ID = "ORDER_AGENT_REASONING_V1"

#: The order the repository ships: real providers first, MANUAL last. MANUAL is
#: the keyless *fallback*, so a deployment holding a credential never reaches a
#: human and a deployment holding none never fails the turn.
_KEYLESS_ORDER = "GOOGLE,NVIDIA,SIMULATOR,MANUAL"

#: The state that produced the defect, kept as a fixture rather than a memory.
_ORDER_WITHOUT_MANUAL = "GOOGLE,NVIDIA,SIMULATOR"


def _settings(order: str, **overrides: Any) -> Settings:
    """A keyless deployment.

    `model_construct` rather than `Settings(...)`: the suite's conftest loads the
    repository `.env`, and a test about "what happens when there is no
    credential" must not inherit one that happens to be sitting on the machine.
    Unset fields still take their declared defaults, so every empty key list and
    empty model pool below is the real default rather than a fabrication.
    """
    return Settings.model_construct(
        environment="test",
        ai_provider_order=order,
        ai_max_payload_bytes=1_000_000,
        ai_timeout_seconds=30.0,
        ai_global_timeout_seconds=60.0,
        **overrides,
    )


def _configuration() -> AIGatewayConfiguration:
    """The packaged gateway document, not a hand-built one.

    The point of these tests is that the *shipped* task and the *shipped*
    provider order compose into a usable route; a fixture task would assert only
    that the code can be made to work in principle.
    """
    settings = Settings.model_construct(environment="test")
    return load_ai_gateway_configuration(settings.ai_gateway_configuration_path).configuration


# --- the route that was missing ---------------------------------------------


def test_a_keyless_deployment_still_builds_a_standard_reasoning_route() -> None:
    routes = build_routes(_settings(_KEYLESS_ORDER))

    standard_manual = [
        route
        for route in routes
        if route.provider_name == "MANUAL" and route.tier is ModelTier.STANDARD
    ]

    assert standard_manual, "a keyless deployment has no STANDARD route to reason on"
    assert standard_manual[0].model == "manual-human-v1"
    # No credential was needed to build it, which is the property that makes it
    # reachable at all on a machine with no keys.
    assert standard_manual[0].credential_fingerprint is None


@pytest.mark.asyncio
async def test_the_reasoning_task_can_actually_select_that_route() -> None:
    """Existing on the pool is not enough -- `candidates` has to return it.

    This is the assertion that stands directly opposite `attempts=0`: the task's
    tier gate, its `allowedProviders` and the route's availability all have to
    agree, and any one of them disagreeing produces the empty candidate set the
    failover loop cannot describe.
    """
    configuration = _configuration()
    pool = AIRoutePool(build_routes(_settings(_KEYLESS_ORDER)), configuration)

    candidates = await pool.candidates(configuration.tasks[_TASK_ID], task_id=_TASK_ID)

    assert candidates, "no candidate route: the turn would fail with attempts=0"
    assert {route.provider_name for route in candidates} == {"MANUAL"}


@pytest.mark.asyncio
async def test_without_manual_a_keyless_deployment_has_no_candidate_at_all() -> None:
    """The defect itself, pinned so the diagnosis survives the fix.

    Without this, a future reader has no way to tell whether the test above is
    asserting something that was ever in doubt.
    """
    configuration = _configuration()
    pool = AIRoutePool(build_routes(_settings(_ORDER_WITHOUT_MANUAL)), configuration)

    candidates = await pool.candidates(configuration.tasks[_TASK_ID], task_id=_TASK_ID)

    assert candidates == ()


@pytest.mark.asyncio
async def test_a_real_credential_outranks_the_human() -> None:
    """MANUAL is the fallback, never the preference.

    A deployment that *does* hold a key must not start diverting reasoning
    turns to an operator queue, which is exactly what putting MANUAL first in
    the order would do. Route order is provider order, so this is the assertion
    that the shipped ordering means what it says.
    """
    configuration = _configuration()
    keyed = _settings(
        _KEYLESS_ORDER,
        google_api_keys=(SecretStr("test-key"),),
        google_standard_models=("models/gemini-3.6-flash",),
    )
    pool = AIRoutePool(build_routes(keyed), configuration)

    candidates = await pool.candidates(configuration.tasks[_TASK_ID], task_id=_TASK_ID)

    assert candidates[0].provider_name == "GOOGLE"
    assert "MANUAL" in {route.provider_name for route in candidates}


def test_manual_is_structurally_unbuildable_in_production() -> None:
    """No new gate was added for this, because one already existed.

    Settings refuses the provider order outright outside development and test,
    so the route cannot be constructed at all -- which is a stronger guarantee
    than the providers' own `POLICY_BLOCKED`, and both are in force.
    """
    # GOOGLE and NVIDIA are named alongside it so the refusal is demonstrably
    # about MANUAL and not about the order being empty.
    with pytest.raises(ValueError, match="MANUAL cannot be configured in production"):
        Settings(environment="production", ai_provider_order="GOOGLE,NVIDIA,MANUAL")


# --- the answer travels the same path a model's would ------------------------


class _Store:
    """An in-memory interception store, answered by the test's own operator."""

    def __init__(self) -> None:
        self.records: dict[str, Interception] = {}
        self.payloads: dict[str, dict[str, Any]] = {}

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: dict[str, Any],
        resume: ResumeCommand,
        expires_at: datetime,
    ) -> Interception:
        record = Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        self.records[interception_id] = record
        self.payloads[interception_id] = dict(request_payload)
        return record

    async def get(self, interception_id: str) -> Interception | None:
        return self.records.get(interception_id)

    async def request_payload(self, interception_id: str) -> dict[str, Any] | None:
        return self.payloads.get(interception_id)

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        record = self.records[interception_id]
        updated = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=InterceptionStatus.ANSWERED,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=datetime.now(UTC),
            answered_by=answered_by,
            response_text=response_text,
        )
        self.records[interception_id] = updated
        self.payloads[interception_id]["responseText"] = response_text
        return updated

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        raise NotImplementedError

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        record = self.records.get(interception_id)
        if record is None or record.status is not InterceptionStatus.PENDING:
            return
        self.records[interception_id] = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=status,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        return [r for r in self.records.values() if r.status is InterceptionStatus.PENDING][:limit]


async def _answer_every_pending(store: _Store, text: str) -> list[dict[str, Any]]:
    """Stand in for the operator working the AI Control Center queue.

    Answers every request rather than only the first: a rejected answer makes
    the gateway re-request on the next route, and an operator who answers once
    leaves each retry to wait out the provider timeout instead of failing
    promptly.
    """
    seen: list[dict[str, Any]] = []
    answered: set[str] = set()
    while True:
        for interception_id, record in list(store.records.items()):
            if record.status is not InterceptionStatus.PENDING or interception_id in answered:
                continue
            seen.append(dict(store.payloads[interception_id]))
            await store.answer(
                interception_id=interception_id, response_text=text, answered_by="operator-1"
            )
            answered.add(interception_id)
        await asyncio.sleep(0.01)


def _gateway(store: _Store) -> RoutePoolReasoningModelGateway:
    configuration = _configuration()
    settings = _settings(_KEYLESS_ORDER, ai_manual_handoff="UI")
    pool = AIRoutePool(build_routes(settings, interception_store=store), configuration)
    return RoutePoolReasoningModelGateway(
        settings=settings, configuration=configuration, route_pool=pool, task_id=_TASK_ID
    )


def _context() -> AgentTurnContext:
    return AgentTurnContext(
        conversation_id="conv-keyless-1",
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


def _valid_action() -> str:
    """What an operator types: the JSON the model would have returned."""
    return json.dumps(
        {
            "business_capability": "order-discovery",
            "action_type": ActionType.CLARIFY.value,
            "decision_summary": "No anchor yet; ask for the one detail that narrows it.",
            "response": {
                "status": "NEEDS_CLARIFICATION",
                "business_capability": "order-discovery",
                "statements": [],
                "suggestions": [],
                "requested_input": "Is CQ363350 the order number on the receipt?",
            },
        }
    )


@pytest.mark.asyncio
async def test_a_human_answers_the_turn_and_is_reported_as_manual() -> None:
    store = _Store()
    gateway = _gateway(store)

    operator = asyncio.create_task(_answer_every_pending(store, _valid_action()))
    try:
        invocation = await gateway.decide(_context())
    finally:
        operator.cancel()

    assert invocation.action.action_type is ActionType.CLARIFY
    # Never the provider whose place the human took.
    assert invocation.provider == "MANUAL"
    assert invocation.model == "manual-human-v1"


@pytest.mark.asyncio
async def test_the_held_request_carries_what_a_human_needs_to_answer_it() -> None:
    """An operator cannot write a valid `AgentAction` from an opaque id.

    The sealed payload has to contain both halves of the real request: the
    system prompt -- which carries the required response schema -- and the
    serialized turn context the model would have reasoned over.
    """
    store = _Store()
    gateway = _gateway(store)

    operator = asyncio.create_task(_answer_every_pending(store, _valid_action()))
    try:
        await gateway.decide(_context())
    finally:
        operator.cancel()

    (payload,) = store.payloads.values()
    assert "REQUIRED RESPONSE SCHEMA" in payload["systemPrompt"]
    assert payload["userPayload"]["mode"] == "DECIDE"
    context = json.loads(payload["userPayload"]["contextJson"])
    assert context["user_message"] == "I need to return something from order CQ363350"


@pytest.mark.asyncio
async def test_a_malformed_human_answer_is_rejected_not_coerced() -> None:
    """A person is at least as able to paste something malformed as a model is.

    The response contract is the same one a model's output meets, so a bad paste
    fails the turn rather than becoming untyped state on the most privileged
    path in the system.
    """
    store = _Store()
    gateway = _gateway(store)

    operator = asyncio.create_task(_answer_every_pending(store, '{"not": "an AgentAction"}'))
    try:
        with pytest.raises(StandardReasoningUnavailable):
            await gateway.decide(_context())
    finally:
        operator.cancel()


@pytest.mark.asyncio
async def test_the_retry_after_a_bad_answer_tells_the_operator_what_was_wrong() -> None:
    """D5. A parse failure used to be retried with a byte-identical payload.

    On a keyless deployment the "next route" after a rejected answer is the same
    person: MANUAL at STANDARD is exhausted, the task escalates, and MANUAL at
    LIGHTWEIGHT opens a second hold. That second hold used to arrive with
    `validationError: ""` -- so the operator was being asked to improve on an
    answer whose defect the platform knew precisely and did not pass on. Two
    turns of the live E2E died `RESPONSE_INVALIDx2` exactly this way.

    What is asserted is that the diagnosis is *real*: the pydantic error names
    the field the malformed answer was missing. A plausible-sounding message
    synthesised by the platform would be worse than the empty string it replaces.
    """
    store = _Store()
    gateway = _gateway(store)

    operator = asyncio.create_task(_answer_every_pending(store, '{"not": "an AgentAction"}'))
    try:
        with pytest.raises(StandardReasoningUnavailable):
            await gateway.decide(_context())
    finally:
        operator.cancel()

    held = list(store.payloads.values())
    assert len(held) > 1, "the escalation must have opened a second hold to diagnose"

    first, second = held[0]["userPayload"], held[1]["userPayload"]
    # The first hold is the original question and carries no diagnosis, because
    # at that point nothing had gone wrong yet.
    assert first["validationError"] == ""
    diagnosis = second["validationError"]
    assert diagnosis, "the operator is being re-asked with no idea what was wrong"
    # The real exception, not a description of one: pydantic's own text, naming
    # the type it raised and the fields the answer failed to supply.
    assert diagnosis.startswith("ValidationError:")
    assert "business_capability" in diagnosis
    assert "action_type" in diagnosis
    # Everything else about the request is unchanged -- the retry is the same
    # question plus the news, not a different question.
    assert second["mode"] == first["mode"]
    assert second["contextJson"] == first["contextJson"]


@pytest.mark.asyncio
async def test_a_hold_that_never_failed_carries_no_diagnosis() -> None:
    """The empty string is the honest answer when nothing has gone wrong.

    Guarding the other direction of D5: a diagnosis field that is populated on a
    first attempt would be an invented failure, and an operator reading one would
    correct a response that was never produced.
    """
    store = _Store()
    gateway = _gateway(store)

    operator = asyncio.create_task(_answer_every_pending(store, _valid_action()))
    try:
        await gateway.decide(_context())
    finally:
        operator.cancel()

    (payload,) = store.payloads.values()
    assert payload["userPayload"]["validationError"] == ""


@pytest.mark.asyncio
async def test_an_answer_that_names_a_forbidden_capability_is_still_refused() -> None:
    """Guards are not softened for a human.

    `business_capability` is validated against the active agent policy by
    `CapabilityGuard` inside the graph, and the shape is validated here -- an
    operator cannot widen the agent's scope by typing a capability the schema
    does not grant. This asserts the half that lives on the response contract:
    an action whose payload does not satisfy its own action type is rejected
    before any of that is reached.
    """
    store = _Store()
    gateway = _gateway(store)

    # CLARIFY without a `response` is exactly what `validate_action_payload`
    # exists to refuse.
    incomplete = json.dumps(
        {
            "business_capability": "order-discovery",
            "action_type": ActionType.CLARIFY.value,
            "decision_summary": "Missing the response the action type requires.",
        }
    )
    operator = asyncio.create_task(_answer_every_pending(store, incomplete))
    try:
        with pytest.raises(StandardReasoningUnavailable):
            await gateway.decide(_context())
    finally:
        operator.cancel()


def test_the_dispatch_ceiling_leaves_a_person_time_to_answer() -> None:
    """A ten-minute hold answered by a twelve-second dispatch timeout is a
    request nobody can ever answer.

    `FinalDispatcher` bounds every `provider.generate` call by
    `ai_timeout_seconds`, so that setting -- not the provider's own willingness
    to wait -- is what decides how long an operator actually has. The values the
    repository ships (280s per attempt inside an 850s budget) have to remain
    *expressible*, or MANUAL is configured but unusable.
    """
    settings = Settings(
        environment="test", ai_timeout_seconds=280.0, ai_global_timeout_seconds=850.0
    )

    assert settings.ai_timeout_seconds == 280.0
    # Comfortably inside what the provider is prepared to hold, so the operator
    # sees the request expire rather than the dispatcher abandoning it silently.
    assert settings.ai_timeout_seconds < 600.0
